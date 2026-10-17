from markupsafe import Markup

from odoo import _, fields, models


def default_acknowledgement(env):
    # Our own HTML, translated: marked safe, or a report prints its tags as text.
    return Markup(env._(
        "<p>I acknowledge receiving the item described above. I agree:</p>"
        "<ul>"
        "<li>to use it only for the purposes it was given for;</li>"
        "<li>not to lend it, copy it or have it copied;</li>"
        "<li>to report its loss or theft without delay;</li>"
        "<li>to return it on request, when the need ends, and at the latest on my last day.</li>"
        "</ul>"
    ))


class ResCompany(models.Model):
    _inherit = "res.company"

    bf_key_manager_id = fields.Many2one(
        "res.users", string="Key manager",
        help="Receives the departure, loss and overdue activities when no one closer can act.")
    bf_key_retention_days = fields.Integer(
        string="Keep hand-out details (days)", default=1095,
        help="After this many days from the return, the name, phone and employer of a person "
             "without an account, and the signature, are erased (Quebec Law 25, s. 23). "
             "Zero: never.")
    bf_key_acknowledgement = fields.Html(
        string="Acknowledgement text", translate=True,
        help="Printed above the signature on the acknowledgement of each hand-out. Empty: the "
             "standard text, in the reader's language.")

    def _bf_key_acknowledgement_text(self):
        """The company's text, or the standard one in the current language. ⚠️ No stored
        default: written at install, it would be English only for an existing company."""
        self.ensure_one()
        return self.bf_key_acknowledgement or default_acknowledgement(self.env)
