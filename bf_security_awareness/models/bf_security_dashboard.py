import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)

# How many trailing months of campaign history the trend line shows.
TREND_MONTHS = 12
# A person is a "repeat clicker" once they fail this many simulations.
REPEAT_FAIL_THRESHOLD = 2

_RISK_META = [
    ("critical", "Critique", "danger"),
    ("high", "Élevé", "warning"),
    ("moderate", "Modéré", "info"),
    ("low", "Faible", "success"),
]


class BfSecurityDashboard(models.AbstractModel):
    """Non-stored model backing the two OWL dashboards.

    ``get_dashboard_data`` is the manager/operator view (org-wide, HR-private —
    guarded by the secaware group). ``get_my_data`` is the self-service view any
    internal employee can open: it is sudo-scoped to the caller's own partner so
    no one ever sees another person's behavioural data.
    """

    _name = "bf.security.dashboard"
    _description = "Tableau de bord — Sensibilisation à la sécurité"
    # `AbstractModel` : ce modèle n'a ni champ ni table, il ne sert que de point
    # d'entrée RPC pour le composant OWL. Déclaré `models.Model` + `_auto = False`,
    # il entrait dans `Registry.check_tables_exist()`, qui ne dispense que
    # `_abstract` et les modèles à `_table_query` — d'où un `ERROR
    # odoo.modules.registry: Model <ce modèle> has no table.` journalisé à chaque
    # passe du chargeur sur une base neuve.

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _company_ids(self):
        return tuple(self.env.companies.ids) or (self.env.company.id,)

    def _fail_states(self):
        return ("clicked", "submitted")

    # ------------------------------------------------------------------ #
    # Manager / Operator view
    # ------------------------------------------------------------------ #
    @api.model
    def get_dashboard_data(self):
        if not self.env.user.has_group(
                "bf_security_awareness.group_bf_secaware_user"):
            raise AccessError(_(
                "Accès réservé à l'équipe de sensibilisation à la sécurité."))
        trend = self._get_trend()
        return {
            "kpis": self._get_kpis(trend),
            "risk_distribution": self._get_risk_distribution(),
            "trend": trend,
            "repeat_clickers": self._get_repeat_clickers(),
            "overdue_training": self._get_overdue_training(),
            "recent_campaigns": self._get_recent_campaigns(),
        }

    def _get_trend(self):
        """Phish-prone % and report % per campaign month, from existing results.

        No snapshot table needed — derived live from ``bf.phishing.result``."""
        self.env.cr.execute(
            """
            SELECT to_char(date_trunc('month', sent_datetime), 'YYYY-MM') AS period,
                   count(*) AS tested,
                   count(*) FILTER (WHERE state IN ('clicked', 'submitted')) AS failed,
                   count(*) FILTER (WHERE reported) AS reported
              FROM bf_phishing_result
             WHERE sent_datetime IS NOT NULL
               AND company_id IN %s
             GROUP BY 1
             ORDER BY 1 DESC
             LIMIT %s
            """,
            (self._company_ids(), TREND_MONTHS),
        )
        rows = list(reversed(self.env.cr.dictfetchall()))
        trend = []
        for r in rows:
            tested = r["tested"] or 0
            trend.append({
                "period": r["period"],
                "tested": tested,
                "phish_prone": round(100.0 * (r["failed"] or 0) / tested, 1)
                if tested else 0.0,
                "report_rate": round(100.0 * (r["reported"] or 0) / tested, 1)
                if tested else 0.0,
            })
        return trend

    def _get_kpis(self, trend):
        Result = self.env["bf.phishing.result"]
        cids = self.env.companies.ids
        base = [("company_id", "in", cids), ("state", "!=", "pending")]
        tested = Result.search_count(base)
        clicked = Result.search_count(
            base + [("state", "in", self._fail_states())])
        reported = Result.search_count(base + [("reported", "=", True)])

        Profile = self.env["bf.security.profile"]
        high = Profile.search_count([
            ("company_id", "in", cids),
            ("risk_level", "in", ("high", "critical"))])
        critical = Profile.search_count([
            ("company_id", "in", cids), ("risk_level", "=", "critical")])

        today = fields.Date.today()
        overdue = self.env["bf.training.assignment"].search_count([
            ("company_id", "in", cids),
            ("state", "not in", ("completed",)),
            ("due_date", "!=", False), ("due_date", "<", today)])

        # Period-over-period deltas read off the trend tail (last vs previous).
        pp_delta = rr_delta = 0.0
        if len(trend) >= 2:
            pp_delta = round(trend[-1]["phish_prone"] - trend[-2]["phish_prone"], 1)
            rr_delta = round(trend[-1]["report_rate"] - trend[-2]["report_rate"], 1)

        return {
            "phish_prone_pct": round(100.0 * clicked / tested, 1) if tested else 0.0,
            "phish_prone_delta": pp_delta,
            "report_rate": round(100.0 * reported / tested, 1) if tested else 0.0,
            "report_rate_delta": rr_delta,
            "tested_people": len(Result.read_group(
                base, ["partner_id"], ["partner_id"])),
            "high_risk": high,
            "critical": critical,
            "overdue_training": overdue,
        }

    def _get_risk_distribution(self):
        Profile = self.env["bf.security.profile"]
        cids = self.env.companies.ids
        grouped = {
            (g["risk_level"] or "low"): g["risk_level_count"]
            for g in Profile.read_group(
                [("company_id", "in", cids)], ["risk_level"], ["risk_level"])
        }
        total = sum(grouped.values()) or 0
        return {
            "total": total,
            "levels": [
                {"level": lvl, "label": label, "color": color,
                 "count": grouped.get(lvl, 0)}
                for lvl, label, color in _RISK_META
            ],
        }

    def _get_repeat_clickers(self):
        """People who fell for >=2 simulations — the priority coaching list."""
        self.env.cr.execute(
            """
            SELECT partner_id,
                   count(*) FILTER (WHERE state IN ('clicked', 'submitted')) AS fails,
                   count(DISTINCT campaign_id) AS campaigns
              FROM bf_phishing_result
             WHERE company_id IN %s
             GROUP BY partner_id
            HAVING count(*) FILTER (WHERE state IN ('clicked', 'submitted')) >= %s
             ORDER BY fails DESC, campaigns DESC
             LIMIT 10
            """,
            (self._company_ids(), REPEAT_FAIL_THRESHOLD),
        )
        rows = self.env.cr.dictfetchall()
        partners = self.env["res.partner"].browse([r["partner_id"] for r in rows])
        names = {p.id: p.display_name for p in partners}
        profiles = {
            p.partner_id.id: p for p in self.env["bf.security.profile"].search(
                [("partner_id", "in", list(names))])
        }
        out = []
        for r in rows:
            prof = profiles.get(r["partner_id"])
            out.append({
                "partner_id": r["partner_id"],
                "name": names.get(r["partner_id"], _("(inconnu)")),
                "fails": r["fails"],
                "campaigns": r["campaigns"],
                "risk_level": prof.risk_level if prof else "low",
            })
        return out

    def _get_overdue_training(self):
        today = fields.Date.today()
        assignments = self.env["bf.training.assignment"].search([
            ("company_id", "in", self.env.companies.ids),
            ("state", "not in", ("completed",)),
            ("due_date", "!=", False), ("due_date", "<", today),
        ], order="due_date asc", limit=10)
        out = []
        for a in assignments:
            out.append({
                "id": a.id,
                "partner": a.partner_id.display_name,
                "course": a.channel_id.name,
                "due_date": fields.Date.to_string(a.due_date),
                "days_over": (today - a.due_date).days,
                "completion": a.completion or 0,
            })
        return out

    def _get_recent_campaigns(self):
        campaigns = self.env["bf.phishing.campaign"].search([
            ("company_id", "in", self.env.companies.ids),
            ("state", "in", ("running", "done")),
        ], order="create_date desc", limit=6)
        out = []
        for c in campaigns:
            out.append({
                "id": c.id,
                "name": c.name,
                "state": c.state,
                "recipients": c.recipient_count,
                "clicked": c.clicked_count,
                "submitted": c.submitted_count,
                "reported": c.reported_count,
                "fail_rate": round(c.fail_rate, 1),
            })
        return out

    # ------------------------------------------------------------------ #
    # Employee self-service view (own data only)
    # ------------------------------------------------------------------ #
    @api.model
    def get_my_data(self):
        partner = self.env.user.partner_id
        if not partner:
            return {"has_data": False}
        # sudo: the behavioural models are HR-private, but everything below is
        # filtered strictly to the caller's own partner, so no cross-person leak.
        Profile = self.env["bf.security.profile"].sudo()
        profile = Profile.search([("partner_id", "=", partner.id)], limit=1)
        assignments = self.env["bf.training.assignment"].sudo().search(
            [("partner_id", "=", partner.id)], order="create_date desc")

        trainings = [{
            "id": a.id,
            "course": a.channel_id.name,
            "channel_id": a.channel_id.id,
            "completion": a.completion or 0,
            "completed": a.completed,
            "due_date": fields.Date.to_string(a.due_date) if a.due_date else False,
            "overdue": bool(a.due_date and not a.completed
                            and a.due_date < fields.Date.today()),
        } for a in assignments]
        to_do = [t for t in trainings if not t["completed"]]

        return {
            "has_data": True,
            "name": partner.display_name,
            "tested": profile.emails_received if profile else 0,
            "reported": profile.reported_count if profile else 0,
            "completed_courses": profile.completed_course_count if profile else 0,
            "resilience": self._resilience_badge(profile),
            "trainings": trainings,
            "todo_count": len(to_do),
        }

    def _resilience_badge(self, profile):
        """Employee-facing, encouraging reframe of the risk level — we surface
        resilience, never a punitive 'critical risk' label to the person."""
        if not profile or profile.emails_received == 0:
            return {"label": "À découvrir", "color": "secondary",
                    "hint": "Vous n'avez pas encore été testé."}
        # Reporting and completed training build resilience; recent failures lower it.
        if profile.risk_level in ("low",) and profile.reported_count:
            return {"label": "Excellent réflexe", "color": "success",
                    "hint": "Continuez de signaler les courriels suspects."}
        if profile.risk_level in ("low", "moderate"):
            return {"label": "Sur la bonne voie", "color": "info",
                    "hint": "Un dernier effort sur la formation assignée."}
        return {"label": "À renforcer", "color": "warning",
                "hint": "Complétez la formation pour aiguiser vos réflexes."}

    # ------------------------------------------------------------------ #
    # Drill-through actions (manager view)
    # ------------------------------------------------------------------ #
    @api.model
    def action_view_high_risk(self):
        return {
            "type": "ir.actions.act_window",
            "name": _("Personnes à risque élevé"),
            "res_model": "bf.security.profile",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("risk_level", "in", ("high", "critical"))],
        }

    @api.model
    def action_view_overdue_training(self):
        return {
            "type": "ir.actions.act_window",
            "name": _("Formations en retard"),
            "res_model": "bf.training.assignment",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("state", "not in", ("completed",)),
                       ("due_date", "!=", False),
                       ("due_date", "<", fields.Date.today())],
        }

    @api.model
    def action_open_profile(self, partner_id):
        profile = self.env["bf.security.profile"].search(
            [("partner_id", "=", partner_id)], limit=1)
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.security.profile",
            "res_id": profile.id,
            "views": [[False, "form"]],
        }

    @api.model
    def action_open_campaign(self, campaign_id):
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.phishing.campaign",
            "res_id": campaign_id,
            "views": [[False, "form"]],
        }

    @api.model
    def action_open_assignment(self, assignment_id):
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.training.assignment",
            "res_id": assignment_id,
            "views": [[False, "form"]],
        }
