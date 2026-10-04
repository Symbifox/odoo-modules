from odoo import _, models
from odoo.exceptions import UserError


class ResUsers(models.Model):
    _inherit = "res.users"

    def _school_is_student_user(self):
        return any(user.share and user.partner_id.sudo().is_student for user in self)

    def write(self, vals):
        # The eLearning lets a user publish their own profile (name, picture, rank) on the
        # public website. A student's account does not.
        if vals.get("website_published") and self._school_is_student_user():
            raise UserError(_("A student's profile is not published on the website."))
        return super().write(vals)

    def _add_karma_batch(self, values_per_user):
        # No karma for students: no rank, no rank email, no badge (validating an email address
        # gave 3 points and queued "New rank: Newbie", found in review).
        values_per_user = {user: values for user, values in values_per_user.items()
                           if not user._school_is_student_user()}
        return super()._add_karma_batch(values_per_user)
