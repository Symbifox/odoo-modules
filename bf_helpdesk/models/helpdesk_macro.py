from odoo import fields, models


class HelpdeskMacro(models.Model):
    _name = "helpdesk.macro"
    _description = "Macro / réponse pré-faite pour helpdesk"
    _order = "sequence, name"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    body_html = fields.Html(
        string="Contenu",
        sanitize=True,
        required=True,
        help="HTML qui sera posté dans le chatter du ticket lors de l'application de la macro.",
    )
    applicable_team_ids = fields.Many2many(
        comodel_name="helpdesk.ticket.team",
        relation="helpdesk_macro_team_rel",
        column1="macro_id",
        column2="team_id",
        string="Équipes applicables",
        help="Vide = la macro s'applique à toutes les équipes.",
    )
    active = fields.Boolean(default=True)
