from odoo import models


class BfEditorialEntry(models.Model):
    _name = "bf.editorial.entry"
    _inherit = ["bf.editorial.entry", "bf.color.mixin"]


class BlogTagCategory(models.Model):
    _name = "blog.tag.category"
    _inherit = ["blog.tag.category", "bf.color.mixin"]
