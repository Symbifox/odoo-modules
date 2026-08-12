from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    # Minor / Guardian relationship
    is_minor_child = fields.Boolean(
        string="Personne mineure",
        help="Cocher si ce contact est une personne mineure (moins de 14 ans). "
        "Les consentements seront adressés aux responsables légaux.",
    )
    legal_guardian_ids = fields.Many2many(
        comodel_name="res.partner",
        relation="res_partner_legal_guardian_rel",
        column1="child_id",
        column2="guardian_id",
        string="Responsables légaux",
        help="Parents ou tuteurs de la personne mineure",
    )
    minor_child_ids = fields.Many2many(
        comodel_name="res.partner",
        relation="res_partner_legal_guardian_rel",
        column1="guardian_id",
        column2="child_id",
        string="Enfants à charge",
    )

    # Consent records
    consent_ids = fields.One2many(
        comodel_name="privacy.consent",
        inverse_name="subject_partner_id",
        string="Enregistrements de consentement",
    )
    consent_count = fields.Integer(
        compute="_compute_consent_stats",
        string="Nombre de consentements",
    )

    # Preferences
    preference_id = fields.Many2one(
        comodel_name="privacy.contact.preference",
        string="Préférences de vie privée",
        compute="_compute_preference_id",
        inverse="_inverse_preference_id",
        help="Préférences de vie privée et de communication du contact",
    )

    # Consent badges (computed)
    has_marketing_consent = fields.Boolean(
        compute="_compute_consent_badges",
        string="Consentement marketing",
        help="Possède un consentement actif accordé pour les finalités marketing",
    )
    has_recording_consent = fields.Boolean(
        compute="_compute_consent_badges",
        string="Consentement enregistrement",
        help="Possède un consentement actif accordé pour les finalités d'enregistrement",
    )
    has_reference_consent = fields.Boolean(
        compute="_compute_consent_badges",
        string="Consentement référence",
        help="Possède un consentement actif accordé pour être utilisé comme référence",
    )

    # Quick access to DNC status
    do_not_contact = fields.Boolean(
        compute="_compute_do_not_contact",
        string="Ne pas contacter",
        help="Indicateur principal « ne pas contacter » provenant des préférences",
    )

    @api.depends("consent_ids")
    def _compute_consent_stats(self):
        for partner in self:
            partner.consent_count = len(partner.consent_ids)

    def _compute_preference_id(self):
        Preference = self.env["privacy.contact.preference"]
        for partner in self:
            preference = Preference.search([
                ("partner_id", "=", partner.id),
                ("company_id", "=", self.env.company.id),
            ], limit=1)
            partner.preference_id = preference.id if preference else False

    def _inverse_preference_id(self):
        # Allow setting preference via this field
        pass

    @api.depends("consent_ids", "consent_ids.status", "consent_ids.purpose_id")
    def _compute_consent_badges(self):
        for partner in self:
            active_consents = partner.consent_ids.filtered(
                lambda c: c.status == "granted"
            )
            # Check for marketing consent
            partner.has_marketing_consent = any(
                c.purpose_id.code == "marketing" for c in active_consents
            )
            # Check for recording consent
            partner.has_recording_consent = any(
                c.purpose_id.code == "recording" for c in active_consents
            )
            # Check for reference consent
            partner.has_reference_consent = any(
                c.purpose_id.code == "reference" for c in active_consents
            )

    def _compute_do_not_contact(self):
        Preference = self.env["privacy.contact.preference"]
        for partner in self:
            preference = Preference.search([
                ("partner_id", "=", partner.id),
                ("company_id", "=", self.env.company.id),
            ], limit=1)
            partner.do_not_contact = preference.do_not_contact if preference else False

    def action_view_consents(self):
        """Open consent records for this partner."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": f"Consentements - {self.name}",
            "res_model": "privacy.consent",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("subject_partner_id", "=", self.id)],
            "context": {"default_subject_partner_id": self.id},
        }

    def action_view_preferences(self):
        """Open or create privacy preferences."""
        self.ensure_one()
        Preference = self.env["privacy.contact.preference"]
        preference = Preference.search([
            ("partner_id", "=", self.id),
            ("company_id", "=", self.env.company.id),
        ], limit=1)

        if preference:
            return {
                "type": "ir.actions.act_window",
                "name": f"Préférences - {self.name}",
                "res_model": "privacy.contact.preference",
                "view_mode": "form",
                "res_id": preference.id,
            }
        else:
            return {
                "type": "ir.actions.act_window",
                "name": f"Préférences - {self.name}",
                "res_model": "privacy.contact.preference",
                "view_mode": "form",
                "context": {"default_partner_id": self.id},
            }

    def action_request_consent(self):
        """Open wizard to request consent.

        If opened from a minor's form: the subject is the minor child,
        and the emails will be sent to all legal guardians automatically.
        If opened from a guardian's form: include all minor children as
        subjects so consent can be requested for each child.
        """
        self.ensure_one()
        subject_ids = [self.id]
        if not self.is_minor_child and self.minor_child_ids:
            # Guardian: include all minor children as subjects
            subject_ids = self.minor_child_ids.ids
        return {
            "type": "ir.actions.act_window",
            "name": "Demander le consentement",
            "res_model": "privacy.consent.request.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_partner_ids": [(6, 0, subject_ids)]},
        }
