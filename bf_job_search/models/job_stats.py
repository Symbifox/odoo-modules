# -*- coding: utf-8 -*-
"""Le tableau de bord : le rythme, et ce qui répond.

Calculé DANS LE RÔLE de la personne : la règle d'accès ne laisse lire que ses
candidatures, aucun sudo ici.

Mesures retenues, déduites de la littérature plutôt que validées
telles quelles : candidatures par semaine contre un objectif (l'intensité de la
recherche prédit l'emploi, Kanfer et al. 2001), taux de réponse et d'entrevue par
canal (une recommandation mène bien plus souvent à l'embauche qu'une candidature
spontanée), délai médian de la première réponse, et qui a fermé.
"""
import statistics
from datetime import timedelta

from markupsafe import Markup, escape

from odoo import api, fields, models

from .job_application import CHANNELS


class JobStats(models.TransientModel):
    _name = "bf.job.stats"
    _description = "Job search dashboard"

    weeks = fields.Integer("Weeks shown", default=8)
    this_week = fields.Integer("Applied this week", compute="_compute_stats")
    weekly_goal = fields.Integer("Weekly goal", compute="_compute_stats")
    open_count = fields.Integer("Open applications", compute="_compute_stats")
    applied_total = fields.Integer("Applications sent", compute="_compute_stats")
    response_rate = fields.Float("Response rate (%)", compute="_compute_stats", digits=(16, 0))
    interview_rate = fields.Float("Interview rate (%)", compute="_compute_stats", digits=(16, 0))
    median_response_days = fields.Float("Median days to a response", compute="_compute_stats",
                                        digits=(16, 1))
    weeks_html = fields.Html("Per week", compute="_compute_stats", sanitize=False)
    channels_html = fields.Html("By channel", compute="_compute_stats", sanitize=False)
    closed_html = fields.Html("Who closed", compute="_compute_stats", sanitize=False)

    @staticmethod
    def _responded(app):
        return bool(app.date_first_response) or app.closed_by in ("employer", "hired") \
            or app.stage in ("interview", "offer", "accepted")

    @staticmethod
    def _interviewed(app):
        return app.stage in ("interview", "offer", "accepted") or bool(app.interview_ids)

    @staticmethod
    def _html_table(head, rows):
        out = Markup('<table class="table table-sm o_bf_job_table"><thead><tr>')
        for h in head:
            out += Markup("<th>%s</th>") % h
        out += Markup("</tr></thead><tbody>")
        for row in rows:
            out += Markup("<tr>") + Markup("").join(Markup("<td>%s</td>") % escape(c) for c in row) \
                + Markup("</tr>")
        return out + Markup("</tbody></table>")

    @api.depends("weeks")
    def _compute_stats(self):
        _ = self.env._
        Apps = self.env["bf.job.application"].with_context(active_test=False)
        apps = Apps.search([("user_id", "=", self.env.uid)])
        sent = apps.filtered("date_applied")
        crit = self.env["bf.job.criteria"].search([("user_id", "=", self.env.uid)], limit=1)
        today = fields.Date.context_today(self)
        monday = today - timedelta(days=today.weekday())
        for rec in self:
            rec.weekly_goal = crit.weekly_goal if crit else 5
            rec.this_week = len(sent.filtered(lambda a: a.date_applied >= monday))
            rec.open_count = len(apps.filtered(lambda a: not a.is_closed and a.active))
            rec.applied_total = len(sent)
            responded = sent.filtered(self._responded)
            rec.response_rate = 100.0 * len(responded) / len(sent) if sent else 0.0
            rec.interview_rate = 100.0 * len(sent.filtered(self._interviewed)) / len(sent) if sent else 0.0
            days = [a.response_days for a in sent if a.date_first_response]
            rec.median_response_days = statistics.median(days) if days else 0.0

            rows = []
            for i in range(max(1, min(rec.weeks, 104))):
                start = monday - timedelta(weeks=i)
                end = start + timedelta(days=7)
                n = len(sent.filtered(lambda a: start <= a.date_applied < end))
                rows.append((fields.Date.to_string(start), n, rec.weekly_goal))
            rec.weeks_html = self._html_table((_("Week of"), _("Applied"), _("Goal")), rows)

            # Les libellés traduits (dict(CHANNELS) rendait l'anglais à tout le monde).
            labels = dict(apps._fields["channel"]._description_selection(self.env))
            rows = []
            for code, _label in CHANNELS:
                group = sent.filtered(lambda a: a.channel == code)
                if not group:
                    continue
                resp = len(group.filtered(self._responded))
                intv = len(group.filtered(self._interviewed))
                rows.append((labels[code], len(group), resp,
                             "%d %%" % round(100.0 * resp / len(group)), intv))
            rec.channels_html = self._html_table(
                (_("Channel"), _("Applied"), _("Answered"), _("Response rate"), _("Interviews")),
                rows) if rows else Markup("<p>%s</p>") % _("No application sent yet.")

            closed = apps.filtered("is_closed")
            by = dict(apps._fields["closed_by"]._description_selection(self.env))
            rows = [(by[k], len(closed.filtered(lambda a, k=k: a.closed_by == k)))
                    for k in ("employer", "me", "nobody", "hired")]
            rec.closed_html = self._html_table((_("Closed by"), _("Applications")), rows)

    @api.model
    def action_open_dashboard(self):
        rec = self.create({})
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Dashboard"),
            "res_model": self._name,
            "res_id": rec.id,
            "view_mode": "form",
            "target": "current",
        }
