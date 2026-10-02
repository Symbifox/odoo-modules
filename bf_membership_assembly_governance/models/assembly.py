from odoo import _, api, fields, models
from odoo.exceptions import UserError


class MembershipAssembly(models.Model):
    _inherit = "bf.membership.assembly"

    corporate_resolution_count = fields.Integer(
        string="Résolutions au registre", compute="_compute_corporate_resolution_count",
        compute_sudo=True,
    )

    @api.depends("proposal_ids.corporate_resolution_ids")
    def _compute_corporate_resolution_count(self):
        for rec in self:
            rec.corporate_resolution_count = len(rec.proposal_ids.corporate_resolution_ids)

    def action_create_corporate_resolutions(self):
        """Toutes les propositions adoptées d'un coup ; celles déjà inscrites ne doublent pas."""
        self.ensure_one()
        adopted = self.proposal_ids.filtered(lambda p: p._adopted())
        if not adopted:
            raise UserError(_("Aucune proposition adoptée à inscrire au registre."))
        return adopted.action_create_corporate_resolution()

    def action_view_corporate_resolutions(self):
        self.ensure_one()
        return self.proposal_ids.corporate_resolution_ids._bf_open_action()
