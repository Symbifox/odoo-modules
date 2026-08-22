"""Experience measurement programs.

A program ties a survey to a measurement intent (relational NPS, transactional
CSAT, continuous pulse, or internal 360 feedback) and aggregates the resulting
feedback entries. Waves (bf.cx.wave) carry the actual sends.
"""
import base64
import io
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class BfCxProgram(models.Model):
    _name = "bf.cx.program"
    _description = "Programme de mesure d'expérience"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "sequence, id"

    name = fields.Char(string="Nom", required=True, tracking=True)
    sequence = fields.Integer(string="Séquence", default=10)
    active = fields.Boolean(default=True)
    program_type = fields.Selection(
        [
            ("nps", "NPS relationnel"),
            ("csat", "CSAT transactionnel"),
            ("pulse", "Pulse continu"),
            ("internal", "Feedback interne (360)"),
        ],
        string="Type de programme",
        default="nps",
        required=True,
        tracking=True,
        help="NPS/Pulse : la note est classée promoteur/passif/détracteur. "
             "CSAT : simple note de satisfaction. Interne : les entrées sont "
             "réservées aux gestionnaires du module.",
    )
    survey_id = fields.Many2one(
        "survey.survey",
        string="Sondage",
        required=True,
        ondelete="restrict",
        tracking=True,
        help="Sondage Odoo servant de support au programme. La question de "
             "score doit être de type « Échelle » (0-10 pour un NPS).",
    )
    score_question_id = fields.Many2one(
        "survey.question",
        string="Question de score",
        domain="[('survey_id', '=', survey_id), ('question_type', '=', 'scale')]",
        help="Question « Échelle » dont la valeur alimente le score. Si vide, "
             "la première réponse de type échelle est utilisée.",
    )
    comment_question_id = fields.Many2one(
        "survey.question",
        string="Question de commentaire",
        domain="[('survey_id', '=', survey_id), ('question_type', 'in', ('text_box', 'char_box'))]",
        help="Question texte dont la réponse devient le commentaire du "
             "feedback. Si vide, la première réponse texte est utilisée.",
    )
    testimonial_question_id = fields.Many2one(
        "survey.question",
        string="Question témoignage",
        domain="[('survey_id', '=', survey_id), ('question_type', '=', 'simple_choice')]",
        help="Question à choix simple demandant la permission de citer le "
             "répondant. Optionnel.",
    )
    testimonial_answer_id = fields.Many2one(
        "survey.question.answer",
        string="Réponse « oui » témoignage",
        domain="[('question_id', '=', testimonial_question_id)]",
        help="Choix de réponse qui marque le feedback comme candidat "
             "témoignage.",
    )
    invite_template_id = fields.Many2one(
        "mail.template",
        string="Gabarit d'invitation",
        domain="[('model', '=', 'survey.user_input')]",
        default=lambda self: self.env.ref(
            "bf_cx.mail_template_cx_invite",
            raise_if_not_found=False,
        ),
        help="Courriel envoyé pour chaque invitation d'une vague.",
    )
    campaign_id = fields.Many2one(
        "utm.campaign",
        string="Campagne UTM",
        help="Campagne associée par défaut aux vagues du programme (le lien "
             "sondage ↔ campagne n'existe pas dans Odoo standard : c'est ce "
             "module qui le porte).",
    )
    user_id = fields.Many2one(
        "res.users",
        string="Responsable",
        default=lambda self: self.env.user,
        tracking=True,
    )
    company_id = fields.Many2one(
        "res.company",
        string="Société",
        required=True,
        default=lambda self: self.env.company,
    )
    reminder_days = fields.Integer(
        string="Rappel après (jours)",
        default=5,
        help="Nombre de jours après l'envoi d'une vague avant le rappel "
             "automatique aux non-répondants. 0 = aucun rappel automatique.",
    )
    reminder_template_id = fields.Many2one(
        "mail.template",
        string="Gabarit de rappel",
        domain="[('model', '=', 'survey.user_input')]",
        default=lambda self: self.env.ref(
            "bf_cx.mail_template_cx_reminder",
            raise_if_not_found=False,
        ),
        help="Courriel de rappel aux non-répondants (un message court et "
             "différencié performe mieux qu'un renvoi identique). Vide = "
             "renvoi du gabarit d'invitation.",
    )
    hide_respondent = fields.Boolean(
        string="Masquer le répondant (360)",
        default=True,
        help="Sur un programme interne, n'inscrit pas le répondant au "
             "registre des feedbacks : les listes, regroupements, exports "
             "et tableaux de bord ne le nomment pas. Le masquage porte sur "
             "le registre, pas sur la base : un gestionnaire garde accès à "
             "la réponse de sondage elle-même. Sur une équipe de trois "
             "personnes, dites-le honnêtement aux répondants.",
    )
    cooldown_days = fields.Integer(
        string="Cadence minimale (jours)",
        default=0,
        help="Quarantaine anti-sursollicitation propre à CE programme "
             "(pratique : 90 j pour un NPS relationnel, 30 j pour du "
             "transactionnel). 0 = le paramètre global s'applique.",
    )
    wave_ids = fields.One2many("bf.cx.wave", "program_id", string="Vagues")
    feedback_ids = fields.One2many(
        "bf.cx.feedback", "program_id", string="Feedbacks"
    )

    pulse_url = fields.Char(
        string="URL publique (pulse)",
        compute="_compute_pulse",
        help="Disponible quand le sondage est en accès « Toute personne "
             "ayant le lien » : lien permanent à diffuser (signatures, "
             "affiches, QR).",
    )
    pulse_snippet = fields.Text(
        string="Extrait HTML pour signature",
        compute="_compute_pulse",
        help="À coller tel quel dans une signature de courriel ou un pied "
             "de communication.",
    )

    wave_count = fields.Integer(compute="_compute_wave_count")
    feedback_count = fields.Integer(compute="_compute_feedback_stats")
    promoter_count = fields.Integer(
        string="Promoteurs", compute="_compute_feedback_stats"
    )
    passive_count = fields.Integer(string="Passifs", compute="_compute_feedback_stats")
    detractor_count = fields.Integer(
        string="Détracteurs", compute="_compute_feedback_stats"
    )
    nps_score = fields.Integer(
        string="Score NPS (historique)",
        compute="_compute_feedback_stats",
        help="% promoteurs − % détracteurs sur TOUT l'historique du "
             "programme, de −100 à +100. Comprend les saisies manuelles "
             "rattachées au programme.",
    )
    nps_window_display = fields.Char(
        string="NPS (fenêtre)",
        compute="_compute_nps_window",
        help="Score sur la fenêtre glissante configurée (défaut 12 mois), "
             "masqué sous 10 réponses : en deçà, la marge d'erreur dépasse "
             "±20 points.",
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

    def _compute_nps_window(self):
        Feedback = self.env["bf.cx.feedback"]
        for program in self:
            summary = Feedback._nps_summary(
                [("program_id", "=", program.id)]
            )
            program.nps_window_display = "%s (n=%s, %s j)" % (
                summary["display"],
                summary["n"],
                summary["days"],
            )

    @api.depends("survey_id.access_mode", "survey_id.access_token")
    def _compute_pulse(self):
        base = (
            self.env["ir.config_parameter"].sudo().get_param("web.base.url") or ""
        ).rstrip("/")
        for program in self:
            survey = program.survey_id
            if survey and survey.access_mode == "public" and base:
                url = "%s/survey/start/%s" % (base, survey.access_token)
                program.pulse_url = url
                program.pulse_snippet = (
                    '<a href="%s" '
                    'style="color:#29ABE1; text-decoration:none; '
                    'font-size:12px;">Comment avons-nous fait&#8239;? '
                    'Donnez-nous votre avis &#8594;</a>' % url
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
            "name": _("Vagues - %s") % self.name,
            "res_model": "bf.cx.wave",
            "view_mode": "list,form",
            "domain": [("program_id", "=", self.id)],
            "context": {"default_program_id": self.id},
        }

    def action_view_feedback(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Feedbacks - %s") % self.name,
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
                _("Le QR exige un sondage en accès public (« Toute personne "
                  "ayant le lien »).")
            )
        try:
            import qrcode
        except ImportError as exc:
            raise UserError(
                _("La librairie Python « qrcode » n'est pas installée dans "
                  "le conteneur Odoo (pip install qrcode).")
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
