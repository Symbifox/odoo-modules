"""Unified feedback registry.

Every measured signal lands here regardless of channel: NPS survey answers
(via survey.user_input._mark_done), email ratings (via rating.rating), manual
entries, internal 360 answers. The closed loop lives here too: a detractor or
a dissatisfied rating schedules a follow-up activity for the account owner.
"""
import logging
from datetime import timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

# Standard NPS buckets on a 0-10 scale (not configurable on purpose: a custom
# bucket boundary would make the score incomparable to any published NPS).
NPS_PROMOTER_MIN = 9
NPS_PASSIVE_MIN = 7

# Core rating module scale (0-5); below RATING_LIMIT_OK is "dissatisfied".
CSAT_KO_BELOW = 3.0

# Below this many answers, a per-person 360 average says more about who
# happened to reply than about the person reviewed. Same honesty rule as the
# n=10 threshold on the NPS window, tightened for the much smaller population
# a 360 draws from.
MIN_360_RESPONSES = 3


def param_is_true(env, key, default=False):
    """Tolerant reader for Boolean ir.config_parameter values.

    Settings checkboxes store the *string* 'True'/'False' (never '1'/'0'),
    so a naive ``== '1'`` reader fails silently. An ABSENT parameter must
    fall back to ``default``: get_param's own sentinel is False (not None),
    so it is read with an explicit None default here.
    """
    raw = env["ir.config_parameter"].sudo().get_param(key, None)
    if raw is None or raw == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on", "t")


class BfCxFeedback(models.Model):
    _name = "bf.cx.feedback"
    _description = "Feedback d'expérience"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date desc, id desc"

    partner_id = fields.Many2one(
        "res.partner", string="Contact", index=True, tracking=True
    )
    date = fields.Date(
        string="Date",
        required=True,
        default=fields.Date.context_today,
        index=True,
    )
    kind = fields.Selection(
        [
            ("nps", "NPS"),
            ("csat", "Satisfaction (CSAT)"),
            ("verbatim", "Commentaire"),
            ("internal", "Interne (360)"),
        ],
        string="Type",
        required=True,
        default="verbatim",
        index=True,
        tracking=True,
    )
    source = fields.Selection(
        [
            ("survey", "Sondage"),
            ("rating", "Évaluation par courriel"),
            ("manual", "Saisie manuelle"),
            ("meeting", "Rencontre"),
            ("other", "Autre"),
        ],
        string="Canal",
        required=True,
        default="manual",
    )
    score = fields.Float(string="Note", digits=(12, 1))
    score_max = fields.Float(string="Note sur", default=10.0, digits=(12, 1))
    nps_bucket = fields.Selection(
        [
            ("promoter", "Promoteur"),
            ("passive", "Passif"),
            ("detractor", "Détracteur"),
        ],
        string="Catégorie NPS",
        compute="_compute_nps_bucket",
        store=True,
    )
    comment = fields.Text(string="Commentaire")
    theme_ids = fields.Many2many(
        "bf.cx.theme",
        string="Thèmes",
        help="Causes récurrentes (délais, communication, prix, qualité…) : "
             "l'axe d'agrégation de la boucle externe. À revoir "
             "périodiquement dans le pivot.",
    )
    program_id = fields.Many2one(
        "bf.cx.program", string="Programme", ondelete="set null", index=True
    )
    wave_id = fields.Many2one(
        "bf.cx.wave", string="Vague", ondelete="set null", index=True
    )
    survey_user_input_id = fields.Many2one(
        "survey.user_input",
        string="Réponse au sondage",
        ondelete="set null",
        copy=False,
    )
    rating_ref_id = fields.Many2one(
        "rating.rating",
        string="Évaluation d'origine",
        ondelete="set null",
        copy=False,
    )
    project_id = fields.Many2one("project.project", string="Projet / mandat")
    company_id = fields.Many2one(
        "res.company",
        string="Société",
        required=True,
        default=lambda self: self.env.company,
    )
    user_id = fields.Many2one(
        "res.users",
        string="Responsable du suivi",
        tracking=True,
        help="Reçoit l'activité de boucle fermée pour un détracteur ou une "
             "note insatisfaite.",
    )
    subject_user_id = fields.Many2one(
        "res.users",
        string="Personne évaluée",
        index=True,
        tracking=True,
        help="Rétroaction interne (360) : la personne SUR QUI porte cette "
             "entrée. À ne pas confondre avec le responsable du suivi, qui "
             "est le porteur du compte client.",
    )
    state = fields.Selection(
        [
            ("new", "Nouveau"),
            ("in_progress", "Suivi en cours"),
            ("done", "Traité"),
        ],
        string="État",
        default="new",
        required=True,
        tracking=True,
    )
    needs_followup = fields.Boolean(
        string="À rappeler",
        compute="_compute_needs_followup",
        store=True,
        help="Détracteur NPS ou note insatisfaite : mérite un suivi de "
             "boucle fermée. C'est ce champ que la tuile du tableau de bord "
             "et le digest surveillent.",
    )
    is_testimonial_candidate = fields.Boolean(
        string="Candidat témoignage",
        help="Le répondant a accepté qu'on le recontacte pour citer ses "
             "commentaires.",
    )
    testimonial_id = fields.Many2one(
        "bf.cx.testimonial",
        string="Témoignage",
        readonly=True,
        copy=False,
    )
    color = fields.Integer(string="Couleur")

    # ── Compute ──────────────────────────────────────────────────────────────

    @api.depends("kind", "score")
    def _compute_nps_bucket(self):
        for rec in self:
            if rec.kind != "nps":
                rec.nps_bucket = False
            elif rec.score >= NPS_PROMOTER_MIN:
                rec.nps_bucket = "promoter"
            elif rec.score >= NPS_PASSIVE_MIN:
                rec.nps_bucket = "passive"
            else:
                rec.nps_bucket = "detractor"

    @api.depends("kind", "score", "score_max", "nps_bucket")
    def _compute_needs_followup(self):
        for rec in self:
            rec.needs_followup = rec._needs_followup()

    @api.depends("partner_id.name", "kind", "date")
    def _compute_display_name(self):
        kind_labels = dict(self._fields["kind"]._description_selection(self.env))
        for rec in self:
            who = rec.partner_id.name or _("Anonyme")
            rec.display_name = "%s - %s (%s)" % (
                kind_labels.get(rec.kind, rec.kind),
                who,
                rec.date or "",
            )

    @api.constrains("kind", "score", "score_max")
    def _check_nps_scale(self):
        """NPS buckets (9-10 / 7-8 / 0-6) only make sense on a 0-10 scale."""
        for rec in self:
            if rec.kind != "nps":
                continue
            if rec.score_max != 10 or not (0 <= rec.score <= 10):
                raise ValidationError(
                    _("Un feedback NPS doit être noté sur une échelle de 0 à "
                      "10 (note reçue : %(score)s/%(max)s).",
                      score=rec.score, max=rec.score_max)
                )

    # ── ORM ──────────────────────────────────────────────────────────────────

    def init(self):
        """Hard net against concurrent double-ingestion of a survey answer."""
        self.env.cr.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "bf_cx_feedback_survey_input_uniq "
            "ON bf_cx_feedback (survey_user_input_id) "
            "WHERE survey_user_input_id IS NOT NULL"
        )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("user_id"):
                partner = self.env["res.partner"].browse(
                    vals.get("partner_id") or []
                )
                program = self.env["bf.cx.program"].browse(
                    vals.get("program_id") or []
                )
                user = partner.user_id or program.user_id
                if user:
                    vals["user_id"] = user.id
        return super().create(vals_list)

    # ── Closed loop ──────────────────────────────────────────────────────────

    def _needs_followup(self):
        """A record that warrants a closed-loop follow-up."""
        self.ensure_one()
        if self.kind == "nps":
            return self.nps_bucket == "detractor"
        if self.kind == "csat":
            return self.score < CSAT_KO_BELOW * (self.score_max / 5.0 or 1.0)
        return False

    def _closed_loop_deadline(self):
        """Explicit follow-up deadline (best practice: contact within 24-72h)."""
        raw = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("bf_cx.followup_days", "2")
        )
        try:
            days = max(0, int(raw))
        except (TypeError, ValueError):
            days = 2
        return fields.Date.context_today(self) + timedelta(days=days)

    def _closed_loop_user(self):
        """Internal assignee for automatic activities, or None.

        Runs sudo from public flows: env.user may be the public user (sudo
        does not change the current user) - an activity assigned to it
        would be invisible to everyone.
        """
        self.ensure_one()
        user = self.user_id or self.program_id.user_id
        if (not user or user.share) and not self.env.user.share:
            user = self.env.user
        if not user or user.share:
            # Public flow (rating link, pulse) with no salesperson and no
            # program owner: fall back to the admin rather than silently
            # dropping the follow-up.
            user = self.env.ref("base.user_admin", raise_if_not_found=False)
        if not user or user.share:
            _logger.warning(
                "bf_cx: no internal user to assign the automatic activity "
                "for feedback %s - skipped",
                self.id,
            )
            return None
        return user

    def _run_closed_loop(self):
        """Schedule a follow-up for detractors / dissatisfied ratings.

        Called by the ingestion paths (survey completion, rating consumption)
        - deliberately NOT by create(), so manual data entry never spams
        activities. Bridge modules extend this (e.g. auto helpdesk ticket).
        """
        if not param_is_true(self.env, "bf_cx.auto_activity", default=True):
            return
        for rec in self:
            if not rec._needs_followup():
                continue
            user = rec._closed_loop_user()
            if not user:
                continue
            try:
                if rec.partner_id:
                    summary = (
                        _("Boucle fermée : recontacter %s")
                        % rec.partner_id.display_name
                    )
                    note = _(
                        "Note de %(score)s/%(max)s reçue. Prendre contact, "
                        "comprendre la cause et consigner le suivi sur cette "
                        "fiche.",
                        score=rec.score,
                        max=int(rec.score_max),
                    )
                elif (rec.comment or "").strip():
                    # Anonymous pulse answer: no one to call back - examine
                    # the verbatim instead of promising a recontact.
                    summary = _("Verbatim anonyme à examiner")
                    note = _(
                        "Note de %(score)s/%(max)s reçue anonymement (lien "
                        "pulse). Lire le commentaire et en tirer les leçons "
                        "- aucun recontact possible.",
                        score=rec.score,
                        max=int(rec.score_max),
                    )
                else:
                    # Anonymous AND silent: nothing actionable, pure noise.
                    continue
                rec.activity_schedule(
                    "mail.mail_activity_data_todo",
                    date_deadline=rec._closed_loop_deadline(),
                    user_id=user.id,
                    summary=summary,
                    note=note,
                )
            except Exception:  # noqa: BLE001 - never break the public flow
                _logger.exception(
                    "bf_cx: closed-loop activity failed for feedback %s",
                    rec.id,
                )

    def _run_testimonial_candidate_loop(self):
        """A testimonial opt-in is perishable: act on it within days."""
        if not param_is_true(
            self.env, "bf_cx.testimonial_activity", default=True
        ):
            return
        for rec in self:
            if not rec.is_testimonial_candidate or not rec.partner_id:
                continue
            user = rec._closed_loop_user()
            if not user:
                continue
            try:
                rec.activity_schedule(
                    "mail.mail_activity_data_todo",
                    date_deadline=fields.Date.context_today(rec)
                    + timedelta(days=5),
                    user_id=user.id,
                    summary=_("Candidat témoignage : recontacter %s")
                    % rec.partner_id.display_name,
                    note=_(
                        "Le répondant a accepté d'être cité. Confirmer le "
                        "témoignage pendant que c'est frais (bouton « Créer "
                        "un témoignage » sur cette fiche)."
                    ),
                )
            except Exception:  # noqa: BLE001 - never break the public flow
                _logger.exception(
                    "bf_cx: testimonial-candidate activity failed for "
                    "feedback %s",
                    rec.id,
                )

    # ── Actions ──────────────────────────────────────────────────────────────

    def action_start_followup(self):
        self.write({"state": "in_progress"})
        return True

    def action_mark_done(self):
        self.write({"state": "done"})
        # Keep the two follow-up trackers honest: closing the record also
        # closes its open automatic to-dos (and vice-versa would drift).
        self.activity_feedback(
            ["mail.mail_activity_data_todo"],
            feedback=_("Suivi fermé depuis la fiche feedback."),
        )
        return True

    def action_reset_new(self):
        self.write({"state": "new"})
        return True

    def action_create_testimonial(self):
        """Turn this feedback's comment into a draft testimonial."""
        self.ensure_one()
        if not self.partner_id:
            raise UserError(
                _("Impossible de créer un témoignage sans contact identifié "
                  "(réponse anonyme).")
            )
        if self.testimonial_id:
            testimonial = self.testimonial_id
        else:
            testimonial = self.env["bf.cx.testimonial"].create(
                {
                    "name": _("Témoignage - %s")
                    % (self.partner_id.display_name or self.date),
                    "partner_id": self.partner_id.id,
                    "body": self.comment or "",
                    "project_id": self.project_id.id,
                    "company_id": self.company_id.id,
                    "feedback_id": self.id,
                }
            )
            self.testimonial_id = testimonial
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.cx.testimonial",
            "res_id": testimonial.id,
            "view_mode": "form",
        }

    # ── Shared NPS math ──────────────────────────────────────────────────────

    # Below this many scored answers, an NPS carries a ±20-25 pt margin of
    # error: showing a number would be statistical noise dressed as insight.
    NPS_MIN_RESPONSES = 10

    @api.model
    def _nps_window_days(self):
        raw = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("bf_cx.nps_window_days", "365")
        )
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            return 365

    @api.model
    def _nps_summary(self, extra_domain=None, days=None, date_from=None,
                     date_to=None):
        """Single source of truth for NPS aggregates (dashboard, digest,
        program). Returns promoter/passive/detractor counts, n, the score
        (None when n is too small to be honest) and a display string.
        Window: explicit date_from/date_to bounds, else a rolling ``days``
        window (default: the configured honest window)."""
        if date_from or date_to:
            bounds = []
            if date_from:
                bounds.append(("date", ">=", date_from))
            if date_to:
                bounds.append(("date", "<=", date_to))
            days = 0
        else:
            if days is None:
                days = self._nps_window_days()
            since = fields.Date.context_today(self) - timedelta(days=days)
            bounds = [("date", ">=", since)]
        domain = [
            ("kind", "=", "nps"),
            ("nps_bucket", "!=", False),
        ] + bounds + (extra_domain or [])
        buckets = {"promoter": 0, "passive": 0, "detractor": 0}
        for bucket, count in self.sudo()._read_group(
            domain, ["nps_bucket"], ["__count"]
        ):
            buckets[bucket] = count
        scored = sum(buckets.values())
        score = (
            round((buckets["promoter"] - buckets["detractor"]) * 100.0 / scored)
            if scored >= self.NPS_MIN_RESPONSES
            else None
        )
        if score is not None:
            display = str(score)
        elif scored:
            display = _("n insuffisant")
        else:
            display = "n/d"
        return {
            "days": days,
            "n": scored,
            "score": score,
            "display": display,
            **buckets,
        }

    # ── Internal 360 aggregates ─────────────────────────────────────────────

    def _bf_cx_360_stats(self, extra_domain=None, group_field="subject_user_id"):
        """Average internal-360 score, grouped by subject or by wave.

        Returns {group_id: {"n", "average", "score_max", "masked"}}.
        ``masked`` is True below MIN_360_RESPONSES answers: with two
        replies an average reports who answered, not how the person
        works, and a number on a screen gets read as a verdict either
        way. Callers decide whether to show the raw average; the display
        helper below does not.

        Runs under the caller's rights on purpose: the record rules keep
        internal entries away from operators, and this must not be the
        hole in that wall.
        """
        domain = [
            ("kind", "=", "internal"),
            (group_field, "!=", False),
            ("score_max", ">", 0),
        ] + (extra_domain or [])
        stats = {}
        for group, count, score_sum, score_max in self._read_group(
            domain,
            [group_field],
            ["__count", "score:sum", "score_max:max"],
        ):
            group_id = group.id if hasattr(group, "id") else group
            stats[group_id] = {
                "n": count,
                "average": round(score_sum / count, 1) if count else 0.0,
                "score_max": score_max or 0.0,
                "masked": count < MIN_360_RESPONSES,
            }
        return stats

    def _bf_cx_360_summary(self, extra_domain=None):
        """Per-person 360 aggregates (the usual entry point)."""
        return self._bf_cx_360_stats(extra_domain, "subject_user_id")

    def _bf_cx_360_display(self, summary):
        """One-line rendering of a 360 aggregate, honest about thin data."""
        if not summary or not summary["n"]:
            return _("Aucune réponse")
        if summary["masked"]:
            return _(
                "%(n)s réponse(s) - moyenne masquée sous %(min)s",
                n=summary["n"],
                min=MIN_360_RESPONSES,
            )
        return _(
            "%(avg)s/%(max)s sur %(n)s réponses",
            avg=summary["average"],
            max=int(summary["score_max"]),
            n=summary["n"],
        )

    # ── Dashboard data (OWL client action) ──────────────────────────────────

    @api.model
    def get_cx_dashboard_data(self, date_from, date_to):
        """Aggregates for the CX dashboard, scoped to [date_from, date_to].

        Runs with the CURRENT user's rights (record rules apply: company
        scoping, internal-360 hidden from operators). Read-only. Datetime
        bounds are built in the user's timezone so a late-evening record
        lands in the right local day.
        """
        company = self.env.company
        base = [("company_id", "=", company.id)]
        period = base + [("date", ">=", date_from), ("date", "<=", date_to)]

        # Local-day bounds → naive UTC for the Datetime domains.
        tz = pytz.timezone(self.env.user.tz or "UTC")
        dt_from = fields.Datetime.to_string(
            tz.localize(
                fields.Datetime.to_datetime("%s 00:00:00" % date_from)
            ).astimezone(pytz.UTC).replace(tzinfo=None)
        )
        dt_to = fields.Datetime.to_string(
            tz.localize(
                fields.Datetime.to_datetime("%s 23:59:59" % date_to)
            ).astimezone(pytz.UTC).replace(tzinfo=None)
        )

        nps = self._nps_summary(
            extra_domain=base, date_from=date_from, date_to=date_to
        )

        # General satisfaction: average CSAT (email ratings & co), on /5.
        csat_rows = self._read_group(
            period + [("kind", "=", "csat")],
            [],
            ["score:avg", "score_max:avg", "__count"],
        )
        csat_avg, csat_max, csat_n = csat_rows[0] if csat_rows else (0, 0, 0)
        csat_display = (
            "%.1f / 5" % (csat_avg * 5.0 / (csat_max or 5.0))
            if csat_n
            else "n/d"
        )

        Complaint = self.env["bf.cx.complaint"]
        complaint_period = [
            ("company_id", "=", company.id),
            ("date_received", ">=", dt_from),
            ("date_received", "<=", dt_to),
        ]
        complaints_received = Complaint.search_count(complaint_period)
        complaints_open = Complaint.search_count(
            [
                ("company_id", "=", company.id),
                ("state", "not in", ("resolved", "closed")),
            ]
        )
        ack_rows = Complaint._read_group(
            complaint_period + [("date_acknowledged", "!=", False)],
            [],
            ["ack_delay_hours:avg"],
        )
        ack_avg = ack_rows[0][0] if ack_rows and ack_rows[0][0] else 0.0

        # Response rate: tokenized answers created in the period on the
        # programs' surveys (wave invitations, post-loss, onboarding…).
        surveys = self.env["bf.cx.program"].search([]).survey_id
        Input = self.env["survey.user_input"]
        input_domain = [
            ("survey_id", "in", surveys.ids),
            ("test_entry", "=", False),
            ("create_date", ">=", dt_from),
            ("create_date", "<=", dt_to),
        ]
        invited = Input.search_count(input_domain)
        completed = Input.search_count(input_domain + [("state", "=", "done")])

        # Monthly trend: NPS buckets + complaints, grouped by month. The
        # keys must be normalized to YYYY-MM: `date` (a Date field) groups
        # to a date, `date_received` (Datetime) groups to a timestamp, so a
        # raw str() would never merge the two for the same month.
        def _month_key(value):
            return str(value)[:7]

        months = {}
        for bucket_month, bucket, count in self._read_group(
            period + [("kind", "=", "nps"), ("nps_bucket", "!=", False)],
            ["date:month", "nps_bucket"],
            ["__count"],
        ):
            entry = months.setdefault(
                _month_key(bucket_month),
                {"promoter": 0, "passive": 0, "detractor": 0, "complaints": 0},
            )
            entry[bucket] = count
        for complaint_month, count in Complaint._read_group(
            complaint_period, ["date_received:month"], ["__count"]
        ):
            entry = months.setdefault(
                _month_key(complaint_month),
                {"promoter": 0, "passive": 0, "detractor": 0, "complaints": 0},
            )
            entry["complaints"] = count

        themes = [
            {"name": theme.name if theme else _("Sans thème"), "count": count}
            for theme, count in self._read_group(
                period + [("theme_ids", "!=", False)],
                ["theme_ids"],
                ["__count"],
            )
        ]
        themes.sort(key=lambda t: -t["count"])

        return {
            "company": company.name,
            "nps": nps,
            "csat": {
                "display": csat_display,
                "n": csat_n,
            },
            "complaints": {
                "received": complaints_received,
                "open": complaints_open,
                "ack_avg_hours": round(ack_avg, 1),
            },
            "response": {
                "invited": invited,
                "completed": completed,
                "rate": round(completed * 100.0 / invited, 1)
                if invited
                else 0.0,
            },
            "followup_todo": self.search_count(
                base + [("needs_followup", "=", True), ("state", "!=", "done")]
            ),
            "months": [
                {"label": label, **values}
                for label, values in sorted(months.items())
            ],
            "themes": themes[:8],
        }
