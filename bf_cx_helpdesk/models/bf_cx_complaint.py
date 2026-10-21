"""Complaint ↔ helpdesk ticket link.

The real Many2one to helpdesk.ticket lives HERE (bridge module), never in
bf_cx core: a comodel absent from the registry would break loading
on tenants without helpdesk_mgmt.
"""
import logging

from odoo import fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


def langue_du_client(env, partner, company):
    """La langue d'un texte de billet qui peut parvenir au client.

    Le nom et la description d'un billet sont lus par l'équipe d'assistance,
    mais aussi par le client : accusé de réception de l'étape initiale (posée
    exprès à la création) et portail « Mes billets » d'helpdesk_mgmt. Le
    client prime : sa langue, sinon celle de sa société, sinon celle de la
    société émettrice. Rend une langue ACTIVE, sinon celle de l'env (un
    `with_context(lang=…)` inactif ferait lever `env.lang`).
    """
    actives = {code for code, _nom in env["res.lang"].get_installed()}
    candidates = (
        partner.lang,
        partner.commercial_partner_id.lang,
        (company or env.company).partner_id.lang,
    )
    for lang in candidates:
        if lang and lang in actives:
            return lang
    return env.lang


class BfCxComplaint(models.Model):
    _inherit = "bf.cx.complaint"

    helpdesk_ticket_id = fields.Many2one(
        "helpdesk.ticket",
        string="Helpdesk ticket",
        ondelete="set null",
        copy=False,
        tracking=True,
    )

    def action_create_helpdesk_ticket(self):
        self.ensure_one()
        if self.helpdesk_ticket_id:
            raise UserError(
                self.env._("Complaint %s already has a ticket: %s.")
                % (self.number, self.helpdesk_ticket_id.number)
            )
        # Les valeurs du billet dans la langue du client (voir
        # `langue_du_client`) ; la note au fil de la plainte, interne, dans
        # celle de l'usager qui clique.
        client = self.with_context(
            lang=langue_du_client(self.env, self.partner_id, self.company_id)
        )
        ticket = self.env["helpdesk.ticket"].create(
            client._prepare_helpdesk_ticket_vals()
        )
        self.helpdesk_ticket_id = ticket
        self.message_post(
            body=self.env._("Helpdesk ticket %s created for this complaint.")
            % ticket.number
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
            "name": self.env._("Complaint %s - %s") % (self.number, self.name),
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
            raise UserError(self.env._("No ticket linked to this complaint."))
        return {
            "type": "ir.actions.act_window",
            "res_model": "helpdesk.ticket",
            "res_id": self.helpdesk_ticket_id.id,
            "view_mode": "form",
        }
