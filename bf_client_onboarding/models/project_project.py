import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

ONBOARDING_STATES = [
    ("not_started", "Non démarré"),
    ("nda_pending", "NDA envoyée"),
    ("nda_signed", "NDA signée"),
    ("intake_pending", "Intake envoyée"),
    ("intake_completed", "Intake reçue"),
    ("kickoff_done", "Kickoff fait"),
    ("active", "Actif"),
    ("on_hold", "En attente"),
]

# Linear order used to compute progress and to drive auto-advance.
_LINEAR_STATES = [
    "not_started",
    "nda_pending",
    "nda_signed",
    "intake_pending",
    "intake_completed",
    "kickoff_done",
    "active",
]


class ProjectProject(models.Model):
    _inherit = "project.project"

    onboarding_state = fields.Selection(
        ONBOARDING_STATES,
        string="État d'onboarding",
        default="not_started",
        tracking=True,
        index=True,
        copy=False,
    )
    onboarding_progress = fields.Float(
        string="Progression",
        compute="_compute_onboarding_progress",
        store=True,
        help="0–100 %, dérivé linéairement de l'état d'onboarding.",
    )
    onboarding_responsible_id = fields.Many2one(
        "res.users",
        string="Responsable onboarding",
        default=lambda self: self.env.user,
        tracking=True,
    )
    onboarding_blocked_reason = fields.Text(
        string="Raison du blocage",
        help="Renseigner quand l'état passe à « En attente ».",
    )
    onboarding_state_last_change = fields.Datetime(
        string="Dernier changement d'état",
        readonly=True,
        copy=False,
    )

    # NDA
    nda_letter_document_id = fields.Many2one(
        "letter.document",
        string="Lettre NDA",
        copy=False,
        help="Lettre NDA générée via bf_letter_writer.",
    )
    nda_libresign_file_uuid = fields.Char(
        string="UUID LibreSign NDA",
        copy=False,
        help="Identifiant retourné par LibreSign pour la NDA en signature.",
    )
    nda_signed_date = fields.Date(string="NDA signée le", copy=False)

    # Intake
    intake_survey_id = fields.Many2one(
        "survey.survey",
        string="Questionnaire intake",
        default=lambda self: self.env.ref(
            "bf_client_onboarding.survey_client_intake", raise_if_not_found=False
        ),
    )
    intake_survey_response_id = fields.Many2one(
        "survey.user_input",
        string="Réponse intake",
        copy=False,
    )
    intake_completed_date = fields.Date(string="Intake reçue le", copy=False)

    # Kickoff
    kickoff_meeting_id = fields.Many2one(
        "meeting.record",
        string="Rencontre kickoff",
        copy=False,
        domain="[('project_id', '=', id)]",
    )
    kickoff_completed_date = fields.Date(string="Kickoff fait le", copy=False)

    # Aggregated read-only signals
    last_meeting_date = fields.Datetime(
        string="Dernière rencontre",
        compute="_compute_last_meeting_date",
    )
    hour_bank_balance = fields.Float(
        string="Solde banque d'heures",
        compute="_compute_hour_bank_balance",
        help="Somme des soldes des banques d'heures qui couvrent ce projet.",
    )

    # ------------------------------------------------------------------
    # Compute
    # ------------------------------------------------------------------
    @api.depends("onboarding_state")
    def _compute_onboarding_progress(self):
        denom = max(len(_LINEAR_STATES) - 1, 1)
        for project in self:
            state = project.onboarding_state
            if state in _LINEAR_STATES:
                project.onboarding_progress = (
                    _LINEAR_STATES.index(state) / denom
                ) * 100.0
            else:
                # on_hold or empty: keep the visual at 0; the badge already
                # signals the blocked state.
                project.onboarding_progress = 0.0

    @api.depends("meeting_record_ids", "meeting_record_ids.date")
    def _compute_last_meeting_date(self):
        for project in self:
            # meeting.record _order = 'date desc, id desc', so first wins.
            project.last_meeting_date = (
                project.meeting_record_ids[:1].date if project.meeting_record_ids else False
            )

    def _compute_hour_bank_balance(self):
        HourBank = self.env["hour.bank.client"].sudo()
        for project in self:
            banks = HourBank.search([("project_ids", "in", project.id)])
            project.hour_bank_balance = sum(banks.mapped("current_balance"))

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------
    def _set_state(self, new_state, body=None):
        """Centralised state mutation: tracks last-change + posts to chatter."""
        self.ensure_one()
        if new_state not in dict(ONBOARDING_STATES):
            raise UserError(_("État d'onboarding inconnu : %s") % new_state)
        self.write({
            "onboarding_state": new_state,
            "onboarding_state_last_change": fields.Datetime.now(),
        })
        if body:
            self.message_post(body=body)

    def action_send_nda(self):
        self.ensure_one()
        if not self.partner_id:
            raise UserError(_("Renseignez un client sur le projet avant d'envoyer la NDA."))
        if self.onboarding_state not in ("not_started", "on_hold"):
            raise UserError(_(
                "La NDA ne peut être envoyée qu'à partir de l'état « Non démarré »."
            ))
        template = self.env.ref(
            "bf_client_onboarding.letter_template_nda", raise_if_not_found=False
        )
        if not template:
            raise UserError(_(
                "Modèle de lettre NDA introuvable. Vérifiez l'installation du module."
            ))
        letter_vals = {
            "name": _("NDA — %s") % self.partner_id.name,
            "partner_id": self.partner_id.id,
            "template_id": template.id,
            "company_id": self.company_id.id or self.env.company.id,
        }
        letter = self.env["letter.document"].create(letter_vals)
        # Apply the template body now so the user can review/edit before send.
        letter.action_apply_template()
        self.nda_letter_document_id = letter.id
        self._set_state(
            "nda_pending",
            body=_(
                "NDA générée : <a href=# data-oe-model=letter.document "
                "data-oe-id=%(lid)s>%(ref)s</a>. Envoyez-la depuis la fiche "
                "lettre puis cliquez « NDA signée » à la réception."
            ) % {"lid": letter.id, "ref": letter.reference or letter.name},
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("NDA"),
            "res_model": "letter.document",
            "res_id": letter.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_mark_nda_signed(self):
        self.ensure_one()
        if self.onboarding_state != "nda_pending":
            raise UserError(_(
                "La NDA ne peut être marquée signée que depuis l'état « NDA envoyée »."
            ))
        self.nda_signed_date = fields.Date.context_today(self)
        self._set_state(
            "nda_signed",
            body=_("NDA marquée signée le %s.") % self.nda_signed_date,
        )

    def action_send_intake(self):
        self.ensure_one()
        if self.onboarding_state != "nda_signed":
            raise UserError(_(
                "L'intake ne peut être envoyée qu'après la signature de la NDA."
            ))
        if not self.intake_survey_id:
            raise UserError(_(
                "Aucun questionnaire intake n'est lié au projet."
            ))
        if not self.partner_id or not self.partner_id.email:
            raise UserError(_(
                "Le contact client doit avoir une adresse courriel pour recevoir l'intake."
            ))
        # Create a survey response in 'new' state for the partner; the email
        # invite is sent via the survey's own action_send_survey wizard, which
        # the user will trigger from the survey form.
        response = self.env["survey.user_input"].create({
            "survey_id": self.intake_survey_id.id,
            "partner_id": self.partner_id.id,
            "email": self.partner_id.email,
        })
        self.intake_survey_response_id = response.id
        self._set_state(
            "intake_pending",
            body=_(
                "Intake créée : <a href=# data-oe-model=survey.user_input "
                "data-oe-id=%(rid)s>réponse #%(rid)s</a>. URL : <a href=%(url)s>%(url)s</a>"
            ) % {"rid": response.id, "url": response.get_start_url()},
        )

    def action_mark_intake_completed(self):
        """Manual fallback when the auto-advance via survey.user_input misses."""
        self.ensure_one()
        if self.onboarding_state != "intake_pending":
            raise UserError(_(
                "L'intake ne peut être marquée reçue que depuis l'état « Intake envoyée »."
            ))
        self.intake_completed_date = fields.Date.context_today(self)
        self._set_state(
            "intake_completed",
            body=_("Intake marquée reçue le %s.") % self.intake_completed_date,
        )

    def action_mark_kickoff_done(self):
        self.ensure_one()
        if self.onboarding_state != "intake_completed":
            raise UserError(_(
                "Le kickoff ne peut être confirmé qu'après réception de l'intake."
            ))
        # If a kickoff meeting is already picked, use it; else fall back to the
        # most recent meeting on the project.
        meeting = self.kickoff_meeting_id or self.meeting_record_ids[:1]
        if not meeting:
            raise UserError(_(
                "Aucune rencontre n'est rattachée au projet — créez d'abord le "
                "compte rendu de kickoff puis revenez ici."
            ))
        self.kickoff_meeting_id = meeting.id
        self.kickoff_completed_date = fields.Date.context_today(self)
        self._set_state(
            "kickoff_done",
            body=_("Kickoff confirmé à partir de la rencontre %s.") % meeting.display_name,
        )

    def action_activate(self):
        self.ensure_one()
        if self.onboarding_state not in ("kickoff_done", "on_hold"):
            raise UserError(_(
                "L'activation n'est possible qu'à partir des états « Kickoff fait » "
                "ou « En attente »."
            ))
        self._set_state(
            "active",
            body=_("Projet activé — onboarding terminé."),
        )

    def action_block(self):
        self.ensure_one()
        if self.onboarding_state in ("on_hold", "active", "not_started"):
            raise UserError(_(
                "L'état « En attente » s'applique aux projets en cours d'onboarding."
            ))
        # Open a wizard would be cleaner, but a quick chatter prompt keeps the
        # surface area small for v1; the reason is captured via the field that
        # becomes editable when state == on_hold.
        self._set_state(
            "on_hold",
            body=_(
                "Onboarding mis en attente. Renseignez la raison dans l'onglet "
                "Progression pour le suivi."
            ),
        )

    def action_unblock(self):
        self.ensure_one()
        if self.onboarding_state != "on_hold":
            raise UserError(_("Le projet n'est pas en attente."))
        # Resume at the step that matches the most recent completed milestone.
        if self.kickoff_completed_date:
            resume = "kickoff_done"
        elif self.intake_completed_date:
            resume = "intake_completed"
        elif self.intake_survey_response_id:
            resume = "intake_pending"
        elif self.nda_signed_date:
            resume = "nda_signed"
        elif self.nda_letter_document_id:
            resume = "nda_pending"
        else:
            resume = "not_started"
        self.onboarding_blocked_reason = False
        self._set_state(
            resume,
            body=_("Onboarding repris à l'état « %s ».") % dict(ONBOARDING_STATES)[resume],
        )

    # ------------------------------------------------------------------
    # Cron — chase stagnant onboardings
    # ------------------------------------------------------------------
    @api.model
    def _cron_chase_stagnant_onboarding(self, stale_days=7):
        """Create a To-Do activity on the responsible user when an onboarding
        state hasn't moved in `stale_days` days. Skips active/on_hold/empty.
        """
        cutoff = fields.Datetime.subtract(fields.Datetime.now(), days=stale_days)
        candidates = self.search([
            ("active", "=", True),
            ("onboarding_state", "in",
             [s for s in _LINEAR_STATES if s != "active"]),
            ("onboarding_state_last_change", "<=", cutoff),
        ])
        activity_type = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
        if not activity_type:
            _logger.warning("bf_client_onboarding: mail.mail_activity_data_todo missing; skipping cron.")
            return
        for project in candidates:
            responsible = project.onboarding_responsible_id or project.user_id
            if not responsible:
                continue
            # Skip if an open chase activity already exists for this project+user.
            existing = self.env["mail.activity"].search_count([
                ("res_model", "=", "project.project"),
                ("res_id", "=", project.id),
                ("user_id", "=", responsible.id),
                ("activity_type_id", "=", activity_type.id),
                ("summary", "=", "Onboarding bloqué"),
            ])
            if existing:
                continue
            project.activity_schedule(
                act_type_xmlid="mail.mail_activity_data_todo",
                summary="Onboarding bloqué",
                note=_(
                    "Le projet est resté à l'état « %s » depuis %s. "
                    "Vérifier le blocage et avancer."
                ) % (
                    dict(ONBOARDING_STATES)[project.onboarding_state],
                    project.onboarding_state_last_change,
                ),
                user_id=responsible.id,
            )
