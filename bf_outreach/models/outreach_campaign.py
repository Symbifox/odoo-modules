# -*- coding: utf-8 -*-
import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

# États de campagne pour lesquels la cadence de relance est suspendue.
FROZEN_STATES = ("paused", "done", "cancelled")


class BfOutreachCampaign(models.Model):
    _name = "bf.outreach.campaign"
    _description = "Outreach campaign"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date_start desc, id desc"

    name = fields.Char(string="Campaign", required=True, tracking=True)
    active = fields.Boolean(default=True)
    color = fields.Integer(string="Color")
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.company,
    )
    user_id = fields.Many2one(
        "res.users",
        string="Owner",
        default=lambda self: self.env.user,
        tracking=True,
    )
    member_ids = fields.Many2many(
        "res.users",
        "bf_outreach_campaign_user_rel",
        "campaign_id",
        "user_id",
        string="Team",
        help="Users the new targets are shared out among, in rotation.",
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="On behalf of",
        help="Client the campaign is run for, when it is a mandate.",
    )
    date_start = fields.Date(
        string="Start date",
        default=fields.Date.context_today,
        tracking=True,
        help="Where the cadence starts: a target never contacted is due "
             "on this date (or on the date it was added, if that is "
             "later).",
    )
    date_end = fields.Date(string="Planned end date", tracking=True)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("running", "Running"),
            ("paused", "Paused"),
            ("done", "Completed"),
            ("cancelled", "Cancelled"),
        ],
        string="Status",
        default="draft",
        required=True,
        tracking=True,
    )
    description = fields.Html(string="Notes and pitch")
    mail_template_id = fields.Many2one(
        "mail.template",
        string="Email template",
        domain="[('model', '=', 'bf.outreach.target')]",
        help="Template offered by the \"Send the email\" button on "
             "targets. Written against the \"Outreach target\" model, so "
             "it can pull in the name, the contact person and the "
             "campaign.",
    )

    # ------------------------------------------------------------------
    # Cadence
    # ------------------------------------------------------------------
    call_target_count = fields.Integer(
        string="Planned calls per target",
        default=3,
        help="How many calls to place to each target. 0 = no call planned.",
    )
    email_target_count = fields.Integer(
        string="Planned emails per target",
        default=3,
        help="How many emails to send to each target. 0 = no email planned.",
    )
    call_interval_days = fields.Integer(
        string="Interval between two calls (days)", default=7
    )
    email_interval_days = fields.Integer(
        string="Interval between two emails (days)", default=14
    )
    letter_target_count = fields.Integer(
        string="Planned letters per target",
        default=0,
        help="How many letters to post to each target. 0 = no letter planned.",
    )
    letter_interval_days = fields.Integer(
        string="Interval between two letters (days)", default=30
    )
    stop_on_reply = fields.Boolean(
        string="Stop at the first reply",
        default=True,
        help="As soon as a target replies, it leaves the automatic "
             "cadence: follow-up carries on by hand, through the stage "
             "and the activities.",
    )
    working_days_only = fields.Boolean(
        string="Working days only",
        default=True,
        help="Pushes to Monday a follow-up that would land on a Saturday "
             "or a Sunday.",
    )
    activity_mode = fields.Selection(
        [
            ("none", "None"),
            ("campaign", "One daily activity on the campaign"),
            ("target", "One activity per due target"),
        ],
        string="Automatic reminders",
        default="campaign",
        required=True,
        help="How the daily reminder of due contacts is created. \"Per "
             "target\" suits short lists; on a long list, prefer the "
             "single reminder on the campaign.",
    )

    # ------------------------------------------------------------------
    # Contenu
    # ------------------------------------------------------------------
    target_ids = fields.One2many(
        "bf.outreach.target", "campaign_id", string="Targets"
    )
    touch_ids = fields.One2many(
        "bf.outreach.touch", "campaign_id", string="Interactions"
    )
    stage_ids = fields.Many2many(
        "bf.outreach.stage",
        "bf_outreach_stage_campaign_rel",
        "campaign_id",
        "stage_id",
        string="Campaign-specific stages",
        help="Leave empty to use the stages shared by every campaign.",
    )

    # ------------------------------------------------------------------
    # Statistiques (non stockées : recalculées à la lecture)
    # ------------------------------------------------------------------
    target_count = fields.Integer(
        string="Number of targets", compute="_compute_statistics"
    )
    open_target_count = fields.Integer(
        string="Open targets", compute="_compute_statistics"
    )
    won_count = fields.Integer(string="Won targets", compute="_compute_statistics")
    lost_count = fields.Integer(string="Dropped targets", compute="_compute_statistics")
    contacted_count = fields.Integer(
        string="Targets contacted", compute="_compute_statistics"
    )
    untouched_count = fields.Integer(
        string="Never contacted", compute="_compute_statistics"
    )
    replied_count = fields.Integer(
        string="Targets that replied", compute="_compute_statistics"
    )
    overdue_count = fields.Integer(
        string="Overdue follow-ups", compute="_compute_statistics"
    )
    due_today_count = fields.Integer(
        string="Due today", compute="_compute_statistics"
    )
    call_count = fields.Integer(string="Calls", compute="_compute_statistics")
    email_count = fields.Integer(string="Emails", compute="_compute_statistics")
    letter_count = fields.Integer(string="Letters", compute="_compute_statistics")
    touch_count = fields.Integer(
        string="Number of interactions", compute="_compute_statistics"
    )
    planned_touch_count = fields.Integer(
        string="Planned touches", compute="_compute_statistics"
    )
    coverage_rate = fields.Float(
        string="Coverage (%)",
        compute="_compute_statistics",
        help="Share of targets contacted at least once.",
    )
    reply_rate = fields.Float(
        string="Reply rate (%)",
        compute="_compute_statistics",
        help="Share of contacted targets that replied.",
    )
    conversion_rate = fields.Float(
        string="Conversion rate (%)",
        compute="_compute_statistics",
        help="Share of targets that reached a \"Won\" stage.",
    )
    progress = fields.Float(
        string="Progress (%)",
        compute="_compute_statistics",
        help="Touches made against the touches the cadence plans for.",
    )

    # ------------------------------------------------------------------
    # Contraintes
    # ------------------------------------------------------------------
    @api.constrains(
        "call_target_count",
        "email_target_count",
        "letter_target_count",
        "call_interval_days",
        "email_interval_days",
        "letter_interval_days",
    )
    def _check_cadence(self):
        labels = {
            "call": _("calls"),
            "email": _("emails"),
            "letter": _("letters"),
        }
        for campaign in self:
            for channel, label in labels.items():
                goal = campaign["%s_target_count" % channel]
                interval = campaign["%s_interval_days" % channel]
                if goal < 0:
                    raise ValidationError(
                        _("The planned number of %s cannot be negative.", label)
                    )
                if goal > 1 and interval < 1:
                    raise ValidationError(
                        _(
                            "The interval between two %s must be at least "
                            "one day.",
                            label,
                        )
                    )

    @api.constrains("date_start", "date_end")
    def _check_dates(self):
        for campaign in self:
            if campaign.date_start and campaign.date_end and campaign.date_end < campaign.date_start:
                raise ValidationError(
                    _("The end date cannot come before the start date.")
                )

    # ------------------------------------------------------------------
    # Calculs
    # ------------------------------------------------------------------
    @api.depends(
        "target_ids.stage_type",
        "target_ids.touch_count",
        "target_ids.call_count",
        "target_ids.email_count",
        "target_ids.letter_count",
        "target_ids.has_reply",
        "target_ids.next_action_date",
        "call_target_count",
        "email_target_count",
        "letter_target_count",
    )
    def _compute_statistics(self):
        today = fields.Date.context_today(self)
        Target = self.env["bf.outreach.target"]
        stats = {campaign_id: self._empty_stats() for campaign_id in self.ids}

        if self.ids:
            base_domain = [("campaign_id", "in", self.ids)]
            for campaign, stage_type, count in Target._read_group(
                base_domain, ["campaign_id", "stage_type"], ["__count"]
            ):
                entry = stats[campaign.id]
                entry["target_count"] += count
                if stage_type == "won":
                    entry["won_count"] += count
                elif stage_type == "lost":
                    entry["lost_count"] += count
                else:
                    entry["open_target_count"] += count

            for field_name, domain in (
                ("contacted_count", [("touch_count", ">", 0)]),
                ("replied_count", [("has_reply", "=", True)]),
                ("overdue_count", [("next_action_date", "<", today)]),
                ("due_today_count", [("next_action_date", "=", today)]),
            ):
                for campaign, count in Target._read_group(
                    base_domain + domain, ["campaign_id"], ["__count"]
                ):
                    stats[campaign.id][field_name] = count

            for campaign, calls, emails, letters, touches in Target._read_group(
                base_domain,
                ["campaign_id"],
                [
                    "call_count:sum",
                    "email_count:sum",
                    "letter_count:sum",
                    "touch_count:sum",
                ],
            ):
                entry = stats[campaign.id]
                entry["call_count"] = calls or 0
                entry["email_count"] = emails or 0
                entry["letter_count"] = letters or 0
                entry["touch_count"] = touches or 0

        for campaign in self:
            entry = stats.get(campaign.id) or self._empty_stats()
            for field_name, value in entry.items():
                campaign[field_name] = value
            campaign.untouched_count = entry["target_count"] - entry["contacted_count"]
            campaign.planned_touch_count = entry["target_count"] * (
                max(campaign.call_target_count, 0)
                + max(campaign.email_target_count, 0)
                + max(campaign.letter_target_count, 0)
            )
            campaign.coverage_rate = (
                100.0 * entry["contacted_count"] / entry["target_count"]
                if entry["target_count"]
                else 0.0
            )
            campaign.reply_rate = (
                100.0 * entry["replied_count"] / entry["contacted_count"]
                if entry["contacted_count"]
                else 0.0
            )
            campaign.conversion_rate = (
                100.0 * entry["won_count"] / entry["target_count"]
                if entry["target_count"]
                else 0.0
            )
            campaign.progress = (
                min(100.0, 100.0 * entry["touch_count"] / campaign.planned_touch_count)
                if campaign.planned_touch_count
                else 0.0
            )

    @api.model
    def _empty_stats(self):
        return {
            "target_count": 0,
            "open_target_count": 0,
            "won_count": 0,
            "lost_count": 0,
            "contacted_count": 0,
            "replied_count": 0,
            "overdue_count": 0,
            "due_today_count": 0,
            "call_count": 0,
            "email_count": 0,
            "letter_count": 0,
            "touch_count": 0,
        }

    # ------------------------------------------------------------------
    # Cycle de vie
    # ------------------------------------------------------------------
    def action_start(self):
        self.filtered(lambda c: c.state != "running").write({"state": "running"})
        return True

    def action_pause(self):
        self.filtered(lambda c: c.state == "running").write({"state": "paused"})
        return True

    def action_done(self):
        self.write({"state": "done"})
        return True

    def action_cancel(self):
        self.write({"state": "cancelled"})
        return True

    def action_reset_to_draft(self):
        self.write({"state": "draft"})
        return True

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------
    def _action_targets(self, name, domain=None, context=None):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "bf_outreach.action_bf_outreach_target"
        )
        action["display_name"] = name
        action["domain"] = [("campaign_id", "=", self.id)] + (domain or [])
        action["context"] = dict(
            self.env.context,
            default_campaign_id=self.id,
            search_default_group_by_stage=1,
            **(context or {}),
        )
        return action

    def action_view_targets(self):
        return self._action_targets(_("Targets"))

    def action_view_overdue(self):
        today = fields.Date.to_string(fields.Date.context_today(self))
        return self._action_targets(
            _("Overdue follow-ups"), domain=[("next_action_date", "<", today)]
        )

    def action_view_due_today(self):
        today = fields.Date.to_string(fields.Date.context_today(self))
        return self._action_targets(
            _("Due today"), domain=[("next_action_date", "<=", today)]
        )

    def action_view_untouched(self):
        return self._action_targets(
            _("Never contacted"), domain=[("touch_count", "=", 0)]
        )

    def action_view_touches(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "bf_outreach.action_bf_outreach_touch"
        )
        action["domain"] = [("campaign_id", "=", self.id)]
        action["context"] = dict(self.env.context, default_campaign_id=self.id)
        return action

    def action_import_targets_file(self):
        """Ouvre l'importateur standard, campagne déjà choisie.

        Les listes de démarchage arrivent en XLSX ou en CSV ; l'importateur
        d'Odoo fait le travail, il suffit de lui pré-remplir la campagne.
        """
        self.ensure_one()
        return {
            "type": "ir.actions.client",
            "tag": "import",
            "params": {
                "model": "bf.outreach.target",
                "context": dict(self.env.context, default_campaign_id=self.id),
            },
        }

    def action_add_targets(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Add targets"),
            "res_model": "bf.outreach.target.import.wizard",
            "view_mode": "form",
            "target": "new",
            "context": dict(self.env.context, default_campaign_id=self.id),
        }

    # ------------------------------------------------------------------
    # Répartition des cibles
    # ------------------------------------------------------------------
    def _next_assignee(self, taken=None):
        """Retourne le prochain utilisateur de l'équipe, à tour de rôle.

        `taken` permet de tenir compte des cibles créées dans la même
        transaction, qui ne sont pas encore comptées en base.
        """
        self.ensure_one()
        members = self.member_ids or self.user_id
        if not members:
            return self.env["res.users"]
        counters = dict.fromkeys(members.ids, 0)
        if self.id:
            for user, count in self.env["bf.outreach.target"]._read_group(
                [("campaign_id", "=", self.id), ("user_id", "in", members.ids)],
                ["user_id"],
                ["__count"],
            ):
                counters[user.id] = count
        for user_id, count in (taken or {}).items():
            if user_id in counters:
                counters[user_id] += count
        # Le membre le moins chargé ; à égalité, l'ordre de l'équipe tranche.
        best = min(members, key=lambda user: (counters.get(user.id, 0), members.ids.index(user.id)))
        return best

    # ------------------------------------------------------------------
    # Langue des textes écrits hors session
    # ------------------------------------------------------------------
    def _outreach_lang(self, user=None):
        """Langue de qui lira un texte écrit sans session (action planifiée).

        L'usager visé, à défaut le partenaire de la société de la campagne, à
        défaut la langue de l'environnement.
        """
        company = self[:1].company_id or self.env.company
        return (user and user.lang) or company.partner_id.lang or self.env.lang

    # ------------------------------------------------------------------
    # Relances automatiques
    # ------------------------------------------------------------------
    @api.model
    def _cron_generate_followup_activities(self):
        campaigns = self.search(
            [("state", "=", "running"), ("activity_mode", "!=", "none")]
        )
        for campaign in campaigns:
            try:
                campaign._generate_followup_activities()
            except Exception:  # noqa: BLE001 - une campagne ne doit pas bloquer les autres
                _logger.exception(
                    "bf_outreach: échec de la génération des relances pour la campagne %s",
                    campaign.id,
                )
        return True

    def _generate_followup_activities(self):
        self.ensure_one()
        today = fields.Date.context_today(self)
        due_targets = self.env["bf.outreach.target"].search(
            [
                ("campaign_id", "=", self.id),
                ("next_action_date", "<=", today),
                ("next_action_date", "!=", False),
            ]
        )
        due_targets = due_targets.filtered(lambda t: t._is_solicitable())
        if not due_targets:
            return self.env["mail.activity"]
        if self.activity_mode == "target":
            return due_targets._ensure_followup_activity()
        return self._ensure_campaign_digest_activity(due_targets)

    def _ensure_campaign_digest_activity(self, due_targets):
        """Une seule activité par campagne et par jour, listant les contacts dus."""
        self.ensure_one()
        today = fields.Date.context_today(self)
        activity_type = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
        model_id = self.env["ir.model"]._get_id(self._name)
        existing = self.env["mail.activity"].search(
            [
                ("res_model_id", "=", model_id),
                ("res_id", "=", self.id),
                ("date_deadline", "=", today),
                ("activity_type_id", "=", activity_type.id if activity_type else False),
            ],
            limit=1,
        )
        overdue = due_targets.filtered(
            lambda t: t.next_action_date and t.next_action_date < today
        )
        # Écrit par l'action planifiée (aucune langue) : langue du responsable
        # qui recevra l'activité.
        assignee = self.user_id or self.env.user
        summary = self.with_context(lang=self._outreach_lang(assignee)).env._(
            "Outreach: %(due)s contact(s) due, %(late)s of them overdue",
            due=len(due_targets),
            late=len(overdue),
        )
        if existing:
            existing.summary = summary
            return existing
        values = {
            "res_model_id": model_id,
            "res_id": self.id,
            "summary": summary,
            "date_deadline": today,
            "user_id": assignee.id,
        }
        if activity_type:
            values["activity_type_id"] = activity_type.id
        return self.env["mail.activity"].create(values)

    def action_generate_followups_now(self):
        """Bouton : générer les relances du jour sans attendre l'action planifiée."""
        activities = self.env["mail.activity"]
        for campaign in self.filtered(lambda c: c.activity_mode != "none"):
            activities |= campaign._generate_followup_activities()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success" if activities else "info",
                "message": _("%s reminder(s) created or updated.", len(activities)),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
