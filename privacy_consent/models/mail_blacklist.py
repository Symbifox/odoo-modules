import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class MailBlacklist(models.Model):
    """Extend mail.blacklist to sync with privacy preferences."""

    _inherit = "mail.blacklist"

    @api.model_create_multi
    def create(self, vals_list):
        """Create blacklist entries and sync with privacy preferences."""
        records = super().create(vals_list)
        records._sync_privacy_preferences(blacklisted=True)
        return records

    def write(self, vals):
        """Update blacklist and sync with privacy preferences."""
        result = super().write(vals)
        if 'active' in vals:
            # Blacklist activated or deactivated
            self._sync_privacy_preferences(blacklisted=vals['active'])
        return result

    def _sync_privacy_preferences(self, blacklisted=True):
        """Sync blacklist changes to privacy contact preferences.

        When an email is blacklisted (e.g., via unsubscribe link):
        - Find the partner with that email
        - Update their privacy preferences to not allow marketing

        Args:
            blacklisted: True if being blacklisted, False if being removed
        """
        Preference = self.env.get('privacy.contact.preference')
        Partner = self.env['res.partner']

        if not Preference:
            return

        for record in self:
            email = record.email
            if not email:
                continue

            # Find partner(s) with this email
            partners = Partner.search([('email', '=ilike', email)])

            for partner in partners:
                # Find or create preference record
                pref = Preference.search([
                    ('partner_id', '=', partner.id),
                ], limit=1)

                if blacklisted:
                    if pref:
                        # Update existing - use sudo and context to avoid recursion
                        if pref.allow_marketing_email:
                            pref.with_context(skip_blacklist_sync=True).write({
                                'allow_marketing_email': False,
                                'opt_out_date': self.env.context.get('today') or False,
                            })
                            _logger.info(
                                "Updated privacy preferences for %s: marketing disabled",
                                email
                            )
                    else:
                        # Create new preference with marketing disabled
                        Preference.with_context(skip_blacklist_sync=True).create({
                            'partner_id': partner.id,
                            'allow_marketing_email': False,
                            'opt_out_date': self.env.context.get('today') or False,
                        })
                        _logger.info(
                            "Created privacy preferences for %s: marketing disabled",
                            email
                        )
                else:
                    # Being removed from blacklist - don't auto-enable marketing
                    # (that should be an explicit user choice)
                    _logger.info(
                        "Email %s removed from blacklist, privacy preferences unchanged",
                        email
                    )
