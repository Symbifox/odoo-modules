from odoo import _, api, fields, models


class BfTrainingActivity(models.Model):
    """Une activité qui consiste à lire un document et à le confirmer."""

    _inherit = "bf.training.activity"

    document_id = fields.Many2one(
        "project.document", string="Document to read",
        help="The policy or the procedure whose reading counts as "
             "training. Each module of an onboarding process has its own.")
    document_latest_version_id = fields.Many2one(
        related="document_id.latest_version_id", string="Current version")
    document_distribution_count = fields.Integer(
        string="Number of distributions", compute="_compute_document_distribution_count")

    _sql_constraints = [
        ("document_unique", "unique (document_id)",
         "This document is already linked to an activity."),
    ]

    def _compute_document_distribution_count(self):
        Distribution = self.env["project.document.distribution"]
        for rec in self:
            rec.document_distribution_count = Distribution.search_count(
                [("document_id", "=", rec.document_id.id)]) if rec.document_id else 0

    @api.onchange("document_id")
    def _onchange_document_id(self):
        """Un document qui change de version doit être relu : c'est le défaut."""
        for rec in self:
            if rec.document_id:
                rec.reopen_on_change = True
                if not rec.name:
                    rec.name = rec.document_id.name

    @api.model
    def _pour_document(self, document, valeurs=None):
        """L'activité qui porte ce document, créée si elle n'existe pas.

        Le pendant de ce que fait le raccord eLearning pour un cours. Un document
        ne donne qu'une activité : c'est la contrainte d'unicité qui le garantit,
        et c'est ce qui permet à une remise de retrouver la sienne sans ambiguïté.
        """
        if not document:
            return self.browse()
        existante = self.search([("document_id", "=", document.id)], limit=1)
        if existante:
            return existante
        base = {
            "name": document.name or _("Document to read"),
            "document_id": document.id,
            "mode": "self_study",
            "reopen_on_change": True,
        }
        base.update(valeurs or {})
        return self.create(base)

    def action_open_document_distributions(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Document distributions"),
            "res_model": "project.document.distribution",
            "view_mode": "list,form",
            "domain": [("document_id", "=", self.document_id.id)],
        }
