from odoo import _, api, models

from odoo.addons.bf_work_hours.models.engine import Item

# Writing an email takes longer than sending a prompt (MyAnalytics credited 5 minutes).
EMAIL_MINUTES = 5


class WorkCollector(models.AbstractModel):
    _inherit = "bf.work.collector"

    @api.model
    def _bf_source_descriptions(self):
        return super()._bf_source_descriptions() + [
            ("email", _("Email"), _("When you sent an email from your mail client (read back from your Sent folder). "
                           "Not to whom, not what it says. Emails sent from Odoo are already seen as "
                           "browser activity.")),
        ]

    @api.model
    def _bf_point_minutes(self):
        return dict(super()._bf_point_minutes(), email=EMAIL_MINUTES)

    @api.model
    def _bf_collect(self, user, start, stop):
        items = super()._bf_collect(user, start, stop)
        # Sent from the mail client and read back by IMAP: a hand wrote it. An
        # email sent from the chatter may be an agent's, or a scheduled send
        # whose time says nothing about when it was written.
        emails = self.env["bf.email"].sudo().with_context(active_test=False).search([
            ("user_id", "=", user.id),
            ("is_from_me", "=", True),
            ("direction", "=", "out"),
            ("source", "=", "imap"),
            ("date", ">=", start),
            ("date", "<", stop),
        ])
        # Out of the Sent folder only: a message with the person's address in From
        # can also land in the inbox (a forged sender, a shared alias).
        emails = emails.filtered(
            lambda e: e.account_id and (e.imap_folder or "").lower() in e.account_id._sent_folder_names())
        items += [Item(e.date, e.date, "email", "point") for e in emails]
        return items
