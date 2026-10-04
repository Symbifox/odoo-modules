from odoo import _, fields, models
from odoo.exceptions import UserError


class RenewCourse(models.TransientModel):
    """Copy a course, contents and files included, for the groups of another school year."""

    _name = "bf.school.slide.renew"
    _description = "Renew a course for another year"

    channel_id = fields.Many2one("slide.channel", required=True, ondelete="cascade")
    name = fields.Char("Name of the copy", required=True)
    group_ids = fields.Many2many(
        "bf.school.group", string="Groups of the new year", required=True,
        domain="[('year_id.state', '!=', 'closed')]")

    def action_renew(self):
        self.ensure_one()
        channel = self.channel_id
        channel.check_access("read")
        if not (self.env.su or channel.user_id == self.env.user
                or self.env.user.has_group("bf_school_core.group_school_manager")):
            raise UserError(_("Only the person responsible for the course renews it."))
        if self.group_ids & channel.school_group_ids:
            raise UserError(_("The copy is for other groups than the course's own."))
        # The copy is the teacher's own course, written under their rights: they must be
        # allowed to create one, and to tie it to these groups (checked by create).
        copy = channel.copy({
            "name": self.name, "school_group_ids": [(6, 0, self.group_ids.ids)],
            "website_published": False, "user_id": self.env.uid})
        return {
            "type": "ir.actions.act_window",
            "res_model": "slide.channel",
            "res_id": copy.id,
            "view_mode": "form",
        }
