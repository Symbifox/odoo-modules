from odoo import models


class BfCxFeedback(models.Model):
    _name = "bf.cx.feedback"
    _inherit = ["bf.cx.feedback", "bf.color.mixin"]


class BfCxTheme(models.Model):
    _name = "bf.cx.theme"
    _inherit = ["bf.cx.theme", "bf.color.mixin"]
