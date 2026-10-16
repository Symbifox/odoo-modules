from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class SchoolGroup(models.Model):
    _inherit = "bf.school.group"

    ministry_group_number = fields.Integer(
        "Ministry group number", help="The group's number in Charlemagne (NO_GROUP_ELEVE and GROUP_ELEVE), 1 to 99. "
                                      "A group's name such as 301 is the school's, this is the Ministry's.")

    @api.constrains("ministry_group_number", "school_id", "year_id")
    def _check_ministry_group_number(self):
        for group in self.filtered("ministry_group_number"):
            if not 1 <= group.ministry_group_number <= 99:
                raise ValidationError(_("The Ministry's group number is between 1 and 99."))
            if self.search_count([("school_id", "=", group.school_id.id), ("year_id", "=", group.year_id.id),
                                  ("ministry_group_number", "=", group.ministry_group_number),
                                  ("id", "!=", group.id)]):
                raise ValidationError(_("Another group of the year already has the Ministry number %s.",
                                        group.ministry_group_number))
