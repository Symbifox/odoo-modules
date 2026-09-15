from odoo import fields, models


class ContactPersonaKpi(models.Model):
    _name = "contact.persona.kpi"
    _description = "KPI sur un contact"
    _order = "date_measured desc, id desc"

    persona_id = fields.Many2one(
        "contact.persona", required=True, ondelete="cascade", index=True,
        string="Persona",
    )
    name = fields.Char(required=True, string="Indicateur")
    value_text = fields.Char(string="Valeur")
    value_float = fields.Float(string="Valeur numérique")
    unit = fields.Char(string="Unité")
    date_measured = fields.Date(default=fields.Date.context_today, string="Mesuré le")
    source = fields.Selection(
        [
            ("manual", "Manuel"),
            ("project_sync", "/project-sync"),
            ("email_management", "Gestion des courriels"),
            ("account", "Comptabilité"),
        ],
        default="manual",
        required=True,
        string="Source",
    )
    notes = fields.Text(string="Notes")
