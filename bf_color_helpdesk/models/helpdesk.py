from odoo import models


class HelpdeskTicketTag(models.Model):
    _name = "helpdesk.ticket.tag"
    _inherit = ["helpdesk.ticket.tag", "bf.color.mixin"]


class HelpdeskTicketTeam(models.Model):
    _name = "helpdesk.ticket.team"
    _inherit = ["helpdesk.ticket.team", "bf.color.mixin"]


class HelpdeskTicket(models.Model):
    _name = "helpdesk.ticket"
    _inherit = ["helpdesk.ticket", "bf.color.mixin"]
