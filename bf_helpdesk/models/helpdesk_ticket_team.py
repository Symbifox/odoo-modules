import re

from odoo import api, fields, models


class HelpdeskTicketTeam(models.Model):
    _inherit = "helpdesk.ticket.team"

    slug = fields.Char(
        string="Slug",
        copy=False,
        index=True,
        help="URL-safe identifier used to expose a public support form at /support/<slug>.",
    )

    public_form_enabled = fields.Boolean(
        string="Public form enabled",
        default=False,
        help="Expose this team via /support/<slug>. Anonymous visitors can file a ticket.",
    )
    public_form_intro_html = fields.Html(
        string="Public form intro",
        sanitize=True,
        help="Optional intro text shown above the public form.",
    )
    public_form_success_html = fields.Html(
        string="Public form success message",
        sanitize=True,
    )

    hour_bank_id = fields.Many2one(
        comodel_name="hour.bank.client",
        string="Hour bank",
        help="Linked client banque d'heures. Tickets in this team will surface live balance and "
             "deduct timesheet hours through the project tied to this hour bank.",
    )
    hour_bank_alert_threshold_hours = fields.Float(
        string="Low balance threshold (h)",
        default=5.0,
    )

    ntfy_critical_enabled = fields.Boolean(
        string="ntfy on critical",
        default=False,
        help="Send a high-priority ntfy push when a ticket in this team is created or escalated to Very High priority.",
    )
    ntfy_priority_threshold = fields.Selection(
        selection=[
            ("2", "High"),
            ("3", "Very High"),
        ],
        string="ntfy priority threshold",
        default="3",
    )

    public_form_url = fields.Char(
        string="Public form URL",
        compute="_compute_public_form_url",
    )

    # --- CSAT survey ---
    csat_survey_id = fields.Many2one(
        comodel_name="survey.survey",
        string="Sondage CSAT",
        domain="[('active', '=', True)]",
        help="Sondage envoyé automatiquement à la fermeture d'un ticket de cette équipe.",
    )

    # --- SLA ---
    sla_response_hours = fields.Float(
        string="SLA — Première réponse (h)",
        default=0.0,
        help="Délai max avant la première réponse. 0 = pas de SLA.",
    )
    sla_resolve_hours = fields.Float(
        string="SLA — Résolution (h)",
        default=0.0,
        help="Délai max avant la fermeture du ticket. 0 = pas de SLA.",
    )

    # --- Auto-acknowledgement on public form ---
    public_form_auto_ack = fields.Boolean(
        string="Accusé réception auto",
        default=True,
        help="Envoie un courriel d'accusé réception immédiat quand un ticket est créé via le formulaire public.",
    )

    # --- Auto-tag rules ---
    auto_tag_rule_ids = fields.One2many(
        comodel_name="helpdesk.auto.tag.rule",
        inverse_name="team_id",
        string="Règles auto-tag",
    )

    public_form_tag_ids = fields.Many2many(
        comodel_name="helpdesk.ticket.tag",
        relation="helpdesk_team_public_form_tag_rel",
        column1="team_id",
        column2="tag_id",
        string="Public form tags",
        help="Tags exposés comme sélecteur sur le formulaire public. "
             "Le visiteur en choisit un, qui est appliqué au ticket. "
             "Souvent utilisé pour un sélecteur 'Département' (ex.: Direction, Opérations, "
             "Finance, RH, Admin, Autre).",
    )
    public_form_tag_label = fields.Char(
        string="Public form tag label",
        default="Département",
        help="Libellé affiché au-dessus du sélecteur de tags sur le formulaire public.",
    )
    public_form_tag_required = fields.Boolean(
        string="Public form tag required",
        default=False,
    )

    @api.depends("slug", "public_form_enabled")
    def _compute_public_form_url(self):
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        for team in self:
            if team.public_form_enabled and team.slug:
                team.public_form_url = f"{base_url}/support/{team.slug}"
            else:
                team.public_form_url = False

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("slug") and vals.get("name"):
                vals["slug"] = self._slugify(vals["name"])
        teams = super().create(vals_list)
        teams._ensure_unique_slugs()
        return teams

    def write(self, vals):
        if vals.get("name") and not vals.get("slug"):
            for team in self:
                if not team.slug:
                    vals_team = dict(vals)
                    vals_team["slug"] = self._slugify(vals.get("name"))
                    super(HelpdeskTicketTeam, team).write(vals_team)
            return True
        res = super().write(vals)
        if "slug" in vals:
            self._ensure_unique_slugs()
        return res

    @staticmethod
    def _slugify(value):
        value = (value or "").lower().strip()
        value = re.sub(r"[^a-z0-9]+", "-", value)
        return value.strip("-") or False

    def _ensure_unique_slugs(self):
        for team in self:
            if not team.slug:
                continue
            dup = self.search([
                ("slug", "=", team.slug),
                ("id", "!=", team.id),
            ], limit=1)
            if dup:
                team.slug = f"{team.slug}-{team.id}"
