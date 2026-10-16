from odoo import _, api, fields, models
from odoo.exceptions import AccessError

SYSTEM = "base.group_system"


class SchoolRecordType(models.Model):
    """A kind of piece the student's file holds.

    The eight pieces of the Regulation respecting private educational institutions at the
    preschool, elementary and secondary levels (E-9.1, r. 3, s. 7) ship as data, with no company:
    every school reads them and only the system administration changes them (changing one changes
    which files are complete in every school). A school adds its own kinds, which carry its company,
    without touching the legal ones.
    """

    _name = "bf.school.record.type"
    _description = "Kind of piece in a student's file"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    requirement = fields.Selection(
        [("always", "Always required"),
         ("when_applicable", "Required when it applies"),
         ("optional", "School's own, not required")],
        required=True, default="optional",
        help="Always required: the file is incomplete without it. Required when it applies: "
             "the school marks the piece received, missing, or not applicable with a note. "
             "School's own: kept in the file, never reported as missing.")
    legal_ref = fields.Char(
        "Legal reference", translate=True, help="Where the requirement comes from, for example "
                                "E-9.1, r. 3, s. 7, para. 2.")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", "Company", index=True, ondelete="restrict",
        help="Empty: a kind shared by every company of the database (the ones that ship with the module), which "
             "only the system administration changes. A kind a school adds carries its company.")

    _sql_constraints = [
        ("code_unique", "UNIQUE(code)", "A kind of piece has its own code."),
    ]

    def _is_system(self):
        return self.env.su or self.env.user.has_group(SYSTEM)

    @api.model_create_multi
    def create(self, vals_list):
        if not self._is_system():
            for vals in vals_list:
                # An empty value is the screen's default, not a request for a shared kind.
                if not vals.get("company_id"):
                    vals["company_id"] = self.env.company.id
                elif vals["company_id"] not in self.env.companies.ids:
                    raise AccessError(_("A kind of piece belongs to one of your companies."))
        return super().create(vals_list)

    def write(self, vals):
        # 🔴 A shared kind decides which files are complete in every school: the record rule keeps the office to
        # its own company's, and a school cannot make a kind shared, or another company's, by writing the company.
        if "company_id" in vals and not self._is_system() and vals["company_id"] not in self.env.companies.ids:
            raise AccessError(_("A kind of piece belongs to one of your companies."))
        return super().write(vals)
