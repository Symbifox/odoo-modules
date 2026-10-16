from odoo import api, fields, models


class SchoolGroup(models.Model):
    _inherit = "bf.school.group"

    homeroom_ids = fields.Many2many(
        "res.users", "bf_school_group_homeroom_rel", "group_id", "user_id", "Homeroom teachers",
        domain=[("share", "=", False)],
        help="The teachers in charge of the group (titulaires): they write the general comment and the "
             "year-end decision of its report cards. At the secondary level the group lists all its teachers, "
             "so who is in charge is said here. Taken from the group's teacher when it has only one.")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            teachers = vals.get("teacher_ids") or []
            if "homeroom_ids" not in vals and len(teachers) == 1 and teachers[0][0] == 6 and len(teachers[0][2]) == 1:
                vals["homeroom_ids"] = [(6, 0, teachers[0][2])]
        return super().create(vals_list)
