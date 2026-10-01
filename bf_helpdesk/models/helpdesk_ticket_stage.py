from odoo import fields, models


class HelpdeskTicketStage(models.Model):
    _inherit = "helpdesk.ticket.stage"

    # Le nom de l'étape sert au classement interne (« Triage », « Dev ») et
    # n'a pas à sortir chez le client. Vide = le nom de l'étape, comme avant.
    portal_label = fields.Char(
        string="Libellé côté client",
        translate=True,
        help="Ce que le client voit au portail pour cette étape. "
             "Vide = le nom de l'étape.",
    )
