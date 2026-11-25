from odoo import _, api, fields, models
from odoo.exceptions import UserError


class BfTrainingAssignment(models.Model):
    _inherit = "bf.training.assignment"

    document_id = fields.Many2one(
        related="activity_id.document_id", string="Document to read", store=True)
    distribution_id = fields.Many2one(
        "project.document.distribution", string="Distribution", ondelete="set null")

    def action_distribute_document(self):
        """Remettre la version en vigueur à la personne, et suivre son accusé."""
        Distribution = self.env["project.document.distribution"]
        for rec in self:
            if not rec.document_id:
                continue
            version = rec.document_id.latest_version_id
            if not version:
                raise UserError(_(
                    "Document \"%s\" has no released version. There is "
                    "nothing to distribute.") % rec.document_id.name)
            existante = Distribution.search([
                ("version_id", "=", version.id),
                ("partner_id", "=", rec.partner_id.id),
            ], limit=1)
            rec.distribution_id = existante or Distribution.create({
                "version_id": version.id,
                "partner_id": rec.partner_id.id,
                "recipient_type": "partner",
                "distribution_date": fields.Datetime.now(),
            })
            if rec.state == "pending":
                rec.state = "in_progress"
        return True
