from odoo import _, api, fields, models
from odoo.exceptions import AccessError


class ContactPersonaKpi(models.Model):
    _name = "contact.persona.kpi"
    _description = "KPI on a contact"
    _order = "date_measured desc, id desc"

    persona_id = fields.Many2one(
        "contact.persona", required=True, ondelete="cascade", index=True,
        string="Persona",
    )
    name = fields.Char(required=True, string="KPI")
    value_text = fields.Char(string="Value")
    value_float = fields.Float(string="Numeric value")
    unit = fields.Char(string="Unit")
    date_measured = fields.Date(default=fields.Date.context_today, string="Measured "
                                                                          "on")
    source = fields.Selection(
        [
            ("manual", "Manual"),
            ("project_sync", "/project-sync"),
            ("email_management", "Email management"),
            ("account", "Accounting"),
        ],
        default="manual",
        required=True,
        string="Source",
    )
    notes = fields.Text(string="Notes")

    # An accounting KPI is the payer rating under another name: a rule
    # hides it from the persona user, but rules are checked before a write, not
    # after, so a persona user could still turn any line into one.
    def _check_account_source(self, vals_list):
        if self.env.su or self.env.user.has_group("bf_persona.group_persona_manager"):
            return
        if any(vals.get("source") == "account" for vals in vals_list) \
                or self.env.context.get("default_source") == "account":
            raise AccessError(_("The KPIs drawn from accounting are "
                                "reserved to the persona manager."))

    @api.model_create_multi
    def create(self, vals_list):
        self._check_account_source(vals_list)
        return super().create(vals_list)

    def write(self, vals):
        self._check_account_source([vals])
        return super().write(vals)
