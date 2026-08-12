# -*- coding: utf-8 -*-
from datetime import timedelta

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.addons.phone_validation.tools.phone_validation import phone_format
from odoo.exceptions import UserError
from odoo.tools import email_normalize

from .outreach_campaign import FROZEN_STATES

# Canaux soumis à une cadence, par ordre de priorité en cas d'égalité de date.
CHANNELS = ("call", "email", "letter")


def format_phone(number, country=None):
    """Numéro en E.164, ou False si illisible. Ne lève jamais.

    Utilisé pour le dédoublonnage et pour le rapprochement des appels
    journalisés ; les modules ponts s'en servent aussi.
    """
    if not number:
        return False
    try:
        formatted = phone_format(
            number,
            country.code if country else None,
            country.phone_code if country else None,
            force_format="E164",
            raise_exception=False,
        )
    except Exception:  # noqa: BLE001 — une commodité ne bloque jamais un flux
        return False
    # Quand la lecture échoue, phone_format renvoie l'entrée telle quelle
    # (« poste 4 » ressort « poste 4 ») : seul un E.164 nous intéresse.
    if not formatted or not formatted.startswith("+"):
        return False
    return formatted


class BfOutreachTarget(models.Model):
    _name = "bf.outreach.target"
    _description = "Cible de démarchage"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "next_action_date asc, priority desc, id desc"

    @api.model
    def _default_stage_id(self):
        campaign_id = self.env.context.get("default_campaign_id")
        return self.env["bf.outreach.stage"]._first_stage(campaign_id)

    name = fields.Char(
        string="Cible",
        required=True,
        tracking=True,
        help="Organisation démarchée, ou nom de la personne s'il s'agit d'un particulier.",
    )
    active = fields.Boolean(default=True)
    campaign_id = fields.Many2one(
        "bf.outreach.campaign",
        string="Campagne",
        required=True,
        ondelete="cascade",
        index=True,
        tracking=True,
    )
    company_id = fields.Many2one(
        related="campaign_id.company_id", store=True, index=True
    )
    campaign_state = fields.Selection(related="campaign_id.state", store=True)
    partner_id = fields.Many2one("res.partner", string="Contact Odoo", tracking=True)
    contact_name = fields.Char(string="Personne-ressource")
    function = fields.Char(string="Fonction")
    email = fields.Char(string="Courriel")
    phone = fields.Char(string="Téléphone")
    mobile = fields.Char(string="Cellulaire")
    website = fields.Char(string="Site web")
    source = fields.Char(
        string="Source",
        help="Provenance de la cible : liste importée, salon, référencement, etc.",
    )
    user_id = fields.Many2one(
        "res.users",
        string="Responsable",
        default=lambda self: self.env.user,
        tracking=True,
    )
    stage_id = fields.Many2one(
        "bf.outreach.stage",
        string="Étape",
        default=lambda self: self._default_stage_id(),
        group_expand="_read_group_stage_ids",
        ondelete="restrict",
        tracking=True,
        domain="['|', ('campaign_ids', '=', False), ('campaign_ids', 'in', campaign_id)]",
    )
    stage_type = fields.Selection(related="stage_id.stage_type", store=True, index=True)
    tag_ids = fields.Many2many(
        "bf.outreach.tag",
        "bf_outreach_target_tag_rel",
        "target_id",
        "tag_id",
        string="Étiquettes",
    )
    priority = fields.Selection(
        [("0", "Normale"), ("1", "Élevée"), ("2", "Urgente")],
        string="Priorité",
        default="0",
        tracking=True,
    )
    color = fields.Integer(string="Couleur")
    note = fields.Html(string="Notes")
    closed_reason = fields.Selection(
        [
            ("converted", "Converti"),
            ("not_interested", "Pas intéressé"),
            ("no_answer", "Jamais joint"),
            ("bad_timing", "Mauvais moment"),
            ("invalid", "Coordonnées invalides"),
            ("competitor", "Déjà servi ailleurs"),
            ("other", "Autre"),
        ],
        string="Motif de clôture",
        tracking=True,
    )
    paused_until = fields.Date(
        string="Ne pas relancer avant",
        tracking=True,
        help="Repousse la prochaine relance, par exemple lorsque la cible demande "
        "d'être rappelée plus tard.",
    )
    do_not_contact = fields.Boolean(
        string="Ne plus contacter",
        tracking=True,
        copy=False,
        index=True,
        help="Exclusion demandée par la cible. La cadence s'arrête définitivement "
        "et la cible ne peut plus être ajoutée à une campagne.",
    )
    do_not_contact_date = fields.Datetime(
        string="Exclusion demandée le", readonly=True, copy=False
    )
    do_not_contact_user_id = fields.Many2one(
        "res.users", string="Exclusion notée par", readonly=True, copy=False
    )
    do_not_contact_reason = fields.Char(
        string="Motif de l'exclusion",
        copy=False,
        help="Ce que la personne a demandé, dans ses mots. Conservé comme preuve "
        "du traitement de la demande.",
    )
    lead_id = fields.Many2one("crm.lead", string="Opportunité", copy=False)

    touch_ids = fields.One2many(
        "bf.outreach.touch", "target_id", string="Interactions"
    )
    campaign_pitch = fields.Html(
        related="campaign_id.description",
        string="Argumentaire de la campagne",
        readonly=True,
        help="Accroche et objections fréquentes de la campagne, sous les yeux "
        "pendant l'appel.",
    )

    # ------------------------------------------------------------------
    # Compteurs et dates (stockés : ils servent aux filtres et aux tris)
    # ------------------------------------------------------------------
    call_count = fields.Integer(
        string="Appels", compute="_compute_touch_stats", store=True
    )
    email_count = fields.Integer(
        string="Courriels", compute="_compute_touch_stats", store=True
    )
    letter_count = fields.Integer(
        string="Lettres", compute="_compute_touch_stats", store=True
    )
    touch_count = fields.Integer(
        string="Nombre d'interactions", compute="_compute_touch_stats", store=True
    )
    last_call_date = fields.Datetime(
        string="Dernier appel", compute="_compute_touch_stats", store=True
    )
    last_email_date = fields.Datetime(
        string="Dernier courriel", compute="_compute_touch_stats", store=True
    )
    last_letter_date = fields.Datetime(
        string="Dernière lettre", compute="_compute_touch_stats", store=True
    )
    last_touch_date = fields.Datetime(
        string="Dernier contact", compute="_compute_touch_stats", store=True
    )
    has_reply = fields.Boolean(
        string="A répondu", compute="_compute_touch_stats", store=True
    )
    first_reply_date = fields.Datetime(
        string="Première réponse", compute="_compute_touch_stats", store=True
    )
    next_call_date = fields.Date(
        string="Prochain appel", compute="_compute_next_dates", store=True
    )
    next_email_date = fields.Date(
        string="Prochain courriel", compute="_compute_next_dates", store=True
    )
    next_letter_date = fields.Date(
        string="Prochaine lettre", compute="_compute_next_dates", store=True
    )
    next_action_date = fields.Date(
        string="Prochaine action",
        compute="_compute_next_dates",
        store=True,
        index=True,
    )
    next_action_kind = fields.Selection(
        [("call", "Appel"), ("email", "Courriel"), ("letter", "Lettre")],
        string="Type de prochaine action",
        compute="_compute_next_dates",
        store=True,
    )
    is_excluded = fields.Boolean(
        string="Exclue du démarchage",
        compute="_compute_is_excluded",
        store=True,
        index=True,
        help="Vrai lorsque la cible, ou le contact Odoo qui lui est lié, a demandé "
        "de ne plus être sollicité.",
    )
    email_normalized = fields.Char(
        string="Courriel normalisé",
        compute="_compute_normalized",
        store=True,
        index=True,
        help="Courriel réduit à sa forme canonique : sert au dédoublonnage et au "
        "rapprochement des réponses entrantes.",
    )
    phone_normalized = fields.Char(
        string="Téléphone (E.164)",
        compute="_compute_normalized",
        store=True,
        index=True,
        help="Numéro en format international : sert au dédoublonnage et au "
        "rapprochement des appels journalisés.",
    )

    @api.depends("do_not_contact", "partner_id.outreach_opt_out")
    def _compute_is_excluded(self):
        """Exclusion connue et STOCKÉE : la nôtre, et celle du contact lié.

        Le « ne pas contacter » du registre de consentement
        (`privacy_consent`) n'entre pas ici : il est calculé, non stocké,
        donc impropre à un champ stocké. Il est consulté à l'exécution par
        `_is_solicitable`, au moment où l'on s'apprête à solliciter.
        """
        for target in self:
            target.is_excluded = bool(
                target.do_not_contact or target.partner_id.outreach_opt_out
            )

    @api.depends("email", "phone", "mobile", "partner_id", "company_id")
    def _compute_normalized(self):
        for target in self:
            target.email_normalized = email_normalize(target.email or "") or False
            number = target.phone or target.mobile
            target.phone_normalized = target._format_phone(number)

    def _format_phone(self, number):
        """Numéro en E.164, ou False si illisible — jamais une exception."""
        self.ensure_one()
        country = (
            self.partner_id.country_id
            or self.company_id.country_id
            or self.env.company.country_id
        )
        return format_phone(number, country)

    # ------------------------------------------------------------------
    # Calculs
    # ------------------------------------------------------------------
    @api.depends(
        "touch_ids",
        "touch_ids.kind",
        "touch_ids.date",
        "touch_ids.is_reply",
    )
    def _compute_touch_stats(self):
        for target in self:
            touches = target.touch_ids.sorted("date")
            calls = touches.filtered(lambda t: t.kind == "call")
            emails = touches.filtered(lambda t: t.kind == "email")
            letters = touches.filtered(lambda t: t.kind == "letter")
            replies = touches.filtered("is_reply")
            target.call_count = len(calls)
            target.email_count = len(emails)
            target.letter_count = len(letters)
            target.touch_count = len(touches)
            target.last_call_date = calls[-1].date if calls else False
            target.last_email_date = emails[-1].date if emails else False
            target.last_letter_date = letters[-1].date if letters else False
            target.last_touch_date = touches[-1].date if touches else False
            target.has_reply = bool(replies)
            target.first_reply_date = replies[0].date if replies else False

    @api.depends(
        "call_count",
        "email_count",
        "letter_count",
        "last_call_date",
        "last_email_date",
        "last_letter_date",
        "has_reply",
        "stage_type",
        "paused_until",
        "do_not_contact",
        "partner_id.outreach_opt_out",
        "create_date",
        "campaign_id.state",
        "campaign_id.date_start",
        "campaign_id.call_target_count",
        "campaign_id.email_target_count",
        "campaign_id.letter_target_count",
        "campaign_id.call_interval_days",
        "campaign_id.email_interval_days",
        "campaign_id.letter_interval_days",
        "campaign_id.stop_on_reply",
        "campaign_id.working_days_only",
    )
    def _compute_next_dates(self):
        for target in self:
            campaign = target.campaign_id
            frozen = (
                not campaign
                or target.is_excluded
                or campaign.state in FROZEN_STATES
                or target.stage_type in ("won", "lost")
                or (campaign.stop_on_reply and target.has_reply)
            )
            if frozen:
                for channel in CHANNELS:
                    target["next_%s_date" % channel] = False
                target.next_action_date = False
                target.next_action_kind = False
                continue

            candidates = []
            for rank, channel in enumerate(CHANNELS):
                next_date = target._compute_next_for(
                    done=target["%s_count" % channel],
                    goal=campaign["%s_target_count" % channel],
                    last_date=target["last_%s_date" % channel],
                    interval=campaign["%s_interval_days" % channel],
                )
                target["next_%s_date" % channel] = next_date
                if next_date:
                    candidates.append((next_date, rank, channel))
            if candidates:
                # À égalité de date, l'ordre de CHANNELS tranche.
                candidates.sort()
                target.next_action_date = candidates[0][0]
                target.next_action_kind = candidates[0][2]
            else:
                target.next_action_date = False
                target.next_action_kind = False

    def _compute_next_for(self, done, goal, last_date, interval):
        """Date du prochain contact d'un type donné, ou False si le quota est atteint."""
        self.ensure_one()
        if goal <= 0 or done >= goal:
            return False
        campaign = self.campaign_id
        if last_date:
            next_date = last_date.date() + timedelta(days=max(interval, 0))
        else:
            created = self.create_date.date() if self.create_date else fields.Date.context_today(self)
            next_date = max(campaign.date_start or created, created)
        if self.paused_until and self.paused_until > next_date:
            next_date = self.paused_until
        if campaign.working_days_only:
            next_date = self._shift_to_working_day(next_date)
        return next_date

    @staticmethod
    def _shift_to_working_day(value):
        """Reporte au lundi une date qui tombe la fin de semaine."""
        if value.weekday() >= 5:
            return value + timedelta(days=7 - value.weekday())
        return value

    # ------------------------------------------------------------------
    # Kanban
    # ------------------------------------------------------------------
    @api.model
    def _read_group_stage_ids(self, stages, domain):
        campaign_id = self.env.context.get("default_campaign_id")
        if campaign_id:
            search_domain = [
                "|",
                ("id", "in", stages.ids),
                "|",
                ("campaign_ids", "=", False),
                ("campaign_ids", "in", campaign_id),
            ]
        else:
            search_domain = ["|", ("id", "in", stages.ids), ("campaign_ids", "=", False)]
        stage_ids = stages.sudo()._search(search_domain, order=stages._order)
        return stages.browse(stage_ids)

    # ------------------------------------------------------------------
    # Surcharges
    # ------------------------------------------------------------------
    @api.onchange("partner_id")
    def _onchange_partner_id(self):
        for target in self:
            partner = target.partner_id
            if not partner:
                continue
            if not target.name:
                target.name = partner.parent_id.name or partner.name
            if partner.is_company:
                target.contact_name = target.contact_name or partner.child_ids[:1].name
            else:
                target.contact_name = target.contact_name or partner.name
                target.function = target.function or partner.function
            target.email = target.email or partner.email
            target.phone = target.phone or partner.phone
            target.mobile = target.mobile or partner.mobile
            target.website = target.website or partner.website

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("stage_id"):
                vals["stage_id"] = (
                    self.env["bf.outreach.stage"]._first_stage(vals.get("campaign_id")).id
                    or False
                )
        return super().create(vals_list)

    def write(self, vals):
        if vals.get("do_not_contact") and "do_not_contact_date" not in vals:
            vals = dict(
                vals,
                do_not_contact_date=fields.Datetime.now(),
                do_not_contact_user_id=self.env.user.id,
            )
        res = super().write(vals)
        if vals.get("do_not_contact"):
            self._apply_exclusion()
        if "stage_id" in vals:
            for target in self:
                if target.stage_type in ("won", "lost"):
                    target.activity_unlink(["mail.mail_activity_data_todo", "mail.mail_activity_data_call"])
        return res

    # ------------------------------------------------------------------
    # Exclusion (LCAP)
    # ------------------------------------------------------------------
    def _apply_exclusion(self):
        """Propage l'exclusion : contact Odoo, activités en attente, chatter."""
        for target in self:
            if target.partner_id and not target.partner_id.outreach_opt_out:
                target.partner_id.sudo().write(
                    {
                        "outreach_opt_out": True,
                        "outreach_opt_out_date": target.do_not_contact_date,
                        "outreach_opt_out_reason": target.do_not_contact_reason,
                    }
                )
            target.activity_unlink(
                ["mail.mail_activity_data_todo", "mail.mail_activity_data_call"]
            )
            body = Markup("<p><b>%s</b></p>") % _("Ne plus contacter — demande enregistrée.")
            if target.do_not_contact_reason:
                body += Markup("<p>%s</p>") % target.do_not_contact_reason
            target.message_post(body=body)

    def _is_solicitable(self):
        """Peut-on solliciter cette cible, ici et maintenant ?

        Contrôle de dernière minute, à faire juste avant un envoi ou la création
        d'un rappel : il ajoute au drapeau stocké la consultation en direct du
        registre de consentement, qu'un retrait peut avoir changé depuis.
        """
        self.ensure_one()
        if self.is_excluded:
            return False
        if self.partner_id and self.partner_id._outreach_is_blocked():
            return False
        return True

    def action_do_not_contact(self):
        """Bouton : la personne demande de ne plus être sollicitée."""
        return {
            "type": "ir.actions.act_window",
            "name": _("Ne plus contacter"),
            "res_model": "bf.outreach.exclude.wizard",
            "view_mode": "form",
            "target": "new",
            "context": dict(self.env.context, default_target_ids=[(6, 0, self.ids)]),
        }

    def action_allow_contact_again(self):
        """Lève l'exclusion — réservé au gestionnaire, et tracé."""
        for target in self:
            target.write({"do_not_contact": False})
            target.message_post(
                body=Markup("<p>%s</p>")
                % _("Exclusion levée par %s.", self.env.user.name)
            )
        return True

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _action_log_wizard(self, kind):
        return {
            "type": "ir.actions.act_window",
            "name": _("Journaliser un contact"),
            "res_model": "bf.outreach.log.wizard",
            "view_mode": "form",
            "target": "new",
            "context": dict(
                self.env.context,
                default_target_ids=[(6, 0, self.ids)],
                default_kind=kind,
            ),
        }

    def action_log_touch(self):
        return self._action_log_wizard(self.env.context.get("default_kind", "call"))

    def action_log_call(self):
        return self._action_log_wizard("call")

    def action_log_email(self):
        return self._action_log_wizard("email")

    def action_log_letter(self):
        return self._action_log_wizard("letter")

    def action_write_letters(self):
        """Ouvre la fusion de lettres de `bf_letter_writer` sur les cibles retenues.

        Le module est optionnel : on ne le déclare pas en dépendance, on le
        détecte à l'exécution. La fusion travaille sur des `res.partner`, donc
        les cibles qui n'en ont pas encore un s'en font créer un.
        """
        if "letter.merge.wizard" not in self.env:
            raise UserError(
                _(
                    "Le module « Letter Writer » n'est pas installé : "
                    "impossible de préparer des lettres."
                )
            )
        self.filtered(lambda t: not t.partner_id).action_create_partner()
        partners = self.mapped("partner_id")
        if not partners:
            raise UserError(_("Aucun destinataire : les cibles n'ont pas de contact."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Préparer les lettres"),
            "res_model": "letter.merge.wizard",
            "view_mode": "form",
            "target": "new",
            "context": dict(
                self.env.context,
                default_recipient_ids=[(6, 0, partners.ids)],
                active_model="res.partner",
                active_ids=partners.ids,
            ),
        }

    def action_send_campaign_email(self):
        """Ouvre le compositeur avec le modèle de la campagne, et journalise l'envoi.

        Refuse net les cibles qu'on n'a plus le droit de solliciter : l'exclusion
        se vérifie au moment de l'envoi, pas seulement à l'import.
        """
        blocked = self.filtered(lambda t: not t._is_solicitable())
        if blocked:
            raise UserError(
                _(
                    "Ces cibles ont demandé de ne plus être sollicitées :\n%s",
                    "\n".join("• %s" % name for name in blocked.mapped("name")),
                )
            )
        without_email = self.filtered(lambda t: not t.email)
        if without_email:
            raise UserError(
                _(
                    "Ces cibles n'ont pas d'adresse courriel :\n%s",
                    "\n".join("• %s" % name for name in without_email.mapped("name")),
                )
            )
        template = self.campaign_id[:1].mail_template_id
        composer_context = dict(
            self.env.context,
            default_model="bf.outreach.target",
            default_composition_mode="comment" if len(self) == 1 else "mass_mail",
            default_template_id=template.id if template else False,
            default_partner_ids=[],
            bf_outreach_send=True,
        )
        if len(self) == 1:
            composer_context["default_res_ids"] = self.ids
        else:
            composer_context["active_ids"] = self.ids
            composer_context["active_model"] = "bf.outreach.target"
        return {
            "type": "ir.actions.act_window",
            "name": _("Courriel de démarchage"),
            "res_model": "mail.compose.message",
            "view_mode": "form",
            "target": "new",
            "context": composer_context,
        }

    def message_post(self, **kwargs):
        message = super().message_post(**kwargs)
        if not self.env.context.get("bf_outreach_touch_log"):
            self._log_touch_from_message(message)
        return message

    def _log_mass_email_touches(self, subject=None):
        """Journalise un courriel envoyé en lot, que le compositeur ne trace pas."""
        Touch = self.env["bf.outreach.touch"]
        values = []
        for target in self:
            if not target.email:
                continue
            values.append(
                {
                    "target_id": target.id,
                    "kind": "email",
                    "direction": "out",
                    "outcome": "sent",
                    "date": fields.Datetime.now(),
                    "summary": subject or _("Courriel envoyé"),
                    "user_id": self.env.user.id,
                }
            )
        return Touch.create(values) if values else Touch

    def _log_touch_from_message(self, message):
        """Un courriel parti de la discussion devient une interaction.

        On ne compte que les messages réellement adressés à la cible — pas les
        notes internes, pas les notifications de suivi.
        """
        self.ensure_one()
        if not message or message.message_type not in ("comment", "email"):
            return
        subtype = self.env.ref("mail.mt_comment", raise_if_not_found=False)
        if subtype and message.subtype_id and message.subtype_id != subtype:
            return
        recipients = set(message.partner_ids.mapped("email_normalized"))
        recipients.discard(False)
        target_email = self.email_normalized
        addressed = bool(
            target_email
            and (
                target_email in recipients
                or (self.partner_id and self.partner_id in message.partner_ids)
            )
        )
        if not addressed:
            return
        self.env["bf.outreach.touch"].create(
            {
                "target_id": self.id,
                "kind": "email",
                "direction": "out",
                "outcome": "sent",
                "date": message.date,
                "summary": message.subject or _("Courriel envoyé"),
                "mail_message_id": message.id,
                "user_id": (message.author_id.user_ids[:1] or self.env.user).id,
            }
        )

    def action_create_partner(self):
        """Crée le contact Odoo correspondant et le lie à la cible."""
        Partner = self.env["res.partner"]
        for target in self:
            if target.partner_id:
                continue
            partner = Partner.create(
                {
                    "name": target.name,
                    "is_company": True,
                    "email": target.email,
                    "phone": target.phone,
                    "mobile": target.mobile,
                    "website": target.website,
                }
            )
            if target.contact_name:
                Partner.create(
                    {
                        "name": target.contact_name,
                        "parent_id": partner.id,
                        "function": target.function,
                        "email": target.email,
                        "phone": target.phone,
                        "type": "contact",
                    }
                )
            target.partner_id = partner
        return True

    def action_create_lead(self):
        """Bascule une cible qualifiée vers le pipeline CRM."""
        self.ensure_one()
        if self.lead_id:
            return self.action_open_lead()
        lead = self.env["crm.lead"].create(
            {
                "name": _("%(campaign)s — %(target)s", campaign=self.campaign_id.name, target=self.name),
                "type": "opportunity",
                "partner_id": self.partner_id.id or False,
                "partner_name": self.name if not self.partner_id else False,
                "contact_name": self.contact_name or False,
                "function": self.function or False,
                "email_from": self.email or False,
                "phone": self.phone or False,
                "mobile": self.mobile or False,
                "website": self.website or False,
                "user_id": self.user_id.id or False,
                "company_id": self.company_id.id,
                "description": self._lead_description(),
                "outreach_target_id": self.id,
            }
        )
        self.lead_id = lead
        self.message_post(
            body=Markup("<p>%s</p>")
            % _("Opportunité créée : %s", lead.name)
        )
        return self.action_open_lead()

    def _lead_description(self):
        self.ensure_one()
        lines = [
            _("Issue de la campagne de démarchage « %s ».", self.campaign_id.name),
            _("Appels : %(calls)s — Courriels : %(emails)s", calls=self.call_count, emails=self.email_count),
        ]
        for touch in self.touch_ids.sorted("date"):
            lines.append(
                "%s — %s%s"
                % (
                    fields.Datetime.to_string(touch.date),
                    touch._kind_label(),
                    " : %s" % touch.summary if touch.summary else "",
                )
            )
        return Markup("<p>%s</p>") % Markup("<br/>").join(lines)

    def action_open_lead(self):
        self.ensure_one()
        if not self.lead_id:
            raise UserError(_("Aucune opportunité n'est liée à cette cible."))
        return {
            "type": "ir.actions.act_window",
            "res_model": "crm.lead",
            "res_id": self.lead_id.id,
            "view_mode": "form",
        }

    def action_open_touches(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "bf_outreach.action_bf_outreach_touch"
        )
        action["domain"] = [("target_id", "=", self.id)]
        action["context"] = dict(
            self.env.context,
            default_target_id=self.id,
            default_campaign_id=self.campaign_id.id,
        )
        return action

    # ------------------------------------------------------------------
    # Relances
    # ------------------------------------------------------------------
    def _ensure_followup_activity(self):
        """Crée, pour chaque cible due, une activité de relance si elle manque."""
        activities = self.env["mail.activity"]
        model_id = self.env["ir.model"]._get_id(self._name)
        call_type = self.env.ref("mail.mail_activity_data_call", raise_if_not_found=False)
        todo_type = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
        pending = set()
        if self.ids:
            pending = {
                activity.res_id
                for activity in self.env["mail.activity"].search(
                    [("res_model_id", "=", model_id), ("res_id", "in", self.ids)]
                )
            }
        for target in self:
            if target.id in pending or not target.next_action_date:
                continue
            if not target._is_solicitable():
                continue
            activity_type = call_type if target.next_action_kind == "call" else todo_type
            values = {
                "res_model_id": model_id,
                "res_id": target.id,
                "date_deadline": target.next_action_date,
                "user_id": (target.user_id or target.campaign_id.user_id or self.env.user).id,
                "summary": _("Appel de démarchage")
                if target.next_action_kind == "call"
                else _("Courriel de démarchage"),
            }
            if activity_type:
                values["activity_type_id"] = activity_type.id
            activities |= self.env["mail.activity"].create(values)
        return activities
