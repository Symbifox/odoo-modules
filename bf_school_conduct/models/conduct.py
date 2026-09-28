from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

FOLLOWUP_CASES = [
    ("failure_risk", "Risk of not reaching the pass mark"),
    ("conduct", "Conduct not in line with the rules"),
    ("intervention_plan", "Provided in the intervention plan"),
]


class ConductRule(models.Model):
    """A rule of conduct (Regulation I-13.3, r. 10.01, and the school's own)."""

    _name = "bf.school.conduct.rule"
    _description = "Rule of conduct"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    is_violence = fields.Boolean(
        "Bullying or violence",
        help="Counted in the annual report on bullying and violence, and the parents of the "
             "students involved are informed promptly (Education Act s. 96.12, Private "
             "Education Act s. 63.5).")
    is_sexual = fields.Boolean(
        "Sexual violence",
        help="From 14, the parents are informed only with the student's consent.")
    active = fields.Boolean(default=True)


class Sanction(models.Model):
    """A graduated sanction, from the list of r. 10.01 s. 5, or a restorative measure."""

    _name = "bf.school.sanction"
    _description = "Sanction"
    _order = "level, sequence, id"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    level = fields.Integer(default=1, help="The rank in the graduation: 1 is the lightest.")
    is_suspension = fields.Boolean(
        help="A suspension requires the reasons and the support measures in the parents' notice.")
    is_restorative = fields.Boolean("Restorative measure")
    active = fields.Boolean(default=True)


class Incident(models.Model):
    """A breach of a rule of conduct, and what the school does about it."""

    _name = "bf.school.incident"
    _description = "Breach of the rules of conduct"
    _inherit = ["mail.thread"]
    _order = "date desc, id desc"

    student_id = fields.Many2one("res.partner", "Student", required=True, ondelete="restrict", index=True,
                                 domain=[("is_student", "=", True)])
    company_id = fields.Many2one("res.company", related="student_id.company_id", store=True)
    date = fields.Datetime(required=True, default=fields.Datetime.now)
    rule_id = fields.Many2one("bf.school.conduct.rule", "Rule", required=True, ondelete="restrict")
    is_violence = fields.Boolean(related="rule_id.is_violence", store=True)
    is_sexual = fields.Boolean(related="rule_id.is_sexual")
    description = fields.Text(required=True, help="What happened. Shared with the family once they are informed.")
    reported_by_id = fields.Many2one("res.users", "Noted by", default=lambda s: s.env.user, readonly=True)
    sanction_id = fields.Many2one("bf.school.sanction", "Sanction", ondelete="restrict", tracking=True)
    restorative_measure = fields.Text("Restorative measure")
    suspension_reasons = fields.Text("Reasons for the suspension")
    support_measures = fields.Text("Support and reintegration measures")
    prior_count = fields.Integer("Previous breaches of this rule", compute="_compute_prior")
    suggested_sanction_id = fields.Many2one("bf.school.sanction", "Suggested sanction",
                                            compute="_compute_prior",
                                            help="The next level after the previous breaches of the same "
                                                 "rule this school year. An aid: a person decides.")
    student_consent = fields.Boolean(
        "Student agrees that the parents be informed",
        help="Required for sexual violence when the student is 14 or older.", tracking=True)
    parents_informed_on = fields.Datetime(readonly=True, tracking=True)
    state = fields.Selection([("open", "Open"), ("closed", "Closed")], default="open", required=True, tracking=True)

    def _school_year_start(self):
        self.ensure_one()
        enrollment = self.student_id.student_enrollment_ids.filtered(
            lambda e: e.state == "active" and e.year_id.state == "current")[:1]
        return enrollment.year_id.date_start

    @api.depends("student_id", "rule_id", "date")
    def _compute_prior(self):
        Sanction = self.env["bf.school.sanction"]
        for incident in self:
            incident.prior_count = 0
            incident.suggested_sanction_id = False
            if not (incident.student_id and incident.rule_id):
                continue
            start = incident._school_year_start()
            domain = [("student_id", "=", incident.student_id.id), ("rule_id", "=", incident.rule_id.id)]
            if incident.id:
                domain.append(("id", "!=", incident.id))
            if start:
                domain.append(("date", ">=", fields.Datetime.to_datetime(start)))
            prior = self.sudo().search(domain)
            incident.prior_count = len(prior)
            # One step per level: "device withheld" shares level 2 with the written
            # reflection and only fits the phone rule.
            ladder = Sanction.browse()
            for sanction in Sanction.search([("is_restorative", "=", False)], order="level, sequence, id"):
                if sanction.level not in ladder.mapped("level"):
                    ladder |= sanction
            if ladder:
                incident.suggested_sanction_id = ladder[min(len(prior), len(ladder) - 1)]

    def _age(self):
        self.ensure_one()
        born = self.student_id.student_birthdate
        return relativedelta(fields.Date.context_today(self), born).years if born else None

    def action_inform_parents(self):
        """Tell the adults who receive notices. Checked here, not on the screen."""
        template = self.env.ref("bf_school_conduct.mail_template_incident", raise_if_not_found=False)
        for incident in self:
            if incident.sanction_id.is_suspension and not (
                    incident.suspension_reasons and incident.support_measures):
                raise UserError(_("A suspension notice gives its reasons and the support measures "
                                  "(Education Act s. 96.27, Private Education Act s. 63.6)."))
            age = incident._age()
            if incident.is_sexual and (age is None or age >= 14) and not incident.student_consent:
                raise UserError(_("Sexual violence: from 14, the parents are informed only with the "
                                  "student's consent. Record it first, or contact the student protector."))
            incident.check_access("write")
            incident.sudo().parents_informed_on = fields.Datetime.now()
            adults = incident.student_id.student_guardian_link_ids.filtered(
                lambda l: l.receives_notices and l.has_parental_authority).guardian_id
            for adult in adults.filtered("email"):
                if not template:
                    break
                lang = adult.lang or incident.company_id.partner_id.lang or "fr_CA"
                template.sudo().with_context(lang=lang, school_lang=lang).send_mail(
                    incident.id, force_send=False,
                    email_layout_xmlid=self.env["bf.school"]._school_mail_layout(),
                    email_values={"recipient_ids": [(6, 0, adult.ids)], "email_to": False})
        self.env.ref("mail.ir_cron_mail_scheduler_action").sudo()._trigger()
        return True

    def write(self, vals):
        if not self.env.su:
            # 🔴 `readonly` guards the screen only. By RPC, a teacher set the date the parents
            # were informed and a breach of sexual violence reached the portal without the
            # student's consent; or moved a breach to a student outside their groups.
            if "parents_informed_on" in vals:
                raise UserError(_("The parents are informed with the button, which checks the conditions."))
            if "student_id" in vals and not self.env.user.has_group("bf_school_core.group_school_manager"):
                raise UserError(_("A breach is not moved to another student: the office does it."))
        return super().write(vals)

    def action_close(self):
        self.write({"state": "closed"})
        return True

    @api.model
    def _annual_report(self, date_from, date_to):
        """Bullying and violence counted for the ministry's annual report (LEP s. 63.8)."""
        incidents = self.sudo().search([("is_violence", "=", True),
                                        ("date", ">=", fields.Datetime.to_datetime(date_from)),
                                        ("date", "<", fields.Datetime.to_datetime(date_to) + relativedelta(days=1))])
        report = {}
        for incident in incidents:
            report[incident.rule_id.name] = report.get(incident.rule_id.name, 0) + 1
        return report


class Followup(models.Model):
    """A student for whom the parents get news at least once a month (Régime s. 29.2)."""

    _name = "bf.school.followup"
    _description = "Monthly follow-up"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "student_id"

    student_id = fields.Many2one("res.partner", "Student", required=True, ondelete="cascade", index=True,
                                 domain=[("is_student", "=", True)])
    company_id = fields.Many2one("res.company", related="student_id.company_id", store=True)
    case = fields.Selection(FOLLOWUP_CASES, required=True)
    responsible_id = fields.Many2one("res.users", "Responsible teacher", required=True,
                                     default=lambda s: s.env.user)
    active = fields.Boolean(default=True)
    communication_ids = fields.One2many("bf.school.followup.communication", "followup_id", "Communications")
    last_communication = fields.Date(compute="_compute_last", store=True)

    @api.depends("student_id.name", "case")
    def _compute_display_name(self):
        # Without it, the to-do and its notice read « bf.school.followup,2 » (QA, 2026-09-27).
        cases = dict(self._fields["case"]._description_selection(self.env))
        for followup in self:
            followup.display_name = "%s · %s" % (followup.student_id.name or "", cases.get(followup.case, ""))

    @api.depends("communication_ids.date")
    def _compute_last(self):
        for followup in self:
            followup.last_communication = max(followup.communication_ids.mapped("date"), default=False)

    @api.model
    def _cron_monthly_reminder(self):
        """At the start of a month: a to-do for every follow-up without news last month."""
        today = fields.Date.context_today(self)
        first_of_month = today.replace(day=1)
        last_month = first_of_month - relativedelta(months=1)
        activity_type = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
        for followup in self.search([]):
            if followup.last_communication and followup.last_communication >= last_month:
                continue
            # The cron has no language: the to-do speaks the teacher's (QA, 2026-09-27).
            env = followup.with_context(lang=followup.responsible_id.lang or self.env.lang).env
            # Odoo's own « assigned to you » notice is half English in fr_CA, signed OdooBot and
            # outside the tenant's layout: the to-do stays in the teacher's activities, no email.
            followup.with_context(mail_activity_quick_update=True).activity_schedule(
                act_type_xmlid="mail.mail_activity_data_todo" if activity_type else False,
                summary=env._("Monthly news to the parents of %s", followup.student_id.name),
                note=env._("Régime pédagogique s. 29.2: at least once a month."),
                user_id=followup.responsible_id.id, date_deadline=today + relativedelta(days=7))


class FollowupCommunication(models.Model):
    _name = "bf.school.followup.communication"
    _description = "Monthly news to the parents"
    _order = "date desc, id desc"

    followup_id = fields.Many2one("bf.school.followup", required=True, ondelete="cascade", index=True)
    student_id = fields.Many2one(related="followup_id.student_id", store=True)
    company_id = fields.Many2one(related="followup_id.company_id", store=True)
    date = fields.Date(required=True, default=fields.Date.context_today)
    summary = fields.Text(required=True)
    channel = fields.Selection([("email", "Email from Symbifox"), ("phone", "Phone call"),
                                ("meeting", "Meeting"), ("other", "Other")], default="email", required=True)
    author_id = fields.Many2one("res.users", default=lambda s: s.env.user, readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        communications = super().create(vals_list)
        communications.filtered(lambda c: c.channel == "email")._send()
        return communications

    def _send(self):
        template = self.env.ref("bf_school_conduct.mail_template_followup", raise_if_not_found=False)
        if not template:
            return
        for communication in self.sudo():
            adults = communication.student_id.student_guardian_link_ids.filtered(
                lambda l: l.receives_notices and l.has_parental_authority).guardian_id
            for adult in adults.filtered("email"):
                lang = adult.lang or communication.company_id.partner_id.lang or "fr_CA"
                template.with_context(lang=lang, school_lang=lang).send_mail(
                    communication.id, force_send=False,
                    email_layout_xmlid=self.env["bf.school"]._school_mail_layout(),
                    email_values={"recipient_ids": [(6, 0, adult.ids)], "email_to": False})
        self.env.ref("mail.ir_cron_mail_scheduler_action").sudo()._trigger()
