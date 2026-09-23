"""Variables de la calculatrice, par personne (« taux = 125 », et la mémoire M)."""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

MEMORY = "M"


class BfCalculatorVariable(models.Model):
    _name = "bf.calculator.variable"
    _description = "Calculator variable"
    _order = "name"

    user_id = fields.Many2one(
        "res.users", string="User", required=True, index=True, ondelete="cascade",
        default=lambda self: self.env.user,
    )
    name = fields.Char(string="Name", required=True)
    value = fields.Float(string="Value", digits=(16, 6), required=True)
    #: La valeur exacte (Decimal en texte) : `value` arrondit à six décimales, et
    #: « taux = 1/3 » puis « 3 * taux » donnait 0,999999.
    value_exact = fields.Char(string="Exact value")

    _sql_constraints = [
        ("name_user_uniq", "unique(user_id, name)", "A variable name is used only once per person."),
    ]

    def write(self, vals):
        # La règle d'écriture ne voit que l'état d'avant : changer user_id
        # déposerait la variable chez quelqu'un d'autre.
        if "user_id" in vals and not self.env.su:
            raise AccessError(_("This field is set by the calculator only."))
        return super().write(vals)

    @api.model
    def _as_dict(self):
        return {v.name: (v.value_exact or v.value)
                for v in self.search([("user_id", "=", self.env.uid)])}

    @api.model
    def _set(self, name, value):
        vals = {"value": float(value), "value_exact": str(value)}
        var = self.search([("user_id", "=", self.env.uid), ("name", "=", name)], limit=1)
        if var:
            var.write(vals)
        else:
            var = self.create({"name": name, **vals})
        return var
