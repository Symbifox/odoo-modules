from odoo import models


class ContactPersonaCategory(models.Model):
    _name = "contact.persona.category"
    _inherit = ["contact.persona.category", "bf.color.mixin"]
