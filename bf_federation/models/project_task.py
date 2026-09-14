"""La tâche, premier genre fédérable, et le seul qui retourne un état.

Tout ce qui est ici est le contrat de `federation.federable` rempli pour
`project.task` : la carte, le miroir, les trois verbes propres au genre
(`card`, `state`, `day`) et les deux tables de retournement d'état.

Le retournement est la seule idée qui ne se devine pas : « Attente - Client »
chez l'émetteur veut dire « à toi de jouer », donc le miroir naît « En cours » ;
et « Terminé » chez le receveur veut dire « ma part est finie », donc la tâche
revient « En cours » chez l'émetteur plutôt que de se fermer.
"""

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

from . import transport

# Chez le RECEVEUR d'une tâche (origine distante) : l'état de l'émetteur, retourné.
STATE_OWNER_TO_MIRROR = {
    "05_waiting_client": "01_in_progress",      # « à toi de jouer »
    "01_in_progress": "06_waiting_external",    # l'émetteur y travaille
    "04_waiting_normal": "04_waiting_normal",
    "06_waiting_external": "06_waiting_external",
    "02_changes_requested": "02_changes_requested",
    "03_approved": "03_approved",
    "1_done": "1_done",
    "1_canceled": "1_canceled",
}
# Chez l'ÉMETTEUR (origine locale) : ce que le receveur a fait de son miroir.
STATE_MIRROR_TO_OWNER = {
    "01_in_progress": "05_waiting_client",
    "05_waiting_client": "01_in_progress",
    "06_waiting_external": "01_in_progress",
    "04_waiting_normal": "04_waiting_normal",
    "02_changes_requested": "02_changes_requested",
    "03_approved": "03_approved",
    "1_done": "01_in_progress",                 # sa part est finie : la tâche revient
    "1_canceled": "01_in_progress",
}
WATCHED = ("name", "description", "priority", "state", "stage_id", "date_deadline", "active",
           "federation_peer_id", "federation_assignee_id")


def _as_id(value):
    """Un identifiant, qu'on ait écrit un entier, un enregistrement ou rien."""
    if hasattr(value, "_name"):
        return value.id if len(value) == 1 else False
    return value or False


def _state_label(env, state):
    selection = dict(env["project.task"]._fields["state"]._description_selection(env))
    return selection.get(state, state or "")


class ProjectTask(models.Model):
    _name = "project.task"
    _inherit = ["project.task", "federation.federable"]

    _federation_kind = "task"
    _federation_verbs = ("card", "state", "day")
    #: Le genre sait quoi faire d'un destinataire proposé. Les autres l'ignorent,
    #: et un pair qui en inscrit un sur un livrable ne fait poser aucune note.
    _federation_addressable = True

    federation_peer_id = fields.Many2one(
        help="Le pair chez qui cette tâche a un miroir. Vider le champ archive le miroir.")
    federation_assignee_id = fields.Many2one(
        "res.partner", string="Destinataire chez le pair", copy=False, tracking=True,
        domain="[('id', 'in', federation_assignee_allowed_ids)]",
        help="À qui, chez le pair, cette tâche est adressée. Une proposition, pas une "
             "assignation : le pair la résout avec sa propre table des personnes "
             "appariées, et garde le dernier mot. Vide : elle arrive chez la personne "
             "qu'il a désignée pour tout recevoir.")
    federation_assignee_allowed_ids = fields.Many2many(
        "res.partner", compute="_compute_federation_assignee_allowed",
        string="Personnes chez le pair")

    @api.depends("federation_peer_id", "federation_peer_id.partner_id")
    def _compute_federation_assignee_allowed(self):
        """Les personnes de l'organisation du pair, telles qu'ICI on les connaît.

        Rien n'est demandé au pair : ce sont nos propres contacts, sous sa fiche.
        C'est ce qui fait que proposer quelqu'un n'apprend rien à personne, dans
        aucun des deux sens.
        """
        for task in self:
            # Le pair peut appartenir à une société que l'utilisateur ne voit pas : le
            # formulaire de la tâche ne doit pas tomber pour autant.
            peer = task.federation_peer_id.sudo()
            maison = peer.partner_id.commercial_partner_id if peer.partner_id else False
            if not maison:
                task.federation_assignee_allowed_ids = self.env["res.partner"]
                continue
            task.federation_assignee_allowed_ids = self.env["res.partner"].search([
                ("id", "child_of", maison.id), ("is_company", "=", False),
                ("email", "!=", False), ("active", "=", True)])

    @api.constrains("federation_assignee_id", "federation_peer_id")
    def _check_federation_assignee(self):
        """Se tromper de destinataire chez un client à cinq pairs n'est pas une coquille."""
        for task in self:
            if not task.federation_assignee_id:
                continue
            if not task.federation_peer_id:
                raise ValidationError(
                    _("Adressez d'abord la tâche à un pair : sans pair, « %s » ne désigne personne.")
                    % task.federation_assignee_id.display_name)
            if task.federation_assignee_id not in task.federation_assignee_allowed_ids:
                raise ValidationError(
                    _("« %s » n'est pas une personne de %s.")
                    % (task.federation_assignee_id.display_name, task.federation_peer_id.name))

    def _federation_check_writer(self, vals):
        """Adresser une tâche fédérée demande le même rôle que la fédérer.

        La vue réserve le champ aux gestionnaires ; une vue n'est pas une garde. Par
        un appel direct, n'importe qui pouvant écrire la tâche ré-adressait le pair.
        """
        super()._federation_check_writer(vals)
        if "federation_assignee_id" not in vals or self.env.su:
            return
        # Un import qui réécrit la même valeur n'adresse rien : seul un changement compte.
        nouveau = _as_id(vals.get("federation_assignee_id"))
        change = bool(nouveau) if not self else any(t.federation_assignee_id.id != nouveau for t in self)
        if change and not self.env.user.has_group("project.group_project_manager"):
            raise AccessError(_("Adresser une tâche fédérée demande le rôle de gestionnaire de projet."))

    @api.onchange("federation_peer_id")
    def _onchange_federation_peer_assignee(self):
        if self.federation_assignee_id and self.federation_assignee_id not in self.federation_assignee_allowed_ids:
            self.federation_assignee_id = False

    # --- Le contrat ----------------------------------------------------------------
    def _federation_allowed_peers(self):
        self.ensure_one()
        return self.project_id.federation_peer_ids

    def _federation_label_the(self):
        return _("la tâche")

    def _federation_label_this(self):
        return _("cette tâche")

    def _federation_watched(self):
        return WATCHED

    def _federation_card(self):
        self.ensure_one()
        tz = self.env["federation.peer"]._our_tz()
        base = self.env["federation.peer"]._our_base_url()
        card = {
            "name": self.name, "description_text": transport.html_to_text(self.description),
            "day": transport.day_in_zone(self.date_deadline, tz), "tz": tz,
            "priority": self.priority or "0", "state": self.state,
            "url": f"{base}/odoo/project/{self.project_id.id}/tasks/{self.id}" if base else False,
        }
        # ⚠️ La clé n'est posée que s'il y a un destinataire. L'empreinte de carte
        # hache toutes les clés sauf `state`, `day`, `url` et `tz` : une clé toujours
        # présente changerait l'empreinte de tous les liens vivants et ferait repartir
        # une carte par tâche à la première écriture qui suit la mise à jour.
        # Et la personne doit être ENCORE de l'organisation du pair : un contact qui en
        # est sorti, ou un pair rattaché à une autre fiche, ne part plus sur la carte.
        # Vu en superutilisateur : la carte ne doit pas dépendre de qui a écrit la tâche,
        # sinon deux rédacteurs de sociétés différentes la feraient alterner.
        assignee = self.sudo().federation_assignee_id
        if assignee and assignee.email and assignee in self.sudo().federation_assignee_allowed_ids:
            card["assignee"] = {"name": assignee.name, "email": assignee.email}
        return card

    @api.model
    def _federation_receive(self, peer, card):
        """Le miroir naît dans le projet fermé du pair, chez la personne que NOUS choisissons.

        L'émetteur peut avoir adressé la tâche à quelqu'un. C'est une proposition :
        elle ne vaut que si le receveur a lui-même apparié ce courriel à un de ses
        comptes. Sinon, le repli, et une note qui dit à qui c'était adressé, pour
        que l'appariement manquant se répare en une ligne au lieu de se deviner.
        """
        project = peer._ensure_mirror_project()
        state = self._federation_available_state(
            STATE_OWNER_TO_MIRROR.get(card.get("state"), "01_in_progress"))
        nom_propose, courriel_propose = peer._proposed_assignee(card)
        resolu = peer._local_assignee(courriel_propose)
        assigne = resolu or peer._fallback_user()
        vals = {
            "name": transport.clean_text(card.get("name"), 500) or _("(sans titre)"),
            "description": self._federation_mirror_description(peer, card),
            "priority": card.get("priority") if card.get("priority") in ("0", "1") else "0",
            "project_id": project.id, "user_ids": [(6, 0, assigne.ids)], "partner_id": False,
            "company_id": peer.company_id.id, "federation_peer_id": peer.id,
        }
        if "time_of_day_id" in self._fields:
            vals["time_of_day_id"] = False
        stage = peer._stage_for(state)
        if stage:
            vals["stage_id"] = stage.id
        task = self.create(vals)
        task.write({"state": state})
        day = transport.valid_day(card.get("day"))
        if day:
            transport.write_deadline_day(task, day, peer._our_tz())
        if courriel_propose:
            task._federation_assignee_note(peer, nom_propose, courriel_propose, resolu)
        return task

    def _federation_assignee_note(self, peer, nom, courriel, resolu):
        """La note posée à la naissance du miroir quand le pair a adressé la tâche.

        Elle nomme l'échec quand il y en a un : un appariement manquant qu'on ne
        sait pas nommer ne se répare jamais.
        """
        self.ensure_one()
        peer._ensure_partner()
        qui = transport.html.escape(nom or courriel or "")
        repli = peer._fallback_user()
        if resolu:
            # Résolu, même si le compte est aussi celui du repli : la ligne existe.
            corps = _("<p>%(pair)s a adressé cette tâche à %(qui)s ; elle est assignée à "
                      "%(compte)s.</p>") % {"pair": transport.html.escape(peer.name),
                                            "qui": qui,
                                            "compte": transport.html.escape(resolu.display_name)}
        elif peer._identity_for(courriel):
            corps = _("<p>%(pair)s a adressé cette tâche à %(qui)s (%(courriel)s). Ce courriel est "
                      "apparié, mais la ligne ne donne aucun compte utilisable ici (compte portail, "
                      "plusieurs comptes, ou compte désigné devenu invalide) : elle est déposée chez "
                      "%(repli)s. Désignez un compte sur la ligne d'appariement.</p>") % {
                "pair": transport.html.escape(peer.name), "qui": qui,
                "courriel": transport.html.escape(courriel),
                "repli": transport.html.escape(repli.display_name) if repli else _("personne")}
        elif not repli:
            corps = _("<p>%(pair)s a adressé cette tâche à %(qui)s (%(courriel)s), qui ne figure "
                      "pas dans les personnes appariées de ce pair, et la personne du repli n'est "
                      "plus un compte interne actif : la tâche n'est assignée à personne.</p>") % {
                "pair": transport.html.escape(peer.name), "qui": qui,
                "courriel": transport.html.escape(courriel)}
        else:
            corps = _("<p>%(pair)s a adressé cette tâche à %(qui)s (%(courriel)s), qui ne figure "
                      "pas dans les personnes appariées de ce pair : elle est déposée chez "
                      "%(repli)s. Ajoutez la ligne d'appariement sur la fiche du pair pour que "
                      "la prochaine arrive au bon endroit.</p>") % {
                "pair": transport.html.escape(peer.name), "qui": qui,
                "courriel": transport.html.escape(courriel),
                "repli": transport.html.escape(repli.display_name)}
        self.sudo().with_context(federation_inbound=True).message_post(
            body=Markup(corps), message_type="comment", subtype_xmlid="mail.mt_note",
            author_id=peer.partner_id.id)

    def _federation_apply_card(self, link, card):
        self.ensure_one()
        vals = {"name": transport.clean_text(card.get("name"), 500) or self.name,
                "priority": card.get("priority") if card.get("priority") in ("0", "1") else "0"}
        if link.origin == "remote":
            vals["description"] = self._federation_mirror_description(link.peer_id, card)
        self._federation_silent().write(vals)
        # Le partage complet réapplique aussi l'état et le jour : le miroir réactivé
        # doit être à jour d'un coup, sans attendre le prochain changement.
        if link.origin == "remote":
            state = self._federation_available_state(
                STATE_OWNER_TO_MIRROR.get(card.get("state"), "01_in_progress"))
            stage = link.peer_id._stage_for(state)
            task = self._federation_silent()
            task.write({"state": state, **({"stage_id": stage.id} if stage else {})})
            day = transport.valid_day(card.get("day"))
            if day is not None:
                transport.write_deadline_day(task, day or False, link.peer_id._our_tz())
        return True

    def _federation_mirror_name(self):
        self.ensure_one()
        return self.display_name

    def _federation_notify_partners(self):
        self.ensure_one()
        return self.user_ids.mapped("partner_id") | self.project_id.user_id.partner_id

    @api.model
    def _federation_mirror_description(self, peer, card):
        header = _("<p><i>Tâche partagée par %s. L'état, le jour d'échéance, les messages et les pièces "
                   "jointes reviennent chez lui ; un message ou une note qui commence par 🔒 reste ici.</i></p>") \
            % transport.html.escape(peer.name)
        text = card.get("description_text") if isinstance(card.get("description_text"), str) else ""
        return header + transport.text_to_html(text)

    def _federation_share_note(self, peer):
        self.ensure_one()
        self.sudo().with_context(federation_inbound=True).message_post(
            body=_("Tâche fédérée avec %s : le miroir apparaîtra chez lui au prochain envoi. Ce qui part : nom, "
                   "description en texte, jour d'échéance, priorité, état, messages et pièces jointes sous le plafond. "
                   "Ce qui reste ici : les notes internes, les heures, les feuilles de temps.") % peer.name,
            message_type="comment", subtype_xmlid="mail.mt_note")

    # --- Les états ------------------------------------------------------------------
    @api.model
    def _federation_available_state(self, state):
        selection = dict(self._fields["state"].selection)
        if state in selection:
            return state
        # Sans les états d'attente client/externe, une tâche qui attend l'autre reste
        # « en cours » : l'état « Attente » d'Odoo est réservé aux dépendances et se
        # réinitialise tout seul.
        return "01_in_progress"

    def _federation_apply_state(self, link, data):
        self.ensure_one()
        remote_state = data.get("state") if isinstance(data.get("state"), str) else ""
        table = STATE_MIRROR_TO_OWNER if link.origin == "local" else STATE_OWNER_TO_MIRROR
        new_state = self._federation_available_state(table.get(remote_state, "01_in_progress"))
        task = self._federation_silent()
        if link.origin == "remote":
            stage = link.peer_id._stage_for(new_state)
            if stage and task.stage_id != stage:
                task.write({"stage_id": stage.id})
        if task.state != new_state:
            task.write({"state": new_state})
        known = remote_state in dict(self._fields["state"].selection)
        label = _state_label(self.env, remote_state) if known else transport.clean_text(remote_state, 40)
        back = _(" : la tâche revient ici") if (link.origin == "local" and new_state == "01_in_progress") else ""
        note = link._note(Markup(_("<p>Chez %s, la tâche est passée à « %s »%s.</p>")) % (link.peer_id.name, label, back))
        link._inbox_notify(note)
        return True

    def _federation_apply_day(self, link, data):
        self.ensure_one()
        day = transport.valid_day(data.get("day"))
        if day is None:
            return False
        task = self._federation_silent()
        # Le jour civil est le même des deux côtés ; l'heure est celle de midi ICI.
        transport.write_deadline_day(task, day or False, link.peer_id._our_tz())
        note = link._note(Markup(_("<p>Chez %s, l'échéance a été déplacée au %s.</p>"))
                          % (link.peer_id.name, day or _("(aucune)")))
        link._inbox_notify(note)
        return True

    # --- Émission : les surcharges ORM, ici et pas dans le mixin ----------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._federation_check_writer(vals)
        tasks = super().create(vals_list)
        tasks._federation_hook_create()
        return tasks

    def write(self, vals):
        # Changer de pair, ou le retirer, retire le destinataire du pair d'avant : sinon
        # la contrainte refuserait le changement, et un partage en lot tomberait en
        # entier pour une seule tâche encore adressée à l'ancien pair.
        if "federation_peer_id" in vals and "federation_assignee_id" not in vals:
            nouveau_pair = _as_id(vals["federation_peer_id"])
            changent = self.filtered(lambda t: t.federation_assignee_id and t.federation_peer_id.id != nouveau_pair)
            if changent and changent != self:
                # ⚠️ Deux copies du dictionnaire : `project.task.write` en retire des clés en
                # place (`milestone_id`), et la seconde écriture les aurait perdues.
                (self - changent).write(dict(vals))
                changent.write(dict(vals, federation_assignee_id=False))
                return True
            if changent:
                vals = dict(vals, federation_assignee_id=False)
        links, before = self._federation_hook_before_write(vals)
        res = super().write(vals)
        self._federation_hook_after_write(vals, links, before)
        return res

    def unlink(self):
        self._federation_hook_unlink()
        return super().unlink()

    @api.constrains("federation_peer_id", "project_id")
    def _check_federation_peer_allowed(self):
        for task in self:
            peer = task.federation_peer_id
            if peer and peer not in task.project_id.federation_peer_ids:
                raise ValidationError(_("Le projet « %s » ne fédère pas avec %s : ajoutez ce pair au projet d'abord.")
                                      % (task.project_id.display_name, peer.name))

    # --- Émission -------------------------------------------------------------------
    def _federation_before_write(self):
        self.ensure_one()
        tz = self.env["federation.peer"]._our_tz()
        return {"state": self.state, "day": transport.day_in_zone(self.date_deadline, tz),
                "stage": self.stage_id.id}

    def _federation_remember_sent(self, link):
        self.ensure_one()
        tz = self.env["federation.peer"]._our_tz()
        link.write({"last_state_sent": self.state,
                    "last_day_sent": transport.day_in_zone(self.date_deadline, tz) or ""})

    def _federation_after_write(self, vals, before, link):
        self.ensure_one()
        peer = link.peer_id
        tz = self.env["federation.peer"]._our_tz()
        # Chez le receveur, glisser le miroir dans « Terminé » termine sa part.
        if link.origin == "remote" and "stage_id" in vals and before.get("stage") != self.stage_id.id:
            done_stage = peer._done_stage()
            if done_stage and self.stage_id == done_stage and self.state not in ("1_done", "1_canceled"):
                self.write({"state": "1_done"})
                return
        if link.origin == "local" and any(f in vals for f in ("name", "description", "priority",
                                                               "federation_assignee_id")):
            card = self._federation_card()
            fp = self.env["federation.link"]._card_fingerprint(card)
            if fp != link.fingerprint:
                link.fingerprint = fp
                peer._enqueue("task.card", card, link)
        # Un changement d'étape recalcule l'état sans qu'il soit dans vals : on compare,
        # on n'exige pas.
        if before.get("state") != self.state and self.state != link.last_state_sent:
            link.last_state_sent = self.state
            peer._enqueue("task.state", {"state": self.state}, link)
        if "date_deadline" in vals:
            day = transport.day_in_zone(self.date_deadline, tz)
            if day != before.get("day"):
                link.last_day_sent = day or ""
                peer._enqueue("task.day", {"day": day, "tz": tz}, link)

    # --- Les recherches, redites ici parce que la tâche est nombreuse ------------------
    def _search_federation_possible(self, operator, value):
        projects = self.env["project.project"].search([("federation_peer_ids", "!=", False)])
        wanted = bool(value) if operator in ("=", "==") else not bool(value)
        return [("project_id", "in" if wanted else "not in", projects.ids)]

    @api.depends("project_id.federation_peer_ids")
    def _compute_federation_allowed(self):
        return super()._compute_federation_allowed()
