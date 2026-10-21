"""Closed-loop extension: optional automatic ticket for detractors."""
import logging

from markupsafe import Markup

from odoo import fields, models
from odoo.exceptions import UserError
from odoo.tools.mail import plaintext2html

from odoo.addons.bf_cx.models.bf_cx_feedback import param_is_true

from .bf_cx_complaint import langue_du_client

_logger = logging.getLogger(__name__)


class BfCxFeedback(models.Model):
    _inherit = "bf.cx.feedback"

    helpdesk_ticket_id = fields.Many2one(
        "helpdesk.ticket",
        string="Follow-up ticket",
        ondelete="set null",
        copy=False,
    )

    def _run_closed_loop(self):
        super()._run_closed_loop()
        if not param_is_true(self.env, "bf_cx.auto_ticket", default=False):
            return
        for rec in self:
            if not rec._needs_followup() or rec.helpdesk_ticket_id:
                continue
            try:
                rec.sudo()._create_followup_ticket()
            except Exception:  # noqa: BLE001 - never break the public flow
                _logger.exception(
                    "bf_cx_helpdesk: auto ticket failed for feedback %s",
                    rec.id,
                )

    def _create_followup_ticket(self):
        self.ensure_one()
        team = self.env.ref(
            "bf_cx_helpdesk.helpdesk_team_complaints",
            raise_if_not_found=False,
        )
        # Nom et description du billet dans la langue du client : ils peuvent
        # lui parvenir (accusé de l'étape initiale, portail). Appelé hors
        # requête de l'équipe (enquête publique, consommation d'une note) :
        # `fb.env._`, pas `_()`, qui lirait la langue du cadre.
        fb = self.with_context(
            lang=langue_du_client(self.env, self.partner_id, self.company_id)
        )
        comment = (self.comment or "").strip()
        description = Markup("<p>%s</p>") % fb.env._(
            "Rating of %(score)s/%(max)s received from %(partner)s.",
            score=self.score,
            max=int(self.score_max),
            partner=self.partner_id.display_name
            or fb.env._("an anonymous respondent"),
        )
        if comment:
            description += Markup("<blockquote>%s</blockquote>") % plaintext2html(
                comment
            )
        vals = {
            "name": fb.env._("Detractor follow-up - %s")
            % (self.partner_id.display_name or fields.Date.to_string(self.date)),
            "description": description,
            "partner_id": self.partner_id.id,
            "user_id": self.user_id.id,
            "company_id": self.company_id.id,
        }
        channel = self.env.ref(
            "bf_cx_helpdesk.helpdesk_channel_cx",
            raise_if_not_found=False,
        )
        if channel:
            vals["channel_id"] = channel.id
        if team:
            vals["team_id"] = team.id
            stage = team._get_applicable_stages()[:1]
            if stage:
                vals["stage_id"] = stage.id
        ticket = self.env["helpdesk.ticket"].create(vals)
        self.helpdesk_ticket_id = ticket
        return ticket

    def action_create_followup_ticket(self):
        self.ensure_one()
        if self.helpdesk_ticket_id:
            raise UserError(
                self.env._("This feedback already has a follow-up ticket: %s.")
                % self.helpdesk_ticket_id.number
            )
        ticket = self._create_followup_ticket()
        return {
            "type": "ir.actions.act_window",
            "res_model": "helpdesk.ticket",
            "res_id": ticket.id,
            "view_mode": "form",
        }
