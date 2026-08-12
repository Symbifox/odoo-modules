from datetime import timedelta

from odoo import api, fields, models


class PrivacyDashboard(models.TransientModel):
    """Tableau de bord des consentements de vie privée avec KPIs."""

    _name = "privacy.dashboard"
    _description = "Tableau de bord des consentements"

    # Tracking fields
    last_refresh = fields.Datetime(string="Dernière actualisation", readonly=True)

    # Consent Status KPIs
    pending_requests = fields.Integer(
        string="Demandes en attente",
        compute="_compute_stats",
    )
    expiring_30_days = fields.Integer(
        string="Expirant 0-30 jours",
        compute="_compute_stats",
    )
    expiring_60_days = fields.Integer(
        string="Expirant 30-60 jours",
        compute="_compute_stats",
    )
    expiring_90_days = fields.Integer(
        string="Expirant 60-90 jours",
        compute="_compute_stats",
    )
    expired_consents = fields.Integer(
        string="Consentements expirés",
        compute="_compute_stats",
    )
    granted_consents = fields.Integer(
        string="Consentements accordés",
        compute="_compute_stats",
    )
    refused_consents = fields.Integer(
        string="Consentements refusés",
        compute="_compute_stats",
    )
    withdrawn_consents = fields.Integer(
        string="Consentements révoqués",
        compute="_compute_stats",
    )

    # Rate KPIs
    consent_rate = fields.Float(
        string="Taux de consentement (%)",
        digits=(5, 0),
        compute="_compute_stats",
    )
    evidence_rate = fields.Float(
        string="Taux de preuve (%)",
        digits=(5, 0),
        compute="_compute_stats",
    )

    # DNC KPIs
    dnc_count = fields.Integer(
        string="Ne pas contacter",
        compute="_compute_stats",
    )

    # Destruction KPIs
    pending_destructions = fields.Integer(
        string="Destructions en attente",
        compute="_compute_stats",
    )

    # Renewal KPIs
    renewal_emails_pending = fields.Integer(
        string="Courriels de renouvellement en attente",
        compute="_compute_stats",
        help="Consentements expirant dans les 90 jours qui n'ont pas encore été renouvelés",
    )

    # Document destruction KPIs
    register_entries_count = fields.Integer(
        string="Entrées au registre",
        compute="_compute_stats",
    )
    register_entries_month = fields.Integer(
        string="Destructions ce mois",
        compute="_compute_stats",
    )
    active_campaigns = fields.Integer(
        string="Campagnes en cours",
        compute="_compute_stats",
    )
    assessments_due = fields.Integer(
        string="Réévaluations dues",
        compute="_compute_stats",
    )
    documents_classified = fields.Integer(
        string="Documents classifiés",
        compute="_compute_stats",
    )
    documents_past_retention = fields.Integer(
        string="Rétention dépassée",
        compute="_compute_stats",
    )

    @api.model
    def default_get(self, fields_list):
        """Override to compute stats on form load."""
        res = super().default_get(fields_list)
        res.update(self._get_dashboard_stats())
        return res

    def _get_dashboard_stats(self):
        """Calculate all dashboard statistics."""
        today = fields.Date.today()
        now = fields.Datetime.now()

        Consent = self.env["privacy.consent"]
        Preference = self.env["privacy.contact.preference"]

        # Date boundaries
        date_30 = now + timedelta(days=30)
        date_60 = now + timedelta(days=60)
        date_90 = now + timedelta(days=90)

        # Consent status counts
        pending_requests = Consent.search_count([("status", "=", "pending")])
        expired_consents = Consent.search_count([("status", "=", "expired")])
        granted_consents = Consent.search_count([("status", "=", "granted")])
        refused_consents = Consent.search_count([("status", "=", "refused")])
        withdrawn_consents = Consent.search_count([("status", "=", "withdrawn")])

        # Expiring consents (granted only)
        expiring_30 = Consent.search_count([
            ("status", "=", "granted"),
            ("expires_at", ">", now),
            ("expires_at", "<=", date_30),
        ])
        expiring_60 = Consent.search_count([
            ("status", "=", "granted"),
            ("expires_at", ">", date_30),
            ("expires_at", "<=", date_60),
        ])
        expiring_90 = Consent.search_count([
            ("status", "=", "granted"),
            ("expires_at", ">", date_60),
            ("expires_at", "<=", date_90),
        ])

        # Consent rate (granted / (granted + refused))
        total_responses = granted_consents + refused_consents
        consent_rate = (granted_consents / total_responses * 100) if total_responses > 0 else 0

        # Evidence rate (consents with evidence / total granted)
        if granted_consents > 0:
            with_evidence = Consent.search_count([
                ("status", "=", "granted"),
                ("evidence_ids", "!=", False),
            ])
            evidence_rate = with_evidence / granted_consents * 100
        else:
            evidence_rate = 0

        # DNC count
        dnc_count = Preference.search_count([("do_not_contact", "=", True)])

        # Pending destructions
        try:
            Destruction = self.env["privacy.destruction.request"]
            pending_destructions = Destruction.search_count([("state", "=", "pending")])
        except Exception:
            pending_destructions = 0

        # Renewal emails pending (consents expiring within 90 days, not yet renewed)
        renewal_emails_pending = Consent.search_count([
            ("status", "=", "granted"),
            ("expires_at", ">", now),
            ("expires_at", "<=", date_90),
            ("renewed_to_id", "=", False),
        ])

        # Document destruction KPIs
        register_entries_count = 0
        register_entries_month = 0
        active_campaigns = 0
        assessments_due = 0
        documents_classified = 0
        documents_past_retention = 0

        try:
            Register = self.env["privacy.destruction.register"]
            register_entries_count = Register.search_count([])
            month_start = today.replace(day=1)
            register_entries_month = Register.search_count([
                ("destruction_date", ">=", month_start),
            ])
        except Exception:
            pass

        try:
            Campaign = self.env["privacy.destruction.campaign"]
            active_campaigns = Campaign.search_count([
                ("state", "in", ["scanning", "review", "approved", "executing"]),
            ])
        except Exception:
            pass

        try:
            Assessment = self.env["privacy.anonymization.assessment"]
            assessments_due = Assessment.search_count([
                ("state", "=", "reassessment_due"),
            ])
        except Exception:
            pass

        try:
            Classification = self.env["privacy.document.classification"]
            documents_classified = Classification.search_count([("active", "=", True)])
            documents_past_retention = Classification.search_count([
                ("active", "=", True),
                ("retention_expiry_date", "<=", today),
                ("retention_expiry_date", "!=", False),
            ])
        except Exception:
            pass

        return {
            "last_refresh": now,
            "pending_requests": pending_requests,
            "expiring_30_days": expiring_30,
            "expiring_60_days": expiring_60,
            "expiring_90_days": expiring_90,
            "expired_consents": expired_consents,
            "granted_consents": granted_consents,
            "refused_consents": refused_consents,
            "withdrawn_consents": withdrawn_consents,
            "consent_rate": round(consent_rate),
            "evidence_rate": round(evidence_rate),
            "dnc_count": dnc_count,
            "pending_destructions": pending_destructions,
            "renewal_emails_pending": renewal_emails_pending,
            "register_entries_count": register_entries_count,
            "register_entries_month": register_entries_month,
            "active_campaigns": active_campaigns,
            "assessments_due": assessments_due,
            "documents_classified": documents_classified,
            "documents_past_retention": documents_past_retention,
        }

    @api.depends_context("uid")
    def _compute_stats(self):
        """Compute all statistics."""
        stats = self._get_dashboard_stats()
        for record in self:
            record.pending_requests = stats["pending_requests"]
            record.expiring_30_days = stats["expiring_30_days"]
            record.expiring_60_days = stats["expiring_60_days"]
            record.expiring_90_days = stats["expiring_90_days"]
            record.expired_consents = stats["expired_consents"]
            record.granted_consents = stats["granted_consents"]
            record.refused_consents = stats["refused_consents"]
            record.withdrawn_consents = stats["withdrawn_consents"]
            record.consent_rate = stats["consent_rate"]
            record.evidence_rate = stats["evidence_rate"]
            record.dnc_count = stats["dnc_count"]
            record.pending_destructions = stats["pending_destructions"]
            record.renewal_emails_pending = stats["renewal_emails_pending"]
            record.register_entries_count = stats["register_entries_count"]
            record.register_entries_month = stats["register_entries_month"]
            record.active_campaigns = stats["active_campaigns"]
            record.assessments_due = stats["assessments_due"]
            record.documents_classified = stats["documents_classified"]
            record.documents_past_retention = stats["documents_past_retention"]

    # === Action Methods ===

    def action_refresh(self):
        """Refresh dashboard data."""
        return {
            "type": "ir.actions.act_window",
            "res_model": "privacy.dashboard",
            "view_mode": "form",
            "target": "inline",
        }

    def action_view_pending_requests(self):
        """View pending consent requests."""
        return {
            "type": "ir.actions.act_window",
            "name": "Demandes en attente",
            "res_model": "privacy.consent",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("status", "=", "pending")],
            "context": {"default_status": "pending"},
        }

    def action_view_expiring_30(self):
        """View consents expiring within 30 days."""
        now = fields.Datetime.now()
        date_30 = now + timedelta(days=30)
        return {
            "type": "ir.actions.act_window",
            "name": "Expirant dans 0-30 jours",
            "res_model": "privacy.consent",
            "views": [[False, "list"], [False, "form"]],
            "domain": [
                ("status", "=", "granted"),
                ("expires_at", ">", now),
                ("expires_at", "<=", date_30),
            ],
        }

    def action_view_expiring_60(self):
        """View consents expiring in 30-60 days."""
        now = fields.Datetime.now()
        date_30 = now + timedelta(days=30)
        date_60 = now + timedelta(days=60)
        return {
            "type": "ir.actions.act_window",
            "name": "Expirant dans 30-60 jours",
            "res_model": "privacy.consent",
            "views": [[False, "list"], [False, "form"]],
            "domain": [
                ("status", "=", "granted"),
                ("expires_at", ">", date_30),
                ("expires_at", "<=", date_60),
            ],
        }

    def action_view_expiring_90(self):
        """View consents expiring in 60-90 days."""
        now = fields.Datetime.now()
        date_60 = now + timedelta(days=60)
        date_90 = now + timedelta(days=90)
        return {
            "type": "ir.actions.act_window",
            "name": "Expirant dans 60-90 jours",
            "res_model": "privacy.consent",
            "views": [[False, "list"], [False, "form"]],
            "domain": [
                ("status", "=", "granted"),
                ("expires_at", ">", date_60),
                ("expires_at", "<=", date_90),
            ],
        }

    def action_view_expired(self):
        """View expired consents."""
        return {
            "type": "ir.actions.act_window",
            "name": "Consentements expirés",
            "res_model": "privacy.consent",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("status", "=", "expired")],
        }

    def action_view_granted(self):
        """View granted consents."""
        return {
            "type": "ir.actions.act_window",
            "name": "Consentements accordés",
            "res_model": "privacy.consent",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("status", "=", "granted")],
        }

    def action_view_refused(self):
        """View refused consents."""
        return {
            "type": "ir.actions.act_window",
            "name": "Consentements refusés",
            "res_model": "privacy.consent",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("status", "=", "refused")],
        }

    def action_view_withdrawn(self):
        """View withdrawn consents."""
        return {
            "type": "ir.actions.act_window",
            "name": "Consentements révoqués",
            "res_model": "privacy.consent",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("status", "=", "withdrawn")],
        }

    def action_view_dnc(self):
        """View Do Not Contact preferences."""
        return {
            "type": "ir.actions.act_window",
            "name": "Ne pas contacter",
            "res_model": "privacy.contact.preference",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("do_not_contact", "=", True)],
        }

    def action_view_pending_destructions(self):
        """View pending destruction requests."""
        return {
            "type": "ir.actions.act_window",
            "name": "Destructions en attente",
            "res_model": "privacy.destruction.request",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("state", "=", "pending")],
        }

    def action_view_register(self):
        """View destruction register."""
        return {
            "type": "ir.actions.act_window",
            "name": "Registre de destruction",
            "res_model": "privacy.destruction.register",
            "views": [[False, "list"], [False, "form"]],
            "domain": [],
        }

    def action_view_register_month(self):
        """View this month's register entries."""
        month_start = fields.Date.today().replace(day=1)
        return {
            "type": "ir.actions.act_window",
            "name": "Destructions ce mois",
            "res_model": "privacy.destruction.register",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("destruction_date", ">=", month_start)],
        }

    def action_view_active_campaigns(self):
        """View active destruction campaigns."""
        return {
            "type": "ir.actions.act_window",
            "name": "Campagnes en cours",
            "res_model": "privacy.destruction.campaign",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("state", "in", ["scanning", "review", "approved", "executing"])],
        }

    def action_view_assessments_due(self):
        """View anonymization assessments due for reassessment."""
        return {
            "type": "ir.actions.act_window",
            "name": "Réévaluations dues",
            "res_model": "privacy.anonymization.assessment",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("state", "=", "reassessment_due")],
        }

    def action_view_documents_classified(self):
        """View classified documents."""
        return {
            "type": "ir.actions.act_window",
            "name": "Documents classifiés",
            "res_model": "privacy.document.classification",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("active", "=", True)],
        }

    def action_view_documents_past_retention(self):
        """View documents past their retention period."""
        today = fields.Date.today()
        return {
            "type": "ir.actions.act_window",
            "name": "Rétention dépassée",
            "res_model": "privacy.document.classification",
            "views": [[False, "list"], [False, "form"]],
            "domain": [
                ("active", "=", True),
                ("retention_expiry_date", "<=", today),
                ("retention_expiry_date", "!=", False),
            ],
        }

    def action_view_renewal_emails_pending(self):
        """View consents pending renewal email."""
        now = fields.Datetime.now()
        date_90 = now + timedelta(days=90)
        return {
            "type": "ir.actions.act_window",
            "name": "Courriels de renouvellement en attente",
            "res_model": "privacy.consent",
            "views": [[False, "list"], [False, "form"]],
            "domain": [
                ("status", "=", "granted"),
                ("expires_at", ">", now),
                ("expires_at", "<=", date_90),
                ("renewed_to_id", "=", False),
            ],
        }
