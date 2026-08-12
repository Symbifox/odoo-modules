from odoo import api, fields, models


class ProjectProject(models.Model):
    _inherit = "project.project"

    # Project-specific consents
    consent_ids = fields.One2many(
        comodel_name="privacy.consent",
        inverse_name="project_id",
        string="Consentements du projet",
        help="Enregistrements de consentement spécifiquement liés à ce projet",
    )
    consent_count = fields.Integer(
        compute="_compute_consent_stats",
        string="Nombre de consentements",
    )
    consent_status = fields.Selection(
        selection=[
            ("none", "Aucun consentement"),
            ("pending", "En attente"),
            ("partial", "Partiel"),
            ("complete", "Tous accordés"),
        ],
        compute="_compute_consent_stats",
        string="Statut des consentements",
        help="Statut global des consentements pour ce projet",
    )

    @api.depends("consent_ids", "consent_ids.status")
    def _compute_consent_stats(self):
        for project in self:
            consents = project.consent_ids
            project.consent_count = len(consents)

            if not consents:
                project.consent_status = "none"
            else:
                statuses = consents.mapped("status")
                if all(s == "granted" for s in statuses):
                    project.consent_status = "complete"
                elif any(s == "pending" for s in statuses):
                    project.consent_status = "pending"
                elif any(s == "granted" for s in statuses):
                    project.consent_status = "partial"
                else:
                    project.consent_status = "none"

    def action_view_consents(self):
        """View consents linked to this project."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": f"Consentements - {self.name}",
            "res_model": "privacy.consent",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("project_id", "=", self.id)],
            "context": {"default_project_id": self.id},
        }

    def action_request_consent(self):
        """Open wizard to request consent for project contacts."""
        self.ensure_one()
        # Get all partners linked to the project (you may want to customize this)
        partner_ids = []
        if self.partner_id:
            partner_ids.append(self.partner_id.id)

        return {
            "type": "ir.actions.act_window",
            "name": "Demander le consentement du projet",
            "res_model": "privacy.consent.request.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_partner_ids": [(6, 0, partner_ids)],
                "default_project_id": self.id,
            },
        }
