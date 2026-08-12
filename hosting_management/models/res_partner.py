# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import timedelta

from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    has_sla = fields.Boolean(
        string="Client sous SLA",
        tracking=True,
        help="Activer pour les clients ayant une entente de niveau de service. "
        "Débloque la section Parc informatique et les fonctionnalités associées.",
    )
    hosting_service_ids = fields.One2many(
        comodel_name="hosting.service",
        inverse_name="partner_id",
        string="Services d'hébergement",
    )
    hosting_service_count = fields.Integer(
        string="Nombre de services d'hébergement",
        compute="_compute_hosting_service_count",
    )
    hosting_endpoint_ids = fields.One2many(
        comodel_name="hosting.endpoint",
        inverse_name="partner_id",
        string="Postes du parc",
    )
    hosting_endpoint_count = fields.Integer(
        string="Nombre de postes",
        compute="_compute_hosting_endpoint_count",
    )
    hosting_license_seat_ids = fields.One2many(
        comodel_name="hosting.license.seat",
        inverse_name="partner_id",
        string="Sièges de licence affectés",
    )
    hosting_license_count = fields.Integer(
        string="Nombre de licences",
        compute="_compute_hosting_license_count",
    )
    voip_did_ids = fields.One2many(
        comodel_name="hosting.voip.did",
        inverse_name="partner_id",
        string="DID téléphoniques",
    )
    voip_did_count = fields.Integer(
        string="Nombre de DID",
        compute="_compute_voip_stats",
    )
    voip_monthly_cost = fields.Float(
        string="Coût mensuel DID",
        compute="_compute_voip_stats",
    )
    voip_usage_cost_month = fields.Float(
        string="Coût usage appels (mois)",
        compute="_compute_voip_stats",
    )
    hosting_services_expiring_count = fields.Integer(
        string="Services expirant",
        compute="_compute_hosting_alerts",
    )
    hosting_services_updates_count = fields.Integer(
        string="Mises à jour disponibles",
        compute="_compute_hosting_alerts",
    )
    hosting_services_storage_alert_count = fields.Integer(
        string="Alertes de stockage",
        compute="_compute_hosting_alerts",
    )

    @api.depends("hosting_service_ids")
    def _compute_hosting_service_count(self):
        for partner in self:
            partner.hosting_service_count = len(partner.hosting_service_ids)

    @api.depends("hosting_endpoint_ids")
    def _compute_hosting_endpoint_count(self):
        for partner in self:
            partner.hosting_endpoint_count = len(partner.hosting_endpoint_ids)

    def action_view_hosting_endpoints(self):
        """Ouvrir les postes du parc pour ce partenaire."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": f"Parc informatique - {self.name}",
            "res_model": "hosting.endpoint",
            "views": [[False, "list"], [False, "form"], [False, "kanban"]],
            "domain": [("partner_id", "=", self.id)],
            "context": {"default_partner_id": self.id},
        }

    @api.depends("hosting_license_seat_ids")
    def _compute_hosting_license_count(self):
        for partner in self:
            partner.hosting_license_count = len(partner.hosting_license_seat_ids)

    def action_view_hosting_licenses(self):
        """Ouvrir les sièges de licence affectés à ce partenaire."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": f"Licences - {self.name}",
            "res_model": "hosting.license.seat",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("partner_id", "=", self.id)],
            "context": {"default_partner_id": self.id, "default_assignee_type": "partner"},
        }

    @api.depends("voip_did_ids", "voip_did_ids.monthly_cost", "voip_did_ids.state")
    def _compute_voip_stats(self):
        Cdr = self.env["hosting.voip.cdr"]
        month_start = fields.Date.context_today(self).replace(day=1)
        for partner in self:
            active_dids = partner.voip_did_ids.filtered(
                lambda d: d.state == "active")
            partner.voip_did_count = len(partner.voip_did_ids)
            partner.voip_monthly_cost = sum(active_dids.mapped("monthly_cost"))
            if partner.id:
                groups = Cdr.read_group(
                    [("partner_id", "=", partner.id),
                     ("call_date", ">=", str(month_start))],
                    ["cost:sum"], [])
                partner.voip_usage_cost_month = (
                    (groups[0].get("cost") if groups else 0.0) or 0.0)
            else:
                partner.voip_usage_cost_month = 0.0

    def action_view_voip_dids(self):
        """Ouvrir les DID téléphoniques pour ce partenaire."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": f"DID - {self.name}",
            "res_model": "hosting.voip.did",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("partner_id", "=", self.id)],
            "context": {"default_partner_id": self.id},
        }

    @api.depends(
        "hosting_service_ids",
        "hosting_service_ids.state",
        "hosting_service_ids.date_expiration",
        "hosting_service_ids.update_available",
        "hosting_service_ids.storage_alert",
    )
    def _compute_hosting_alerts(self):
        today = fields.Date.today()
        warning_date = today + timedelta(days=90)
        for partner in self:
            services = partner.hosting_service_ids.filtered(
                lambda s: s.state == "active"
            )
            partner.hosting_services_expiring_count = len(
                services.filtered(
                    lambda s: s.date_expiration
                    and today < s.date_expiration <= warning_date
                )
            )
            partner.hosting_services_updates_count = len(
                services.filtered(lambda s: s.update_available)
            )
            partner.hosting_services_storage_alert_count = len(
                services.filtered(lambda s: s.storage_alert)
            )

    def action_view_hosting_services(self):
        """Ouvrir les services d'hébergement pour ce partenaire."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": f"Services d'hébergement - {self.name}",
            "res_model": "hosting.service",
            "views": [[False, "list"], [False, "form"], [False, "kanban"]],
            "domain": [("partner_id", "=", self.id)],
            "context": {"default_partner_id": self.id},
        }

    def action_view_hosting_services_expiring(self):
        """Ouvrir les services d'hébergement expirant pour ce partenaire."""
        self.ensure_one()
        today = fields.Date.today()
        warning_date = today + timedelta(days=90)
        return {
            "type": "ir.actions.act_window",
            "name": f"Services expirant - {self.name}",
            "res_model": "hosting.service",
            "views": [[False, "list"], [False, "form"], [False, "kanban"]],
            "domain": [
                ("partner_id", "=", self.id),
                ("state", "=", "active"),
                ("date_expiration", ">", today),
                ("date_expiration", "<=", warning_date),
            ],
            "context": {"default_partner_id": self.id},
        }

    def action_view_hosting_services_updates(self):
        """Ouvrir les services d'hébergement avec mises à jour disponibles."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": f"Mises à jour disponibles - {self.name}",
            "res_model": "hosting.service",
            "views": [[False, "list"], [False, "form"], [False, "kanban"]],
            "domain": [
                ("partner_id", "=", self.id),
                ("state", "=", "active"),
                ("update_available", "=", True),
            ],
            "context": {"default_partner_id": self.id},
        }

    def action_view_hosting_services_storage_alert(self):
        """Ouvrir les services d'hébergement avec alertes de stockage."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": f"Alertes de stockage - {self.name}",
            "res_model": "hosting.service",
            "views": [[False, "list"], [False, "form"], [False, "kanban"]],
            "domain": [
                ("partner_id", "=", self.id),
                ("state", "=", "active"),
                ("storage_alert", "=", True),
            ],
            "context": {"default_partner_id": self.id},
        }
