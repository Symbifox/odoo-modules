from odoo import fields, models


class HrEmployee(models.Model):
    _name = "hr.employee"
    _inherit = ["hr.employee", "bf.color.mixin"]


class HrEmployeePublic(models.Model):
    """The public profile must carry every stored field of the employee.

    Odoo serves people without HR rights from ``hr.employee.public`` and treats
    any stored employee field missing there as private: reading an employee's
    name would then fail on the prefetched ``color_hex``.
    """

    _inherit = "hr.employee.public"

    color_hex = fields.Char(string="Own color", readonly=True)


class HrEmployeeCategory(models.Model):
    _name = "hr.employee.category"
    _inherit = ["hr.employee.category", "bf.color.mixin"]
