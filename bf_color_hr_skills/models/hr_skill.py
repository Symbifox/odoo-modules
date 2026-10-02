from odoo import models


class HrSkillType(models.Model):
    _name = "hr.skill.type"
    _inherit = ["hr.skill.type", "bf.color.mixin"]
