# -*- coding: utf-8 -*-
"""Une candidature, dans le vocabulaire de la personne qui postule.

Un modèle à part plutôt qu'une
couche sur le CRM, qui parle de vente (revenu attendu, vendeur, gagné) et que
l'instance de foyer cache.

Deux choses qu'un pipeline de vente ne sait pas dire, et qui changent la lecture :

* **qui a fermé** une candidature (l'employeur, moi, personne) : sans cela le taux
  de réponse se perd dans des raisons de perte libres ;
* **l'après-candidature** : relances bornées, entrevue, remerciement, offre.
"""
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import html2plaintext

STAGES = [
    ("spotted", "Spotted"),
    ("preparing", "Preparing"),
    ("applied", "Applied"),
    ("interview", "Interview"),
    ("offer", "Offer"),
    ("accepted", "Accepted"),
]
STAGE_ORDER = [s for s, _label in STAGES]

OUTCOMES = [
    ("rejected", "Rejected by the employer"),
    ("cancelled", "Position cancelled"),
    ("not_pursued", "Not pursued"),
    ("withdrawn", "Withdrawn"),
    ("declined", "Offer declined"),
    ("no_response", "No response"),
]
CLOSED_BY = {
    "rejected": "employer",
    "cancelled": "employer",
    "not_pursued": "me",
    "withdrawn": "me",
    "declined": "me",
    "no_response": "nobody",
}

CHANNELS = [
    ("job_board", "Job board"),
    ("employer_site", "Employer's website"),
    ("recruiter", "Recruiter"),
    ("referral", "Referral"),
    ("network", "Network"),
    ("unsolicited", "Unsolicited application"),
    ("other", "Other"),
]

#: Activités que porte une candidature, par type (xmlid court).
ACT_FOLLOWUP = "bf_job_search.mail_act_job_followup"
ACT_NO_NEWS = "bf_job_search.mail_act_job_no_news"
ACT_PREPARE = "bf_job_search.mail_act_job_prepare"
ACT_THANKS = "bf_job_search.mail_act_job_thanks"
ACT_REFEREES = "bf_job_search.mail_act_job_referees"
ACT_OFFER = "bf_job_search.mail_act_job_offer"
ACT_FEEDBACK = "bf_job_search.mail_act_job_feedback"
#: Celles qui chassent une réponse : inutiles dès qu'une réponse arrive.
CHASING = (ACT_FOLLOWUP, ACT_NO_NEWS)


class JobApplication(models.Model):
    _name = "bf.job.application"
    # ⚠️ Le fil privé AVANT mail.thread (voir private.py).
    _inherit = ["bf.job.private.thread", "bf.job.private.name",
                "mail.thread", "mail.activity.mixin"]
    _description = "Job application"
    _order = "priority desc, date_applied desc, id desc"

    # Pas de suivi (tracking) sur le poste, l'employeur ni la date : un administrateur
    # lit ``mail.tracking.value`` sans règle.
    name = fields.Char("Position", required=True)
    employer = fields.Char("Employer", required=True)
    user_id = fields.Many2one(
        "res.users", string="Person", required=True, readonly=True, index=True,
        default=lambda self: self.env.user, ondelete="cascade")
    active = fields.Boolean(default=True)
    color = fields.Integer()

    stage = fields.Selection(
        STAGES, string="Stage", required=True, default="spotted", tracking=True,
        group_expand="_group_expand_stage", index=True)
    outcome = fields.Selection(OUTCOMES, string="Closed as", tracking=True, readonly=True)
    closed_by = fields.Selection(
        [("open", "Still open"), ("employer", "The employer"), ("me", "Me"),
         ("nobody", "Nobody (no response)"), ("hired", "Hired")],
        string="Closed by", compute="_compute_closed_by", store=True,
        help="Who ended the application. Reading the response rate depends on it: a role "
             "you dropped is not a refusal.")
    is_closed = fields.Boolean(compute="_compute_closed_by", store=True)
    close_note = fields.Text("Closing note")

    priority = fields.Selection(
        [("0", "Stretch"), ("1", "Moderate fit"), ("2", "Good fit"), ("3", "Strong fit")],
        string="Fit", default="1")
    fit_note = fields.Text("Fit note", help="Why it fits, and the gaps to address.")

    channel = fields.Selection(CHANNELS, string="Channel", default="job_board", required=True)
    source_url = fields.Char("Advert link")
    recruiter = fields.Char("Recruiter or agency")
    referred_by = fields.Char("Referred by")
    location = fields.Char("Location")
    posting = fields.Html(
        "Advert", sanitize=True,
        help="The full advert. Job boards remove it within weeks; keep it to prepare the "
             "interview.")

    date_spotted = fields.Date("Spotted on", default=fields.Date.context_today)
    closing_date = fields.Date("Advert closes")
    date_applied = fields.Date("Applied on")
    reply_promised_date = fields.Date(
        "Reply promised by", help="A date the employer gave. Follow-ups wait until the day "
                                  "after it.")
    date_first_response = fields.Date(
        "First response on", help="The first reply of any kind: refusal, interview, question.")
    response_days = fields.Integer(
        "Days to first response", compute="_compute_response_days", store=True,
        aggregator="avg")
    date_closed = fields.Date("Closed on", readonly=True)

    currency_id = fields.Many2one(
        "res.currency", string="Currency", default=lambda self: self.env.company.currency_id)
    salary_min = fields.Monetary("Salary from", currency_field="currency_id")
    salary_max = fields.Monetary("Salary up to", currency_field="currency_id")
    salary_period = fields.Selection(
        [("year", "Per year"), ("month", "Per month"), ("hour", "Per hour")],
        string="Pay period", default="year")
    hours_per_week = fields.Float("Hours per week")
    duration_months = fields.Integer(
        "Contract length (months)", help="Leave at 0 for a permanent role.")

    cv_attachment_id = fields.Many2one(
        "ir.attachment", string="CV sent", copy=False,
        domain="[('res_model', '=', 'bf.job.application'), ('res_id', '=', id)]",
        help="The exact version you sent. Attach it to this application first.")
    letter_attachment_id = fields.Many2one(
        "ir.attachment", string="Cover letter sent", copy=False,
        domain="[('res_model', '=', 'bf.job.application'), ('res_id', '=', id)]")

    followup_count = fields.Integer("Follow-ups sent", readonly=True)
    last_followup_date = fields.Date("Last follow-up on", readonly=True)

    interview_ids = fields.One2many("bf.job.interview", "application_id", string="Interviews")
    interview_count = fields.Integer(compute="_compute_interview_count")
    reference_ids = fields.One2many("bf.job.reference", "application_id", string="Referees")

    offer_date = fields.Date("Offer received on")
    offer_deadline = fields.Date("Answer the offer by")
    offer_salary = fields.Monetary("Salary offered", currency_field="currency_id")
    offer_written = fields.Boolean(
        "Written offer received", help="Ask for the offer in writing before accepting it.")
    offer_note = fields.Text("Offer conditions",
                             help="Trial period, start date, checks, benefits.")

    criteria_warning = fields.Char(
        "Does not meet my criteria", compute="_compute_criteria_warning")
    details_purged = fields.Boolean(
        "Details purged", readonly=True,
        help="The advert, salary, notes and files were removed; the log entry remains.")

    # -------------------------------------------------------------- calculs
    @api.model
    def _group_expand_stage(self, values, domain):
        return STAGE_ORDER

    @api.depends("outcome", "stage")
    def _compute_closed_by(self):
        for rec in self:
            if rec.outcome:
                rec.closed_by = CLOSED_BY[rec.outcome]
            elif rec.stage == "accepted":
                rec.closed_by = "hired"
            else:
                rec.closed_by = "open"
            rec.is_closed = rec.closed_by != "open"

    @api.depends("date_applied", "date_first_response")
    def _compute_response_days(self):
        for rec in self:
            if rec.date_applied and rec.date_first_response:
                rec.response_days = max(0, (rec.date_first_response - rec.date_applied).days)
            else:
                rec.response_days = 0

    @api.depends("interview_ids")
    def _compute_interview_count(self):
        for rec in self:
            rec.interview_count = len(rec.interview_ids)

    @api.depends_context("uid")
    @api.depends("salary_max", "salary_min", "salary_period", "currency_id", "hours_per_week",
                 "duration_months", "employer", "name", "posting")
    def _compute_criteria_warning(self):
        crit = self.env["bf.job.criteria"].search([("user_id", "=", self.env.uid)], limit=1)
        for rec in self:
            rec.criteria_warning = rec._criteria_problems(crit) if crit else False

    def _criteria_problems(self, crit):
        self.ensure_one()
        _ = self.env._
        problems = []
        top = self.salary_max or self.salary_min
        if crit.salary_floor and top:
            if self.currency_id == crit.currency_id and self.salary_period == crit.salary_period:
                if top < crit.salary_floor:
                    problems.append(_("pay below my floor"))
            else:
                problems.append(_("pay not compared (other currency or period)"))
        if crit.min_hours_week and self.hours_per_week and self.hours_per_week < crit.min_hours_week:
            problems.append(_("fewer hours than my minimum"))
        if crit.min_duration_months and self.duration_months \
                and self.duration_months < crit.min_duration_months:
            problems.append(_("contract shorter than my minimum"))
        employer = (self.employer or "").lower()
        if any(w in employer for w in crit._lines(crit.excluded_employers)):
            problems.append(_("employer on my list to avoid"))
        text = " ".join([(self.name or "").lower(),
                         html2plaintext(self.posting or "").lower()])
        words = [w for w in crit._lines(crit.excluded_words) if w in text]
        if words:
            problems.append(_("mentions: %s", ", ".join(words)))
        return "; ".join(problems) or False

    @api.constrains("cv_attachment_id", "letter_attachment_id")
    def _check_attachments_are_this_records(self):
        """🔴 Écrire un id de pièce jointe quelconque puis relire
        la fiche donnait son nom (lu en superutilisateur). Seules les pièces de CETTE
        candidature."""
        for rec in self:
            for att in (rec.cv_attachment_id | rec.letter_attachment_id).sudo():
                if att.res_model != self._name or att.res_id != rec.id:
                    raise ValidationError(self.env._(
                        "Choose a file attached to this application."))

    # ----------------------------------------------------------- écritures
    def write(self, vals):
        reopening = "outcome" in vals and not vals["outcome"]
        if not self.env.su and "stage" in vals and not reopening and self.filtered("outcome"):
            # Le glisser-déposer du kanban ne rouvre pas une candidature fermée.
            raise UserError(self.env._("This application is closed. Reopen it first."))
        stage = vals.get("stage")
        before = {rec.id: rec.stage for rec in self} if stage else {}
        if stage == "applied":
            for rec in self:
                if not rec.date_applied and "date_applied" not in vals:
                    super(JobApplication, rec).write(
                        {"date_applied": fields.Date.context_today(rec)})
        res = super().write(vals)
        if stage:
            for rec in self:
                if before.get(rec.id) != stage:
                    rec._on_stage_reached(stage)
        if "reply_promised_date" in vals or "date_applied" in vals:
            self.filtered(lambda r: r.stage == "applied" and not r.is_closed)._reschedule_first_followup()
        return res

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        for rec in recs:
            if rec.stage == "applied" and not rec.date_applied:
                rec.date_applied = fields.Date.context_today(rec)
            if rec.stage != "spotted":
                rec._on_stage_reached(rec.stage)
        return recs

    def _on_stage_reached(self, stage):
        """Les rappels qui portent les bonnes pratiques, à l'arrivée dans une étape."""
        self.ensure_one()
        if self.is_closed and stage != "accepted":
            return
        if stage == "applied":
            if not self.followup_count:
                self._schedule_followup(first=True)
        elif stage == "interview":
            self._done_or_cancel(CHASING)
            self._mark_first_response()
            self._ask_to_warn_referees()
        elif stage == "offer":
            self._done_or_cancel(CHASING)
            self._mark_first_response()
            if not self.offer_date:
                self.offer_date = fields.Date.context_today(self)
            self._schedule_offer_answer()
        elif stage == "accepted":
            self._done_or_cancel(CHASING + (ACT_OFFER,))
            self.date_closed = fields.Date.context_today(self)

    # ---------------------------------------------------------- activités
    def _act(self, xmlid, deadline, note="", summary=False):
        """Pose (ou déplace) l'activité de ce type, à la personne. Une seule par type."""
        self.ensure_one()
        act_type = self.env.ref(xmlid)
        existing = self.activity_ids.filtered(lambda a: a.activity_type_id == act_type)
        vals = {"date_deadline": deadline}
        if note:
            vals["note"] = note
        if summary:
            vals["summary"] = summary
        if existing:
            existing[:1].write(vals)
            (existing - existing[:1]).unlink()
            return existing[:1]
        return self.activity_schedule(
            xmlid, date_deadline=deadline, note=note, summary=summary or act_type.summary or "",
            user_id=self.user_id.id)

    def _done_or_cancel(self, xmlids):
        types = [self.env.ref(x) for x in xmlids]
        self.activity_ids.filtered(lambda a: a.activity_type_id in types).unlink()

    def _owner_criteria(self):
        """Les critères du propriétaire (créés au besoin, avec leurs valeurs par
        défaut). Dans le rôle de la personne, c'est elle ; en superutilisateur
        (import, essais), on va les chercher pour elle."""
        self.ensure_one()
        Crit = self.env["bf.job.criteria"]
        if not self.env.su and self.user_id == self.env.user:
            return Crit._for_user()
        Crit = Crit.sudo()
        return Crit.search([("user_id", "=", self.user_id.id)], limit=1) \
            or Crit.create({"user_id": self.user_id.id})

    def _first_followup_date(self, crit):
        self.ensure_one()
        applied = self.date_applied or fields.Date.context_today(self)
        due = applied + timedelta(days=crit.first_followup_days)
        if self.reply_promised_date:
            due = self.reply_promised_date + timedelta(days=1)
        elif self.closing_date and self.closing_date >= due:
            # On ne relance pas tant que l'annonce court : l'employeur trie souvent
            # après la clôture.
            due = self.closing_date + timedelta(days=1)
        return due

    def _schedule_followup(self, first=False):
        self.ensure_one()
        crit = self._owner_criteria()
        today = fields.Date.context_today(self)
        if first:
            due = self._first_followup_date(crit)
            self._act(ACT_FOLLOWUP, due, note=self.env._(
                "<p>No news yet: send a short follow-up to ask where the process stands "
                "and confirm your interest. Then click <b>Follow-up sent</b>.</p>"))
        elif self.followup_count < crit.max_followups:
            self._act(ACT_FOLLOWUP, today + timedelta(days=crit.next_followup_days),
                      note=self.env._("<p>Still no news after your follow-up: one more short "
                                      "message, then click <b>Follow-up sent</b>.</p>"))
        else:
            self._done_or_cancel((ACT_FOLLOWUP,))
            self._act(ACT_NO_NEWS, today + timedelta(days=crit.next_followup_days),
                      note=self.env._("<p>No reply after your follow-ups. Silence is common: "
                                      "close this application as <b>No response</b> and put "
                                      "your energy elsewhere.</p>"))

    def _reschedule_first_followup(self):
        follow = self.env.ref(ACT_FOLLOWUP)
        for rec in self:
            if rec.followup_count == 0 and rec.activity_ids.filtered(
                    lambda a: a.activity_type_id == follow):
                rec._schedule_followup(first=True)

    def _mark_first_response(self):
        if not self.date_first_response and self.date_applied:
            self.date_first_response = fields.Date.context_today(self)

    def _ask_to_warn_referees(self):
        refs = self.reference_ids.filtered(lambda r: r.state == "named")
        if refs:
            names = ", ".join(refs.mapped("referee_id.name"))
            self._act(ACT_REFEREES, fields.Date.context_today(self), note=self.env._(
                "<p>Warn your referees that they may be called soon: %s.</p>", names))

    def _schedule_offer_answer(self):
        deadline = self.offer_deadline or fields.Date.context_today(self) + timedelta(days=3)
        self._act(ACT_OFFER, deadline, note=self.env._(
            "<ul><li>Ask for the offer in writing, and the date they need an answer by.</li>"
            "<li>Negotiate before accepting: many employers would pay more if asked.</li>"
            "<li>Read the conditions: trial period, start date, checks.</li>"
            "<li>Do not resign from a current job before the agreement is signed.</li></ul>"))

    # ------------------------------------------------------------ boutons
    def _check_mine(self):
        self.check_access("write")
        if self.filtered(lambda r: r.is_closed):
            raise UserError(self.env._("This application is closed. Reopen it first."))

    def action_followup_sent(self):
        """Une relance vient de partir : on compte, et on pose la suivante (ou la
        question de fermer)."""
        self._check_mine()
        today = fields.Date.context_today(self)
        for rec in self:
            if rec.stage not in ("applied",):
                raise UserError(self.env._("Follow-ups are for applications waiting for a reply."))
            rec.write({"followup_count": rec.followup_count + 1, "last_followup_date": today})
            rec._done_or_cancel((ACT_FOLLOWUP,))
            rec._schedule_followup()
            rec.message_post(body=self.env._("Follow-up %s sent.", rec.followup_count))
        return True

    def action_response_received(self):
        """Une réponse quelconque (question, demande de disponibilités) : on cesse
        de relancer."""
        self._check_mine()
        for rec in self:
            rec._mark_first_response()
            rec._done_or_cancel(CHASING)
            rec.message_post(body=self.env._("Response received."))
        return True

    def action_warn_referees(self):
        """Les répondants nommés pour ces candidatures sont prévenus."""
        self.check_access("write")
        self.reference_ids.filtered(lambda r: r.state == "named").action_warned()
        return True

    def action_open_close_wizard(self):
        self.check_access("write")
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Close the application"),
            "res_model": "bf.job.close.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_application_ids": self.ids},
        }

    def _close(self, outcome, note=False, ask_feedback=False):
        """Fermer : appelé par l'assistant, dans le rôle de la personne."""
        today = fields.Date.context_today(self)
        for rec in self:
            vals = {"outcome": outcome, "date_closed": today}
            if note:
                vals["close_note"] = note
            if CLOSED_BY[outcome] == "employer":
                rec._mark_first_response()
            rec.write(vals)
            rec.activity_ids.unlink()
            if ask_feedback and outcome == "rejected":
                rec._act(ACT_FEEDBACK, today + timedelta(days=1), note=self.env._(
                    "<p>Ask, briefly and politely, what would have made your application "
                    "stronger. Not every employer answers; the ones who do are worth "
                    "keeping in touch with.</p>"))
            rec.message_post(body=self.env._(
                "Closed: %s.", dict(self._fields["outcome"]._description_selection(self.env))[outcome]))

    def action_reopen(self):
        self.check_access("write")
        for rec in self:
            rec.write({"outcome": False, "date_closed": False})
            rec.message_post(body=self.env._("Reopened."))
            if rec.stage == "applied":
                # Les relances reprennent là où elles en étaient.
                rec._schedule_followup(first=not rec.followup_count)
        return True

    def action_open_interviews(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Interviews"),
            "res_model": "bf.job.interview",
            "view_mode": "list,form",
            "domain": [("application_id", "=", self.id)],
            "context": {"default_application_id": self.id},
        }

    # ----------------------------------------------------------- journal
    def _log_result(self):
        """Le résultat, en clair, pour le journal des démarches."""
        self.ensure_one()
        _ = self.env._
        if self.outcome:
            return dict(self._fields["outcome"]._description_selection(self.env))[self.outcome]
        if self.stage == "accepted":
            return _("Hired")
        return dict(self._fields["stage"]._description_selection(self.env))[self.stage]
