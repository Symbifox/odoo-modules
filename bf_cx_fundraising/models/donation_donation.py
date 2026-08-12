"""Donor experience survey after a donation is confirmed.

The anchor is donation.validate() (OCA donation module), the single
state method that moves a donation from draft to done and posts the
journal entry. One survey per donation (bf_cx_donor_survey_sent), and
because a loyal donor gives often, the program cooldown (recommended:
90 days) is the main protection against oversolicitation. Everything
is wrapped so a survey hiccup can never block validating a donation.
"""
import logging

from odoo import _, fields, models

_logger = logging.getLogger(__name__)


class DonationDonation(models.Model):
    _inherit = "donation.donation"

    bf_cx_donor_survey_sent = fields.Boolean(
        string="Sondage donateur envoyé", copy=False
    )

    def validate(self):
        res = super().validate()
        try:
            with self.env.cr.savepoint():
                self._bf_cx_send_donor_survey()
        except Exception:  # noqa: BLE001 - never block donation validation
            _logger.exception(
                "bf_cx_fundraising: donor survey failed for donations %s",
                self.ids,
            )
        return res

    def _bf_cx_send_donor_survey(self):
        raw = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("bf_cx.donor_program_id")
        )
        try:
            program_id = int(raw or 0)
        except (TypeError, ValueError):
            program_id = 0
        if not program_id:
            return
        program = (
            self.env["bf.cx.program"].sudo().browse(program_id).exists()
        )
        if not program or not program.survey_id or not program.invite_template_id:
            return
        # A per-program cadence (cooldown_days) overrides the global
        # cooldown; 0 falls back to the global parameter.
        days = program.cooldown_days or None
        for donation in self:
            if donation.state != "done":
                continue
            if donation.bf_cx_donor_survey_sent:
                continue
            partner = donation.partner_id
            if not partner or not partner.email:
                continue
            allowed, blocked = partner._bf_cx_split_solicitable(days=days)
            if blocked:
                donation.message_post(
                    body=_(
                        "Sondage donateur non envoyé : %s a été sollicité "
                        "récemment (garde-fou anti-sursollicitation)."
                    )
                    % partner.display_name
                )
                continue
            answer = program.survey_id.sudo()._create_answer(
                partner=partner, check_attempts=False
            )
            program.invite_template_id.sudo().send_mail(
                answer.id, force_send=False
            )
            partner._bf_cx_mark_solicited()
            donation.bf_cx_donor_survey_sent = True
            donation.message_post(
                body=_(
                    "Sondage donateur « %(program)s » envoyé à %(partner)s "
                    "après la validation du don.",
                    program=program.name,
                    partner=partner.display_name,
                )
            )
