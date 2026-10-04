import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)


class SchoolAccount(models.Model):
    _inherit = "bf.school.account"

    portal_user_id = fields.Many2one(
        "res.users", "Portal user", readonly=True, copy=False, ondelete="set null",
        help="The student's portal user, tied to this directory account: the student signs in "
             "to the portal through the school's directory.")

    # 🔴 Set by the system only, at creation too: an account created by the office with the
    # administrator as its portal user had the administrator bound to the office's own
    # directory subject by the next synchronisation (found in review).
    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and any("portal_user_id" in vals for vals in vals_list):
            raise AccessError(_("A student's portal user is kept by the system."))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and "portal_user_id" in vals:
            raise AccessError(_("A student's portal user is kept by the system."))
        return super().write(vals)

    def action_sync(self):
        done = super().action_sync()
        self._school_portal_sync()
        return done

    def _school_portal_refusal(self):
        """Why this account cannot have a portal user of its own, or an empty string."""
        self.ensure_one()
        student = self.student_id
        if not student.is_student:
            return _("%s is not marked as a student.", student.name)
        # An adult made a "student" for the occasion would get a second user acting for
        # their own children (found in review): a student's card is nobody's guardian, and
        # a card that already has a user keeps it.
        if student.guardian_student_link_ids:
            return _("%s is the guardian of a student.", student.name)
        others = student.with_context(active_test=False).user_ids - self.portal_user_id
        if others:
            return _("%(student)s already has a user (%(login)s).", student=student.name,
                     login=", ".join(others.mapped("login")))
        return ""

    def _school_portal_user_ok(self, user):
        """Only a portal user born for this student is ever written by the synchronisation."""
        return user.share and not user._is_internal() and user.partner_id == self.student_id

    def _school_portal_sync(self):
        """Give each synchronised account its portal user, and make it follow the account.

        A suspended account archives the user even when the directory could not be reached:
        a student who left must not keep the portal waiting for the next synchronisation.
        One account that fails does not stop the others.
        """
        for account in self.sudo():
            try:
                with self.env.cr.savepoint():
                    account._school_portal_sync_one()
            except (UserError, AccessError) as error:
                _logger.warning("bf_school_student_portal: %s: %s", account.username, error)
                if not self.env.context.get("school_quiet"):
                    account._message_log(body=_("Portal user not synchronised: %s", error))

    def _school_portal_sync_one(self):
        self.ensure_one()
        user = self.portal_user_id.with_context(active_test=False)
        if user and not self._school_portal_user_ok(user):
            raise UserError(_("The portal user %s is not this student's: left alone.", user.login))
        provider = self.school_id.student_oauth_provider_id
        if self.state != "active" or not provider or not self.directory_pk:
            if user.active:
                user.active = False
                self._message_log(body=_("Portal user archived."))
            return
        subject = str(self.directory_pk)
        if not user:
            refusal = self._school_portal_refusal()
            if refusal:
                raise UserError(refusal)
            # The school's company: res.users.create moves the student's card to the user's
            # company, which would otherwise be the one running the job.
            company = self.school_id.company_id or self.env.company
            user = self.env["res.users"].sudo().with_context(no_reset_password=True).create({
                "company_id": company.id, "company_ids": [(6, 0, company.ids)],
                "login": "%s@%s" % (self.username, self.school_id.code
                                    or "school-%s" % self.school_id.id),
                "partner_id": self.student_id.id,
                "groups_id": [(6, 0, self.env.ref("base.group_portal").ids)],
                "oauth_provider_id": provider.id, "oauth_uid": subject,
            })
            self.portal_user_id = user
            self._message_log(body=_("Portal user created: %s.", user.login))
            return
        vals = {}
        if not user.active:
            vals["active"] = True
        if user.oauth_provider_id != provider or user.oauth_uid != subject:
            vals.update({"oauth_provider_id": provider.id, "oauth_uid": subject})
        if vals:
            user.write(vals)
            self._message_log(body=_("Portal user brought back in line with the account."))

    @api.model
    def _cron_school_portal_sync(self):
        # Quiet: a refusal is logged on the account when the office synchronises it, not daily.
        self.search([]).with_context(school_quiet=True)._school_portal_sync()
