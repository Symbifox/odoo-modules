from odoo import _, api, fields, models
from odoo.exceptions import UserError


class FederationShareWizard(models.TransientModel):
    _name = "federation.share.wizard"
    _description = "Fédérer des tâches avec un pair"

    peer_id = fields.Many2one("federation.peer", string="Pair", required=True,
                              domain="[('state', '=', 'active'), ('id', 'in', allowed_peer_ids)]")
    task_ids = fields.Many2many("project.task", string="Tâches",
                                default=lambda self: self.env.context.get("active_ids", []))
    allowed_peer_ids = fields.Many2many("federation.peer", compute="_compute_allowed", string="Pairs permis")
    skipped_count = fields.Integer(compute="_compute_allowed")

    @api.depends("task_ids", "peer_id")
    def _compute_allowed(self):
        for wiz in self:
            peers = self.env["federation.peer"]
            for task in wiz.task_ids:
                peers |= task.project_id.federation_peer_ids
            wiz.allowed_peer_ids = peers.filtered(lambda p: p.state == "active")
            wiz.skipped_count = len(wiz.task_ids.filtered(lambda t: wiz.peer_id and wiz.peer_id not in t.project_id.federation_peer_ids))

    def action_share(self):
        self.ensure_one()
        if not self.env.user.has_group("project.group_project_manager"):
            raise UserError(_("Fédérer une tâche demande le rôle de gestionnaire de projet."))
        allowed = self.task_ids.filtered(lambda t: self.peer_id in t.project_id.federation_peer_ids)
        if not allowed:
            raise UserError(_("Aucune des tâches choisies n'est dans un projet qui fédère avec %s.") % self.peer_id.name)
        tasks = allowed.filtered(lambda t: t.federation_peer_id != self.peer_id)
        tasks.write({"federation_peer_id": self.peer_id.id})
        skipped = len(self.task_ids) - len(allowed)
        msg = _("%s tâche(s) fédérée(s) avec %s.") % (len(tasks), self.peer_id.name)
        if skipped:
            msg += _(" %s laissée(s) de côté : leur projet ne fédère pas avec ce pair.") % skipped
        return {"type": "ir.actions.client", "tag": "display_notification",
                "params": {"title": _("Fédération"), "type": "success" if not skipped else "warning", "sticky": bool(skipped), "message": msg}}
