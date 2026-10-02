from odoo import models

from odoo.addons.bf_color.models.color_utils import normalize_hex


class BfShiftAssignment(models.Model):
    _name = "bf.shift.assignment"
    _inherit = ["bf.shift.assignment", "bf.color.mixin"]

    def _bf_color_own(self):
        """Only a free color set on the shift itself.

        The shift's ``color`` is its template's (related): taken as the shift's
        own color, it painted every « Day » shift alike and hid the agenda's
        per-employee colors whenever the employee had no color of their own.
        """
        return normalize_hex(self.color_hex)
