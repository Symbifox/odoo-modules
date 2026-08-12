# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import fields, models


class BfSolicitCode(models.Model):
    """Solicit codes (RE-style) — contact-preference / do-not-solicit flags
    attached to a constituent, e.g. « Ne pas solliciter », « Ne pas appeler »."""

    _name = "bf.solicit.code"
    _description = "Code de sollicitation"
    _order = "sequence, name"

    name = fields.Char(string="Libellé", required=True, translate=True)
    code = fields.Char(string="Code")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    excludes_all = fields.Boolean(
        string="Exclut toute sollicitation",
        help="Si coché, ce code signifie que le constituant ne doit recevoir "
        "aucune sollicitation (ex. « Ne me sollicitez pas »).",
    )
    note = fields.Text(string="Note")

    _sql_constraints = [
        ("name_uniq", "unique(name)", "Ce code de sollicitation existe déjà."),
    ]
