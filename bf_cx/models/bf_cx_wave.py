"""Send waves.

A wave is one batch send of a program's survey to a list of contacts. Each
recipient gets an individual survey.user_input (token mode) created through
survey._create_answer(), tagged with the wave, then the invitation template
is rendered per answer. Reminders go out to non-respondents either manually
or through the daily cron.
"""
import logging

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class BfCxWave(models.Model):
    _name = "bf.cx.wave"
    _description = "Vague d'envoi"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    name = fields.Char(
        string="Nom",
        required=True,
        default=lambda self: _("Vague du %s") % fields.Date.context_today(self),
    )
    program_id = fields.Many2one(
        "bf.cx.program",
        string="Programme",
        required=True,
        ondelete="cascade",
        index=True,
    )
    program_type = fields.Selection(
        related="program_id.program_type", readonly=True
    )
    company_id = fields.Many2one(
        related="program_id.company_id", store=True, readonly=True
    )
    state = fields.Selection(
        [("draft", "Brouillon"), ("sent", "Envoyée"), ("closed", "Fermée")],
        string="État",
        default="draft",
        required=True,
        tracking=True,
    )
    partner_ids = fields.Many2many(
        "res.partner",
        string="Destinataires",
        help="Contacts à inviter. Ceux sans adresse courriel sont ignorés à "
             "l'envoi (et listés dans le journal de la vague).",
    )
    subject_user_id = fields.Many2one(
        "res.users",
        string="Personne évaluée",
        index=True,
        tracking=True,
        help="Sur un programme interne, la personne SUR QUI porte la "
             "rétroaction : les destinataires l'évaluent, elle ne s'évalue "
             "pas nécessairement elle-même. Laisser vide pour un pouls "
             "interne qui porte sur l'organisation et non sur quelqu'un.",
    )
    campaign_id = fields.Many2one(
        "utm.campaign",
        string="Campagne UTM",
        compute="_compute_campaign_id",
        store=True,
        readonly=False,
        help="Repris du programme par défaut ; modifiable par vague.",
    )
    deadline = fields.Datetime(
        string="Date limite de réponse",
        help="Transmise aux réponses générées (le sondage refuse les "
             "réponses après cette date).",
    )
    sent_date = fields.Datetime(string="Envoyée le", readonly=True, copy=False)
    reminder_date = fields.Datetime(
        string="Dernier rappel", readonly=True, copy=False
    )
    user_input_ids = fields.One2many(
        "survey.user_input", "bf_cx_wave_id", string="Réponses"
    )

    invited_count = fields.Integer(
        string="Invités", compute="_compute_input_stats"
    )
    completed_count = fields.Integer(
        string="Réponses complétées", compute="_compute_input_stats"
    )
    completion_rate = fields.Float(
        string="Taux de réponse (%)", compute="_compute_input_stats"
    )
    nps_score = fields.Integer(string="Score NPS", compute="_compute_nps_score")
    subject_summary = fields.Char(
        string="Résultat 360",
        compute="_compute_subject_summary",
        help="Moyenne des notes reçues par la personne évaluée dans cette "
             "vague, masquée sous un seuil de réponses.",
    )

    # ── Constraints ──────────────────────────────────────────────────────────

    @api.constrains("subject_user_id", "program_id")
    def _check_subject_is_internal(self):
        """A subject only means something on an internal 360 program.

        Setting one on a client program would silently file client answers
        under an employee's name.
        """
        for wave in self:
            if (
                wave.subject_user_id
                and wave.program_id.program_type != "internal"
            ):
                raise ValidationError(
                    _("Une personne évaluée ne se règle que sur un programme "
                      "de type « Feedback interne (360) ». Le programme "
                      "« %(program)s » est de type « %(type)s ».",
                      program=wave.program_id.name,
                      type=dict(
                          wave.program_id._fields["program_type"]
                          ._description_selection(self.env)
                      ).get(wave.program_id.program_type),
                      )
                )

    # ── Compute ──────────────────────────────────────────────────────────────

    def _compute_subject_summary(self):
        Feedback = self.env["bf.cx.feedback"]
        rated = self.filtered("subject_user_id")
        (self - rated).subject_summary = False
        if not rated:
            return
        # Grouped per WAVE, not per subject: the same person can be
        # reviewed by several waves over time, and each wave reports its
        # own round.
        summaries = Feedback._bf_cx_360_stats(
            [("wave_id", "in", rated.ids)], "wave_id"
        )
        for wave in rated:
            wave.subject_summary = Feedback._bf_cx_360_display(
                summaries.get(wave.id)
            )

    @api.depends("program_id")
    def _compute_campaign_id(self):
        for wave in self:
            if not wave.campaign_id:
                wave.campaign_id = wave.program_id.campaign_id

    def _compute_input_stats(self):
        grouped = {
            wave.id: {"total": 0, "done": 0}
            for wave in self
        }
        for wave, state, count in self.env["survey.user_input"]._read_group(
            [("bf_cx_wave_id", "in", self.ids), ("test_entry", "=", False)],
            ["bf_cx_wave_id", "state"],
            ["__count"],
        ):
            stats = grouped[wave.id]
            stats["total"] += count
            if state == "done":
                stats["done"] += count
        for wave in self:
            stats = grouped[wave.id]
            wave.invited_count = stats["total"]
            wave.completed_count = stats["done"]
            wave.completion_rate = (
                stats["done"] * 100.0 / stats["total"] if stats["total"] else 0.0
            )

    def _compute_nps_score(self):
        Feedback = self.env["bf.cx.feedback"]
        grouped = {
            wave.id: {"promoter": 0, "passive": 0, "detractor": 0}
            for wave in self
        }
        for wave, bucket, count in Feedback._read_group(
            [("wave_id", "in", self.ids), ("nps_bucket", "!=", False)],
            ["wave_id", "nps_bucket"],
            ["__count"],
        ):
            grouped[wave.id][bucket] += count
        for wave in self:
            stats = grouped[wave.id]
            scored = stats["promoter"] + stats["passive"] + stats["detractor"]
            wave.nps_score = (
                round((stats["promoter"] - stats["detractor"]) * 100.0 / scored)
                if scored
                else 0
            )

    # ── Actions ──────────────────────────────────────────────────────────────

    def action_send(self):
        """Create one tokenized answer per recipient and email the invites."""
        for wave in self:
            if wave.state == "closed":
                raise UserError(_("La vague « %s » est fermée.") % wave.name)
            program = wave.program_id
            if not program.survey_id:
                raise UserError(
                    _("Le programme « %s » n'a pas de sondage.") % program.name
                )
            template = program.invite_template_id
            if not template:
                raise UserError(
                    _("Le programme « %s » n'a pas de gabarit d'invitation.")
                    % program.name
                )
            already = wave.user_input_ids.partner_id
            to_invite = wave.partner_ids - already
            skipped = to_invite.filtered(lambda p: not p.email)
            to_invite -= skipped
            if program.program_type == "internal":
                # The anti-oversolicitation guard is a CLIENT concept: an
                # internal 360 pulse must neither block itself nor burn the
                # employee-partner's client solicitation budget.
                cooled = self.env["res.partner"]
            else:
                to_invite, cooled = to_invite._bf_cx_split_solicitable(
                    days=program.cooldown_days or None
                )
            if not to_invite:
                if wave.state == "draft":
                    if cooled:
                        raise UserError(
                            _("Aucun destinataire à inviter : %s sollicité(s) "
                              "il y a moins de %s jours (garde-fou "
                              "anti-sursollicitation, réglable dans les "
                              "paramètres).")
                            % (
                                ", ".join(cooled.mapped("display_name")),
                                self.env["ir.config_parameter"]
                                .sudo()
                                .get_param(
                                    "bf_cx.solicitation_cooldown_days", "30"
                                ),
                            )
                        )
                    raise UserError(
                        _("Aucun destinataire avec adresse courriel à inviter.")
                    )
                # Re-run on a sent wave with nothing new: just log, do not
                # rewrite the state nor claim "0 invitations sent".
                wave.message_post(
                    body=_("Aucun nouveau destinataire à inviter.")
                )
                continue
            for partner in to_invite:
                answer = program.survey_id._create_answer(
                    partner=partner,
                    check_attempts=False,
                    deadline=wave.deadline,
                    bf_cx_wave_id=wave.id,
                )
                template.send_mail(answer.id, force_send=False)
            body_lines = [
                _("%d invitation(s) envoyée(s).") % len(to_invite),
            ]
            if already:
                body_lines.append(
                    _("%d contact(s) déjà invité(s) dans cette vague, ignoré(s).")
                    % len(already & wave.partner_ids)
                )
            if skipped:
                body_lines.append(
                    _("Ignorés (aucune adresse courriel) : %s")
                    % ", ".join(skipped.mapped("display_name"))
                )
            if cooled:
                body_lines.append(
                    _("Reportés par les garde-fous (sollicitation récente, "
                      "liste à ne pas contacter, recouvrement ou courriel "
                      "bloqué) - le cron retentera : %s")
                    % ", ".join(cooled.mapped("display_name"))
                )
            if program.program_type != "internal":
                to_invite._bf_cx_mark_solicited()
            wave.message_post(body=Markup("<br/>").join(body_lines))
            vals = {"state": "sent"}
            if not wave.sent_date:
                vals["sent_date"] = fields.Datetime.now()
            wave.write(vals)
        return True

    def action_remind(self):
        """Re-send to non-respondents (short dedicated template when set)."""
        for wave in self:
            if wave.state != "sent":
                continue
            if wave.deadline and wave.deadline < fields.Datetime.now():
                # The survey refuses answers past the deadline: a reminder
                # would invite people to a dead link.
                wave.reminder_date = wave.reminder_date or fields.Datetime.now()
                wave.message_post(
                    body=_("Rappel non envoyé : la date limite de réponse "
                           "est passée.")
                )
                continue
            program = wave.program_id
            template = program.reminder_template_id or program.invite_template_id
            if not template:
                continue
            pending = wave.user_input_ids.filtered(
                lambda i: i.state != "done" and i.partner_id.email
            )
            for answer in pending:
                template.send_mail(answer.id, force_send=False)
            # A reminder is a real touch: it must count in the solicitation
            # budget (no cooldown CHECK though - blocking the single
            # reminder would defeat it).
            if program.program_type != "internal":
                pending.partner_id._bf_cx_mark_solicited()
            wave.reminder_date = fields.Datetime.now()
            wave.message_post(
                body=_("Rappel envoyé à %d non-répondant(s).") % len(pending)
            )
        return True

    def action_close(self):
        self.write({"state": "closed"})
        return True

    def action_reopen(self):
        self.write({"state": "sent"})
        return True

    def action_view_feedback(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Feedbacks - %s") % self.name,
            "res_model": "bf.cx.feedback",
            "view_mode": "list,kanban,form,graph,pivot",
            "domain": [("wave_id", "=", self.id)],
        }

    # ── Cron ─────────────────────────────────────────────────────────────────

    @api.model
    def _cron_send_reminders(self):
        """Daily wave upkeep: close expired waves, remind non-respondents,
        retry recipients that the outbound guards had deferred."""
        now = fields.Datetime.now()

        # 1. Close waves whose answer deadline has passed: they would
        #    otherwise stay 'sent' forever and get rescanned daily.
        expired = self.search(
            [("state", "=", "sent"), ("deadline", "<", now)]
        )
        for wave in expired:
            wave.action_close()
            wave.message_post(
                body=_("Vague fermée automatiquement : date limite de "
                       "réponse dépassée.")
            )

        # 2. Single reminder to non-respondents after program.reminder_days.
        waves = self.search(
            [
                ("state", "=", "sent"),
                ("reminder_date", "=", False),
                ("sent_date", "!=", False),
                ("program_id.reminder_days", ">", 0),
            ]
        )
        for wave in waves:
            elapsed = (now - wave.sent_date).days
            if elapsed >= wave.program_id.reminder_days:
                try:
                    wave.action_remind()
                except Exception:  # noqa: BLE001 - keep the cron alive per wave
                    _logger.exception(
                        "bf_cx: reminder failed for wave %s", wave.id
                    )

        # 3. Retry deferred recipients: a guard must DELAY, not silently
        #    drop (dropping the recently-contacted skews the sample).
        for wave in self.search([("state", "=", "sent")]):
            pending = (
                wave.partner_ids - wave.user_input_ids.partner_id
            ).filtered(lambda p: p.email)
            if not pending:
                continue
            allowed, _blocked = pending._bf_cx_split_solicitable(
                days=wave.program_id.cooldown_days or None
            )
            if not allowed:
                continue
            try:
                wave.action_send()
            except Exception:  # noqa: BLE001 - keep the cron alive per wave
                _logger.exception(
                    "bf_cx: deferred-recipient retry failed for wave %s",
                    wave.id,
                )
        return True
