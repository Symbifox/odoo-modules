"""Open-loop exclusion for mass mailings.

Overrides mailing.mailing._get_remaining_recipients(), the single choke
point used by both the manual send (_action_send_mail) and the queue
cron (_process_mass_mailing_queue), to drop recipients whose contact is
in an open CX loop: a feedback flagged for follow-up and not yet done,
or an open complaint. Matching is done on normalized emails, which is
model-agnostic (mailing.contact for mailing lists, res.partner, or any
recipient model exposing an email field). Opt-in per mailing via
bf_cx_exclude_open_loops, default False: unchecked, core behaviour is
untouched. This module never sends anything by itself.
"""
import logging

from odoo import fields, models, tools

_logger = logging.getLogger(__name__)

# A complaint is "open" until it is at least resolved: the loop is still
# being closed during reception, acknowledgement and analysis.
COMPLAINT_OPEN_STATES = ("received", "ack", "analysis")


class MailingMailing(models.Model):
    _inherit = "mailing.mailing"

    bf_cx_exclude_open_loops = fields.Boolean(
        string="Exclure les boucles CX ouvertes",
        help="À l'envoi, retire les destinataires dont le contact a un "
             "feedback à rappeler non traité ou une plainte ouverte : pas "
             "de promotion à quelqu'un qu'on est en train de rattraper. "
             "Décochée, le comportement standard est inchangé.",
    )

    def _get_remaining_recipients(self):
        """Filter out open-loop contacts when the mailing opted in."""
        res_ids = super()._get_remaining_recipients()
        if not res_ids or not self.bf_cx_exclude_open_loops:
            return res_ids
        try:
            return self._bf_cx_filter_open_loops(res_ids)
        except Exception:  # noqa: BLE001 - never block the mailing flow
            _logger.exception(
                "bf_cx_mass_mailing: open-loop filter failed for mailing "
                "%s, falling back to the unfiltered recipients",
                self.ids,
            )
            return res_ids

    def _bf_cx_open_loop_emails(self):
        """Normalized emails of contacts currently in an open CX loop."""
        partners = (
            self.env["bf.cx.feedback"]
            .sudo()
            .search(
                [
                    ("needs_followup", "=", True),
                    ("state", "!=", "done"),
                    ("partner_id", "!=", False),
                ]
            )
            .partner_id
        )
        complaints = (
            self.env["bf.cx.complaint"]
            .sudo()
            .search([("state", "in", COMPLAINT_OPEN_STATES)])
        )
        partners |= complaints.partner_id
        emails = {p.email_normalized for p in partners if p.email_normalized}
        # A complaint filed without a contact card still carries an email.
        for complaint in complaints:
            normalized = tools.email_normalize(
                complaint.contact_email, strict=False
            )
            if normalized:
                emails.add(normalized)
        return emails

    def _bf_cx_filter_open_loops(self, res_ids):
        """Drop the res_ids whose email matches an open-loop contact.

        Order is preserved. When the recipient model has no usable email
        field, or when no loop is open, the list is returned as is.
        """
        self.ensure_one()
        excluded_emails = self._bf_cx_open_loop_emails()
        if not excluded_emails:
            return res_ids
        records = self.env[self.mailing_model_real].sudo().browse(res_ids)
        email_field = next(
            (
                name
                for name in ("email_normalized", "email_from", "email")
                if name in records._fields
            ),
            None,
        )
        if email_field is None:
            return res_ids
        kept = []
        dropped = 0
        for record in records:
            raw = record[email_field]
            normalized = (
                raw
                if email_field == "email_normalized"
                else tools.email_normalize(raw, strict=False)
            )
            if normalized and normalized in excluded_emails:
                dropped += 1
                continue
            kept.append(record.id)
        if dropped:
            _logger.info(
                "bf_cx_mass_mailing: mailing %s excluded %s recipient(s) "
                "in an open CX loop",
                self.id,
                dropped,
            )
        return kept
