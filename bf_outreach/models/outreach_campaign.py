# -*- coding: utf-8 -*-
import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

# États de campagne pour lesquels la cadence de relance est suspendue.
FROZEN_STATES = ("paused", "done", "cancelled")


class BfOutreachCampaign(models.Model):
    _name = "bf.outreach.campaign"
    _description = "Campagne de démarchage"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date_start desc, id desc"

    name = fields.Char(string="Campagne", required=True, tracking=True)
    active = fields.Boolean(default=True)
    color = fields.Integer(string="Couleur")
    company_id = fields.Many2one(
        "res.company",
        string="Société",
        required=True,
        default=lambda self: self.env.company,
    )
    user_id = fields.Many2one(
        "res.users",
        string="Responsable",
        default=lambda self: self.env.user,
        tracking=True,
    )
    member_ids = fields.Many2many(
        "res.users",
        "bf_outreach_campaign_user_rel",
        "campaign_id",
        "user_id",
        string="Équipe",
        help="Utilisateurs entre qui les nouvelles cibles sont réparties à tour de rôle.",
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Client mandant",
        help="Client pour qui la campagne est menée, lorsqu'il s'agit d'un mandat.",
    )
    date_start = fields.Date(
        string="Début",
        default=fields.Date.context_today,
        tracking=True,
        help="Point de départ de la cadence : une cible jamais contactée est "
        "due à cette date (ou à sa date d'ajout si elle est postérieure).",
    )
    date_end = fields.Date(string="Fin prévue", tracking=True)
    state = fields.Selection(
        [
            ("draft", "Brouillon"),
            ("running", "En cours"),
            ("paused", "En pause"),
            ("done", "Terminée"),
            ("cancelled", "Annulée"),
        ],
        string="État",
        default="draft",
        required=True,
        tracking=True,
    )
    description = fields.Html(string="Notes et argumentaire")
    mail_template_id = fields.Many2one(
        "mail.template",
        string="Modèle de courriel",
        domain="[('model', '=', 'bf.outreach.target')]",
        help="Modèle proposé par le bouton « Envoyer le courriel » sur les cibles. "
        "Rédigé sur le modèle « Cible de démarchage », il peut donc reprendre le "
        "nom, la personne-ressource et la campagne.",
    )

    # ------------------------------------------------------------------
    # Cadence
    # ------------------------------------------------------------------
    call_target_count = fields.Integer(
        string="Appels visés par cible",
        default=3,
        help="Nombre d'appels à faire à chaque cible. 0 = aucun appel prévu.",
    )
    email_target_count = fields.Integer(
        string="Courriels visés par cible",
        default=3,
        help="Nombre de courriels à envoyer à chaque cible. 0 = aucun courriel prévu.",
    )
    call_interval_days = fields.Integer(
        string="Intervalle entre deux appels (jours)", default=7
    )
    email_interval_days = fields.Integer(
        string="Intervalle entre deux courriels (jours)", default=14
    )
    letter_target_count = fields.Integer(
        string="Lettres visées par cible",
        default=0,
        help="Nombre de lettres à poster à chaque cible. 0 = aucune lettre prévue.",
    )
    letter_interval_days = fields.Integer(
        string="Intervalle entre deux lettres (jours)", default=30
    )
    stop_on_reply = fields.Boolean(
        string="Arrêter à la première réponse",
        default=True,
        help="Dès qu'une cible répond, elle sort de la cadence automatique : "
        "le suivi se poursuit à la main, via l'étape et les activités.",
    )
    working_days_only = fields.Boolean(
        string="Jours ouvrables seulement",
        default=True,
        help="Reporte au lundi une relance qui tomberait un samedi ou un dimanche.",
    )
    activity_mode = fields.Selection(
        [
            ("none", "Aucune"),
            ("campaign", "Une activité quotidienne sur la campagne"),
            ("target", "Une activité par cible due"),
        ],
        string="Rappels automatiques",
        default="campaign",
        required=True,
        help="Comment le rappel quotidien des contacts dus est créé. "
        "« Par cible » convient aux petites listes ; sur une grande liste, "
        "préférer le rappel unique sur la campagne.",
    )

    # ------------------------------------------------------------------
    # Contenu
    # ------------------------------------------------------------------
    target_ids = fields.One2many(
        "bf.outreach.target", "campaign_id", string="Cibles"
    )
    touch_ids = fields.One2many(
        "bf.outreach.touch", "campaign_id", string="Interactions"
    )
    stage_ids = fields.Many2many(
        "bf.outreach.stage",
        "bf_outreach_stage_campaign_rel",
        "campaign_id",
        "stage_id",
        string="Étapes propres à la campagne",
        help="Laisser vide pour utiliser les étapes communes à toutes les campagnes.",
    )

    # ------------------------------------------------------------------
    # Statistiques (non stockées : recalculées à la lecture)
    # ------------------------------------------------------------------
    target_count = fields.Integer(
        string="Nombre de cibles", compute="_compute_statistics"
    )
    open_target_count = fields.Integer(
        string="Dossiers ouverts", compute="_compute_statistics"
    )
    won_count = fields.Integer(string="Gagnées", compute="_compute_statistics")
    lost_count = fields.Integer(string="Abandonnées", compute="_compute_statistics")
    contacted_count = fields.Integer(
        string="Cibles contactées", compute="_compute_statistics"
    )
    untouched_count = fields.Integer(
        string="Jamais contactées", compute="_compute_statistics"
    )
    replied_count = fields.Integer(
        string="Cibles ayant répondu", compute="_compute_statistics"
    )
    overdue_count = fields.Integer(
        string="Relances en retard", compute="_compute_statistics"
    )
    due_today_count = fields.Integer(
        string="À faire aujourd'hui", compute="_compute_statistics"
    )
    call_count = fields.Integer(string="Appels", compute="_compute_statistics")
    email_count = fields.Integer(string="Courriels", compute="_compute_statistics")
    letter_count = fields.Integer(string="Lettres", compute="_compute_statistics")
    touch_count = fields.Integer(
        string="Nombre d'interactions", compute="_compute_statistics"
    )
    planned_touch_count = fields.Integer(
        string="Contacts prévus", compute="_compute_statistics"
    )
    coverage_rate = fields.Float(
        string="Couverture (%)",
        compute="_compute_statistics",
        help="Part des cibles contactées au moins une fois.",
    )
    reply_rate = fields.Float(
        string="Taux de réponse (%)",
        compute="_compute_statistics",
        help="Part des cibles contactées qui ont répondu.",
    )
    conversion_rate = fields.Float(
        string="Taux de conversion (%)",
        compute="_compute_statistics",
        help="Part des cibles arrivées à une étape « Gagnée ».",
    )
    progress = fields.Float(
        string="Avancement (%)",
        compute="_compute_statistics",
        help="Contacts réalisés par rapport aux contacts prévus par la cadence.",
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
            "call": _("appels"),
            "email": _("courriels"),
            "letter": _("lettres"),
        }
        for campaign in self:
            for channel, label in labels.items():
                goal = campaign["%s_target_count" % channel]
                interval = campaign["%s_interval_days" % channel]
                if goal < 0:
                    raise ValidationError(
                        _("Le nombre de %s visés ne peut pas être négatif.", label)
                    )
                if goal > 1 and interval < 1:
                    raise ValidationError(
                        _(
                            "L'intervalle entre deux %s doit être d'au moins une journée.",
                            label,
                        )
                    )

    @api.constrains("date_start", "date_end")
    def _check_dates(self):
        for campaign in self:
            if campaign.date_start and campaign.date_end and campaign.date_end < campaign.date_start:
                raise ValidationError(
                    _("La date de fin ne peut pas précéder la date de début.")
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
        return self._action_targets(_("Cibles"))

    def action_view_overdue(self):
        today = fields.Date.to_string(fields.Date.context_today(self))
        return self._action_targets(
            _("Relances en retard"), domain=[("next_action_date", "<", today)]
        )

    def action_view_due_today(self):
        today = fields.Date.to_string(fields.Date.context_today(self))
        return self._action_targets(
            _("À faire aujourd'hui"), domain=[("next_action_date", "<=", today)]
        )

    def action_view_untouched(self):
        return self._action_targets(
            _("Jamais contactées"), domain=[("touch_count", "=", 0)]
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
            "name": _("Ajouter des cibles"),
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
        summary = _("Démarchage : %(due)s contact(s) dus, dont %(late)s en retard") % {
            "due": len(due_targets),
            "late": len(overdue),
        }
        if existing:
            existing.summary = summary
            return existing
        values = {
            "res_model_id": model_id,
            "res_id": self.id,
            "summary": summary,
            "date_deadline": today,
            "user_id": (self.user_id or self.env.user).id,
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
                "message": _("%s rappel(s) créé(s) ou mis à jour.", len(activities)),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
