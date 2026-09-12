from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from . import transport

WATCHED = ("name", "description", "priority", "state", "stage_id", "date_deadline", "active", "federation_peer_id")


class ProjectTask(models.Model):
    _inherit = "project.task"

    federation_peer_id = fields.Many2one("federation.peer", string="Fédérée avec", copy=False, tracking=True,
                                         domain="[('state', '=', 'active'), ('id', 'in', federation_allowed_peer_ids)]",
                                         help="Le pair chez qui cette tâche a un miroir. Vider le champ archive le miroir.")
    federation_allowed_peer_ids = fields.Many2many("federation.peer", compute="_compute_federation_allowed",
                                                   string="Pairs permis par le projet")
    federation_possible = fields.Boolean(compute="_compute_federation_allowed", string="Fédération possible",
                                         search="_search_federation_possible")
    federation_link_id = fields.Many2one("federation.link", compute="_compute_federation", string="Lien fédéré",
                                         search="_search_federation_link_id")
    federation_remote_url = fields.Char(compute="_compute_federation", string="Chez le pair")
    federation_origin = fields.Selection([("local", "Partagée d'ici"), ("remote", "Reçue du pair")],
                                         compute="_compute_federation", string="Origine", search="_search_federation_origin")

    # Un champ calculé non stocké sans méthode de recherche est écarté EN SILENCE d'un domaine :
    # « origine = reçue » rendrait toutes les tâches. Chaque champ filtrable a donc sa recherche.
    def _search_federation_origin(self, operator, value):
        links = self.env["federation.link"].sudo().search([("origin", operator, value)])
        return [("id", "in", links.mapped("task_id").ids)]

    def _search_federation_link_id(self, operator, value):
        links = self.env["federation.link"].sudo().search([("id", operator, value)])
        return [("id", "in", links.mapped("task_id").ids)]

    def _search_federation_possible(self, operator, value):
        projects = self.env["project.project"].search([("federation_peer_ids", "!=", False)])
        wanted = bool(value) if operator in ("=", "==") else not bool(value)
        return [("project_id", "in" if wanted else "not in", projects.ids)]

    def _federation_link(self, include_inactive=False):
        self.ensure_one()
        Link = self.env["federation.link"].sudo()
        if include_inactive:
            Link = Link.with_context(active_test=False)
        return Link.search([("task_id", "=", self.id)], limit=1)

    @api.model
    def _federation_links_for(self, tasks, include_inactive=False):
        Link = self.env["federation.link"].sudo()
        if include_inactive:
            Link = Link.with_context(active_test=False)
        return {l.task_id.id: l for l in Link.search([("task_id", "in", tasks.ids)])}

    @api.depends("project_id.federation_peer_ids")
    def _compute_federation_allowed(self):
        for task in self:
            task.federation_allowed_peer_ids = task.project_id.federation_peer_ids
            task.federation_possible = bool(task.project_id.federation_peer_ids)

    @api.constrains("federation_peer_id", "project_id")
    def _check_federation_peer_allowed(self):
        for task in self:
            peer = task.federation_peer_id
            if peer and peer not in task.project_id.federation_peer_ids:
                raise ValidationError(_("Le projet « %s » ne fédère pas avec %s : ajoutez ce pair au projet d'abord.")
                                      % (task.project_id.display_name, peer.name))

    @api.depends("federation_peer_id")
    def _compute_federation(self):
        links = self._federation_links_for(self.filtered("id")) if self.ids else {}
        for task in self:
            link = links.get(task.id) or self.env["federation.link"]
            task.federation_link_id = link
            task.federation_remote_url = link.remote_url if link else False
            task.federation_origin = link.origin if link else False

    def _federation_card(self):
        self.ensure_one()
        tz = self.env["federation.peer"]._our_tz()
        base = self.env["federation.peer"]._our_base_url()
        return {
            "name": self.name, "description_text": transport.html_to_text(self.description),
            "day": transport.day_in_zone(self.date_deadline, tz), "tz": tz,
            "priority": self.priority or "0", "state": self.state,
            "url": f"{base}/odoo/project/{self.project_id.id}/tasks/{self.id}" if base else False,
        }

    def _federation_check_writer(self, vals):
        """Fédérer, retirer ou changer le pair d'une tâche demande le rôle de gestionnaire de projet,
        quelle que soit la porte (formulaire, liste, RPC)."""
        if "federation_peer_id" not in vals or self.env.su or self.env.context.get("federation_inbound"):
            return
        if not self.env.user.has_group("project.group_project_manager"):
            raise AccessError(_("Fédérer une tâche demande le rôle de gestionnaire de projet."))

    # --- Émission : ce qui change ici part chez le pair -------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._federation_check_writer(vals)
        tasks = super().create(vals_list)
        if not self.env.context.get("federation_inbound"):
            for task in tasks.filtered("federation_peer_id"):
                task._federation_share()
        return tasks

    def write(self, vals):
        if self.env.context.get("federation_inbound") or not any(f in vals for f in WATCHED):
            return super().write(vals)
        self._federation_check_writer(vals)
        links = self._federation_links_for(self, include_inactive=True)
        if not links and "federation_peer_id" not in vals:
            return super().write(vals)
        if "federation_peer_id" in vals and vals["federation_peer_id"]:
            for task in self:
                link = links.get(task.id)
                if link and link.origin == "remote" and link.peer_id.id != vals["federation_peer_id"]:
                    raise UserError(_("Une tâche reçue d'un pair ne peut pas être fédérée avec un autre pair."))
        tz = self.env["federation.peer"]._our_tz()
        before = {t.id: {"state": t.state, "day": transport.day_in_zone(t.date_deadline, tz),
                         "peer": t.federation_peer_id.id, "active": t.active, "stage": t.stage_id.id} for t in self}
        res = super().write(vals)
        for task in self:
            task._federation_after_write(vals, before.get(task.id, {}), links.get(task.id))
        return res

    def unlink(self):
        links = self._federation_links_for(self)
        for task in self:
            link = links.get(task.id)
            if not link:
                continue
            if link.origin == "local":
                link.peer_id._enqueue("link.archive", {"reason": _("supprimée")}, link, task=task)
            else:
                link.peer_id._enqueue("mirror.dropped", {"reason": _("supprimé")}, link, task=task)
        return super().unlink()

    def _federation_share(self):
        """Créer le lien d'origine locale et envoyer la carte complète ; re-partager renvoie
        la carte complète aussi, pour que le miroir réactivé soit à jour."""
        self.ensure_one()
        peer = self.federation_peer_id
        Link = self.env["federation.link"].sudo().with_context(active_test=False)
        link = Link.search([("task_id", "=", self.id), ("peer_id", "=", peer.id)], limit=1)
        card = self._federation_card()
        if link and link.origin == "remote":
            raise UserError(_("Une tâche reçue d'un pair ne peut pas être fédérée de nouveau."))
        if link:
            link.write({"active": True, "fingerprint": self.env["federation.link"]._card_fingerprint(card),
                        "last_state_sent": self.state, "last_day_sent": card["day"] or ""})
            peer._enqueue("task.share", card, link)
            return link
        link = Link.create({"peer_id": peer.id, "task_id": self.id, "origin": "local",
                            "fingerprint": self.env["federation.link"]._card_fingerprint(card),
                            "last_state_sent": self.state, "last_day_sent": card["day"] or ""})
        peer._enqueue("task.share", card, link)
        self.sudo().with_context(federation_inbound=True).message_post(
            body=_("Tâche fédérée avec %s : le miroir apparaîtra chez lui au prochain envoi. Ce qui part : nom, "
                   "description en texte, jour d'échéance, priorité, état, messages et pièces jointes sous le plafond. "
                   "Ce qui reste ici : les notes internes, les heures, les feuilles de temps.") % peer.name,
            message_type="comment", subtype_xmlid="mail.mt_note")
        return link

    def _federation_after_write(self, vals, before, link):
        self.ensure_one()
        tz = self.env["federation.peer"]._our_tz()
        if "federation_peer_id" in vals:
            new_peer = self.federation_peer_id
            if link and link.origin == "remote":
                if not new_peer and link.active:
                    # Le receveur détache son miroir : l'original n'est pas touché, il en est averti.
                    link.peer_id._enqueue("mirror.dropped", {"reason": _("détaché")}, link)
                    link.active = False
                return
            if new_peer:
                if link and link.peer_id != new_peer and link.active:
                    link.peer_id._enqueue("link.archive", {"reason": _("retirée du partage")}, link)
                    link.active = False
                self._federation_share()
                return
            if link and link.active:
                link.peer_id._enqueue("link.archive", {"reason": _("retirée du partage")}, link)
                link.active = False
                return
        if not link or not link.active:
            return
        peer = link.peer_id
        if "active" in vals and before.get("active") != self.active:
            if link.origin == "remote":
                if not self.active:
                    peer._enqueue("mirror.dropped", {"reason": _("archivé")}, link)
                    link.active = False
                return
            peer._enqueue("link.restore" if self.active else "link.archive", {"reason": _("archivée")}, link)
            return
        # Chez le receveur, glisser le miroir dans « Terminé » termine sa part.
        if link.origin == "remote" and "stage_id" in vals and before.get("stage") != self.stage_id.id:
            done_stage = peer._done_stage()
            if done_stage and self.stage_id == done_stage and self.state not in ("1_done", "1_canceled"):
                self.write({"state": "1_done"})
                return
        if link.origin == "local" and any(f in vals for f in ("name", "description", "priority")):
            card = self._federation_card()
            fp = self.env["federation.link"]._card_fingerprint(card)
            if fp != link.fingerprint:
                link.fingerprint = fp
                peer._enqueue("task.card", card, link)
        # Un changement d'étape recalcule l'état sans qu'il soit dans vals : on compare, on n'exige pas.
        if before.get("state") != self.state and self.state != link.last_state_sent:
            link.last_state_sent = self.state
            peer._enqueue("task.state", {"state": self.state}, link)
        if "date_deadline" in vals:
            day = transport.day_in_zone(self.date_deadline, tz)
            if day != before.get("day"):
                link.last_day_sent = day or ""
                peer._enqueue("task.day", {"day": day, "tz": tz}, link)
