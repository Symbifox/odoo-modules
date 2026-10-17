# -*- coding: utf-8 -*-
"""Mes critères : ce qu'une offre doit respecter, et le rythme des relances.

Une fiche par personne. Les critères signalent, ils ne jettent rien : la personne
décide (plancher salarial, heures, durée, secteurs et employeurs écartés).

Délais par défaut, tirés de sources publiques : Québec.ca dit deux
semaines sans date annoncée ; un sondage canadien (Accountemps 2011) place 71 %
des cadres sous deux semaines ; les conseillers de carrière disent au plus deux
relances espacées d'environ deux semaines.
"""
from odoo import api, fields, models
from odoo.exceptions import ValidationError


class JobCriteria(models.Model):
    _name = "bf.job.criteria"
    _inherit = ["bf.job.private.name"]
    _description = "My job search criteria"
    _rec_name = "user_id"

    user_id = fields.Many2one(
        "res.users", string="Person", required=True, readonly=True, index=True,
        default=lambda self: self.env.user, ondelete="cascade")
    currency_id = fields.Many2one(
        "res.currency", string="Currency", required=True,
        default=lambda self: self.env.company.currency_id)
    salary_floor = fields.Monetary(
        "Salary floor", currency_field="currency_id",
        help="Lowest pay you would accept. Compared with an application's top of the "
             "range, only when both use the same currency and period.")
    salary_period = fields.Selection(
        [("year", "Per year"), ("month", "Per month"), ("hour", "Per hour")],
        string="Floor period", default="year", required=True)
    min_hours_week = fields.Float("Minimum hours per week")
    min_duration_months = fields.Integer(
        "Minimum contract length (months)",
        help="Fixed-term roles shorter than this are flagged. Permanent roles always pass.")
    excluded_employers = fields.Text(
        "Employers to avoid", help="One per line. Matched anywhere in the employer's name, "
                                   "ignoring case.")
    excluded_words = fields.Text(
        "Words to avoid", help="One per line, for example a sector. Matched in the position "
                               "and the advert, ignoring case.")
    weekly_goal = fields.Integer(
        "Applications per week (goal)", default=5,
        help="Shown on the dashboard. Research links the intensity of a search to finding "
             "a job, and Employment Insurance expects a sustained search.")
    first_followup_days = fields.Integer(
        "First follow-up after (days)", default=10,
        help="Counted from the application date. When the employer promised a reply by a "
             "date, the follow-up waits until the day after it.")
    next_followup_days = fields.Integer("Next follow-up after (days)", default=14)
    max_followups = fields.Integer(
        "Follow-ups before closing", default=2,
        help="After this many follow-ups with no news, you are asked whether to close the "
             "application as 'no response'.")

    _sql_constraints = [
        ("user_unique", "unique(user_id)", "Each person has one set of criteria."),
    ]

    @api.constrains("first_followup_days", "next_followup_days", "max_followups", "weekly_goal")
    def _check_positive(self):
        for rec in self:
            if min(rec.first_followup_days, rec.next_followup_days) < 1 or rec.max_followups < 0 \
                    or rec.weekly_goal < 0:
                raise ValidationError(self.env._(
                    "Follow-up delays are at least one day; counts cannot be negative."))

    @api.model
    def _for_user(self):
        """Les critères de la personne qui lit, créés au besoin (dans son rôle :
        la règle n'autorise que les siens)."""
        crit = self.search([("user_id", "=", self.env.uid)], limit=1)
        return crit or self.create({"user_id": self.env.uid})

    @api.model
    def action_open_my_criteria(self):
        crit = self._for_user()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("My criteria"),
            "res_model": self._name,
            "res_id": crit.id,
            "view_mode": "form",
            "target": "current",
        }

    @staticmethod
    def _lines(text):
        return [w.strip().lower() for w in (text or "").splitlines() if w.strip()]
