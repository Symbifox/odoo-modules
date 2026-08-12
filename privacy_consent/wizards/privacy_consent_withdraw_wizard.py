from odoo import api, fields, models
from odoo.exceptions import UserError


class PrivacyConsentWithdrawWizard(models.TransientModel):
    _name = "privacy.consent.withdraw.wizard"
    _description = "Assistant de retrait de consentement"

    consent_id = fields.Many2one(
        comodel_name="privacy.consent",
        string="Consentement",
        required=True,
        readonly=True,
    )
    partner_id = fields.Many2one(
        related="consent_id.subject_partner_id",
        string="Contact",
        readonly=True,
    )
    purpose_id = fields.Many2one(
        related="consent_id.purpose_id",
        string="Finalité",
        readonly=True,
    )
    withdrawal_reason = fields.Selection(
        selection=[
            ("request", "Demande du sujet"),
            ("data_deletion", "Demande de suppression des données"),
            ("error", "Consentement donné par erreur"),
            ("no_longer_needed", "Plus nécessaire"),
            ("other", "Autre"),
        ],
        string="Raison",
        required=True,
    )
    withdrawal_notes = fields.Text(
        string="Notes supplémentaires",
        help="Tout détail supplémentaire concernant ce retrait",
    )
    update_preferences = fields.Boolean(
        string="Mettre à jour les préférences du contact",
        default=True,
        help="Mettre aussi à jour les préférences du contact en fonction de ce retrait",
    )

    def action_withdraw(self):
        """Withdraw the consent and update records."""
        self.ensure_one()

        if self.consent_id.status != "granted":
            raise UserError("Seuls les consentements accordés peuvent être retirés.")

        # Build reason text
        reason_label = dict(self._fields["withdrawal_reason"].selection).get(
            self.withdrawal_reason, self.withdrawal_reason
        )
        reason_text = f"Raison : {reason_label}"
        if self.withdrawal_notes:
            reason_text += f"\n{self.withdrawal_notes}"

        # Update consent
        self.consent_id.write({
            "status": "withdrawn",
            "withdrawn_at": fields.Datetime.now(),
            "withdrawal_reason": reason_text,
        })

        # Post message
        self.consent_id.message_post(
            body=f"Consentement retiré.\n{reason_text}",
            message_type="notification",
        )

        # Update preferences if requested
        if self.update_preferences:
            self._update_contact_preferences()

        return {"type": "ir.actions.act_window_close"}

    def _update_contact_preferences(self):
        """Update contact preferences based on purpose type."""
        partner = self.consent_id.subject_partner_id
        purpose_code = self.consent_id.purpose_id.code

        Preference = self.env["privacy.contact.preference"]
        preference = Preference.search([
            ("partner_id", "=", partner.id),
            ("company_id", "=", self.env.company.id),
        ], limit=1)

        if not preference:
            # Create preference record
            preference = Preference.create({
                "partner_id": partner.id,
            })

        # Update based on purpose
        if purpose_code == "marketing":
            preference.write({
                "allow_marketing_email": False,
                "opt_out_reason": "privacy",
                "opt_out_date": fields.Date.today(),
            })
        elif purpose_code in ("recording", "reference"):
            # Just add a note
            note = f"Consentement pour {purpose_code} retiré le {fields.Date.today()}"
            existing_notes = preference.notes or ""
            preference.write({
                "notes": f"{existing_notes}\n{note}".strip(),
            })
