"""Win/loss feedback: survey the contact when an opportunity is lost.

No BF precedent existed on the loss path - the anchor is the core
crm.lead.action_set_lost(). One survey per lead (bf_cx_loss_survey_sent),
cooldown applied, and everything wrapped so a survey hiccup can never
block marking a deal lost.
"""
import logging

from odoo import _, fields, models

_logger = logging.getLogger(__name__)


class CrmLead(models.Model):
    _inherit = "crm.lead"

    bf_cx_loss_survey_sent = fields.Boolean(
        string="Sondage post-perte envoyé", copy=False
    )

    def action_set_won(self):
        res = super().action_set_won()
        try:
            with self.env.cr.savepoint():
                self._bf_cx_enroll_won()
        except Exception:  # noqa: BLE001 - never block the won flow
            _logger.exception(
                "bf_cx_crm: NPS enrollment failed for leads %s", self.ids
            )
        return res

    def _bf_cx_enroll_won(self):
        """Add a fresh customer to the NEXT DRAFT wave of the relational
        program (no immediate send: the wave goes out manually and the
        cooldown applies then - a freshly signed client should not be
        surveyed on day one)."""
        raw = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("bf_cx.won_program_id")
        )
        try:
            program_id = int(raw or 0)
        except (TypeError, ValueError):
            program_id = 0
        if not program_id:
            return
        program = self.env["bf.cx.program"].sudo().browse(program_id).exists()
        if not program:
            return
        for lead in self:
            partner = lead.partner_id
            if not partner or not partner.email:
                continue
            wave = self.env["bf.cx.wave"].sudo().search(
                [("program_id", "=", program.id), ("state", "=", "draft")],
                order="id desc",
                limit=1,
            )
            if not wave:
                wave = self.env["bf.cx.wave"].sudo().create(
                    {
                        "name": _("Prochaine vague (enrôlements gagnés)"),
                        "program_id": program.id,
                    }
                )
            if partner not in wave.partner_ids:
                wave.partner_ids = [(4, partner.id)]
                lead.message_post(
                    body=_(
                        "Client ajouté à la prochaine vague du programme "
                        "« %s » (aucun envoi immédiat)."
                    )
                    % program.name
                )
        return True

    def action_set_lost(self, **additional_values):
        res = super().action_set_lost(**additional_values)
        try:
            with self.env.cr.savepoint():
                self._bf_cx_send_loss_survey()
        except Exception:  # noqa: BLE001 - never block the lost flow
            _logger.exception(
                "bf_cx_crm: loss survey failed for leads %s", self.ids
            )
        return res

    def _bf_cx_send_loss_survey(self):
        raw = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("bf_cx.loss_program_id")
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
        for lead in self:
            if lead.bf_cx_loss_survey_sent:
                continue
            partner = lead.partner_id
            if not partner or not partner.email:
                continue
            allowed, blocked = partner._bf_cx_split_solicitable(
                days=program.cooldown_days or None
            )
            if blocked:
                lead.message_post(
                    body=_(
                        "Sondage post-perte non envoyé : %s a été sollicité "
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
            lead.bf_cx_loss_survey_sent = True
            lead.message_post(
                body=_(
                    "Sondage post-perte « %(program)s » envoyé à %(partner)s "
                    "(motif de perte : %(reason)s).",
                    program=program.name,
                    partner=partner.display_name,
                    reason=lead.lost_reason_id.name or _("non précisé"),
                )
            )
