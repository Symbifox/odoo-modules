# -*- coding: utf-8 -*-
"""Une entrevue : la préparer, la vivre, remercier le lendemain.

Sources : Tahatū (ex-Careers.govt.nz) dit de remercier par
courriel le lendemain, Robert Half NZ dans les 24 h, Québec.ca recommande un
courriel de remerciement ; chaque intervieweur, quelques phrases, personnalisées.
Aucune étude contrôlée n'en mesure l'effet : le module ne promet rien, il rappelle.
Préparation : recherche sur l'organisation, récits STAR, questions à poser
(Tahatū, Québec.ca) ; prévenir ses répondants (Tahatū).
"""
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.misc import clean_context

from .job_application import ACT_PREPARE, ACT_THANKS

KINDS = [
    ("screening", "Phone screening"),
    ("video", "Video call"),
    ("in_person", "In person"),
    ("panel", "Panel"),
    ("technical", "Technical or case"),
    ("assessment", "Assessment or test"),
    ("other", "Other"),
]


class JobInterview(models.Model):
    _name = "bf.job.interview"
    _inherit = ["bf.job.private.name"]
    _description = "Job interview"
    _order = "date_start desc, id desc"
    _bf_job_internal_fields = ("prep_activity_id", "thanks_activity_id", "calendar_event_id")

    application_id = fields.Many2one(
        "bf.job.application", string="Application", required=True, ondelete="cascade",
        index=True, domain="[('user_id', '=', uid)]")
    # 🔴 compute_sudo=False : un champ lié se calcule en superutilisateur par défaut,
    # et un onchange avec l'id d'une candidature d'autrui en rendait l'employeur et
    # le poste.
    user_id = fields.Many2one(related="application_id.user_id", store=True, index=True,
                              compute_sudo=False)
    employer = fields.Char(related="application_id.employer", compute_sudo=False)
    position = fields.Char(related="application_id.name", string="Position", compute_sudo=False)
    name = fields.Char(compute="_compute_name", store=True, compute_sudo=False)

    kind = fields.Selection(KINDS, string="Kind", default="video", required=True)
    date_start = fields.Datetime("When")
    duration = fields.Float("Duration (hours)", default=1.0)
    location = fields.Char("Place or link")
    interviewers = fields.Text(
        "Interviewers", help="One per line, with their email address when you have it: "
                             "Jane Doe <jane@example.com>")

    prep_research = fields.Boolean("Researched the organisation")
    prep_star = fields.Boolean(
        "STAR stories ready", help="Situation, task, action, result, for the advert's main "
                                   "requirements.")
    prep_questions = fields.Boolean("Questions to ask them ready")
    prep_referees = fields.Boolean("Referees warned")
    prep_logistics = fields.Boolean("Logistics checked", help="Place or link, who to ask for, "
                                                              "time zone.")
    prep_progress = fields.Integer("Preparation", compute="_compute_prep_progress")

    note = fields.Text("What was discussed",
                       help="Write one or two points right after: the thank-you note recalls one.")
    thanked_date = fields.Date("Thank-you sent on", readonly=True)
    thank_you_draft = fields.Text("Thank-you draft", compute="_compute_thank_you_draft")
    calendar_event_id = fields.Many2one("calendar.event", string="Calendar event", readonly=True,
                                        ondelete="set null", copy=False)
    # Les deux activités de cette entrevue, posées sur la candidature. Une activité
    # faite est supprimée par Odoo : le lien retombe à vide de lui-même.
    prep_activity_id = fields.Many2one("mail.activity", readonly=True, ondelete="set null", copy=False)
    thanks_activity_id = fields.Many2one("mail.activity", readonly=True, ondelete="set null", copy=False)

    # -------------------------------------------------------------- calculs
    @api.depends("kind", "application_id.employer", "date_start")
    def _compute_name(self):
        kinds = dict(KINDS)
        for rec in self:
            when = fields.Date.to_string(fields.Date.context_today(rec, rec.date_start)) \
                if rec.date_start else ""
            rec.name = " ".join(p for p in (kinds.get(rec.kind, ""), "-",
                                            rec.application_id.employer or "", when) if p)

    @api.depends("prep_research", "prep_star", "prep_questions", "prep_referees", "prep_logistics")
    def _compute_prep_progress(self):
        for rec in self:
            done = sum((rec.prep_research, rec.prep_star, rec.prep_questions,
                        rec.prep_referees, rec.prep_logistics))
            rec.prep_progress = done * 20

    def _interviewer_names(self):
        names = []
        for line in (self.interviewers or "").splitlines():
            line = line.split("<")[0].strip(" ,;")
            if line:
                names.append(line)
        return names

    @api.depends("interviewers", "application_id.name", "note")
    def _compute_thank_you_draft(self):
        _ = self.env._
        for rec in self:
            names = rec._interviewer_names() or [_("[name]")]
            drafts = []
            for name in names:
                drafts.append(_(
                    "Subject: Thank you - %(position)s\n\n"
                    "Hello %(name)s,\n\n"
                    "Thank you for your time today. I enjoyed our conversation, in particular "
                    "[one point from the interview].\n\n"
                    "It confirmed my interest in the %(position)s role, and I would be glad to "
                    "answer any further question.\n\n"
                    "Kind regards,",
                    position=rec.application_id.name or "", name=name.split()[0]))
            rec.thank_you_draft = "\n\n----------\n\n".join(drafts)

    @api.constrains("application_id")
    def _check_application_is_mine(self):
        """Une entrevue ne se rattache qu'à une candidature de la personne qui écrit
        (Odoo ne revérifie pas la règle après un ``write``)."""
        if self.env.su:
            return
        for rec in self:
            if rec.application_id.sudo().user_id != self.env.user:
                raise ValidationError(self.env._(
                    "An interview can only belong to one of your applications."))

    # ----------------------------------------------------------- écritures
    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        for rec in recs:
            app = rec.application_id
            if app.stage in ("spotted", "preparing", "applied") and not app.is_closed:
                app.stage = "interview"
            rec._schedule_activities()
        return recs

    def write(self, vals):
        res = super().write(vals)
        if "date_start" in vals:
            self._schedule_activities()
            for rec in self.filtered("calendar_event_id"):
                if rec.date_start:
                    rec.calendar_event_id.with_context(no_mail_to_attendees=True).write(
                        rec._calendar_values())
                else:
                    # Plus de date : plus d'événement (au lieu d'une erreur serveur).
                    event = rec.calendar_event_id
                    rec.sudo().calendar_event_id = False
                    event.unlink()
        return res

    def unlink(self):
        (self.prep_activity_id | self.thanks_activity_id).unlink()
        events = self.calendar_event_id
        res = super().unlink()
        events.unlink()
        return res

    def _schedule_activities(self):
        today = fields.Date.context_today(self)
        for rec in self:
            app = rec.application_id
            if app.is_closed:
                continue
            day = fields.Date.context_today(rec, rec.date_start) if rec.date_start else None
            prep_due = max(today, day - timedelta(days=1)) if day else today
            prep, thanks = rec.prep_activity_id.exists(), rec.thanks_activity_id.exists()
            note = self.env._(
                "<ul><li>Re-read the advert and what you sent.</li>"
                "<li>Research the organisation and, if you can, the interviewers.</li>"
                "<li>Prepare STAR stories for the main requirements.</li>"
                "<li>Prepare two or three questions to ask them.</li>"
                "<li>Warn your referees.</li><li>Check the place or link, and the time zone.</li></ul>")
            if prep:
                prep.write({"date_deadline": prep_due})
            elif rec.prep_progress < 100:
                rec.sudo().prep_activity_id = app.activity_schedule(
                    ACT_PREPARE, date_deadline=prep_due, note=note, user_id=app.user_id.id)
            if day and not rec.thanked_date:
                thanks_due = day + timedelta(days=1)
                if thanks:
                    thanks.write({"date_deadline": thanks_due})
                else:
                    rec.sudo().thanks_activity_id = app.activity_schedule(
                        ACT_THANKS, date_deadline=thanks_due, user_id=app.user_id.id,
                        note=self.env._("<p>Thank each interviewer by email: a few sentences, "
                                        "one point from the conversation, your interest. The "
                                        "interview holds a draft.</p>"))

    # ------------------------------------------------------------ boutons
    def action_thanked(self):
        self.check_access("write")
        today = fields.Date.context_today(self)
        for rec in self:
            rec.thanked_date = today
            rec.thanks_activity_id.exists().unlink()
            rec.application_id.message_post(body=self.env._("Thank-you note sent (%s).", rec.name))
        return True

    def action_prepared(self):
        self.check_access("write")
        for rec in self:
            rec.write({"prep_research": True, "prep_star": True, "prep_questions": True,
                       "prep_referees": True, "prep_logistics": True})
            rec.prep_activity_id.exists().unlink()
        return True

    def _calendar_values(self):
        self.ensure_one()
        start = self.date_start
        return {
            "name": self.env._("Interview: %s", self.application_id.employer),
            "start": start,
            "stop": start + timedelta(hours=self.duration or 1.0),
            "location": self.location or False,
            "privacy": "private",
            "show_as": "busy",
        }

    def action_add_to_calendar(self):
        """Un événement PRIVÉ, la personne seule y assiste, aucune invitation ne part :
        l'agenda du ménage est partagé, la candidature ne l'est pas."""
        self.check_access("write")
        for rec in self:
            if not rec.date_start:
                raise UserError(self.env._("Set the date and time of the interview first."))
            if rec.calendar_event_id:
                continue
            vals = rec._calendar_values()
            vals.update({
                "user_id": rec.user_id.id,
                "partner_ids": [(6, 0, [rec.user_id.partner_id.id])],
                "description": False,
            })
            # Contexte nettoyé : ouvert depuis la candidature, il porte active_model et
            # active_id, et le calendrier aurait lié l'événement à la candidature.
            event = self.env["calendar.event"].with_context(
                clean_context(self.env.context), no_mail_to_attendees=True,
                active_model=False, active_id=False, active_ids=False).create(vals)
            rec.sudo().calendar_event_id = event
        return True
