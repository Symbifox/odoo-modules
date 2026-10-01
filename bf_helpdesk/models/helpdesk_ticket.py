import json
import logging
import re
import urllib.error
import urllib.request
from datetime import timedelta

from markupsafe import escape

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import email_normalize, format_datetime, formataddr, hmac, html2plaintext

from .dispatch_mark import DISPATCH_MARK

from .isolation import each_isolated

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
    # Calculés en sudo : seuls les usagers de projet lisent les banques
    # d'heures, et un agent d'assistance doit pouvoir ouvrir le billet.
    hour_bank_balance = fields.Float(
        string="Solde banque (h)",
        compute="_compute_hour_bank_balance",
        compute_sudo=True,
    )
    hour_bank_low = fields.Boolean(
        string="Solde bas",
        compute="_compute_hour_bank_balance",
        compute_sudo=True,
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
            "default_email_layout_xmlid": self.MAIL_LAYOUT,
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

    # ------------------------------------------------------------------
    # Parcours client — ce que le portail et l'accusé de réception disent
    # ------------------------------------------------------------------
    portal_status = fields.Char(
        string="Statut côté client",
        compute="_compute_portal_status",
        help="Le libellé client de l'étape, ou l'attente quand il y en a une.",
    )
    portal_status_hint = fields.Char(
        string="Précision côté client",
        compute="_compute_portal_status",
    )
    client_lang = fields.Char(
        string="Langue du client",
        help="Langue des courriels au client : la sienne, sinon celle du "
             "formulaire qu'il a rempli, sinon celle de la société.",
    )
    ack_sent_date = fields.Datetime(
        string="Accusé de réception envoyé",
        readonly=True,
        copy=False,
    )
    ack_expected_text = fields.Char(
        string="Délai annoncé",
        compute="_compute_ack_expected_text",
        help="La phrase de l'accusé qui annonce la première réponse.",
    )

    # Plafond d'accusés par adresse et par heure. Un répondeur qui ne porte
    # aucun en-tête d'automate crée un billet par réponse : sans plafond,
    # chaque accusé en relancerait un autre.
    ACK_MAX_PER_SENDER_HOUR = 5

    @api.depends("stage_id", "waiting_state", "first_response_date",
                 "sla_response_deadline")
    def _compute_portal_status(self):
        for ticket in self:
            stage = ticket.stage_id
            hint = ""
            if stage.closed:
                status = stage.portal_label or stage.name
            elif ticket.waiting_state == "client":
                status = _("Votre réponse est attendue")
                hint = _("Nous attendons votre réponse pour poursuivre. "
                         "Vous pouvez répondre ci-dessous ou par courriel.")
            elif ticket.waiting_state == "external":
                status = _("En attente d'un tiers")
                hint = _("Nous attendons un retour d'un fournisseur ou d'une "
                         "autre partie. Nous vous tiendrons au courant.")
            else:
                status = stage.portal_label or stage.name or ""
                if not ticket.first_response_date and ticket.sla_response_deadline:
                    hint = _("Première réponse prévue au plus tard le %s.",
                             ticket._bf_format_client_datetime(
                                 ticket.sla_response_deadline))
            ticket.portal_status = status
            ticket.portal_status_hint = hint

    @api.depends("sla_response_deadline", "team_id.sla_calendar_id", "client_lang")
    def _compute_ack_expected_text(self):
        for ticket in self:
            if not ticket.sla_response_deadline:
                ticket.ack_expected_text = ""
                continue
            when = ticket._bf_format_client_datetime(ticket.sla_response_deadline)
            ticket.ack_expected_text = (
                "You will receive a first reply by %s." % when
                if ticket._bf_client_is_en()
                else "Vous recevrez une première réponse au plus tard le %s." % when
            )

    def _bf_client_is_en(self):
        self.ensure_one()
        return (self.client_lang or "").startswith("en")

    def _bf_format_client_datetime(self, value):
        """Une date lisible par le client, dans le fuseau de l'équipe."""
        self.ensure_one()
        # Le calendrier de l'équipe (resource.calendar) est illisible au
        # portail : sans sudo, /my/tickets tombait en 403.
        tz = (
            self.sudo().team_id.sla_calendar_id.tz
            or self.sudo().partner_id.tz
            or self.env.user.tz
            or "America/Toronto"
        )
        lang = self.client_lang or self.sudo().partner_id.lang or self.env.lang
        return format_datetime(
            self.with_context(lang=lang).env, value, tz=tz,
            dt_format="d MMMM 'à' H'h'mm" if (lang or "").startswith("fr")
            else "MMMM d 'at' h:mm a",
        )

    def _bf_ack_skip_reason(self):
        """Pourquoi ce billet ne reçoit pas d'accusé, ou False s'il en reçoit un."""
        self.ensure_one()
        team = self.team_id
        if self.ack_sent_date:
            return "déjà envoyé"
        if not team or not self.channel_id or self.channel_id not in team.ack_channel_ids:
            return "canal non retenu"
        if self.stage_id.closed:
            return "billet fermé"
        # L'étape initiale qui porte un gabarit envoie déjà son propre courriel.
        if self.stage_id.mail_template_id:
            return "gabarit d'étape"
        email = email_normalize(self.partner_email or self.partner_id.email or "")
        if not email:
            return "aucune adresse"
        own = {
            email_normalize(a) for a in (
                team.alias_id.display_name, self.company_id.email,
            ) if a
        }
        if email in own:
            return "adresse de l'équipe"
        since = fields.Datetime.now() - timedelta(hours=1)
        recent = self.sudo().search_count([
            ("id", "!=", self.id),
            ("ack_sent_date", ">=", since),
            ("partner_email", "ilike", email),
        ])
        if recent >= self.ACK_MAX_PER_SENDER_HOUR:
            return "plafond horaire"
        return False

    def _bf_send_ack(self):
        """Accusé de réception : une fois par billet, jamais une réponse.

        Le courriel part par la file (mail.mail), sans message au fil : il ne
        compte donc pas comme première réponse au SLA. Une note interne garde
        la trace de l'envoi au billet. Les en-têtes d'automate évitent qu'un
        répondeur, en face, ne réponde à l'accusé.
        """
        template = self.env.ref(
            "bf_helpdesk.mail_template_ticket_ack", raise_if_not_found=False,
        )
        if not template or self.env.context.get("bf_helpdesk_no_ack"):
            return
        for ticket in self:
            reason = ticket._bf_ack_skip_reason()
            if reason:
                _logger.debug("bf_helpdesk: pas d'accusé pour %s (%s)",
                              ticket.number, reason)
                continue
            if ticket._bf_send_client_mail(
                template, "auto-replied",
                _("Accusé de réception envoyé à %s."),
            ):
                ticket.sudo().ack_sent_date = fields.Datetime.now()

    def _bf_send_client_mail(self, template, auto_submitted, note):
        """Envoyer un courriel automatique au client, avec une trace au billet.

        Par la file (mail.mail) et non par le fil : ce n'est pas une réponse,
        ni pour le SLA ni pour les relances. Le mail.mail porte tout de même
        son mail.message (sans l'afficher au fil) : une réponse du client
        revient donc sur ce billet, pas dans un nouveau. `note` reçoit
        l'adresse en %s. Rend True si le courriel est en file.
        """
        self.ensure_one()
        email = self.partner_email or self.partner_id.email
        if not email:
            return False
        try:
            template.sudo().send_mail(
                self.id,
                force_send=False,
                email_layout_xmlid=self.MAIL_LAYOUT,
                email_values={"headers": repr({
                    "Auto-Submitted": auto_submitted,
                    "X-Auto-Response-Suppress": "All",
                })},
            )
        except Exception:
            _logger.exception(
                "bf_helpdesk: échec du courriel %s pour le billet %s",
                template.name, self.number,
            )
            return False
        self.sudo().message_post(
            body=note % email,
            message_type="notification",
            subtype_xmlid="mail.mt_note",
        )
        return True

    # ------------------------------------------------------------------
    # Relances d'un billet en attente du client (« bump, bump, solve »)
    # ------------------------------------------------------------------
    # Deux relances, puis la fermeture annoncée, si l'équipe les a activées.
    # Chaque délai court depuis le plus récent de : début de l'attente,
    # dernière réponse publique d'un agent, dernière relance. Une réponse du
    # client arrête tout (et rouvre un billet fermé par les relances).
    reminder_excluded = fields.Boolean(
        string="Sans relance automatique",
        tracking=True,
        copy=False,
        help="Aucune relance ni fermeture automatique sur ce billet, "
             "même si l'équipe les a activées.",
    )
    reminder_count = fields.Integer(
        string="Relances envoyées",
        readonly=True,
        copy=False,
    )
    reminder_last_date = fields.Datetime(
        string="Dernière relance",
        readonly=True,
        copy=False,
    )
    last_staff_reply_date = fields.Datetime(
        string="Dernière réponse de l'équipe",
        readonly=True,
        copy=False,
    )
    reminder_auto_closed = fields.Boolean(
        string="Fermé faute de réponse",
        readonly=True,
        copy=False,
        help="Fermé par les relances. Une réponse du client le rouvre.",
    )
    reminder_next_date = fields.Datetime(
        string="Prochaine relance",
        compute="_compute_reminder_next_date",
    )

    REMINDER_STEPS = 2  # relances avant la fermeture

    def _bf_reminder_applies(self):
        self.ensure_one()
        return bool(
            self.team_id.reminder_enabled
            and self.waiting_state == "client"
            and not self.stage_id.closed
            and not self.reminder_excluded
            and self.reminder_count <= self.REMINDER_STEPS
        )

    def _bf_reminder_due_date(self):
        """Échéance de la prochaine étape du cycle, ou False."""
        self.ensure_one()
        if not self._bf_reminder_applies():
            return False
        team = self.team_id
        days = (
            team.reminder_first_days,
            team.reminder_second_days,
            team.reminder_close_days,
        )[self.reminder_count]
        anchor = max(filter(None, (
            self.sla_paused_since or self.write_date,
            self.last_staff_reply_date,
            self.reminder_last_date,
        )))
        calendar = team.sla_calendar_id
        hours_per_day = (calendar.hours_per_day or 8.0) if calendar else 24.0
        return self._sla_add_hours(team, anchor, max(days, 1) * hours_per_day)

    @api.depends("waiting_state", "reminder_count", "reminder_last_date",
                 "last_staff_reply_date", "reminder_excluded",
                 "team_id.reminder_enabled")
    def _compute_reminder_next_date(self):
        for ticket in self:
            ticket.reminder_next_date = (
                ticket._bf_reminder_due_date() if ticket.id else False
            )

    def _bf_reminder_step(self):
        """Jouer l'étape due : relance 1, relance 2, puis fermeture."""
        self.ensure_one()
        team = self.team_id
        now = fields.Datetime.now()
        if self.reminder_count < self.REMINDER_STEPS:
            template = self.env.ref(
                "bf_helpdesk.mail_template_waiting_reminder",
                raise_if_not_found=False,
            )
            if not template or not self._bf_send_client_mail(
                template, "auto-generated",
                _("Relance %s envoyée à %%s.", self.reminder_count + 1),
            ):
                # Pas d'adresse ou envoi en échec : on n'insiste pas à
                # chaque passage de la tâche, l'agent reprend la main.
                self.sudo().write({"reminder_count": self.REMINDER_STEPS + 1})
                self._bf_reminder_hand_back(_("Relance impossible : aucune "
                                              "adresse ou envoi en échec."))
                return
            self.sudo().write({
                "reminder_count": self.reminder_count + 1,
                "reminder_last_date": now,
            })
            return
        stage = team.reminder_close_stage_id
        if not stage:
            self.sudo().write({"reminder_count": self.REMINDER_STEPS + 1})
            self._bf_reminder_hand_back(_(
                "Le client n'a pas répondu aux deux relances. L'équipe n'a "
                "pas d'étape de fermeture automatique : à fermer ou relancer "
                "à la main."))
            return
        self._bf_skip_stage_template()
        self.sudo().with_context(bf_helpdesk_no_csat=True).write({
            "stage_id": stage.id,
            "waiting_state": False,
            "reminder_count": self.REMINDER_STEPS + 1,
            "reminder_auto_closed": True,
        })
        template = self.env.ref(
            "bf_helpdesk.mail_template_waiting_autoclose",
            raise_if_not_found=False,
        )
        if template:
            self._bf_send_client_mail(
                template, "auto-generated",
                _("Fermé faute de réponse ; avis envoyé à %s. "
                  "Une réponse du client rouvrira le billet."),
            )

    SKIP_STAGE_TEMPLATE_KEY = "bf_helpdesk.skip_stage_template"

    def _bf_skip_stage_template(self):
        """Pour ces billets, ne pas envoyer le gabarit de l'étape à la prochaine écriture.

        Quand nous envoyons nous-mêmes le courriel de fermeture (relances,
        incident), le gabarit de l'étape en ferait un second. Le contexte ne
        suffit pas : le suivi s'écrit au flush, avec un contexte nettoyé. On
        passe donc par les données de la transaction.
        """
        data = self.env.cr.precommit.data.setdefault(self.SKIP_STAGE_TEMPLATE_KEY, set())
        data.update(self.ids)

    def _track_template(self, changes):
        res = super()._track_template(changes)
        skip = self.env.cr.precommit.data.get(self.SKIP_STAGE_TEMPLATE_KEY) or set()
        if self[:1].id in skip:
            res.pop("stage_id", None)
        return res

    def _bf_reminder_hand_back(self, note):
        self.ensure_one()
        self.sudo().activity_schedule(
            act_type_xmlid="mail.mail_activity_data_todo",
            summary=_("Sans réponse du client"),
            note=note,
            user_id=self.user_id.id or self.env.uid,
        )

    @api.model
    def _cron_waiting_reminders(self):
        """Tâche horaire : relances et fermetures dues des billets en attente."""
        tickets = self.search([
            ("waiting_state", "=", "client"),
            ("stage_id.closed", "=", False),
            ("reminder_excluded", "=", False),
            ("team_id.reminder_enabled", "=", True),
            ("reminder_count", "<=", self.REMINDER_STEPS),
        ])
        now = fields.Datetime.now()

        def _step(ticket):
            due = ticket._bf_reminder_due_date()
            if due and due <= now:
                ticket._bf_reminder_step()
        each_isolated(tickets, _step, "bf_helpdesk relances", budget_s=300)

    # ------------------------------------------------------------------
    # Courriels : gabarit maître, sujet stable, historique cité
    # ------------------------------------------------------------------
    email_thread_subject = fields.Char(
        string="Sujet du fil de courriel",
        readonly=True,
        copy=False,
        help="Le sujet d'origine, figé à la création. Les courriels le "
             "reprennent même si le billet est renommé : un sujet qui change "
             "casse le fil dans la messagerie du client.",
    )

    MAIL_LAYOUT = "bf_helpdesk.mail_layout_helpdesk"
    # Gabarits génériques que le gabarit maître remplace sur un billet. Un
    # gabarit propre à un autre usage (facture, devis) n'arrive jamais ici.
    GENERIC_LAYOUTS = {
        False, None, "",
        "mail.mail_notification_layout",
        "mail.mail_notification_light",
        "mail.mail_notification_layout_with_responsible_signature",
        "bluefox_branding.bf_mail_layout",
        "bluefox_branding.bf_mail_layout_with_signature",
    }
    HISTORY_SIZE = 3
    _SUBJECT_TOKEN = re.compile(r"\[([A-Z]{1,8}[0-9]{3,})\]")

    def _bf_email_from(self):
        """Expéditeur des gabarits clients : un nom, pas une adresse nue.

        L'adresse est l'alias de l'équipe, sinon celle de la société ; le nom
        est « Société — Équipe ». Sans nom, le client lisait « bonjour » ou
        « bonjour@exemple.com ».
        """
        self.ensure_one()
        company = self.company_id or self.env.company
        alias = self.team_id.alias_id
        address = alias.alias_full_name if alias and alias.alias_name else ""
        if "@" not in (address or ""):
            # Alias sans domaine (aucun domaine d'alias sur la base) : pas une adresse.
            address = company.email
        if not address:
            return ""
        name = "%s — %s" % (company.name, self.team_id.name) if self.team_id else company.name
        return formataddr((name, address))

    def _message_compute_subject(self):
        self.ensure_one()
        return "[%s] %s" % (self.number, self.email_thread_subject or self.name)

    def _notify_by_email_prepare_rendering_context(self, message, msg_vals=False,
                                                   model_description=False,
                                                   force_email_company=False,
                                                   force_email_lang=False):
        values = super()._notify_by_email_prepare_rendering_context(
            message, msg_vals=msg_vals, model_description=model_description,
            force_email_company=force_email_company,
            force_email_lang=force_email_lang,
        )
        values["bf_history"] = self._bf_mail_history(message) if len(self) == 1 else []
        # Envoyé par le compte système (gabarit d'étape, tâche planifiée) : pas
        # de signature, qui se lisait « -- System ».
        root = self.env.ref("base.partner_root", raise_if_not_found=False)
        if root and message.author_id == root:
            values["signature"] = ""
        return values

    def _bf_mail_history(self, message):
        """Les derniers messages publics avant `message`, du plus récent au plus ancien."""
        self.ensure_one()
        if not message or not message.id or message.is_internal or message.subtype_id.internal:
            return self.env["mail.message"]
        return self.env["mail.message"].sudo().search([
            ("model", "=", self._name),
            ("res_id", "=", self.id),
            ("id", "<", message.id),
            ("message_type", "in", ("comment", "email")),
            ("is_internal", "=", False),
            ("subtype_id.internal", "=", False),
        ], order="id desc", limit=self.HISTORY_SIZE)

    def _notify_by_email_render_layout(self, message, recipients_group,
                                       msg_vals=False, render_values=None):
        layout = msg_vals.get("email_layout_xmlid") if msg_vals else message.email_layout_xmlid
        if layout in self.GENERIC_LAYOUTS:
            msg_vals = dict(msg_vals or {}, email_layout_xmlid=self.MAIL_LAYOUT)
        return super()._notify_by_email_render_layout(
            message, recipients_group, msg_vals=msg_vals,
            render_values=render_values,
        )

    def _creation_subtype(self):
        if self.env.context.get("bf_helpdesk_rerouted"):
            return self.env.ref("mail.mt_comment")
        return super()._creation_subtype()

    @api.model
    def _bf_ticket_from_subject(self, msg):
        """Le billet que désigne l'étiquette [HT00012] du sujet, si l'envoyeur y a droit.

        Secours quand les en-têtes de fil sont perdus (messagerie qui les
        retire, transfert). Seuls le client du billet et ses abonnés peuvent
        s'y rattacher par le sujet : sinon, deviner un numéro suffirait pour
        écrire dans le billet d'un autre.
        """
        match = self._SUBJECT_TOKEN.search(msg.get("subject") or "")
        sender = email_normalize(msg.get("email_from") or msg.get("from") or "")
        if not match or not sender:
            return self.browse()
        ticket = self.sudo().search([("number", "=", match.group(1))], limit=1)
        if not ticket:
            return self.browse()
        # Le client et ses abonnés CLIENTS seulement : l'adresse d'un agent
        # se contrefait trop aisément dans un From.
        client_followers = ticket.message_partner_ids.filtered(
            lambda p: not any(not u.share for u in p.user_ids))
        allowed = {
            email_normalize(e) for e in (
                [ticket.partner_email, ticket.partner_id.email]
                + client_followers.mapped("email")
            ) if e
        }
        return ticket if sender in allowed else self.browse()

    # ------------------------------------------------------------------
    # Notifications au client : résumé quotidien, courriels coupés par billet
    # ------------------------------------------------------------------
    client_muted_partner_ids = fields.Many2many(
        "res.partner", "helpdesk_ticket_muted_partner_rel", "ticket_id",
        "partner_id", string="Courriels coupés pour",
        help="Contacts qui ne reçoivent plus les réponses de ce billet par "
             "courriel. Ils reçoivent quand même les demandes d'information "
             "et la résolution, et voient tout au portail.",
    )

    def _bf_notification_is_mandatory(self):
        """Envois qui passent toujours : demande d'information, résolution."""
        self.ensure_one()
        return bool(
            self.env.context.get("bf_helpdesk_mandatory")
            or self.waiting_state == "client"
            or self.stage_id.closed
        )

    def _bf_is_agent_public_message(self, message, msg_vals):
        msg_vals = msg_vals or {}
        message_type = msg_vals.get("message_type") or message.message_type
        if message_type not in ("comment", "email"):
            return False
        subtype = self.env["mail.message.subtype"].browse(
            msg_vals.get("subtype_id") or message.subtype_id.id)
        if msg_vals.get("is_internal", message.is_internal) or subtype.internal:
            return False
        author = self.env["res.partner"].browse(
            msg_vals.get("author_id") or message.author_id.id)
        return any(not u.share for u in author.sudo().user_ids)

    def _bf_agent_event(self, message, msg_vals):
        """L'événement d'assistance que porte ce message, pour la matrice des agents."""
        # Posé par notre propre code (assignation, échéances). Un client au
        # portail peut passer un contexte à /mail/message/post : on l'ignore.
        event = self.env.context.get("bf_hd_event") if not self.env.user.share else False
        if event:
            return event
        msg_vals = msg_vals or {}
        subtype_id = msg_vals.get("subtype_id") or message.subtype_id.id
        if subtype_id and subtype_id == self.env["ir.model.data"]._xmlid_to_res_id(
                "helpdesk_mgmt.hlp_tck_created"):
            return "new_ticket"
        if message and message.id and self._bf_is_client_reply(message):
            return "client_reply"
        return False

    def _bf_agent_summary(self, event, message=None):
        self.ensure_one()
        name = self.email_thread_subject or self.name
        if event == "new_ticket":
            return _("Nouveau billet : %(name)s (%(team)s)", name=name, team=self.team_id.name or "")
        if event == "assigned":
            return _("Billet assigné : %s", name)
        if event == "client_reply":
            author = message.author_id.name if message else self.partner_id.name
            return _("Réponse de %(author)s : %(name)s", author=author or "", name=name)
        if event == "sla_risk":
            return _("SLA à risque : %s", name)
        if event == "sla_breach":
            return _("SLA dépassé : %s", name)
        return name

    def _message_auto_subscribe_notify(self, partner_ids, template):
        # L'avis « vous avez été assigné » d'Odoo passe par la matrice.
        return super(
            HelpdeskTicket, self.with_context(bf_hd_event="assigned")
        )._message_auto_subscribe_notify(partner_ids, template)

    def _notify_get_recipients(self, message, msg_vals, **kwargs):
        recipients = super()._notify_get_recipients(message, msg_vals, **kwargs)
        if len(self) != 1 or self.env.context.get("bf_hd_dispatch") is DISPATCH_MARK:
            return recipients

        # Agents : ceux qui ont une préférence pour cet événement sortent des
        # destinataires ordinaires et passent par le répartiteur.
        event = self._bf_agent_event(message, msg_vals)
        if event:
            agent_pids = [r["id"] for r in recipients if r.get("type") == "user"]
            agents = self.env["res.users"].sudo().search([
                ("partner_id", "in", agent_pids), ("share", "=", False)])
            if event == "new_ticket":
                # « Nouveau billet dans mes équipes » : un membre de l'équipe qui
                # l'a demandé est avisé même s'il ne suit pas l'équipe.
                author_id = (msg_vals or {}).get("author_id") or message.author_id.id
                members = self.sudo().team_id.user_ids.filtered(
                    lambda u: not u.share and u.partner_id.id != author_id) - agents
                prefs = self.env["helpdesk.notify.pref"].sudo()._for(members, event)
                agents |= members.filtered(lambda u: u.id in prefs)
            if agents:
                untouched = self.env["helpdesk.agent.notify.item"]._dispatch(
                    self, agents, event, self._bf_agent_summary(event, message))
                handled = set((agents - untouched).partner_id.ids)
                recipients = [r for r in recipients if r["id"] not in handled]

        # Clients : résumé quotidien et courriels coupés, sauf envoi obligatoire.
        if not self._bf_is_agent_public_message(message, msg_vals):
            return recipients
        if self._bf_notification_is_mandatory():
            return recipients
        muted = set(self.client_muted_partner_ids.ids)
        client_ids = [r["id"] for r in recipients if r.get("type") != "user"]
        daily = set(self.env["res.partner"].sudo().browse(client_ids).filtered(
            lambda p: p.helpdesk_notify_mode == "daily").ids)
        kept, queued = [], []
        for rdata in recipients:
            pid = rdata["id"]
            if rdata.get("type") == "user":
                kept.append(rdata)
            elif pid in muted:
                continue
            elif pid in daily:
                queued.append(pid)
            else:
                kept.append(rdata)
        if queued and message and message.id:
            self.env["helpdesk.client.digest.item"].sudo().create([
                {"partner_id": pid, "ticket_id": self.id, "message_id": message.id}
                for pid in queued
            ])
        return kept

    def _bf_mute_token(self, partner_id):
        self.ensure_one()
        return hmac(self.env(su=True), "bf_helpdesk-mute", (self.id, int(partner_id)))

    def _bf_mute_url(self, partner_id):
        self.ensure_one()
        return "%s/helpdesk/courriels/%s/%s/%s" % (
            self.get_base_url(), self.id, int(partner_id), self._bf_mute_token(partner_id))

    def _bf_set_muted(self, partner, muted):
        self.ensure_one()
        self.sudo().write({"client_muted_partner_ids": [(4 if muted else 3, partner.id)]})
        self.sudo().message_post(
            body=(_("%s ne reçoit plus les réponses de ce billet par courriel.")
                  if muted else
                  _("%s reçoit de nouveau les réponses de ce billet par courriel."))
            % partner.display_name,
            message_type="notification",
            subtype_xmlid="mail.mt_note",
        )

    # ------------------------------------------------------------------
    # Articles d'aide liés au billet
    # ------------------------------------------------------------------
    article_ids = fields.Many2many(
        "helpdesk.article", "helpdesk_ticket_article_rel", "ticket_id",
        "article_id", string="Articles d'aide",
        help="Articles envoyés au client ou qui décrivent ce problème.",
    )

    def action_create_article(self):
        """Préparer un article d'aide depuis ce billet, non publié.

        Le contenu part de la description du client et de la dernière réponse
        publique de l'équipe : c'est souvent tout l'article, à nettoyer des
        noms et des détails propres à ce client avant de publier.
        """
        self.ensure_one()
        reply = self.message_ids.filtered(
            lambda m: self._bf_is_staff_public_reply(m)
        ).sorted(lambda m: (m.date, m.id))[-1:]
        body = (
            "<h2>%s</h2>%s<h2>%s</h2>%s" % (
                escape(_("Le problème")), self.description or "",
                escape(_("La solution")),
                reply.body or "<p>%s</p>" % escape(_("À rédiger.")),
            )
        )
        article = self.env["helpdesk.article"].create({
            "name": self.name,
            "body": body,
            "team_ids": [(6, 0, self.team_id.ids)],
            "source_ticket_id": self.id,
            "ticket_ids": [(4, self.id)],
            "is_published": False,
        })
        return {
            "type": "ir.actions.act_window",
            "res_model": "helpdesk.article",
            "res_id": article.id,
            "view_mode": "form",
            "target": "current",
        }

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
        # La recherche lit les personas en sudo, avec l'opérateur et la valeur de
        # l'appelant : sans ce contrôle, un client au portail ou un agent sans le
        # rôle persona comptait les clients par qualité de payeur ou par ton.
        if not self.env.su and not self.env.user.has_group("bf_persona.group_persona_user"):
            raise AccessError(_("La recherche par persona est réservée au rôle persona."))
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
    # SLA — première réponse et résolution
    # ------------------------------------------------------------------
    # Horaire d'affaires : si l'équipe a un calendrier, les échéances se
    # comptent en heures ouvrées, congés du calendrier compris. Sans
    # calendrier, elles restent en heures civiles, comme avant la 18.0.4.6.0.
    # Pause : le temps passé en « Attente — Client.e » ne compte pas contre la
    # résolution. La première réponse, elle, ne se met jamais en pause.
    # À risque : il reste moins de SLA_RISK_RATIO du temps alloué à l'horloge
    # qui court (réponse tant qu'il n'y en a pas, résolution ensuite).
    first_response_date = fields.Datetime(
        string="Première réponse",
        readonly=True,
        copy=False,
        help="Premier message public d'un membre de l'équipe. "
             "Une note interne ne compte pas.",
    )
    sla_paused_since = fields.Datetime(
        string="SLA en pause depuis",
        readonly=True,
        copy=False,
    )
    sla_paused_hours = fields.Float(
        string="Heures en pause (SLA)",
        readonly=True,
        copy=False,
        help="Heures, ouvrées si l'équipe a un horaire, passées en attente "
             "du client. Elles repoussent l'échéance de résolution.",
    )
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
    sla_state = fields.Selection(
        selection=[
            ("none", "Sans SLA"),
            ("ok", "Dans les délais"),
            ("at_risk", "À risque"),
            ("paused", "En pause"),
            ("breached", "Dépassé"),
            ("met", "Respecté"),
        ],
        string="État SLA",
        compute="_compute_sla_state",
        store=True,
        index=True,
        help="Recalculé à chaque changement du billet et toutes les heures "
             "par la tâche planifiée, puisque le temps qui passe ne déclenche "
             "aucun recalcul.",
    )
    sla_response_breach = fields.Boolean(
        string="SLA réponse dépassée",
        compute="_compute_sla_breach",
    )
    sla_resolve_breach = fields.Boolean(
        string="SLA résolution dépassée",
        compute="_compute_sla_breach",
    )

    sla_notified_state = fields.Char(
        readonly=True, copy=False,
        help="Dernier état SLA signalé aux agents : évite de signaler deux "
             "fois « à risque » ou « dépassé ».",
    )

    SLA_RISK_RATIO = 0.25

    @api.model
    def _sla_add_hours(self, team, start, hours):
        """Échéance à `hours` heures de `start`, ouvrées si l'équipe a un horaire."""
        calendar = team.sla_calendar_id
        if calendar:
            deadline = calendar.plan_hours(hours, start, compute_leaves=True)
            if deadline:
                return deadline
            _logger.warning(
                "Horaire %s : impossible de planifier %s h depuis %s, "
                "repli sur les heures civiles.", calendar.name, hours, start,
            )
        return start + timedelta(hours=hours)

    @api.model
    def _sla_hours_between(self, team, start, end):
        """Heures écoulées entre deux instants, ouvrées si l'équipe a un horaire."""
        if not start or not end or end <= start:
            return 0.0
        calendar = team.sla_calendar_id
        if calendar:
            return calendar.get_work_hours_count(start, end, compute_leaves=True)
        return (end - start).total_seconds() / 3600.0

    @api.depends(
        "create_date",
        "sla_paused_hours",
        "team_id.sla_response_hours",
        "team_id.sla_resolve_hours",
        "team_id.sla_calendar_id",
    )
    def _compute_sla_deadlines(self):
        for ticket in self:
            base = ticket.create_date
            team = ticket.team_id
            response_hours = team.sla_response_hours or 0.0
            resolve_hours = team.sla_resolve_hours or 0.0
            ticket.sla_response_deadline = (
                self._sla_add_hours(team, base, response_hours)
                if base and response_hours else False
            )
            ticket.sla_resolve_deadline = (
                self._sla_add_hours(
                    team, base, resolve_hours + (ticket.sla_paused_hours or 0.0),
                )
                if base and resolve_hours else False
            )

    def _sla_remaining_ratio(self, deadline, allotted_hours, now):
        """Part du temps alloué qui reste avant `deadline` (0 si dépassé)."""
        self.ensure_one()
        if not allotted_hours:
            return 1.0
        remaining = self._sla_hours_between(self.team_id, now, deadline)
        return max(remaining, 0.0) / allotted_hours

    @api.depends(
        "sla_response_deadline",
        "sla_resolve_deadline",
        "first_response_date",
        "waiting_state",
        "stage_id.closed",
        "closed_date",
    )
    def _compute_sla_state(self):
        now = fields.Datetime.now()
        for ticket in self:
            response_dl = ticket.sla_response_deadline
            resolve_dl = ticket.sla_resolve_deadline
            if not response_dl and not resolve_dl:
                ticket.sla_state = "none"
                continue
            first = ticket.first_response_date
            if ticket.stage_id.closed:
                end = ticket.closed_date or now
                late_response = response_dl and (first or end) > response_dl
                late_resolve = resolve_dl and end > resolve_dl
                ticket.sla_state = (
                    "breached" if late_response or late_resolve else "met"
                )
                continue
            # Réponse en retard : dépassement définitif, même après coup.
            if response_dl and (first or now) > response_dl:
                ticket.sla_state = "breached"
                continue
            if ticket.waiting_state == "client":
                ticket.sla_state = "paused"
                continue
            if resolve_dl and now > resolve_dl:
                ticket.sla_state = "breached"
                continue
            # Horloge qui court : la réponse tant qu'il n'y en a pas.
            if response_dl and not first:
                ratio = ticket._sla_remaining_ratio(
                    response_dl, ticket.team_id.sla_response_hours, now,
                )
            elif resolve_dl:
                ratio = ticket._sla_remaining_ratio(
                    resolve_dl,
                    ticket.team_id.sla_resolve_hours
                    + (ticket.sla_paused_hours or 0.0),
                    now,
                )
            else:
                ratio = 1.0
            ticket.sla_state = (
                "at_risk" if ratio < self.SLA_RISK_RATIO else "ok"
            )

    @api.depends(
        "sla_response_deadline",
        "sla_resolve_deadline",
        "first_response_date",
        "waiting_state",
        "stage_id.closed",
    )
    def _compute_sla_breach(self):
        # Bandeaux de la fiche : ce qui est en retard *maintenant* et qui
        # appelle un geste. Le bilan définitif est dans `sla_state`.
        now = fields.Datetime.now()
        for ticket in self:
            open_ = not ticket.stage_id.closed
            ticket.sla_response_breach = bool(
                open_
                and ticket.sla_response_deadline
                and not ticket.first_response_date
                and ticket.sla_response_deadline < now
            )
            ticket.sla_resolve_breach = bool(
                open_
                and ticket.waiting_state != "client"
                and ticket.sla_resolve_deadline
                and ticket.sla_resolve_deadline < now
            )

    def _sla_track_pause(self, was_paused):
        """Ouvre ou ferme la pause SLA selon l'attente client et l'étape."""
        now = fields.Datetime.now()
        for ticket in self:
            paused = (
                ticket.waiting_state == "client" and not ticket.stage_id.closed
            )
            before = was_paused.get(ticket.id, False)
            if paused and not before:
                ticket.sla_paused_since = now
            elif before and not paused and ticket.sla_paused_since:
                ticket.write({
                    "sla_paused_hours": (ticket.sla_paused_hours or 0.0)
                    + self._sla_hours_between(
                        ticket.team_id, ticket.sla_paused_since, now,
                    ),
                    "sla_paused_since": False,
                })

    def _message_post_after_hook(self, message, msg_vals):
        res = super()._message_post_after_hook(message, msg_vals)
        if self._bf_is_staff_public_reply(message):
            date = message.date or fields.Datetime.now()
            pending = self.filtered(lambda t: not t.first_response_date)
            if pending:
                pending.sudo().first_response_date = date
            # L'agent relance lui-même la conversation : le cycle des
            # relances automatiques repart de ce message.
            self.sudo().write({
                "last_staff_reply_date": date,
                "reminder_count": 0,
            })
        elif (
            not self.env.context.get("bf_helpdesk_autoreply")
            and self._bf_is_client_reply(message)
        ):
            self._bf_on_client_reply()
        return res

    def _bf_is_client_reply(self, message):
        """Message public du CLIENT de ce billet : lui, son organisation, ou l'adresse du billet.

        Un inconnu en copie, ou un courriel rattaché par les en-têtes, ne lève
        pas l'attente et ne rouvre rien.
        Pas de critère « abonné » : poster abonne l'auteur, et au moment du
        contrôle l'inconnu était déjà devenu abonné.
        """
        if message.message_type not in ("comment", "email"):
            return False
        if message.is_internal or message.subtype_id.internal:
            return False
        author = message.author_id
        if not author or any(not u.share for u in author.user_ids):
            return False
        author_email = email_normalize(author.email or message.email_from or "")
        for ticket in self:
            client = ticket.partner_id.commercial_partner_id
            if author == ticket.partner_id or (client and author.commercial_partner_id == client):
                return True
            if author_email and author_email == email_normalize(ticket.partner_email or ""):
                return True
        return False

    def _bf_on_client_reply(self):
        """Le client a répondu : fin de l'attente, cycle de relances remis à 0.

        Un billet fermé par les relances se rouvre, comme l'annonçait le
        courriel de fermeture. Un billet fermé à la main reste fermé.
        """
        for ticket in self.sudo():
            vals = {"reminder_count": 0, "reminder_last_date": False}
            if ticket.stage_id.closed:
                if not ticket.reminder_auto_closed:
                    continue
                stage = ticket.team_id._get_applicable_stages().filtered(
                    lambda s: not s.closed
                )[:1]
                if not stage:
                    continue
                vals.update({
                    "stage_id": stage.id,
                    "reminder_auto_closed": False,
                    "waiting_state": False,
                })
                ticket.write(vals)
                ticket.message_post(
                    body=_("Rouvert : le client a répondu après la fermeture "
                           "automatique."),
                    message_type="notification",
                    subtype_xmlid="mail.mt_note",
                )
                continue
            if ticket.waiting_state == "client":
                vals["waiting_state"] = False
            ticket.write(vals)

    @api.model
    def _bf_is_staff_public_reply(self, message):
        """Message public écrit par un membre interne de l'équipe."""
        if message.message_type not in ("comment", "email"):
            return False
        if message.is_internal or message.subtype_id.internal:
            return False
        users = message.author_id.user_ids
        return bool(users) and any(not u.share for u in users)

    @api.model
    def _cron_sla_breach_activity(self):
        """Tâche horaire : recalcule l'état SLA des billets ouverts, puis pose
        une activité de suivi sur ceux qui viennent de passer en dépassement.

        L'état dépend du temps qui passe : aucune écriture ne le recalcule
        tout seul, d'où le passage par add_to_compute.
        """
        open_tickets = self.search([
            ("active", "=", True),
            ("stage_id.closed", "=", False),
            "|",
            ("sla_response_deadline", "!=", False),
            ("sla_resolve_deadline", "!=", False),
        ])
        self.env.add_to_compute(self._fields["sla_state"], open_tickets)
        open_tickets.flush_recordset(["sla_state"])
        # L'état change aussi à l'écriture du billet, pas seulement ici : on
        # compare donc au dernier état NOTIFIÉ, pas à l'état d'avant la passe.
        Items = self.env["helpdesk.agent.notify.item"]

        def _signal(ticket):
            state = ticket.sla_state
            if state == ticket.sla_notified_state:
                return
            if state in ("at_risk", "breached"):
                event = "sla_risk" if state == "at_risk" else "sla_breach"
                people = ticket.user_id or ticket.team_id.user_ids
                Items._dispatch(ticket, people, event, ticket._bf_agent_summary(event))
            if state == "breached":
                # À l'entrée en dépassement seulement : une activité marquée
                # faite ne revient plus toutes les heures.
                ticket.activity_schedule(
                    act_type_xmlid="mail.mail_activity_data_todo",
                    summary="SLA dépassé",
                    note=(
                        "<p>Ce ticket a dépassé un SLA configuré sur l'équipe. "
                        "Vérifier et réagir.</p>"
                    ),
                    user_id=ticket.user_id.id or self.env.uid,
                )
            ticket.sudo().sla_notified_state = state
        each_isolated(open_tickets, _signal, "bf_helpdesk SLA")

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

    csat_ids = fields.One2many(
        "helpdesk.ticket.csat", "ticket_id", string="Satisfaction",
    )
    csat_rating = fields.Selection(
        related="csat_ids.rating", string="Note de satisfaction",
    )

    def _bf_csat_schedule(self):
        """À la fermeture : prévoir le sondage d'une question (mode natif).

        Un seul sondage par billet : un billet rouvert puis refermé n'est pas
        sondé deux fois. Le délai laisse au client le temps de constater que
        c'est réglé, et à l'agent celui de rouvrir si ce ne l'est pas.
        """
        Csat = self.env["helpdesk.ticket.csat"].sudo()
        now = fields.Datetime.now()
        for ticket in self:
            team = ticket.team_id
            if team.csat_mode != "native":
                continue
            if Csat.search_count([
                ("ticket_id", "=", ticket.id),
                ("state", "in", ("scheduled", "sent", "answered")),
            ]):
                continue
            csat = Csat.create({
                "ticket_id": ticket.id,
                "user_id": ticket.user_id.id,
                "scheduled_date": now + timedelta(hours=team.csat_delay_hours or 0),
            })
            if not team.csat_delay_hours:
                # Envoi du système, pas de l'agent : les garde-fous de
                # sollicitation (bf_cx_helpdesk) lisent les préférences de
                # contact, que l'agent n'a pas le droit de lire (AccessError à
                # la fermeture). sudo() garde l'agent
                # comme auteur.
                if ticket.sudo()._send_csat_invite():
                    # Le sondage annonce déjà la fin de la demande : pas de
                    # second courriel par le gabarit d'étape.
                    ticket._bf_skip_stage_template()
                elif csat.state == "scheduled":
                    csat._cancel(_("garde-fou de sollicitation"))

    def _send_csat_invite(self):
        """Point de passage unique de l'envoi du sondage de satisfaction.

        bf_cx_helpdesk le surcharge pour ses garde-fous de sollicitation, et
        il doit être appelé au moment où le courriel part, pas à la fermeture.
        bf_cx_helpdesk ne dépend pas de ce module : sa classe peut se trouver
        au-dessus ou au-dessous de la nôtre. Au-dessous, on lui passe la main
        (il rappelle _bf_csat_deliver une fois le garde-fou franchi) ; sans
        cela, le garde-fou ne jouait jamais.
        """
        parent = super()
        if hasattr(parent, "_send_csat_invite"):
            return parent._send_csat_invite()
        return self._bf_csat_deliver()

    def _bf_csat_deliver(self):
        """L'envoi lui-même, selon le mode de l'équipe, sans garde-fou."""
        self.ensure_one()
        team = self.team_id
        if team.csat_mode == "native":
            csat = self.env["helpdesk.ticket.csat"].sudo().search([
                ("ticket_id", "=", self.id), ("state", "=", "scheduled"),
            ], limit=1)
            return csat._send() if csat else False
        if team.csat_mode != "survey":
            return False
        return self._send_csat_survey_invite()

    def _send_csat_survey_invite(self):
        """Ancien envoi par survey.survey (mode « survey »), à la fermeture."""
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
        self.sudo().write({"csat_user_input_id": user_input.id})
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
        string="Portée de validation",
        compute="_compute_scope_aligned",
        compute_sudo=True,
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
    # Triage IA — une passe par le pont (bf_ai_bridge), sans outil
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

    def _triage_payload(self):
        """Le billet, tel qu'il part vers le pont.

        Les stages et les membres voyagent avec le billet : le pont n'a aucun
        accès à Odoo, c'est ce qui lui permet de trier sans serveur MCP et sans
        outil. C'est aussi ce qui rend sa réponse vérifiable — une valeur qui
        n'est dans aucune des deux listes est rejetée au retour.
        """
        self.ensure_one()
        partenaire = self.partner_name or (
            self.partner_id.name if self.partner_id else ""
        )
        return {
            "numero": self.number or "",
            "sujet": self.name or "",
            "client": partenaire or "Inconnu",
            "equipe": self.team_id.name or "",
            "stages": self.team_id._get_applicable_stages().mapped("name"),
            "membres": self.team_id.user_ids.mapped("name"),
            "description": html2plaintext(self.description or ""),
        }

    def _triage_html(self, donnees):
        """Composer la suggestion affichée à partir du JSON rendu par le pont.

        Le HTML se monte ici, il ne se demande pas au modèle. Un modèle à qui
        on réclame du HTML en rend *presque* toujours : le jour où il rend
        autre chose, ça atterrit tel quel dans un champ en lecture seule et
        plus personne ne sait pourquoi la page est de travers.
        """
        sections = (
            ("categorisation", _("Catégorisation"), None),
            ("stage", _("Stage suggéré"), "stage_motif"),
            ("assignation", _("Assignation suggérée"), "assignation_motif"),
            ("reponse", _("Brouillon de première réponse"), None),
        )
        morceaux = []
        for cle, libelle, cle_motif in sections:
            valeur = (donnees.get(cle) or "").strip()
            motif = (donnees.get(cle_motif) or "").strip() if cle_motif else ""
            if not valeur and not motif:
                continue
            if valeur and motif:
                texte = "%s — %s" % (valeur, motif)
            else:
                texte = valeur or motif
            morceaux.append("<p><strong>%s :</strong> %s</p>" % (
                escape(libelle), escape(texte)))
        confiance = donnees.get("confiance")
        if isinstance(confiance, int) and confiance:
            morceaux.append("<p><em>%s</em></p>" % escape(
                _("Confiance : %s %%", confiance)))
        if not morceaux:
            return "<p>%s</p>" % escape(_("Le triage n'a rien proposé."))
        return "".join(morceaux)

    def _triage_echec(self, message):
        """Déposer l'échec dans le billet au lieu de le perdre dans une fenêtre.

        L'état passe à « erreur » et le bouton reste offert : un échec de
        transport se réessaie, contrairement à une erreur de configuration qui,
        elle, lève avant d'arriver ici.
        """
        self.write({
            "triage_state": "error",
            "triage_suggestion_html": "<p><strong>%s</strong> %s</p>" % (
                escape(_("Erreur :")), escape(message)),
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
        if not self.env.su and not self.env.user.has_group("bf_persona.group_persona_user"):
            raise AccessError(_("Le persona est réservé au rôle persona."))
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
        self._bf_check_linked_vals(vals_list)
        Partner = self.env["res.partner"]
        for vals in vals_list:
            if not vals.get("email_thread_subject"):
                vals["email_thread_subject"] = vals.get("name")
            if not vals.get("client_lang"):
                partner = Partner.browse(vals.get("partner_id") or [])
                company = self.env["res.company"].browse(vals.get("company_id")) or self.env.company
                vals["client_lang"] = partner.lang or company.partner_id.lang or self.env.lang
        tickets = super().create(vals_list)
        tickets._sla_track_pause({})
        for ticket in tickets:
            ticket._apply_auto_tag_rules()
            ticket._maybe_notify_ntfy_critical(reason="created")
        if not self.env.context.get("import_file"):
            tickets._bf_send_ack()
        return tickets

    def _bf_check_linked_vals(self, vals_list):
        """Deux liens que la fiche lit ensuite en sudo.

        La réponse de sondage n'est posée que par l'envoi du sondage : écrite
        par RPC, celle d'un autre client remontait dans la vue 360. L'élément de
        matrice doit être lisible par qui le lie : sinon la matrice et l'état
        d'un élément d'un projet qu'il ne suit pas en sortaient."""
        if self.env.su:
            return
        # Ni posée, ni effacée : effacer une mauvaise réponse la ferait
        # disparaître de la vue 360.
        if any(vals.get("csat_user_input_id") for vals in vals_list) or (
                self and any("csat_user_input_id" in vals for vals in vals_list)
                and self.filtered("csat_user_input_id")):
            raise AccessError(_("La réponse de sondage d'un billet n'est pas modifiable."))
        items = [vals["knowledge_item_id"] for vals in vals_list if vals.get("knowledge_item_id")]
        if items:
            self.env["project.knowledge.item"].browse(items).check_access("read")

    def write(self, vals):
        self._bf_check_linked_vals([vals])
        old_priority = {t.id: t.priority for t in self}
        before_closed = {t.id: bool(t.stage_id.closed) for t in self}
        was_paused = {
            t.id: t.waiting_state == "client" and not t.stage_id.closed
            for t in self
        }
        res = super().write(vals)
        if "waiting_state" in vals or "stage_id" in vals:
            self._sla_track_pause(was_paused)
        if "priority" in vals and not self.env.context.get("bf_hd_no_escalation_ntfy"):
            for ticket in self:
                if old_priority.get(ticket.id) != ticket.priority:
                    ticket._maybe_notify_ntfy_critical(reason="escalated")
        if "waiting_state" in vals and "reminder_count" not in vals:
            # Nouvelle attente, ou fin d'attente : le cycle repart de zéro.
            self.sudo().write({"reminder_count": 0, "reminder_last_date": False})
        if "stage_id" in vals:
            no_csat = self.env.context.get("bf_helpdesk_no_csat")
            for ticket in self:
                if ticket.stage_id.closed and not before_closed.get(ticket.id):
                    if no_csat:
                        continue
                    if ticket.team_id.csat_mode == "native":
                        ticket._bf_csat_schedule()
                    elif ticket.sudo()._send_csat_invite():  # voir _bf_csat_schedule
                        ticket._bf_skip_stage_template()
                elif not ticket.stage_id.closed and ticket.reminder_auto_closed:
                    ticket.sudo().reminder_auto_closed = False
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
        # Fil perdu : le sujet porte [HT00012] et l'envoyeur est du billet.
        # La passerelle poste alors le message sur ce billet au lieu d'en
        # ouvrir un nouveau.
        existing = self._bf_ticket_from_subject(msg)
        if existing:
            _logger.info(
                "bf_helpdesk: courriel rattaché au billet %s par le sujet",
                existing.number,
            )
            # La passerelle poste ensuite avec _creation_subtype() : sans ce
            # marqueur, la réponse partirait comme « Billet créé ».
            return existing.with_context(bf_helpdesk_rerouted=True)
        # Le canal se consigne ici : c'est le seul endroit qui sait que le
        # billet vient d'un courriel. Une valeur fournie par l'alias l'emporte.
        custom_values = dict(custom_values or {})
        if not custom_values.get("channel_id"):
            channel = self.env.ref(
                "helpdesk_mgmt.helpdesk_ticket_channel_email",
                raise_if_not_found=False,
            )
            if channel:
                custom_values["channel_id"] = channel.id
        # mail.thread remplit le champ _rec_name avec le sujet ; ici, c'est
        # « number » : le billet né d'un courriel prenait son sujet pour numéro
        # (ni HT00000 ni référence dans les courriels).
        custom_values.setdefault("number", "/")
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
