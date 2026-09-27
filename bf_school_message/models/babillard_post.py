from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

FAMILIES = "school_families"


class BabillardPost(models.Model):
    _inherit = "bf.babillard.post"

    # 🔴 If this module is uninstalled, a family announcement must NOT become an
    # "all staff" one (the base default). "groupes" with no group reaches nobody:
    # the posts stay readable by the editors and invisible to everyone else.
    audience = fields.Selection(
        selection_add=[(FAMILIES, "School families")],
        ondelete={FAMILIES: "set groupes"})
    school_id = fields.Many2one(
        "bf.school", string="School", ondelete="restrict",
        default=lambda s: s._school_default_school())
    school_group_ids = fields.Many2many(
        "bf.school.group", string="School groups",
        domain="[('school_id', '=', school_id), ('year_id.state', '=', 'current')]",
        help="Empty: every family of the school. Otherwise the families of these groups only.")

    @api.model
    def _school_default_school(self):
        """A teacher's school: the one of the groups they teach this year."""
        return self.env["bf.school.group"].search(
            [("teacher_ids", "in", self.env.uid), ("year_id.state", "=", "current")],
            limit=1).school_id

    def _school_is_teacher_post(self, user):
        """A family announcement to groups that this user teaches, every one of them."""
        self.ensure_one()
        groups = self.sudo().school_group_ids
        return bool(self.audience == FAMILIES and groups
                    and all(user in g.teacher_ids for g in groups))

    @api.constrains("audience", "school_group_ids", "auteur_user_id")
    def _check_teacher_scope(self):
        """A teacher writes to the families of their own groups, and nobody else.

        🔴 The record rule only says "one of the groups is mine" (a domain cannot
        say "all of them"), and a rule is checked BEFORE a write, not after: a
        teacher could otherwise add a colleague's group, or turn their own
        announcement into an "all staff" one. This constraint runs after.
        """
        user = self.env.user
        if self.env.su or user.has_group("bf_babillard.group_babillard_redacteur"):
            return
        for post in self:
            if post.auteur_user_id != user or not post._school_is_teacher_post(user):
                raise AccessError(_(
                    "A teacher writes to the families of their own groups only. "
                    "Choose one or more of your groups, or ask the school office."))

    def action_voir_manquants(self):
        """A teacher sees who has not confirmed a family announcement to their groups."""
        self.ensure_one()
        user = self.env.user
        if (not user.has_group("bf_babillard.group_babillard_redacteur")
                and self._school_is_teacher_post(user)):
            missing = self.sudo()._destinataires() - self.sudo().lecture_ids.user_id
            return {
                "type": "ir.actions.act_window",
                "name": _("Waiting for confirmation"),
                "res_model": "res.partner",
                "view_mode": "list",
                "domain": [("id", "in", missing.partner_id.ids)],
                "target": "current",
            }
        return super().action_voir_manquants()

    @api.constrains("audience", "school_id", "school_group_ids")
    def _check_school_audience(self):
        for post in self.filtered(lambda p: p.audience == FAMILIES):
            if not post.school_id:
                raise UserError(_("Name the school whose families receive this announcement."))
            if post.school_group_ids.school_id - post.school_id:
                raise UserError(_("Every group must belong to the school of the announcement."))

    # --- Who is reached ----------------------------------------------------

    def _school_students(self):
        """The students enrolled today in the school, or in the chosen groups."""
        self.ensure_one()
        domain = [("state", "=", "active"), ("year_id.state", "=", "current"),
                  ("school_id", "=", self.school_id.id)]
        if self.school_group_ids:
            domain.append(("group_id", "in", self.school_group_ids.ids))
        return self.env["bf.school.enrollment"].sudo().search(domain).student_id

    def _school_notice_links(self):
        """The guardian links of the reached students whose adult receives notices."""
        self.ensure_one()
        return self._school_students().student_guardian_link_ids.filtered("receives_notices")

    def _school_recipient_map(self):
        """{user id: the students this user is reached for}.

        One entry per active user of an adult who receives notices. An adult
        with three children in the audience appears once, with three students:
        one email, not three.
        """
        self.ensure_one()
        result = {}
        links = self._school_notice_links()
        for link in links:
            for user in link.guardian_id.user_ids.filtered("active"):
                result.setdefault(user.id, self.env["res.partner"].sudo())
                result[user.id] |= link.student_id
        return result

    def _school_partner_map(self):
        """{partner id: the students this adult is told about}, portal account or not.

        🔴 The email goes to the ADULT, not to the portal account: a school that
        publishes before inviting every family would otherwise lose, silently, the
        families it has not invited yet. The portal (and the read receipt) stays
        reserved to accounts: see `_school_recipient_map`.
        """
        self.ensure_one()
        result = {}
        links = self._school_notice_links()
        for link in links:
            result.setdefault(link.guardian_id.id, self.env["res.partner"].sudo())
            result[link.guardian_id.id] |= link.student_id
        return result

    def _destinataires(self):
        self.ensure_one()
        if self.audience != FAMILIES:
            return super()._destinataires()
        return self.env["res.users"].sudo().browse(list(self._school_recipient_map()))

    # --- Telling the families ------------------------------------------------

    def _apres_passage_au_fil(self):
        super()._apres_passage_au_fil()
        # A family is told of EVERY announcement, not only those that ask for a
        # confirmation: families do not browse a feed, the email is the feed.
        self.filtered(lambda p: p.audience == FAMILIES and p.state == "publie"
                      and not p.avis_envoye_le)._school_notify_families()

    def _envoyer_avis_lecture(self):
        families = self.filtered(lambda p: p.audience == FAMILIES)
        super(BabillardPost, self - families)._envoyer_avis_lecture()
        families._school_notify_families()

    @staticmethod
    def _school_first_names(students):
        names = [(s.name or "").split(" ")[0] for s in students.sorted("name")]
        return ", ".join(n for n in names if n)

    def _school_notify_families(self):
        """One email per adult, in their language, naming their children."""
        if not self:
            return
        template = self.env.ref("bf_school_message.mail_template_school_news",
                                raise_if_not_found=False)
        for post in self:
            if post.avis_envoye_le:
                continue
            recipients = post._school_partner_map()
            house_lang = post.company_id.partner_id.lang or post._langue_de_la_maison()
            sent = unreachable = 0
            for partner in self.env["res.partner"].sudo().browse(list(recipients)):
                if not partner.email or not template:
                    unreachable += 1
                    continue
                lang = partner.lang or house_lang
                template.sudo().with_context(
                    lang=lang,
                    school_lang=lang,
                    school_children=self._school_first_names(recipients[partner.id]),
                    school_url="/my/school/news/%s" % post.id,
                ).send_mail(
                    post.id, force_send=False,
                    email_values={"recipient_ids": [(6, 0, partner.ids)], "email_to": False},
                    email_layout_xmlid=post._mise_en_page())
                sent += 1
            post_l = post.sudo().with_context(lang=house_lang)
            post_l.write({"avis_envoye_le": fields.Datetime.now()})
            body = post_l.env._("Notice emailed to %(n)s family member(s).", n=sent)
            if unreachable:
                body += " " + post_l.env._(
                    "%(n)s adult(s) who receive notices have no email address and were not told.",
                    n=unreachable)
            post_l.message_post(
                body=body,
                message_type="notification", subtype_xmlid="mail.mt_note")
        self.env.ref("mail.ir_cron_mail_scheduler_action").sudo()._trigger()

    # --- The portal ------------------------------------------------------------

    @api.model
    def _school_news_for(self, user):
        """The announcements this user may read on the portal, newest first."""
        links = user.partner_id._school_portal_links().filtered("receives_notices")
        groups = links.student_id.student_enrollment_ids.filtered(
            lambda e: e.state == "active" and e.year_id.state == "current").group_id
        if not groups:
            return self.browse()
        # ⚠️ This domain says, from the adult's side, what `_school_recipient_map`
        # says from the announcement's side. `test_feed_matches_recipients` holds
        # the two together: change one, the test tells you to change the other.
        return self.sudo().search([
            ("state", "=", "publie"), ("audience", "=", FAMILIES),
            ("school_id", "in", groups.school_id.ids),
            "|", ("school_group_ids", "=", False), ("school_group_ids", "in", groups.ids),
        ], limit=200)

    def _school_read_on(self, user):
        self.ensure_one()
        return self.sudo().lecture_ids.filtered(lambda l: l.user_id == user)[:1].date
