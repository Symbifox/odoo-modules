from dateutil.relativedelta import relativedelta
from markupsafe import Markup

from odoo import api, fields, models

PARAM_MONTHS = "bf_employee_photo_age.months"
DEFAULT_MONTHS = 24


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    photo_date = fields.Datetime(
        string="Photo taken on", readonly=True, copy=False, groups="hr.group_hr_user",
        help="When the current photo was put on file. Empty when there is no photo, "
             "only a drawn avatar.")
    photo_age_months = fields.Integer(
        string="Photo age (months)", compute="_compute_photo_age", groups="hr.group_hr_user")
    photo_is_stale = fields.Boolean(
        string="Photo to update", compute="_compute_photo_age", search="_search_photo_is_stale",
        groups="hr.group_hr_user")

    @api.model
    def _bf_photo_months(self):
        try:
            return max(int(self.env["ir.config_parameter"].sudo().get_param(PARAM_MONTHS, DEFAULT_MONTHS)), 0)
        except (TypeError, ValueError):
            return DEFAULT_MONTHS

    @api.depends("photo_date")
    def _compute_photo_age(self):
        months = self._bf_photo_months()
        now = fields.Datetime.now()
        for employee in self:
            if not employee.photo_date:
                employee.photo_age_months = 0
                employee.photo_is_stale = False
                continue
            delta = relativedelta(now, employee.photo_date)
            employee.photo_age_months = delta.years * 12 + delta.months
            employee.photo_is_stale = bool(months) and employee.photo_age_months >= months

    def _search_photo_is_stale(self, operator, value):
        months = self._bf_photo_months()
        if operator not in ("=", "!=") or not isinstance(value, bool):
            raise NotImplementedError()
        stale = [("photo_date", "!=", False), ("photo_date", "<=", fields.Datetime.now() - relativedelta(months=months))] \
            if months else [("id", "=", False)]
        if (operator == "=") == value:
            return stale
        return ["!", "&"] + stale if months else []

    # --- Keeping the date -------------------------------------------------------

    def _bf_photo_attachments(self):
        """{employee id: attachment} of the stored photo, when it is a raster image.

        An SVG is never a photo: it is a drawn avatar (Odoo's initials, or
        bf_avatar's), or a logo.
        """
        attachments = self.env["ir.attachment"].sudo().search([
            ("res_model", "=", "hr.employee"), ("res_field", "=", "image_1920"),
            ("res_id", "in", self.ids)])
        return {a.res_id: a for a in attachments
                if (a.mimetype or "").startswith("image/") and "svg" not in a.mimetype}

    def _bf_photo_sync_date(self):
        photos = self._bf_photo_attachments()
        for employee in self.sudo():
            attachment = photos.get(employee.id)
            date = attachment.write_date if attachment else False
            if employee.photo_date != date:
                employee.with_context(bf_photo_sync=True).photo_date = date
                if attachment:
                    employee._bf_photo_close_reminders()

    @api.model_create_multi
    def create(self, vals_list):
        employees = super().create(vals_list)
        employees._bf_photo_sync_date()
        return employees

    def write(self, vals):
        result = super().write(vals)
        if "image_1920" in vals and not self.env.context.get("bf_photo_sync"):
            self._bf_photo_sync_date()
        return result

    # --- Reminder ---------------------------------------------------------------

    def _bf_photo_reminder_target(self):
        """The reminder goes on the person's own contact: every internal user can
        open it, where an employee file is for HR only."""
        self.ensure_one()
        return self.user_id.partner_id if self.user_id and not self.user_id.share else False

    def _bf_photo_close_reminders(self):
        activity_type = self.env.ref("bf_employee_photo_age.mail_activity_type_photo_update",
                                     raise_if_not_found=False)
        for employee in self:
            target = employee._bf_photo_reminder_target()
            if not target or not activity_type:
                continue
            self.env["mail.activity"].sudo().search([
                ("res_model", "=", "res.partner"), ("res_id", "=", target.id),
                ("activity_type_id", "=", activity_type.id)]).action_feedback(
                    feedback=self.env._("A new photo is on file."))

    @api.model
    def _cron_photo_reminders(self):
        months = self._bf_photo_months()
        if not months:
            return 0
        activity_type = self.env.ref("bf_employee_photo_age.mail_activity_type_photo_update")
        employees = self.sudo().search([("photo_is_stale", "=", True), ("user_id", "!=", False)])
        # Same test as hr: without self-editing, a picture changed in the
        # preferences never reaches an employee file that already has one.
        self_edit = bool(self.env["ir.config_parameter"].sudo().get_param("hr.hr_employee_self_edit"))
        created = 0
        for employee in employees:
            target = employee._bf_photo_reminder_target()
            if not target:
                continue
            if self.env["mail.activity"].sudo().search_count([
                    ("res_model", "=", "res.partner"), ("res_id", "=", target.id),
                    ("activity_type_id", "=", activity_type.id)]):
                continue
            user = employee.user_id
            lang = user.lang or self.env.lang
            env = employee.with_context(lang=lang).env
            if self_edit:
                how = env._("Put a recent one in your preferences: your avatar at the top right, "
                            "then Preferences.")
            else:
                how = env._("Send a recent one to the person in charge of HR, who will put it on file.")
            # The note sits on the person's contact, which every internal user can
            # open: it says the photo is due, not how old it is (that stays HR's).
            note = Markup("<p>%s</p><p>%s</p>") % (
                env._("Your photo on file is due for an update."), how)
            target.activity_schedule(
                activity_type_id=activity_type.id, user_id=user.id,
                summary=env._("Update your photo"), note=note)
            created += 1
        return created
