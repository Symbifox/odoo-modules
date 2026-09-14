"""L'échéancier fédéré : deux firmes, un chantier, une seule suite de dates.

L'échéancier autonome de `bf_gantt` est fait pour être montré à quelqu'un. Ce
module le montre à une autre instance, et la suite avec : la ligne qui glisse, le
jalon atteint, l'avancement qui monte.

Trois choix valent d'être sus avant de toucher à ce fichier.

**Les lignes voyagent en bloc, jamais en différence.** Un échéancier se retouche
par ses lignes bien plus souvent que par son titre, et une différence exigerait
que les deux côtés s'entendent sur l'identité de chaque ligne. La carte porte
donc l'échéancier entier, les lignes du miroir sont remplacées d'un coup, et une
empreinte évite de renvoyer ce qui n'a pas bougé. Chaque ligne porte une `key`
(son identifiant chez l'émetteur) pour que les dépendances se rattachent.

🔴 **Le statut du plan voyage sous la clé `status`, pas `state`.** L'empreinte du
socle ignore `state`, `day`, `url` et `tz`, parce que la tâche a des verbes à part
pour son état et son échéance. Un plan n'en a pas : sous `state`, passer
l'échéancier de « En cours » à « Terminé » ne changerait pas l'empreinte, et rien
ne partirait.

**Les heures prévues ne traversent pas.** `allocated_hours` est l'effort qu'une
firme met dans une ligne, c'est-à-dire sa marge. Un échéancier partagé porte des
dates.
"""

import logging

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.bf_federation.models import transport

_logger = logging.getLogger(__name__)

#: Un échéancier de chantier fait quelques dizaines de lignes. Le plafond est là
#: pour qu'une carte hostile ne fasse pas travailler la base indéfiniment.
MAX_LIGNES = 500
MAX_DEPENDANCES = 20
ETATS_LIGNE = ("todo", "doing", "done", "cancel")
ETATS_PLAN = ("draft", "active", "done", "cancel")
#: Ce qu'un utilisateur ne peut pas écrire sur un échéancier reçu.
CHAMPS_DU_MIROIR = {"name", "state", "date_start", "date_end", "note", "item_ids",
                    "partner_id", "project_id"}


class BfGanttPlan(models.Model):
    _name = "bf.gantt.plan"
    _inherit = ["bf.gantt.plan", "federation.federable"]

    _federation_kind = "gantt"
    _federation_verbs = ("card",)

    federation_is_mirror = fields.Boolean(
        string="Échéancier reçu d'un pair", compute="_compute_federation_is_mirror", store=True,
        help="Un échéancier reçu se lit : la prochaine version de l'émetteur le remplacera.")

    @api.depends("federation_peer_id")
    def _compute_federation_is_mirror(self):
        links = self._federation_links_for(self.filtered("id"), include_inactive=True) if self.ids else {}
        for plan in self:
            link = links.get(plan.id)
            plan.federation_is_mirror = bool(link and link.origin == "remote")

    # --- Le contrat ------------------------------------------------------------------
    def _federation_allowed_peers(self):
        """Le client du plan, puis les pairs du projet cité. Jamais tous les pairs."""
        self.ensure_one()
        if self.partner_id:
            return self.env["federation.peer"]._for_partner(self.partner_id)
        return self.project_id.federation_peer_ids if self.project_id else self.env["federation.peer"]

    # ⚠️ `@api.constrains` ne surveille que les champs nommés : la garde doit nommer
    # ceux qui définissent la portée, sinon changer de client après le partage laisse
    # l'échéancier fédéré avec le pair d'un autre.
    @api.constrains("federation_peer_id", "partner_id", "project_id")
    def _check_federation_peer_allowed(self):
        return super()._check_federation_peer_allowed()

    @api.depends("partner_id", "project_id", "project_id.federation_peer_ids")
    def _compute_federation_allowed(self):
        return super()._compute_federation_allowed()

    def _federation_label_the(self):
        return _("l'échéancier")

    def _federation_label_this(self):
        return _("cet échéancier")

    def _federation_watched(self):
        return ("name", "state", "date_start", "date_end", "note", "active",
                "federation_peer_id", "partner_id", "project_id")

    def _federation_notify_partners(self):
        self.ensure_one()
        return self.user_id.partner_id or self.create_uid.partner_id

    def _federation_items(self):
        """Les lignes, dans l'ordre d'affichage, sans les heures prévues."""
        self.ensure_one()
        lignes = []
        for item in self.item_ids.sorted(lambda i: (i.sequence, i.date_start or fields.Date.today(), i.id))[:MAX_LIGNES]:
            lignes.append({
                "key": str(item.id),
                "name": item.name or "",
                "sequence": item.sequence,
                "lane": item.lane or "",
                "date_start": fields.Date.to_string(item.date_start) if item.date_start else "",
                "date_end": fields.Date.to_string(item.date_end) if item.date_end else "",
                "is_milestone": bool(item.is_milestone),
                "progress": item.progress or 0,
                "assignee": item.assignee or "",
                "item_state": item.state or "todo",
                "depends_on": [str(i) for i in item.depend_on_ids.ids][:MAX_DEPENDANCES],
                "note_text": item.note or "",
            })
        return lignes

    def _federation_card(self):
        self.ensure_one()
        base = self.env["federation.peer"]._our_base_url()
        return {
            "name": self.name or "",
            "status": self.state or "draft",
            "date_start": fields.Date.to_string(self.date_start) if self.date_start else "",
            "date_end": fields.Date.to_string(self.date_end) if self.date_end else "",
            "note_text": transport.html_to_text(self.note) if self.note else "",
            "items": self._federation_items(),
            "url": f"{base}/odoo/action-bf_gantt.action_bf_gantt_plan/{self.id}" if base else False,
        }

    # --- Réception --------------------------------------------------------------------
    @api.model
    def _federation_plan_vals(self, peer, card):
        statut = card.get("status")
        debut = transport.valid_day(card.get("date_start")) or False
        fin = transport.valid_day(card.get("date_end")) or False
        if debut and fin and fin < debut:
            fin = debut
        base = transport.clean_text(card.get("name"), 240) or _("(sans titre)")
        return {
            "name": _("%s (%s)") % (base, transport.clean_text(peer.name, 40)),
            "state": statut if statut in ETATS_PLAN else "draft",
            "date_start": debut,
            "date_end": fin,
            "note": transport.text_to_html(transport.clean_text(card.get("note_text"), 20000)),
        }

    @api.model
    def _federation_receive(self, peer, card):
        vals = self._federation_plan_vals(peer, card)
        vals.update({
            "partner_id": peer.partner_id.id,
            "project_id": peer._ensure_mirror_project().id,
            "user_id": peer.mirror_user_id.id or False,
            "federation_peer_id": peer.id,
            "portal_published": False,
        })
        plan = self.create(vals)
        plan._federation_load_items(card)
        plan.message_post(
            body=Markup(_("<p>Échéancier reçu de %s : <b>%s</b>, %s ligne(s). Il se lit ici ; "
                          "chaque changement de l'émetteur le remplacera.</p>"))
            % (peer.name, plan.name, len(plan.item_ids)),
            message_type="comment", subtype_xmlid="mail.mt_note")
        return plan

    @api.model
    def _federation_clean_items(self, card):
        """Les lignes reçues, nettoyées : dates valides, avancement borné, statuts connus.

        Une ligne sans début valide est écartée plutôt que de faire tomber toute la
        carte : `date_start` est requis, et une seule ligne hostile rendrait sinon
        l'échéancier impossible à recevoir, pour toujours.
        """
        lignes = card.get("items")
        if not isinstance(lignes, list):
            return []
        propres, vues = [], set()
        for ligne in lignes[:MAX_LIGNES]:
            if not isinstance(ligne, dict):
                continue
            key = transport.clean_text(ligne.get("key"), 40)
            debut = transport.valid_day(ligne.get("date_start"))
            nom = transport.clean_text(ligne.get("name"), 240)
            if not key or key in vues or not debut or not nom:
                continue
            vues.add(key)
            fin = transport.valid_day(ligne.get("date_end")) or False
            jalon = bool(ligne.get("is_milestone"))
            if jalon or (fin and fin < debut):
                fin = debut
            etat = ligne.get("item_state")
            depend = ligne.get("depends_on") if isinstance(ligne.get("depends_on"), list) else []
            propres.append({
                "key": key,
                "vals": {
                    "name": nom,
                    "sequence": transport.as_int(ligne.get("sequence"), 10),
                    "lane": transport.clean_text(ligne.get("lane"), 120) or False,
                    "date_start": debut,
                    "date_end": fin,
                    "is_milestone": jalon,
                    "progress": max(0, min(100, transport.as_int(ligne.get("progress"), 0))),
                    "assignee": transport.clean_text(ligne.get("assignee"), 120) or False,
                    "state": etat if etat in ETATS_LIGNE else "todo",
                    "note": transport.clean_text(ligne.get("note_text"), 4000) or False,
                },
                "depends_on": [transport.clean_text(d, 40) for d in depend[:MAX_DEPENDANCES]],
            })
        return propres

    @staticmethod
    def _federation_acyclic(lignes):
        """Les dépendances reçues, privées de celles qui fermeraient un cycle.

        🔴 `bf.gantt.item` refuse un cycle par contrainte. Une carte qui en porte un,
        par erreur ou par malveillance, ferait tomber la réception entière en 422,
        et la boîte de sortie de l'émetteur insisterait deux semaines. On garde donc
        les arêtes dans l'ordre d'arrivée et on écarte celle qui fermerait la boucle.
        """
        connues = {l["key"] for l in lignes}
        graphe = {l["key"]: [] for l in lignes}

        def atteint(depart, cible):
            pile, vus = [depart], set()
            while pile:
                n = pile.pop()
                if n == cible:
                    return True
                if n in vus:
                    continue
                vus.add(n)
                pile.extend(graphe.get(n, []))
            return False

        for ligne in lignes:
            gardees = []
            for dep in ligne["depends_on"]:
                if dep not in connues or dep == ligne["key"] or dep in gardees:
                    continue
                # « ligne dépend de dep » : refusé si dep dépend déjà, de proche en proche, de ligne.
                if atteint(dep, ligne["key"]):
                    continue
                gardees.append(dep)
                graphe[ligne["key"]].append(dep)
            ligne["depends_on"] = gardees
        return lignes

    def _federation_load_items(self, card):
        """Poser les lignes reçues. Remplacées en bloc, jamais fusionnées."""
        self.ensure_one()
        lignes = self._federation_acyclic(self._federation_clean_items(card))
        plan = self._federation_silent()
        plan.item_ids.unlink()
        Item = self.env["bf.gantt.item"].sudo().with_context(federation_inbound=True)
        par_cle = {}
        for ligne in lignes:
            vals = dict(ligne["vals"], plan_id=plan.id)
            par_cle[ligne["key"]] = Item.create(vals)
        for ligne in lignes:
            if ligne["depends_on"]:
                par_cle[ligne["key"]].write({"depend_on_ids": [(6, 0, [par_cle[d].id for d in ligne["depends_on"]])]})
        return True

    def _federation_apply_card(self, link, card):
        self.ensure_one()
        if link.origin != "remote":
            return False
        ancien = (self.date_start, self.date_end, self.state)
        self._federation_silent().write(self._federation_plan_vals(link.peer_id, card))
        self._federation_load_items(card)
        nouveau = (self.date_start, self.date_end, self.state)
        if nouveau != ancien:
            note = link._note(Markup(_("<p>Chez %s, l'échéancier a changé : du %s au %s, statut %s.</p>"))
                              % (link.peer_id.name, self.date_start or "?", self.date_end or "?",
                                 dict(self._fields["state"].selection).get(self.state, self.state)))
            link._inbox_notify(note)
        return True

    # --- Émission -------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._federation_check_writer(vals)
        plans = super().create(vals_list)
        plans._federation_hook_create()
        return plans

    def write(self, vals):
        self._federation_guard_mirror(vals)
        links, before = self._federation_hook_before_write(vals)
        res = super().write(vals)
        self._federation_hook_after_write(vals, links, before)
        return res

    def unlink(self):
        self._federation_hook_unlink()
        return super().unlink()

    def _federation_guard_mirror(self, vals):
        """Un échéancier reçu se lit, et ne se publie pas au portail du pair."""
        if self.env.context.get("federation_inbound") or self.env.su:
            return
        interdits = CHAMPS_DU_MIROIR & set(vals)
        publie = bool(vals.get("portal_published"))
        if not interdits and not publie:
            return
        for plan in self:
            if not plan.federation_is_mirror:
                continue
            if publie:
                raise UserError(
                    _("Cet échéancier est reçu de %s : il a été partagé avec votre entreprise, "
                      "pas avec vos clients. Il ne se publie pas au portail.") % plan.federation_peer_id.name)
            raise UserError(
                _("Cet échéancier est reçu de %s : il se lit ici. Le prochain changement de "
                  "l'émetteur le remplacerait, et le travail fait dessus serait perdu.")
                % plan.federation_peer_id.name)

    def _federation_after_write(self, vals, before, link):
        self.ensure_one()
        if link.origin != "local":
            return
        self._federation_push_if_changed(link)

    def _federation_push_if_changed(self, link=None):
        """Renvoyer l'échéancier au pair s'il a changé depuis le dernier envoi."""
        self.ensure_one()
        link = link or self._federation_link()
        if not link or link.origin != "local" or not link.active:
            return False
        card = self._federation_card()
        fp = self.env["federation.link"]._card_fingerprint(card)
        if fp == link.fingerprint:
            return False
        link.sudo().fingerprint = fp
        link.peer_id._enqueue("gantt.card", card, link)
        return True

    def _federation_push_items_changed(self):
        """Une ligne a bougé : chaque plan partagé repart, une fois, s'il a changé."""
        if self.env.context.get("federation_inbound") or not self:
            return
        links = self._federation_links_for(self)
        for plan in self:
            link = links.get(plan.id)
            if link and link.origin == "local" and link.active:
                plan._federation_push_if_changed(link)

    def action_federation_push(self):
        """Renvoyer l'échéancier tel qu'il est, même s'il n'a pas changé."""
        self.ensure_one()
        if not self.env.su and not self.env.user.has_group("project.group_project_manager"):
            raise AccessError(_("Renvoyer un échéancier à un pair demande le rôle de gestionnaire de projet."))
        link = self._federation_link()
        if not link or link.origin != "local" or not link.active:
            raise UserError(_("Cet échéancier n'est pas partagé avec un pair."))
        card = self._federation_card()
        link.fingerprint = self.env["federation.link"]._card_fingerprint(card)
        link.peer_id._enqueue("gantt.card", card, link)
        self.message_post(body=_("Échéancier renvoyé à %s : %s ligne(s).") % (link.peer_id.name, len(self.item_ids)),
                          message_type="comment", subtype_xmlid="mail.mt_note")
        return True

    def _federation_share_note(self, peer):
        self.ensure_one()
        self.sudo().with_context(federation_inbound=True).message_post(
            body=_("Échéancier fédéré avec %s : ses lignes, ses couloirs, ses jalons, ses dépendances et "
                   "leur avancement. Ce qui reste ici : les heures prévues de chaque ligne.") % peer.name,
            message_type="comment", subtype_xmlid="mail.mt_note")


class BfGanttItem(models.Model):
    _inherit = "bf.gantt.item"

    @api.model_create_multi
    def create(self, vals_list):
        self._federation_guard_mirror_items(self.env["bf.gantt.plan"].browse(
            [v["plan_id"] for v in vals_list if v.get("plan_id")]).exists())
        items = super().create(vals_list)
        items._federation_push_plans()
        return items

    def write(self, vals):
        self._federation_guard_mirror_items(self.mapped("plan_id"))
        plans = self.mapped("plan_id")
        res = super().write(vals)
        (plans | self.mapped("plan_id"))._federation_push_items_changed()
        return res

    def unlink(self):
        self._federation_guard_mirror_items(self.mapped("plan_id"))
        plans = self.mapped("plan_id")
        res = super().unlink()
        plans.exists()._federation_push_items_changed()
        return res

    def _federation_guard_mirror_items(self, plans):
        if self.env.context.get("federation_inbound") or self.env.su:
            return
        for plan in plans:
            if plan.federation_is_mirror:
                raise UserError(
                    _("Les lignes de cet échéancier sont reçues de %s : elles se lisent ici.")
                    % plan.federation_peer_id.name)

    def _federation_push_plans(self):
        self.mapped("plan_id")._federation_push_items_changed()
