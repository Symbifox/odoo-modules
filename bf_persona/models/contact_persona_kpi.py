from odoo import fields, models


class ContactPersonaKpi(models.Model):
    _name = "contact.persona.kpi"
    _description = "KPI sur un contact"
    _order = "date_measured desc, id desc"

    persona_id = fields.Many2one(
        "contact.persona", required=True, ondelete="cascade", index=True,
    )
    name = fields.Char(required=True)
    value_text = fields.Char()
    value_float = fields.Float()
    unit = fields.Char()
    date_measured = fields.Date(default=fields.Date.context_today)
    source = fields.Selection(
        [
            ("manual", "Manuel"),
            ("project_sync", "/project-sync"),
            ("email_management", "Gestion des courriels"),
            ("account", "Comptabilité"),
        ],
        default="manual",
        required=True,
    )
    notes = fields.Text()
