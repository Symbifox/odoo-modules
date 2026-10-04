from odoo import _, api, models
from odoo.exceptions import AccessDenied, UserError


class ResUsers(models.Model):
    _inherit = "res.users"

    @api.model
    def _auth_oauth_signin(self, provider, validation, params):
        """Through a school's directory, sign in an existing student user, never create one.

        🔴 auth_oauth creates a user for any unknown subject when the website allows sign-up
        (b2c), and the directory holds staff too: every directory account would open a
        portal user. Only a student directory account gives one (bf.school.account).
        """
        schools = self.env["bf.school"].sudo().search([("student_oauth_provider_id", "=", provider)])
        if schools:
            self = self.with_context(no_user_creation=True)
        return super(ResUsers, self)._auth_oauth_signin(provider, validation, params)

    def _school_is_student_user(self):
        return any(user.share and user.partner_id.sudo().is_student for user in self)

    def _check_credentials(self, credential, env):
        # When the school signs its students in through its directory, that is the only door:
        # a password an administrator gave would open the portal outside it, and keep working
        # whatever the directory says (found in review). Without a directory, a student user
        # exists only if an administrator made it, password included.
        if credential.get("type") == "password" and self._school_is_student_user():
            accounts = self.env["bf.school.account"].sudo().search(
                [("student_id", "in", self.partner_id.ids)])
            if accounts.school_id.filtered("student_oauth_provider_id"):
                raise AccessDenied()
        return super()._check_credentials(credential, env)

    @property
    def SELF_WRITEABLE_FIELDS(self):
        # 🔴 A student's name, address and picture are the school's record (and the name goes
        # to the directory): over RPC and through the eLearning profile page, a student
        # renamed their own card (found in review). They keep their language and time zone.
        fields_ = super().SELF_WRITEABLE_FIELDS
        if (self or self.env.user)._school_is_student_user():
            return [name for name in fields_ if name in ("lang", "tz")]
        return fields_

    def _action_reset_password(self, signup_type="reset"):
        # A reset would give the student a password of their own, outside the directory, sent
        # to the address on the card, often a parent's (found in review).
        if signup_type == "reset" and self._school_is_student_user():
            raise UserError(_("A student signs in through the school's directory: the school "
                              "office gives a new password."))
        return super()._action_reset_password(signup_type=signup_type)

    def _deactivate_portal_user(self, **post):
        # Deleting the user would take the student's card, which the school keeps.
        if self._school_is_student_user():
            raise UserError(_("A student's account is managed by the school."))
        return super()._deactivate_portal_user(**post)
