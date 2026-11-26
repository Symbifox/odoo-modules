"""Experience measurement programs.

A program ties a survey to a measurement intent (relational NPS, transactional
CSAT, continuous pulse, or internal 360 feedback) and aggregates the resulting
feedback entries. Waves (bf.cx.wave) carry the actual sends.
"""
import base64
import io
import logging

from markupsafe import escape

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class BfCxProgram(models.Model):
    _name = "bf.cx.program"
    _description = "Experience measurement program"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "sequence, id"

    # Translatable since 18.0.1.13.2: the default programs read in each
    # reader's language, a team's own wording stays as typed.
    name = fields.Char(string="Name", required=True, tracking=True,
                       translate=True)
    sequence = fields.Integer(string="Sequence", default=10)
    active = fields.Boolean(default=True)
    program_type = fields.Selection(
        [
            ("nps", "Relationship NPS"),
            ("csat", "Transactional CSAT"),
            ("pulse", "Continuous pulse"),
            ("internal", "Internal feedback (360)"),
        ],
        string="Program type",
        default="nps",
        required=True,
        tracking=True,
        help="NPS/Pulse: the score is classified as "
             "promoter/passive/detractor. CSAT: a plain satisfaction "
             "score. Internal: entries are restricted to the module's "
             "managers.",
    )
    survey_id = fields.Many2one(
        "survey.survey",
        string="Survey",
        required=True,
        ondelete="restrict",
        tracking=True,
        help="Odoo survey supporting the program. The score question must "
             "be of the \"Scale\" type (0-10 for an NPS).",
    )
    score_question_id = fields.Many2one(
        "survey.question",
        string="Score question",
        domain="[('survey_id', '=', survey_id), ('question_type', '=', 'scale')]",
        help="\"Scale\" question whose value feeds the score. If empty, "
             "the first scale answer is used.",
    )
    comment_question_id = fields.Many2one(
        "survey.question",
        string="Comment question",
        domain="[('survey_id', '=', survey_id), ('question_type', 'in', ('text_box', 'char_box'))]",
        help="Text question whose answer becomes the feedback comment. If "
             "empty, the first text answer is used.",
    )
    testimonial_question_id = fields.Many2one(
        "survey.question",
        string="Testimonial question",
        domain="[('survey_id', '=', survey_id), ('question_type', '=', 'simple_choice')]",
        help="Single-choice question asking permission to quote the "
             "respondent. Optional.",
    )
    testimonial_answer_id = fields.Many2one(
        "survey.question.answer",
        string="Testimonial \"yes\" answer",
        domain="[('question_id', '=', testimonial_question_id)]",
        help="Answer choice that marks the feedback as a testimonial "
             "candidate.",
    )
    testimonial_direct_answer_id = fields.Many2one(
        "survey.question.answer",
        string="\"Yes, no recontact\" answer",
        domain="[('question_id', '=', testimonial_question_id)]",
        help="Answer choice that counts as consent: the testimonial is "
             "created directly as \"Consent obtained\", the survey answer "
             "serving as proof. Its label must say how the respondent "
             "will be named. Optional.",
    )
    invite_template_id = fields.Many2one(
        "mail.template",
        string="Invitation template",
        domain="[('model', '=', 'survey.user_input')]",
        default=lambda self: self.env.ref(
            "bf_cx.mail_template_cx_invite",
            raise_if_not_found=False,
        ),
        help="Email sent for each invitation of a wave.",
    )
    campaign_id = fields.Many2one(
        "utm.campaign",
        string="UTM campaign",
        help="Campaign attached by default to the program's waves "
             "(standard Odoo has no survey ↔ campaign link: this module "
             "provides it).",
    )
    user_id = fields.Many2one(
        "res.users",
        string="Owner",
        default=lambda self: self.env.user,
        tracking=True,
    )
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.company,
    )
    reminder_days = fields.Integer(
        string="Reminder after (days)",
        default=5,
        help="Number of days after a wave is sent before the automatic "
             "reminder to non-respondents. 0 = no automatic reminder.",
    )
    reminder_template_id = fields.Many2one(
        "mail.template",
        string="Reminder template",
        domain="[('model', '=', 'survey.user_input')]",
        default=lambda self: self.env.ref(
            "bf_cx.mail_template_cx_reminder",
            raise_if_not_found=False,
        ),
        help="Reminder email to non-respondents (a short, distinct "
             "message works better than an identical resend). Empty = the "
             "invitation template is resent.",
    )
    hide_respondent = fields.Boolean(
        string="Hide the respondent (360)",
        default=True,
        help="On an internal program, does not enter the respondent in "
             "the feedback register: lists, groupings, exports and "
             "dashboards do not name them. The masking applies to the "
             "register, not to the database: a manager still has access "
             "to the survey answer itself. On a team of three, tell "
             "respondents so honestly.",
    )
    cooldown_days = fields.Integer(
        string="Minimum cadence (days)",
        default=0,
        help="Over-solicitation quarantine specific to THIS program "
             "(practice: 90 days for a relationship NPS, 30 days for "
             "transactional). 0 = the global setting applies.",
    )
    wave_ids = fields.One2many("bf.cx.wave", "program_id", string="Waves")
    feedback_ids = fields.One2many(
        "bf.cx.feedback", "program_id", string="Feedback"
    )

    pulse_url = fields.Char(
        string="Public URL (pulse)",
        compute="_compute_pulse",
        help="Available when the survey's access is \"Anyone with the "
             "link\": a permanent link to share (signatures, posters, QR).",
    )
    pulse_snippet = fields.Text(
        string="HTML snippet for a signature",
        compute="_compute_pulse",
        help="To paste as is into an email signature or a communication "
             "footer.",
    )

    wave_count = fields.Integer(compute="_compute_wave_count")
    feedback_count = fields.Integer(compute="_compute_feedback_stats")
    promoter_count = fields.Integer(
        string="Promoters", compute="_compute_feedback_stats"
    )
    passive_count = fields.Integer(string="Passives", compute="_compute_feedback_stats")
    detractor_count = fields.Integer(
        string="Detractors", compute="_compute_feedback_stats"
    )
    nps_score = fields.Integer(
        string="NPS score (all time)",
        compute="_compute_feedback_stats",
        help="% promoters − % detractors over the program's ENTIRE "
             "history, from −100 to +100. Includes manual entries "
             "attached to the program.",
    )
    nps_window_display = fields.Char(
        string="NPS (window)",
        compute="_compute_nps_window",
        help="Score over the configured rolling window (12 months by "
             "default), hidden below 10 answers: under that, the margin "
             "of error exceeds ±20 points.",
    )

    # ── Compute ──────────────────────────────────────────────────────────────

    def _compute_wave_count(self):
        counts = {
            program.id: count
            for program, count in self.env["bf.cx.wave"]._read_group(
                [("program_id", "in", self.ids)], ["program_id"], ["__count"]
            )
        }
        for program in self:
            program.wave_count = counts.get(program.id, 0)

    def _compute_feedback_stats(self):
        Feedback = self.env["bf.cx.feedback"]
        totals = {
            program.id: {"count": 0, "promoter": 0, "passive": 0, "detractor": 0}
            for program in self
        }
        for program, bucket, count in Feedback._read_group(
            [("program_id", "in", self.ids)],
            ["program_id", "nps_bucket"],
            ["__count"],
        ):
            stats = totals[program.id]
            stats["count"] += count
            if bucket:
                stats[bucket] += count
        for program in self:
            stats = totals[program.id]
            program.feedback_count = stats["count"]
            program.promoter_count = stats["promoter"]
            program.passive_count = stats["passive"]
            program.detractor_count = stats["detractor"]
            scored = stats["promoter"] + stats["passive"] + stats["detractor"]
            program.nps_score = (
                round((stats["promoter"] - stats["detractor"]) * 100.0 / scored)
                if scored
                else 0
            )

    @api.depends_context("lang")
    def _compute_nps_window(self):
        Feedback = self.env["bf.cx.feedback"]
        for program in self:
            summary = Feedback._nps_summary(
                [("program_id", "=", program.id)]
            )
            program.nps_window_display = _(
                "%(display)s (n=%(n)s, %(days)s d)",
                display=summary["display"],
                n=summary["n"],
                days=summary["days"],
            )

    @api.depends("survey_id.access_mode", "survey_id.access_token")
    @api.depends_context("lang")
    def _compute_pulse(self):
        base = (
            self.env["ir.config_parameter"].sudo().get_param("web.base.url") or ""
        ).rstrip("/")
        for program in self:
            survey = program.survey_id
            if survey and survey.access_mode == "public" and base:
                url = "%s/survey/start/%s" % (base, survey.access_token)
                program.pulse_url = url
                # Pasted into the user's own signature: their language.
                program.pulse_snippet = (
                    '<a href="%s" style="color:#29ABE2; '
                    'text-decoration:none; font-size:12px;">%s &#8594;</a>'
                    % (url, escape(_("How did we do? Give us your feedback")))
                )
            else:
                program.pulse_url = False
                program.pulse_snippet = False

    # ── Onchange ─────────────────────────────────────────────────────────────

    @api.onchange("survey_id")
    def _onchange_survey_id(self):
        """Preselect the first scale/text questions of the chosen survey."""
        if not self.survey_id:
            return
        questions = self.survey_id.question_ids
        if self.score_question_id.survey_id != self.survey_id:
            self.score_question_id = questions.filtered(
                lambda q: q.question_type == "scale"
            )[:1]
        if self.comment_question_id.survey_id != self.survey_id:
            self.comment_question_id = questions.filtered(
                lambda q: q.question_type in ("text_box", "char_box")
            )[:1]

    # ── Actions ──────────────────────────────────────────────────────────────

    def action_view_waves(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Waves - %s") % self.name,
            "res_model": "bf.cx.wave",
            "view_mode": "list,form",
            "domain": [("program_id", "=", self.id)],
            "context": {"default_program_id": self.id},
        }

    def action_view_feedback(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Feedback - %s") % self.name,
            "res_model": "bf.cx.feedback",
            "view_mode": "list,kanban,form,graph,pivot",
            "domain": [("program_id", "=", self.id)],
            "context": {"default_program_id": self.id},
        }

    def action_download_qr(self):
        """Downloadable QR code pointing at the public pulse URL.

        Lazy import on purpose: declaring qrcode in external_dependencies
        would block the whole module install on images without it.
        """
        self.ensure_one()
        if not self.pulse_url:
            raise UserError(
                _("The QR code requires a survey with public access "
                  "(\"Anyone with the link\").")
            )
        try:
            import qrcode
        except ImportError as exc:
            raise UserError(
                _("The \"qrcode\" Python library is not installed in the "
                  "Odoo container (pip install qrcode).")
            ) from exc
        buffer = io.BytesIO()
        image = qrcode.make(self.pulse_url, border=2)
        pil_image = getattr(image, "get_image", lambda: image)()
        pil_image.save(buffer, format="PNG")
        attachment = self.env["ir.attachment"].create(
            {
                "name": "qr-%s.png" % (self.survey_id.title or self.name),
                "datas": base64.b64encode(buffer.getvalue()),
                "res_model": self._name,
                "res_id": self.id,
                "mimetype": "image/png",
            }
        )
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%s?download=true" % attachment.id,
            "target": "self",
        }

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _bf_cx_effective_cooldown(self):
        """Cadence réellement appliquée à ce programme, en jours.

        La cadence propre au programme l'emporte sur le paramètre global, et
        seul un zéro fait retomber sur ce dernier - c'est la lecture que fait
        `action_send` via `program.cooldown_days or None`. Le calcul vit ici
        pour qu'il n'existe qu'à un seul endroit : un message d'erreur qui
        recalcule la règle de son côté finit toujours par annoncer un chiffre
        que le garde-fou n'a pas utilisé.
        """
        self.ensure_one()
        if self.cooldown_days:
            return self.cooldown_days
        raw = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("bf_cx.solicitation_cooldown_days", "30")
        )
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 30

    def _feedback_kind(self):
        """Map the program type to the unified feedback kind."""
        self.ensure_one()
        return {
            "nps": "nps",
            "pulse": "nps",
            "csat": "csat",
            "internal": "internal",
        }[self.program_type]
