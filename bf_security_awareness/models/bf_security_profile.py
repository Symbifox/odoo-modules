from odoo import _, api, fields, models

# Defaults used when the matching ir.config_parameter is unset. The data file
# seeds these keys so an admin can tune scoring without touching code.
DEFAULTS = {
    "weight_submitted": 100.0,
    "weight_clicked": 60.0,
    "weight_opened": 20.0,
    "weight_reported": -40.0,
    "risk_window_days": 365.0,
    "training_mitigation": 15.0,
    "difficulty_weight_easy": 1.0,
    "difficulty_weight_medium": 0.8,
    "difficulty_weight_hard": 0.6,
}


class BfSecurityProfile(models.Model):
    """Per-person risk profile aggregated across every campaign — the
    "keeping a profile of each person tested" requirement."""

    _name = "bf.security.profile"
    _description = "Profil de risque de sécurité"
    _inherit = ["mail.thread"]
    _order = "risk_score desc"
    _rec_name = "partner_id"

    partner_id = fields.Many2one(
        "res.partner", string="Personne", required=True,
        ondelete="cascade", index=True,
    )
    company_id = fields.Many2one(
        "res.company", string="Société",
        default=lambda self: self.env.company,
    )

    result_ids = fields.One2many(
        "bf.phishing.result", "profile_id", string="Résultats")
    assignment_ids = fields.One2many(
        "bf.training.assignment", "profile_id", string="Formations")

    # --- Aggregates -----------------------------------------------------
    campaign_count = fields.Integer(
        compute="_compute_stats", string="Campagnes", store=True)
    emails_received = fields.Integer(
        compute="_compute_stats", string="Courriels reçus", store=True)
    opened_count = fields.Integer(
        compute="_compute_stats", string="Ouverts", store=True)
    clicked_count = fields.Integer(
        compute="_compute_stats", string="Clics", store=True)
    submitted_count = fields.Integer(
        compute="_compute_stats", string="Identifiants soumis", store=True)
    reported_count = fields.Integer(
        compute="_compute_stats", string="Signalements", store=True)
    last_tested_date = fields.Datetime(
        compute="_compute_stats", string="Dernier test", store=True)
    last_failed_date = fields.Datetime(
        compute="_compute_stats", string="Dernier échec", store=True)

    assigned_course_count = fields.Integer(
        compute="_compute_training", string="Formations assignées", store=True)
    completed_course_count = fields.Integer(
        compute="_compute_training", string="Formations complétées", store=True)
    training_completion = fields.Integer(
        compute="_compute_training", string="Avancement formation (%)",
        store=True)

    risk_score = fields.Float(
        compute="_compute_risk", string="Score de risque", store=True)
    risk_level = fields.Selection(
        [
            ("low", "Faible"),
            ("moderate", "Modéré"),
            ("high", "Élevé"),
            ("critical", "Critique"),
        ],
        compute="_compute_risk", string="Niveau de risque", store=True,
    )

    _sql_constraints = [
        ("partner_uniq", "unique(partner_id)",
         "Un seul profil de risque par personne."),
    ]

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @api.model
    def _get_or_create(self, partner_id):
        profile = self.search([("partner_id", "=", partner_id)], limit=1)
        if not profile:
            profile = self.create({"partner_id": partner_id})
        return profile

    def _param(self, key):
        raw = self.env["ir.config_parameter"].sudo().get_param(
            "bf_security_awareness.%s" % key)
        try:
            return float(raw) if raw is not None else DEFAULTS[key]
        except (TypeError, ValueError):
            return DEFAULTS[key]

    def _difficulty_weight(self, difficulty):
        return {
            "easy": self._param("difficulty_weight_easy"),
            "medium": self._param("difficulty_weight_medium"),
            "hard": self._param("difficulty_weight_hard"),
        }.get(difficulty, self._param("difficulty_weight_medium"))

    # ------------------------------------------------------------------ #
    # Computes
    # ------------------------------------------------------------------ #
    @api.depends("result_ids.state", "result_ids.reported",
                 "result_ids.sent_datetime")
    def _compute_stats(self):
        for prof in self:
            results = prof.result_ids
            prof.campaign_count = len(results.mapped("campaign_id"))
            prof.emails_received = len(results.filtered(
                lambda r: r.state != "pending"))
            prof.opened_count = len(results.filtered(
                lambda r: r.state in ("opened", "clicked", "submitted")))
            prof.clicked_count = len(results.filtered(
                lambda r: r.state in ("clicked", "submitted")))
            prof.submitted_count = len(results.filtered(
                lambda r: r.state == "submitted"))
            prof.reported_count = len(results.filtered("reported"))
            sent = results.filtered("sent_datetime")
            prof.last_tested_date = max(
                sent.mapped("sent_datetime"), default=False)
            failed = results.filtered(
                lambda r: r.state in ("clicked", "submitted"))
            # A submitted result may have no clicked_datetime (e.g. a direct
            # submit); fall back to submitted_datetime and drop falsy values so
            # max() never compares bool against datetime.
            fail_dates = [
                r.clicked_datetime or r.submitted_datetime for r in failed]
            prof.last_failed_date = max(
                [d for d in fail_dates if d], default=False)

    @api.depends("assignment_ids.state", "assignment_ids.completion")
    def _compute_training(self):
        for prof in self:
            assignments = prof.assignment_ids
            prof.assigned_course_count = len(assignments)
            completed = assignments.filtered("completed")
            prof.completed_course_count = len(completed)
            if assignments:
                prof.training_completion = int(
                    sum(assignments.mapped("completion")) / len(assignments))
            else:
                prof.training_completion = 0

    @api.depends("result_ids.state", "result_ids.reported",
                 "result_ids.clicked_datetime", "completed_course_count")
    def _compute_risk(self):
        now = fields.Datetime.now()
        for prof in self:
            window = max(prof._param("risk_window_days"), 1.0)
            w_sub = prof._param("weight_submitted")
            w_clk = prof._param("weight_clicked")
            w_open = prof._param("weight_opened")
            w_rep = prof._param("weight_reported")

            weighted_sum = 0.0
            weight_total = 0.0
            for res in prof.result_ids:
                if res.state == "pending":
                    continue
                # Recency: linear decay over the window.
                ref_dt = (res.clicked_datetime or res.opened_datetime
                          or res.sent_datetime or res.create_date)
                # Sub-day precision: integer .days made the decay jump at the
                # window edge; total_seconds keeps it smooth.
                age_days = (
                    (now - ref_dt).total_seconds() / 86400.0 if ref_dt else 0.0)
                recency = max(0.0, 1.0 - (age_days / window))
                if recency <= 0:
                    continue
                diff_w = prof._difficulty_weight(
                    res.campaign_id.template_id.difficulty)

                if res.state == "submitted":
                    points = w_sub
                elif res.state == "clicked":
                    points = w_clk
                elif res.state == "opened":
                    points = w_open
                else:  # sent, not opened
                    points = 0.0
                if res.reported:
                    points += w_rep

                weight = diff_w * recency
                weighted_sum += points * weight
                weight_total += weight

            raw = (weighted_sum / weight_total) if weight_total else 0.0
            mitigation = (
                prof._param("training_mitigation") * prof.completed_course_count)
            score = max(0.0, min(100.0, raw - mitigation))
            prof.risk_score = score
            if score >= 80:
                prof.risk_level = "critical"
            elif score >= 50:
                prof.risk_level = "high"
            elif score >= 20:
                prof.risk_level = "moderate"
            else:
                prof.risk_level = "low"

    # ------------------------------------------------------------------ #
    # Actions / cron
    # ------------------------------------------------------------------ #
    def action_recompute(self):
        self._compute_stats()
        self._compute_training()
        self._compute_risk()
        return True

    @api.model
    def _cron_recompute_profiles(self):
        # Stored computes refresh on dependency writes, but the recency decay is
        # time-based and must be re-evaluated on a schedule.
        self.search([]).invalidate_recordset(
            ["risk_score", "risk_level", "last_tested_date", "last_failed_date"])
        self.search([]).action_recompute()

    def action_view_results(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Résultats"),
            "res_model": "bf.phishing.result",
            "view_mode": "list,form",
            "domain": [("profile_id", "=", self.id)],
        }

    def action_view_training(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Formations"),
            "res_model": "bf.training.assignment",
            "view_mode": "list,form",
            "domain": [("profile_id", "=", self.id)],
        }
