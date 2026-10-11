# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import api, fields, models


class BfSolicitCode(models.Model):
    """Solicit codes (RE-style) — contact-preference / do-not-solicit flags
    attached to a constituent, e.g. « Ne pas solliciter », « Ne pas appeler »."""

    _name = "bf.solicit.code"
    _description = "Solicit code"
    _order = "sequence, name"

    name = fields.Char(string="Label", required=True, translate=True)
    code = fields.Char(string="Code")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    excludes_all = fields.Boolean(
        string="Excludes all solicitation",
        help="If checked, this code means the constituent must receive no "
             "solicitation at all (e.g. \"Do not solicit me\").",
    )
    note = fields.Text(string="Note")

    _sql_constraints = [
        ("name_uniq", "unique(name)", "This solicit code already exists."),
    ]

    @api.model
    def _bf_name_donation_menu(self):
        """Name the Fundraising app menu in every installed language.

        The menu belongs to the OCA `donation` module, which this module renames:
        donation's catalogue gives it "Don" in French, and no catalogue of this
        module reaches a record of another module. Called at install (noupdate
        data) and by the upgrade that introduced it.
        """
        menu = self.env.ref("donation.donation_top_menu", raise_if_not_found=False)
        langs = [code for code, _name in self.env["res.lang"].get_installed() if code != "en_US"]
        names = {code: self.with_context(lang=code).env._("Fundraising") for code in langs}
        # A language without a translation keeps what donation's catalogue gave it.
        names = {code: name for code, name in names.items() if name != "Fundraising"}
        if menu and names:
            menu.sudo().update_field_translations("name", names)
