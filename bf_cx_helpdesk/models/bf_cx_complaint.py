"""Complaint ↔ helpdesk ticket link.

The real Many2one to helpdesk.ticket lives HERE (bridge module), never in
bf_cx core: a comodel absent from the registry would break loading
on tenants without helpdesk_mgmt.
"""
import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class BfCxComplaint(models.Model):
    _inherit = "bf.cx.complaint"

    helpdesk_ticket_id = fields.Many2one(
        "helpdesk.ticket",
        string="Ticket helpdesk",
        ondelete="set null",
        copy=False,
        tracking=True,
    )

    def action_create_helpdesk_ticket(self):
        self.ensure_one()
        if self.helpdesk_ticket_id:
            raise UserError(
                _("La plainte %s a déjà un ticket : %s.")
                % (self.number, self.helpdesk_ticket_id.number)
            )
        ticket = self.env["helpdesk.ticket"].create(
            self._prepare_helpdesk_ticket_vals()
        )
        self.helpdesk_ticket_id = ticket
        self.message_post(
            body=_("Ticket helpdesk %s créé pour cette plainte.") % ticket.number
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": "helpdesk.ticket",
            "res_id": ticket.id,
            "view_mode": "form",
        }

    def _prepare_helpdesk_ticket_vals(self):
        self.ensure_one()
        team = self.env.ref(
            "bf_cx_helpdesk.helpdesk_team_complaints",
            raise_if_not_found=False,
        )
        vals = {
            "name": _("Plainte %s - %s") % (self.number, self.name),
            "description": self.description or "<p>%s</p>" % (self.name or ""),
            "priority": {
                "low": "0",
                "medium": "1",
                "high": "2",
                "critical": "3",
            }.get(self.severity, "1"),
            "partner_id": self.partner_id.id,
            "partner_name": self.partner_id.name or self.contact_name,
            "partner_email": self.partner_id.email or self.contact_email,
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
            # Set the initial stage explicitly so the stage email template
            # (acknowledgement) fires - the compute alone does not trigger
            # _track_template on create in every path.
            stage = team._get_applicable_stages()[:1]
            if stage:
                vals["stage_id"] = stage.id
        return vals

    def action_open_helpdesk_ticket(self):
        self.ensure_one()
        if not self.helpdesk_ticket_id:
            raise UserError(_("Aucun ticket lié à cette plainte."))
        return {
            "type": "ir.actions.act_window",
            "res_model": "helpdesk.ticket",
            "res_id": self.helpdesk_ticket_id.id,
            "view_mode": "form",
        }
