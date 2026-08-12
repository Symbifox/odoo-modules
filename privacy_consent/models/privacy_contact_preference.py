import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class PrivacyContactPreference(models.Model):
    _name = "privacy.contact.preference"
    _description = "Préférences de contact"
    _inherit = ["mail.thread"]
    _rec_name = "partner_id"

    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Contact",
        required=True,
        ondelete="cascade",
        index=True,
    )

    # Communication preferences
    allow_service_email = fields.Boolean(
        string="Autoriser les courriels de service",
        default=True,
        tracking=True,
        help="Autoriser les communications essentielles liées au service",
    )
    allow_marketing_email = fields.Boolean(
        string="Autoriser les courriels marketing",
        default=False,
        tracking=True,
        help="Autoriser les communications promotionnelles et marketing",
    )
    allow_phone = fields.Boolean(
        string="Autoriser les appels téléphoniques",
        default=True,
        tracking=True,
        help="Autoriser le contact par téléphone",
    )
    allow_sms = fields.Boolean(
        string="Autoriser les SMS",
        default=False,
        tracking=True,
        help="Autoriser les messages texte SMS",
    )

    # Master override
    do_not_contact = fields.Boolean(
        string="Ne pas contacter",
        default=False,
        tracking=True,
        help="Interrupteur principal pour désactiver tout contact (remplace les paramètres individuels)",
    )

    # Preferences
    preferred_language = fields.Selection(
        selection=[
            ("fr_CA", "Français (Canada)"),
            ("en_CA", "Anglais (Canada)"),
            ("en_US", "Anglais (États-Unis)"),
        ],
        string="Langue préférée",
        tracking=True,
    )
    preferred_contact_time = fields.Selection(
        selection=[
            ("morning", "Matin (8 h - 12 h)"),
            ("afternoon", "Après-midi (12 h - 17 h)"),
            ("evening", "Soirée (17 h - 20 h)"),
            ("anytime", "N'importe quand"),
        ],
        string="Heure de contact préférée",
        default="anytime",
    )

    # Opt-out tracking
    opt_out_reason = fields.Selection(
        selection=[
            ("too_frequent", "Trop fréquent"),
            ("not_relevant", "Non pertinent"),
            ("privacy", "Préoccupations de vie privée"),
            ("other", "Autre"),
        ],
        string="Raison du désabonnement",
        help="Raison pour laquelle le contact s'est désabonné des communications",
    )
    opt_out_date = fields.Date(
        string="Date de désabonnement",
    )

    notes = fields.Text(string="Notes")

    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Société",
        default=lambda self: self.env.company,
    )

    _sql_constraints = [
        (
            "partner_company_uniq",
            "unique(partner_id, company_id)",
            "Chaque contact ne peut avoir qu'un seul enregistrement de préférences par société !",
        ),
    ]

    @api.onchange("do_not_contact")
    def _onchange_do_not_contact(self):
        if self.do_not_contact:
            self.opt_out_date = fields.Date.today()

    def action_opt_out_all(self):
        """Opt out from all communications."""
        self.write({
            "do_not_contact": True,
            "allow_marketing_email": False,
            "allow_phone": False,
            "allow_sms": False,
            "opt_out_date": fields.Date.today(),
        })
        return True

    def action_reset_preferences(self):
        """Reset to default preferences."""
        self.write({
            "do_not_contact": False,
            "allow_service_email": True,
            "allow_marketing_email": False,
            "allow_phone": True,
            "allow_sms": False,
            "opt_out_reason": False,
            "opt_out_date": False,
        })
        return True

    # -------------------------------------------------------------------------
    # Marketing Blacklist Sync
    # -------------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        """Create preferences and sync with marketing blacklist."""
        records = super().create(vals_list)
        if not self.env.context.get('skip_blacklist_sync'):
            records._sync_marketing_blacklist()
        return records

    def write(self, vals):
        """Update preferences and sync with marketing blacklist if needed."""
        result = super().write(vals)
        # Only sync if relevant fields changed and not already syncing
        if (any(f in vals for f in ['allow_marketing_email', 'do_not_contact'])
                and not self.env.context.get('skip_blacklist_sync')):
            self._sync_marketing_blacklist()
        return result

    def _sync_marketing_blacklist(self):
        """Sync privacy preferences with email marketing blacklist.

        When a contact opts out of marketing:
        - Add their email to mail.blacklist (global blacklist)
        - Set opt_out on any mailing.contact records

        When a contact opts back in:
        - Remove from mail.blacklist
        - Clear opt_out on mailing.contact records
        """
        Blacklist = self.env.get('mail.blacklist')
        MailingContact = self.env.get('mailing.contact')

        if not Blacklist:
            _logger.debug("mail.blacklist not available, skipping sync")
            return

        for pref in self:
            email = pref.partner_id.email
            if not email:
                continue

            # Determine if should be blacklisted
            should_blacklist = pref.do_not_contact or not pref.allow_marketing_email

            if should_blacklist:
                # Add to blacklist
                self._add_to_blacklist(email)
                # Also opt-out from mailing contacts
                if MailingContact:
                    self._optout_mailing_contacts(email)
                _logger.info(
                    "Added %s to marketing blacklist (do_not_contact=%s, allow_marketing=%s)",
                    email, pref.do_not_contact, pref.allow_marketing_email
                )
            else:
                # Remove from blacklist (only if explicitly allowing marketing)
                if pref.allow_marketing_email:
                    self._remove_from_blacklist(email)
                    if MailingContact:
                        self._optin_mailing_contacts(email)
                    _logger.info("Removed %s from marketing blacklist", email)

    def _add_to_blacklist(self, email):
        """Add email to global mail blacklist."""
        Blacklist = self.env['mail.blacklist'].sudo()
        existing = Blacklist.search([('email', '=ilike', email)], limit=1)
        if not existing:
            Blacklist.create({'email': email})
        elif not existing.active:
            # Re-activate if was removed
            existing.write({'active': True})

    def _remove_from_blacklist(self, email):
        """Remove email from global mail blacklist."""
        Blacklist = self.env['mail.blacklist'].sudo()
        existing = Blacklist.search([('email', '=ilike', email)], limit=1)
        if existing:
            # Use the remove wizard method or just deactivate
            existing.write({'active': False})

    def _optout_mailing_contacts(self, email):
        """Set opt_out on all mailing.contact records with this email."""
        MailingContact = self.env['mailing.contact'].sudo()
        contacts = MailingContact.search([('email', '=ilike', email)])
        if contacts:
            # Opt-out from all subscriptions
            contacts.write({'opt_out': True})
            # Also update subscription records if they exist
            if 'mailing.contact.subscription' in self.env:
                subs = self.env['mailing.contact.subscription'].sudo().search([
                    ('contact_id', 'in', contacts.ids)
                ])
                if subs:
                    subs.write({'opt_out': True})

    def _optin_mailing_contacts(self, email):
        """Clear opt_out on all mailing.contact records with this email."""
        MailingContact = self.env['mailing.contact'].sudo()
        contacts = MailingContact.search([('email', '=ilike', email)])
        if contacts:
            contacts.write({'opt_out': False})
            if 'mailing.contact.subscription' in self.env:
                subs = self.env['mailing.contact.subscription'].sudo().search([
                    ('contact_id', 'in', contacts.ids)
                ])
                if subs:
                    subs.write({'opt_out': False})
