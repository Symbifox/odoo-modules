from odoo import _, api, fields, models
from odoo.exceptions import AccessError

#: What a course tied to school groups always is. Members only: the course and its contents
#: are invisible to anyone else. No comments nor reviews: a student would write under their
#: name in front of the other families. No karma: ranks and their emails are a game the
#: school did not choose. No "course completed" email: a parent who opens the contents with
#: their child would receive it. No "content published" email either: it went to the students
#: and to the address on the child's card, often a parent's (found in review). The families
#: see the contents on the portal.
SCHOOL_COURSE_VALUES = {
    "visibility": "members", "enroll": "invite", "allow_comment": False,
    "karma_gen_channel_rank": 0, "karma_gen_channel_finish": 0,
    "completed_template_id": False, "publish_template_id": False,
}
#: Who else would join: a user group enrolled whole (Portal: every family of the school) or
#: allowed to upload. Only the school groups decide.
SCHOOL_COURSE_CLEARED = ("enroll_group_ids", "upload_group_ids")
NO_QUIZ_KARMA = {
    "quiz_first_attempt_reward": 0, "quiz_second_attempt_reward": 0,
    "quiz_third_attempt_reward": 0, "quiz_fourth_attempt_reward": 0,
}


class SlideChannel(models.Model):
    _inherit = "slide.channel"

    school_group_ids = fields.Many2many(
        "bf.school.group", "bf_school_slide_channel_group_rel", "channel_id", "group_id",
        "School groups",
        help="Their students are the course's attendees, kept up to date as students join "
             "or leave the group. The course is then shown to its attendees only.")
    school_include_guardians = fields.Boolean(
        "Parents follow the course", default=True,
        help="The adults who receive the school's notices for these students are attendees "
             "too: at primary school, they open the course with the child. They get no email "
             "when a content is published.")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("school_group_ids"):
                vals.update(SCHOOL_COURSE_VALUES)
                vals.update({field: [(5, 0, 0)] for field in SCHOOL_COURSE_CLEARED})
        channels = super().create(vals_list)
        channels._school_check_groups()
        channels.filtered("school_group_ids")._school_sync_members()
        return channels

    def write(self, vals):
        res = super().write(vals)
        if "school_group_ids" in vals:
            self._school_check_groups()
        school = self.filtered("school_group_ids")
        locked = set(SCHOOL_COURSE_VALUES) | set(SCHOOL_COURSE_CLEARED)
        if school and ("school_group_ids" in vals or set(vals) & locked):
            school._school_lock()
            if "school_group_ids" in vals:
                school.slide_ids.sudo().write(NO_QUIZ_KARMA)
        if {"school_group_ids", "school_include_guardians"} & set(vals):
            self._school_sync_members()
        return res

    def _school_lock(self):
        for channel in self:
            vals = {key: value for key, value in SCHOOL_COURSE_VALUES.items()
                    if channel._fields[key].convert_to_write(channel[key], channel) != value}
            vals.update({field: [(5, 0, 0)] for field in SCHOOL_COURSE_CLEARED if channel[field]})
            if vals:
                super(SlideChannel, channel).write(vals)

    def _school_check_groups(self):
        """A teacher ties a course to the groups they teach; the office to any group."""
        if self.env.su or self.env.user.has_group("bf_school_core.group_school_manager"):
            return
        for channel in self:
            foreign = channel.school_group_ids.filtered(lambda g: self.env.user not in g.teacher_ids)
            if foreign:
                raise AccessError(_("You do not teach %s: only the school office ties a course "
                                    "to another teacher's group.", ", ".join(foreign.mapped("name"))))

    # --- Attendees ---------------------------------------------------------------

    def _school_target_partners(self):
        self.ensure_one()
        students = self.school_group_ids.enrollment_ids.filtered(
            lambda e: e.state == "active").student_id.filtered("active")
        guardians = self.env["res.partner"]
        if self.school_include_guardians:
            guardians = students.student_guardian_link_ids.filtered(
                "receives_notices").guardian_id.filtered("active")
        return students, guardians

    def _school_sync_members(self):
        """Make the attendees the groups' students (and their parents), and nobody else it added.

        🔴 Only the memberships the bridge created are taken away: an attendee the teacher
        invited by hand stays. Parents are attendees without following the course: the
        eLearning emails every follower at each content published, and families asked
        for fewer notices, not more.
        """
        Membership = self.env["slide.channel.partner"].sudo()
        for channel in self.sudo():
            students, guardians = (channel._school_target_partners() if channel.school_group_ids
                                   else (self.env["res.partner"], self.env["res.partner"]))
            target = students | guardians
            current = Membership.search([("channel_id", "=", channel.id)])
            # An attendee invited by hand but not enrolled would not see the contents.
            to_add = target - current.filtered(
                lambda m: m.active and m.member_status != "invited").partner_id
            if to_add:
                channel._action_add_members(to_add)
                Membership.search([("channel_id", "=", channel.id),
                                   ("partner_id", "in", to_add.ids)]).write({"school_synced": True})
                followers = to_add & guardians
                if followers:
                    channel.message_unsubscribe(partner_ids=followers.ids)
            gone = current.filtered(lambda m: m.active and m.school_synced
                                    and m.partner_id not in target)
            if gone:
                channel._remove_membership(gone.partner_id.ids)

    @api.model
    def _school_sync_for_groups(self, groups):
        if groups:
            self.sudo().search([("school_group_ids", "in", groups.ids)])._school_sync_members()

    @api.model
    def _cron_school_sync_members(self):
        # Also the courses left without a group (a group deleted): their attendees go.
        synced = self.env["slide.channel.partner"].sudo().search([("school_synced", "=", True)])
        (self.sudo().search([("school_group_ids", "!=", False)]) | synced.channel_id
         )._school_sync_members()

    # --- Next year -------------------------------------------------------------

    def action_school_renew(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Renew for another year"),
            "res_model": "bf.school.slide.renew",
            "view_mode": "form",
            "target": "new",
            "context": {"default_channel_id": self.id, "default_name": self.name},
        }


class SlideChannelPartner(models.Model):
    _inherit = "slide.channel.partner"

    school_synced = fields.Boolean(
        "Added from a school group", readonly=True,
        help="Taken away when the student leaves the group. An attendee invited by hand stays.")


class Slide(models.Model):
    _inherit = "slide.slide"

    @api.model_create_multi
    def create(self, vals_list):
        slides = super().create(vals_list)
        slides.filtered(lambda s: s.channel_id.school_group_ids).sudo().write(NO_QUIZ_KARMA)
        return slides

    def write(self, vals):
        res = super().write(vals)
        if set(vals) & (set(NO_QUIZ_KARMA) | {"channel_id"}):
            school = self.filtered(lambda s: s.channel_id.school_group_ids)
            drift = school.filtered(lambda s: any(s[k] for k in NO_QUIZ_KARMA))
            if drift:
                super(Slide, drift.sudo()).write(NO_QUIZ_KARMA)
        return res
