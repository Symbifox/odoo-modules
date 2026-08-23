import json
import logging
import urllib.request
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import html2plaintext

_logger = logging.getLogger(__name__)

NTFY_RELAY_PARAM = "bf_helpdesk.ntfy_webhook_url"
NTFY_TIMEOUT_PARAM = "bf_helpdesk.ntfy_webhook_timeout"

# Cap the timesheet description lifted from a chatter note.
_TIMESHEET_DESC_MAX_LEN = 500


class HelpdeskTicket(models.Model):
    _inherit = "helpdesk.ticket"

    waiting_state = fields.Selection(
        selection=[
            ("client", "Attente — Client.e"),
            ("external", "Attente — Externe"),
        ],
        string="État d'attente",
        copy=False,
        tracking=True,
        help="Marque ce ticket comme en attente d'une partie tierce. Indépendant du stage : "
             "on peut être en stage 'En cours' ET en attente client.",
    )

    hour_bank_id = fields.Many2one(
        comodel_name="hour.bank.client",
        string="Banque d'heures",
        compute="_compute_hour_bank_id",
        store=True,
        readonly=True,
    )
    hour_bank_balance = fields.Float(
        string="Solde banque (h)",
        compute="_compute_hour_bank_balance",
    )
    hour_bank_low = fields.Boolean(
        string="Solde bas",
        compute="_compute_hour_bank_balance",
    )

    # ------------------------------------------------------------------
    # Timesheets — direct time entry on the ticket (BF-native; no OCA
    # helpdesk_mgmt_timesheet timer stack). Lines land on the ticket's
    # project so they feed the team hour bank (aggregated by project).
    # ------------------------------------------------------------------
    timesheet_ids = fields.One2many(
        comodel_name="account.analytic.line",
        inverse_name="ticket_id",
        string="Feuilles de temps",
    )
    total_hours = fields.Float(
        string="Temps total (h)",
        compute="_compute_total_hours",
        store=True,
        help="Somme des heures saisies sur ce ticket.",
    )

    @api.depends("timesheet_ids.unit_amount")
    def _compute_total_hours(self):
        for ticket in self:
            ticket.total_hours = sum(ticket.timesheet_ids.mapped("unit_amount"))

    @api.onchange("team_id")
    def _onchange_team_id_default_project(self):
        """Default the ticket project to the team's project / hour-bank project
        so timesheet lines feed the hour bank. Only fills when empty."""
        for ticket in self.filtered(lambda t: not t.project_id and t.team_id):
            project = (
                ticket.team_id.default_project_id
                or ticket.team_id.hour_bank_id.project_ids[:1]
            )
            if project:
                ticket.project_id = project.id

    @api.model
    def _bf_resolve_timesheet_employee(self):
        """Return the hr.employee for the connected user, or raise."""
        employee = self.env.user.employee_id or self.env["hr.employee"].search(
            [
                ("user_id", "=", self.env.uid),
                ("company_id", "in", self.env.companies.ids),
            ],
            limit=1,
        )
        if not employee:
            raise UserError(_("Aucun employé n'est associé à votre compte utilisateur."))
        return employee

    def action_bf_create_chatter_timesheet(self, duration_hours, body_html=""):
        """Create a timesheet line tied to this ticket from a chatter note.

        Named identically to bf_chatter_timesheet's ``project.task`` method so
        the shared OWL Composer patch can log time from a ticket's chatter with
        the same UX. Lines carry the ticket's project so they deduct from the
        team hour bank. Also callable directly (e.g. from the MCP bridge).
        """
        self.ensure_one()
        try:
            duration_hours = float(duration_hours or 0)
        except (TypeError, ValueError):
            raise ValidationError(_("Durée invalide."))
        if duration_hours <= 0:
            raise ValidationError(_("La durée doit être supérieure à 0."))

        project = self.project_id or self.team_id.hour_bank_id.project_ids[:1]
        if not project:
            raise UserError(_(
                "Ce ticket n'a pas de projet. Associez un projet au ticket "
                "(ou à l'équipe / la banque d'heures) avant de saisir du temps."
            ))
        if not project.allow_timesheets:
            raise UserError(_(
                "Les feuilles de temps ne sont pas activées sur le projet « %s ».",
                project.display_name,
            ))
        employee = self._bf_resolve_timesheet_employee()

        description = (html2plaintext(body_html or "") or "").strip() or self.name
        if len(description) > _TIMESHEET_DESC_MAX_LEN:
            description = description[: _TIMESHEET_DESC_MAX_LEN - 1].rstrip() + "…"

        line = self.env["account.analytic.line"].create({
            "name": description,
            "date": fields.Date.context_today(self),
            "unit_amount": duration_hours,
            "ticket_id": self.id,
            "project_id": project.id,
            "task_id": self.task_id.id if self.task_id else False,
            "employee_id": employee.id,
        })
        return {
            "id": line.id,
            "name": line.name,
            "unit_amount": line.unit_amount,
        }

    # ------------------------------------------------------------------
    # Branded client update — open the composer preloaded with a branded
    # template, editable, never auto-sent. When bluefox_branding is
    # installed its composer swaps mail.mail_notification_layout for the
    # branded shell; otherwise the stock layout is used (no hard dep).
    # ------------------------------------------------------------------
    def action_send_client_update(self):
        self.ensure_one()
        template = self.env.ref(
            "bf_helpdesk.mail_template_client_update", raise_if_not_found=False,
        )
        ctx = {
            "default_model": "helpdesk.ticket",
            "default_res_ids": self.ids,
            "default_composition_mode": "comment",
            "default_email_layout_xmlid": "mail.mail_notification_layout",
            "default_partner_ids": self.partner_id.ids,
        }
        if template:
            ctx["default_template_id"] = template.id
        return {
            "type": "ir.actions.act_window",
            "name": _("Mise à jour au client"),
            "res_model": "mail.compose.message",
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "new",
            "context": ctx,
        }

    # ------------------------------------------------------------------
    # Client portal access — surface the /my/ticket URL and whether the
    # client can actually see it. Subscribing the client as follower makes
    # the OCA portal list the ticket and routes stage-change emails to them.
    # No portal invite email is sent from here (outbound stays manual).
    # ------------------------------------------------------------------
    portal_ticket_url = fields.Char(
        string="Lien portail client",
        compute="_compute_portal_ticket_url",
    )
    partner_has_portal_access = fields.Boolean(
        string="Client a un accès portail",
        compute="_compute_partner_has_portal_access",
    )

    def _compute_portal_ticket_url(self):
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        for ticket in self:
            ticket.portal_ticket_url = (
                f"{base}/my/ticket/{ticket.id}" if ticket.id and base else False
            )

    @api.depends("partner_id")
    def _compute_partner_has_portal_access(self):
        portal_group = self.env.ref("base.group_portal", raise_if_not_found=False)
        internal_group = self.env.ref("base.group_user", raise_if_not_found=False)
        for ticket in self:
            granted = False
            for user in ticket.partner_id.user_ids:
                groups = user.groups_id
                if (portal_group and portal_group in groups) or (
                    internal_group and internal_group in groups
                ):
                    granted = True
                    break
            ticket.partner_has_portal_access = granted

    def action_subscribe_partner_follower(self):
        """Add the ticket's client as a follower (no portal invite email)."""
        self.ensure_one()
        if not self.partner_id:
            raise UserError(_("Ce ticket n'a pas de client à abonner."))
        self.message_subscribe(partner_ids=self.partner_id.ids)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Client abonné"),
                "message": _("%s suivra désormais ce ticket.")
                % self.partner_id.display_name,
                "type": "success",
                "sticky": False,
            },
        }

    # ------------------------------------------------------------------
    # Persona panel — read-only mirror of contact.persona for quick
    # composer hints when answering the ticket
    # ------------------------------------------------------------------
    persona_id = fields.Many2one(
        comodel_name="contact.persona",
        string="Persona",
        compute="_compute_persona_id",
        search="_search_persona_id",
        store=False,
    )

    @api.model
    def _search_persona_id(self, operator, value):
        Persona = self.env["contact.persona"].sudo()
        domain = [("id", operator, value)] if operator in ("=", "!=", "in", "not in") else [("id", operator, value)]
        partners = Persona.search(domain).mapped("partner_id")
        return [("partner_id", "in", partners.ids)]
    persona_addressing_style = fields.Selection(
        related="persona_id.addressing_style",
        string="Style d'adresse",
        readonly=True,
    )
    persona_preferred_salutation = fields.Char(
        related="persona_id.preferred_salutation",
        string="Salutation préférée",
        readonly=True,
    )
    persona_closing_formula = fields.Char(
        related="persona_id.closing_formula",
        string="Formule de clôture",
        readonly=True,
    )
    persona_tone_summary = fields.Selection(
        related="persona_id.tone_summary",
        string="Ton du contact",
        readonly=True,
    )
    persona_our_tone_summary = fields.Selection(
        related="persona_id.our_tone_summary",
        string="Notre ton",
        readonly=True,
    )
    persona_payer_quality = fields.Selection(
        related="persona_id.payer_quality",
        string="Qualité de paiement",
        readonly=True,
    )

    # ------------------------------------------------------------------
    # SLA — first response + resolution deadlines
    # ------------------------------------------------------------------
    sla_response_deadline = fields.Datetime(
        string="Échéance première réponse",
        compute="_compute_sla_deadlines",
        store=True,
    )
    sla_resolve_deadline = fields.Datetime(
        string="Échéance résolution",
        compute="_compute_sla_deadlines",
        store=True,
    )
    sla_response_breach = fields.Boolean(
        string="SLA réponse dépassée",
        compute="_compute_sla_breach",
    )
    sla_resolve_breach = fields.Boolean(
        string="SLA résolution dépassée",
        compute="_compute_sla_breach",
    )

    @api.depends("create_date", "team_id.sla_response_hours", "team_id.sla_resolve_hours")
    def _compute_sla_deadlines(self):
        for ticket in self:
            base = ticket.create_date
            if not base:
                ticket.sla_response_deadline = False
                ticket.sla_resolve_deadline = False
                continue
            response_hours = ticket.team_id.sla_response_hours or 0.0
            resolve_hours = ticket.team_id.sla_resolve_hours or 0.0
            ticket.sla_response_deadline = (
                base + timedelta(hours=response_hours)
            ) if response_hours else False
            ticket.sla_resolve_deadline = (
                base + timedelta(hours=resolve_hours)
            ) if resolve_hours else False

    @api.depends("sla_response_deadline", "sla_resolve_deadline",
                 "stage_id.closed", "message_ids", "closed_date")
    def _compute_sla_breach(self):
        now = fields.Datetime.now()
        for ticket in self:
            # Response breach: deadline passed, ticket still open, no outbound message from staff
            response_breach = False
            if (
                ticket.sla_response_deadline
                and ticket.sla_response_deadline < now
                and not ticket.stage_id.closed
            ):
                outbound = ticket.message_ids.filtered(
                    lambda m: m.message_type == "comment"
                    and m.author_id
                    and m.author_id != ticket.partner_id
                )
                response_breach = not outbound
            ticket.sla_response_breach = response_breach
            # Resolve breach: deadline passed and ticket still open
            resolve_breach = False
            if ticket.sla_resolve_deadline and ticket.sla_resolve_deadline < now:
                resolve_breach = not ticket.stage_id.closed
            ticket.sla_resolve_breach = resolve_breach

    @api.model
    def _cron_sla_breach_activity(self):
        """Daily cron — drop a follow-up activity on tickets newly in breach.

        Searches on the *stored* deadline datetime fields (not the non-stored
        breach booleans, which Odoo silently drops from the domain) and then
        re-validates the breach in Python to apply the staff-message check.
        """
        now = fields.Datetime.now()
        candidates = self.search([
            ("active", "=", True),
            ("stage_id.closed", "=", False),
            "|",
            ("sla_response_deadline", "<", now),
            ("sla_resolve_deadline", "<", now),
        ])
        for ticket in candidates:
            if not (ticket.sla_response_breach or ticket.sla_resolve_breach):
                continue
            existing = self.env["mail.activity"].search([
                ("res_model", "=", self._name),
                ("res_id", "=", ticket.id),
                ("summary", "=", "SLA dépassé"),
            ], limit=1)
            if existing:
                continue
            ticket.activity_schedule(
                act_type_xmlid="mail.mail_activity_data_todo",
                summary="SLA dépassé",
                note=(
                    "<p>Ce ticket a dépassé un SLA configuré sur l'équipe. "
                    "Vérifier et réagir.</p>"
                ),
                user_id=ticket.user_id.id or self.env.uid,
            )

    # ------------------------------------------------------------------
    # Macros
    # ------------------------------------------------------------------
    def action_apply_macro(self):
        """Open the macro picker wizard (single-select)."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Appliquer une macro",
            "res_model": "helpdesk.macro.apply.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_ticket_id": self.id},
        }

    # ------------------------------------------------------------------
    # Auto-tag — apply matching rules at creation
    # ------------------------------------------------------------------
    def _apply_auto_tag_rules(self):
        """Scan team rules against subject + description and add matching tags."""
        for ticket in self:
            if not ticket.team_id:
                continue
            rules = ticket.team_id.auto_tag_rule_ids.filtered(lambda r: r.active)
            if not rules:
                continue
            text = (ticket.name or "") + " " + (ticket.description or "")
            tag_ids = []
            for rule in rules:
                if rule._matches(text):
                    tag_ids.append(rule.tag_id.id)
            if tag_ids:
                ticket.write({"tag_ids": [(4, tid) for tid in tag_ids]})

    # ------------------------------------------------------------------
    # CSAT — auto-send survey on close
    # ------------------------------------------------------------------
    csat_user_input_id = fields.Many2one(
        comodel_name="survey.user_input",
        string="Réponse CSAT",
        copy=False,
        readonly=True,
    )
    csat_state = fields.Selection(
        related="csat_user_input_id.state",
        string="État CSAT",
        readonly=True,
    )

    def _send_csat_invite(self):
        self.ensure_one()
        team = self.team_id
        if not team or not team.csat_survey_id:
            return False
        if self.csat_user_input_id:
            return False  # already sent
        partner = self.partner_id
        email = self.partner_email or (partner.email if partner else None)
        if not email:
            return False
        survey = team.csat_survey_id.sudo()
        try:
            user_input = survey._create_answer(
                partner=partner if partner else False,
                email=email,
            )
        except Exception:
            _logger.exception(
                "bf_helpdesk: CSAT _create_answer failed for ticket %s", self.number,
            )
            return False
        self.write({"csat_user_input_id": user_input.id})
        template = self.env.ref(
            "survey.mail_template_user_input_invite", raise_if_not_found=False,
        )
        if template:
            template.sudo().send_mail(
                user_input.id, force_send=False,
                email_layout_xmlid="mail.mail_notification_light",
            )
        return user_input

    # ------------------------------------------------------------------
    # Knowledge matrix link — quick scope validation
    # ------------------------------------------------------------------
    knowledge_item_id = fields.Many2one(
        comodel_name="project.knowledge.item",
        string="Élément matrice",
        help="Lie ce ticket à un élément de la matrice de connaissances du projet "
             "pour valider l'alignement avec le scope du mandat.",
    )
    knowledge_matrix_id = fields.Many2one(
        comodel_name="project.knowledge.matrix",
        string="Matrice",
        related="knowledge_item_id.matrix_id",
        readonly=True,
        store=False,
    )
    knowledge_item_state = fields.Selection(
        related="knowledge_item_id.state",
        string="État de l'élément",
        readonly=True,
    )
    scope_aligned = fields.Selection(
        selection=[
            ("aligned", "Dans le scope"),
            ("pending", "Élément en attente"),
            ("out_of_scope", "Hors scope"),
            ("unset", "Non vérifié"),
        ],
        string="Validation scope",
        compute="_compute_scope_aligned",
        store=False,
    )

    # ------------------------------------------------------------------
    # Convert ticket → meeting.record
    # ------------------------------------------------------------------
    meeting_record_ids = fields.One2many(
        comodel_name="meeting.record",
        inverse_name="helpdesk_ticket_id",
        string="Rencontres liées",
    )
    meeting_record_count = fields.Integer(
        string="Nb. rencontres",
        compute="_compute_meeting_record_count",
    )

    @api.depends("meeting_record_ids")
    def _compute_meeting_record_count(self):
        for ticket in self:
            ticket.meeting_record_count = len(ticket.meeting_record_ids)

    def action_create_meeting_record(self):
        """Create a draft meeting.record from this ticket and open it.

        Pre-fills the meeting with the ticket title, links it back to the
        ticket and to the team's hour bank project (if any).
        """
        self.ensure_one()
        Meeting = self.env["meeting.record"]
        project = (
            self.knowledge_item_id.matrix_id.project_id
            or self.team_id.hour_bank_id.project_ids[:1]
            or self.env["project.project"].browse()
        )
        meeting = Meeting.create({
            "name": f"[{self.number}] {self.name}",
            "date": fields.Datetime.now(),
            "project_id": project.id if project else False,
            "helpdesk_ticket_id": self.id,
        })
        return {
            "type": "ir.actions.act_window",
            "res_model": "meeting.record",
            "res_id": meeting.id,
            "view_mode": "form",
            "target": "current",
        }

    # ------------------------------------------------------------------
    # Triage IA via the bf_llm gateway — one-shot, provider-agnostic
    # ------------------------------------------------------------------
    triage_state = fields.Selection(
        selection=[
            ("none", "Pas de triage"),
            ("pending", "En cours"),
            ("done", "Triage prêt"),
            ("error", "Erreur"),
        ],
        default="none",
        copy=False,
    )
    triage_suggestion_html = fields.Html(
        string="Suggestion IA",
        readonly=True,
        copy=False,
        sanitize=True,
    )
    triage_last_run = fields.Datetime(
        string="Dernier triage",
        readonly=True,
        copy=False,
    )

    def _triage_prompt(self):
        self.ensure_one()
        team_stages = self.team_id._get_applicable_stages().mapped("name") or ["Nouveau", "En cours", "Terminé"]
        team_users = self.team_id.user_ids.mapped("name") or ["—"]
        partner = self.partner_name or (self.partner_id.name if self.partner_id else "Inconnu")
        # Strip HTML from description to keep prompt small
        desc = self.description or ""
        desc_text = self.env["mail.render.mixin"]._replace_local_links(desc)
        return (
            "Tu es un.e adjoint.e helpdesk Blue Fox. Voici un nouveau ticket :\n\n"
            f"# Ticket {self.number}\n"
            f"**Sujet** : {self.name}\n"
            f"**Client** : {partner}\n"
            f"**Équipe** : {self.team_id.name}\n"
            f"**Stages disponibles** : {', '.join(team_stages)}\n"
            f"**Membres de l'équipe** : {', '.join(team_users)}\n\n"
            f"**Description (HTML)** :\n{desc_text}\n\n"
            "Réponds en français, en HTML simple (p, ul, li, strong), avec ces 3 sections :\n"
            "1. **Catégorisation** — 1 phrase qui résume le type de demande.\n"
            "2. **Stage suggéré** — 1 stage parmi la liste, avec justification.\n"
            "3. **Assignation suggérée** — 1 membre de l'équipe (ou « non-assigné si X »), avec justification.\n"
            "4. **Brouillon de première réponse** — 2-3 phrases qui acknowledge la demande "
            "et indiquent les prochaines étapes. Ton chaleureux mais concis.\n"
            "Pas de bla-bla. Pas d'introduction. Pas de conclusion."
        )

    def action_triage_with_claude(self):
        """Run AI triage on this ticket via the bf_llm gateway.

        Provider resolution, key handling (Fernet-encrypted in
        ``bf.llm.provider``), and the Anthropic/OpenAI HTTP transport now live
        in ``bf_llm`` — this module no longer holds an API URL or key.

        - When no LLM provider is configured, degrade gracefully: show a soft
          (non-blocking) notification, leave the ticket state untouched. The
          GenFox/``bf_claude_chat`` module stays a soft, optional dep; bf_llm
          provides its own encrypted key store, so triage works without it.
        - Transient/model errors come back on the normalized envelope
          (``res["error"]``): the suggestion field shows the error and the
          state goes to ``error`` so the user can retry.
        - Success → state ``done``, HTML stored, ``triage_last_run`` set.
        """
        self.ensure_one()
        prompt = self._triage_prompt()
        try:
            provider = self.env["bf.llm"].for_feature("triage")
        except UserError as exc:
            # No default LLM provider configured → soft degrade, no state change.
            _logger.info(
                "bf_helpdesk: triage skipped on ticket %s, no LLM provider: %s",
                self.number, exc,
            )
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Triage IA indisponible",
                    "message": str(exc),
                    "type": "warning",
                    "sticky": False,
                },
            }
        res = provider.chat(messages=[{"role": "user", "content": prompt}])
        if res.get("error"):
            _logger.warning(
                "bf_helpdesk: triage LLM error on ticket %s: %s",
                self.number, res["error"],
            )
            self.write({
                "triage_state": "error",
                "triage_suggestion_html": f"<p><strong>Erreur :</strong> {res['error']}</p>",
                "triage_last_run": fields.Datetime.now(),
            })
            return True
        self.write({
            "triage_state": "done",
            "triage_suggestion_html": res["text"] or "",
            "triage_last_run": fields.Datetime.now(),
        })
        return True

    def action_view_meeting_records(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "meeting.record",
            "view_mode": "list,form",
            "domain": [("helpdesk_ticket_id", "=", self.id)],
            "context": {"default_helpdesk_ticket_id": self.id},
        }

    @api.depends("knowledge_item_id", "knowledge_item_id.state")
    def _compute_scope_aligned(self):
        for ticket in self:
            if not ticket.knowledge_item_id:
                ticket.scope_aligned = "unset"
                continue
            state = ticket.knowledge_item_id.state
            if state in ("done", "accepted"):
                ticket.scope_aligned = "aligned"
            elif state in ("pending", "in_progress", "proposed"):
                ticket.scope_aligned = "pending"
            elif state in ("rejected", "na", "superseded"):
                ticket.scope_aligned = "out_of_scope"
            else:
                ticket.scope_aligned = "unset"

    @api.depends("partner_id")
    def _compute_persona_id(self):
        Persona = self.env["contact.persona"].sudo()
        partners = self.mapped("partner_id")
        if not partners:
            for ticket in self:
                ticket.persona_id = False
            return
        personas = Persona.search([("partner_id", "in", partners.ids)])
        by_partner = {p.partner_id.id: p.id for p in personas}
        for ticket in self:
            ticket.persona_id = by_partner.get(ticket.partner_id.id, False)

    def action_open_persona(self):
        self.ensure_one()
        if self.persona_id:
            return {
                "type": "ir.actions.act_window",
                "res_model": "contact.persona",
                "res_id": self.persona_id.id,
                "view_mode": "form",
                "target": "current",
            }
        # No persona yet — open create wizard pre-filled with the partner
        return {
            "type": "ir.actions.act_window",
            "res_model": "contact.persona",
            "view_mode": "form",
            "target": "current",
            "context": {"default_partner_id": self.partner_id.id},
        }

    @api.depends("team_id", "team_id.hour_bank_id")
    def _compute_hour_bank_id(self):
        for ticket in self:
            ticket.hour_bank_id = ticket.team_id.hour_bank_id or False

    # Decoupled from bluefox_branding: helpdesk tracking emails keep Odoo's
    # default notification layout (mail.mail_notification_light). The previous
    # override swapped in the branded bf_mail_layout; that override has been
    # removed so this module renders without the optional white-label module.

    @api.depends("hour_bank_id", "hour_bank_id.current_balance",
                 "team_id.hour_bank_alert_threshold_hours")
    def _compute_hour_bank_balance(self):
        for ticket in self:
            if ticket.hour_bank_id:
                ticket.hour_bank_balance = ticket.hour_bank_id.current_balance
                threshold = ticket.team_id.hour_bank_alert_threshold_hours or 0.0
                ticket.hour_bank_low = ticket.hour_bank_balance <= threshold
            else:
                ticket.hour_bank_balance = 0.0
                ticket.hour_bank_low = False

    # ------------------------------------------------------------------
    # ntfy critical hook
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        tickets = super().create(vals_list)
        for ticket in tickets:
            ticket._apply_auto_tag_rules()
            ticket._maybe_notify_ntfy_critical(reason="created")
        return tickets

    def write(self, vals):
        old_priority = {t.id: t.priority for t in self}
        before_closed = {t.id: bool(t.stage_id.closed) for t in self}
        res = super().write(vals)
        if "priority" in vals:
            for ticket in self:
                if old_priority.get(ticket.id) != ticket.priority:
                    ticket._maybe_notify_ntfy_critical(reason="escalated")
        if "stage_id" in vals:
            for ticket in self:
                if ticket.stage_id.closed and not before_closed.get(ticket.id):
                    ticket._send_csat_invite()
        return res

    # ------------------------------------------------------------------
    # IMAP gateway hardening
    # ------------------------------------------------------------------
    @api.model
    def message_new(self, msg, custom_values=None):
        """Defensive overrides on top of OCA's mail.thread message_new:

        - Drop autoresponder loops (Auto-Submitted, X-AutoReply, Precedence: bulk)
          — these create noise tickets when a customer's mail server bounces
          back our notification.
        - Strip the most obvious quoted-tail noise from the body so the ticket
          opens with the actual question, not the quoted previous reply.
        - Skip empty subjects with body length < 10 chars (likely cron blow-back).
        """
        if self._is_autoresponder(msg):
            _logger.info(
                "bf_helpdesk: dropping autoresponder mail-gateway message "
                "(subject=%r, from=%r)",
                msg.get("subject"), msg.get("from"),
            )
            # Return an empty record so mail.thread treats this as handled
            return self.browse()
        return super().message_new(msg, custom_values=custom_values)

    @api.model
    def _is_autoresponder(self, msg):
        """Detect common autoresponder/loop signals across SMTP and X- headers."""
        headers = msg.get("custom_headers") or {}
        # mail.thread normalizes some headers into msg dict; others are in raw headers
        auto_submitted = (
            (msg.get("auto-submitted") or headers.get("Auto-Submitted") or "")
            .strip().lower()
        )
        if auto_submitted and auto_submitted != "no":
            return True
        x_auto = headers.get("X-Auto-Response-Suppress") or headers.get("X-AutoReply")
        if x_auto:
            return True
        precedence = (headers.get("Precedence") or "").strip().lower()
        if precedence in ("bulk", "auto_reply", "list", "junk"):
            return True
        # Subject prefixes that almost always mean automation
        subject = (msg.get("subject") or "").lower()
        if subject.startswith(("auto:", "automatic reply", "out of office",
                               "absent du bureau", "réponse automatique",
                               "delivery status notification",
                               "undeliverable:", "mail delivery failed",
                               "mailer-daemon")):
            return True
        return False

    def _maybe_notify_ntfy_critical(self, reason="created"):
        self.ensure_one()
        team = self.team_id
        if not team or not team.ntfy_critical_enabled:
            return
        threshold = team.ntfy_priority_threshold or "3"
        # Selection lex order (str) is fine for "0".."3"
        if (self.priority or "0") < threshold:
            return
        url = self.env["ir.config_parameter"].sudo().get_param(NTFY_RELAY_PARAM)
        if not url:
            _logger.info(
                "bf_helpdesk: ntfy enabled on team %s but %s not set, skipping",
                team.name, NTFY_RELAY_PARAM,
            )
            return
        try:
            timeout = float(
                self.env["ir.config_parameter"].sudo().get_param(NTFY_TIMEOUT_PARAM, "5")
            )
        except (TypeError, ValueError):
            timeout = 5.0

        payload = {
            "_id": self.id,
            "_model": self._name,
            "_action": reason,
            "number": self.number,
            "name": self.name,
            "priority": self.priority,
            "team_id": team.id,
            "team_name": team.name,
            "partner_name": self.partner_name or (self.partner_id.name if self.partner_id else ""),
            "partner_email": self.partner_email or (self.partner_id.email if self.partner_id else ""),
            "url": self.get_base_url() + "/odoo/helpdesk-tickets/" + str(self.id),
        }
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status >= 400:
                    _logger.warning(
                        "bf_helpdesk: ntfy relay returned %s for ticket %s",
                        resp.status, self.number,
                    )
        except Exception:
            _logger.exception(
                "bf_helpdesk: ntfy relay POST failed for ticket %s", self.number,
            )
