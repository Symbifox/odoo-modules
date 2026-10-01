import re

from odoo import _, api, fields, models
from odoo.tools import is_html_empty as html_is_empty


def _format_hour(value, en=False):
    """8.5 → « 8 h 30 » ; en anglais, « 8:30 a.m. ». 17.0 → « 17 h », « 5 p.m. »."""
    hours = int(value)
    minutes = int(round((value - hours) * 60))
    if minutes == 60:
        hours, minutes = hours + 1, 0
    if en:
        suffix = "a.m." if hours < 12 else "p.m."
        h12 = hours % 12 or 12
        return f"{h12}:{minutes:02d} {suffix}" if minutes else f"{h12} {suffix}"
    return f"{hours} h {minutes:02d}" if minutes else f"{hours} h"


# Les courriels et le portail parlent la langue du client ; ce module est
# écrit en français, et l'anglais ne peut pas passer par un .po (le français
# occupe la case source). Les phrases montrées au client ont donc leurs deux
# versions ici.
_DAYS = {
    "fr": ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"],
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
}


class HelpdeskTicketTeam(models.Model):
    _inherit = "helpdesk.ticket.team"

    slug = fields.Char(
        string="Adresse courte (slug)",
        copy=False,
        index=True,
        help="Identifiant d'adresse du formulaire public de soutien : /support/<adresse courte>.",
    )

    public_form_enabled = fields.Boolean(
        string="Formulaire public actif",
        default=False,
        help="Ouvre le formulaire /support/<adresse courte> : un visiteur sans compte peut y déposer une demande.",
    )
    public_form_intro_html = fields.Html(
        string="Introduction du formulaire public",
        sanitize=True,
        help="Texte d'introduction facultatif, affiché au-dessus du formulaire public.",
    )
    public_form_success_html = fields.Html(
        string="Message de confirmation du formulaire public",
        sanitize=True,
    )

    hour_bank_id = fields.Many2one(
        comodel_name="hour.bank.client",
        string="Banque d'heures",
        help="Banque d'heures du client. Les billets de l'équipe en affichent le solde et "
             "en déduisent les heures saisies, par le projet lié à cette banque.",
        groups="base.group_user",
    )
    hour_bank_alert_threshold_hours = fields.Float(
        string="Seuil de solde bas (h)",
        default=5.0,
        groups="base.group_user",
    )

    ntfy_critical_enabled = fields.Boolean(
        string="Alerte ntfy des billets critiques",
        default=False,
        help="Envoie une alerte ntfy prioritaire quand un billet de l'équipe est créé ou passe en priorité très élevée.",
        groups="base.group_user",
    )
    ntfy_priority_threshold = fields.Selection(
        selection=[
            ("2", "High"),
            ("3", "Very High"),
        ],
        string="Priorité minimale pour ntfy",
        default="3",
        groups="base.group_user",
    )

    public_form_url = fields.Char(
        string="Adresse du formulaire public",
        compute="_compute_public_form_url",
    )

    # --- CSAT survey ---
    csat_survey_id = fields.Many2one(
        comodel_name="survey.survey",
        string="Sondage CSAT",
        domain="[('active', '=', True)]",
        help="Sondage envoyé automatiquement à la fermeture d'une demande de cette équipe.",
    )

    # --- Satisfaction v2 (18.0.4.7.0) ---
    # « survey » garde l'ancien envoi par survey.survey, immédiat à la
    # fermeture ; « native » est le sondage d'une question de bf_helpdesk.
    csat_mode = fields.Selection(
        [
            ("none", "Aucun sondage"),
            ("native", "Sondage d'une question"),
            ("survey", "Sondage (module Sondages)"),
        ],
        string="Satisfaction",
        default="none",
        required=True,
    )
    csat_delay_hours = fields.Integer(
        string="Envoi après (h)",
        default=24,
        help="Délai entre la fermeture et l'envoi. Un billet rouvert "
             "entre-temps n'est pas sondé. 0 = à la fermeture.",
    )
    csat_expiry_days = fields.Integer(
        string="Réponse acceptée pendant (jours)", default=28,
    )
    csat_ces_enabled = fields.Boolean(
        string="Question d'effort (CES)",
        help="Ajoute « L'équipe m'a rendu la tâche facile » à la page de "
             "réponse, facultative pour le client.",
    )
    csat_followup_user_id = fields.Many2one(
        "res.users", string="Suivi des notes négatives",
        domain="[('share', '=', False)]",
        help="Reçoit l'activité de suivi d'une note de 1 ou 2. "
             "Vide = l'agent du billet.",
        groups="base.group_user",
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
    sla_calendar_id = fields.Many2one(
        comodel_name="resource.calendar",
        string="Horaire d'affaires (SLA)",
        default=lambda self: self.env.company.resource_calendar_id,
        help="Les délais se comptent en heures ouvrées de cet horaire, "
             "congés compris. Vide = heures civiles, 24 h sur 24.",
    )

    # --- Accusé de réception, tous canaux (18.0.4.7.0) ---
    # Remplace `public_form_auto_ack`, qui ne couvrait que /support/<slug>.
    # Une équipe existante garde ce qu'elle avait (le canal Web si l'accusé
    # était coché) : la migration ne met aucun nouveau courriel en route.
    ack_channel_ids = fields.Many2many(
        comodel_name="helpdesk.ticket.channel",
        relation="helpdesk_team_ack_channel_rel",
        column1="team_id",
        column2="channel_id",
        string="Accusé de réception pour",
        help="Canaux dont les nouveaux billets reçoivent un accusé de "
             "réception, une seule fois par billet. Vide = aucun accusé. "
             "Vide par défaut, même pour une équipe neuve : aucun envoi sans "
             "réglage.",
    )
    ack_notice_html = fields.Html(
        string="Avis temporaire",
        sanitize=True,
        translate=True,
        help="Ajouté à l'accusé de réception, par exemple pour annoncer des "
             "délais allongés pendant les congés.",
    )
    ack_notice_until = fields.Date(
        string="Avis affiché jusqu'au",
        help="Dernier jour où l'avis temporaire est ajouté. Vide = sans fin.",
    )
    business_hours_text = fields.Char(
        string="Horaire annoncé au client",
        translate=True,
        help="Par exemple « du lundi au vendredi, de 8 h 30 à 17 h ». "
             "Vide = résumé tiré de l'horaire d'affaires de l'équipe.",
    )

    # --- Relances d'un billet en attente du client ---
    # Désactivées par défaut : les activer met des courriels en route.
    reminder_enabled = fields.Boolean(
        string="Relancer le client en attente",
        default=False,
        help="Deux relances automatiques d'un billet en attente du client, "
             "puis sa fermeture annoncée. Une réponse du client arrête tout.",
    )
    reminder_first_days = fields.Integer(
        string="Première relance après (jours)", default=3,
        help="Jours ouvrés si l'équipe a un horaire d'affaires.",
    )
    reminder_second_days = fields.Integer(
        string="Deuxième relance après (jours)", default=4,
    )
    reminder_close_days = fields.Integer(
        string="Fermeture après (jours)", default=3,
    )
    reminder_close_stage_id = fields.Many2one(
        comodel_name="helpdesk.ticket.stage",
        string="Étape de fermeture",
        domain="[('closed', '=', True)]",
        help="Étape où le billet passe sans réponse après les deux "
             "relances. Vide = l'agent reçoit une activité à la place.",
    )

    business_hours_summary = fields.Char(
        string="Horaire annoncé",
        compute="_compute_business_hours_summary",
    )
    ack_notice_active = fields.Boolean(
        string="Avis temporaire en cours",
        compute="_compute_ack_notice_active",
    )

    @api.model
    def _default_ack_channels(self):
        channels = self.env["helpdesk.ticket.channel"]
        for xmlid in (
            "helpdesk_mgmt.helpdesk_ticket_channel_web",
            "helpdesk_mgmt.helpdesk_ticket_channel_email",
        ):
            channels |= self.env.ref(xmlid, raise_if_not_found=False) or channels
        return channels

    @api.depends("ack_notice_html", "ack_notice_until")
    def _compute_ack_notice_active(self):
        today = fields.Date.context_today(self)
        for team in self:
            until = team.ack_notice_until
            team.ack_notice_active = bool(
                not html_is_empty(team.ack_notice_html)
                and (not until or until >= today)
            )

    @api.depends(
        "business_hours_text",
        "sla_calendar_id.two_weeks_calendar",
        "sla_calendar_id.attendance_ids",
    )
    @api.depends_context("lang")
    def _compute_business_hours_summary(self):
        for team in self:
            team.business_hours_summary = team._business_hours_summary()

    def _business_hours_summary(self):
        """L'horaire annoncé : le texte saisi, sinon un résumé du calendrier.

        Le résumé prend, pour chaque jour, la première arrivée et le dernier
        départ (la pause du midi ne s'annonce pas), puis regroupe les jours
        consécutifs qui ont les mêmes heures. Un horaire en semaines paires
        et impaires ne se résume pas : on n'annonce rien plutôt que le faux.
        """
        self.ensure_one()
        if self.business_hours_text:
            return self.business_hours_text
        calendar = self.sla_calendar_id
        if not calendar or calendar.two_weeks_calendar:
            return ""
        spans = {}
        for att in calendar.attendance_ids:
            if att.day_period == "lunch" or att.display_type:
                continue
            day = int(att.dayofweek)
            start, end = spans.get(day, (att.hour_from, att.hour_to))
            spans[day] = (min(start, att.hour_from), max(end, att.hour_to))
        if not spans:
            return ""
        en = (self.env.lang or "").startswith("en")
        days = _DAYS["en" if en else "fr"]
        groups = []
        for day in sorted(spans):
            if groups and groups[-1][1] == day - 1 and groups[-1][2] == spans[day]:
                groups[-1][1] = day
            else:
                groups.append([day, day, spans[day]])
        parts = []
        for first, last, (start, end) in groups:
            if en:
                hours = f"{_format_hour(start, True)} to {_format_hour(end, True)}"
                span = days[first] if first == last else f"{days[first]} to {days[last]}"
                parts.append(f"{span}, {hours}")
            else:
                hours = f"de {_format_hour(start)} à {_format_hour(end)}"
                span = (f"le {days[first]}" if first == last
                        else f"du {days[first]} au {days[last]}")
                parts.append(f"{span}, {hours}")
        return ("; " if en else " ; ").join(parts)

    # --- Auto-tag rules ---
    auto_tag_rule_ids = fields.One2many(
        comodel_name="helpdesk.auto.tag.rule",
        inverse_name="team_id",
        string="Règles auto-tag",
        groups="base.group_user",
    )

    public_form_tag_ids = fields.Many2many(
        comodel_name="helpdesk.ticket.tag",
        relation="helpdesk_team_public_form_tag_rel",
        column1="team_id",
        column2="tag_id",
        string="Étiquettes du formulaire public",
        help="Tags exposés comme sélecteur sur le formulaire public. "
             "Le visiteur en choisit un, qui est appliqué au ticket. "
             "Souvent utilisé pour un sélecteur 'Département' (ex.: Direction, Opérations, "
             "Finance, RH, Admin, Autre).",
    )
    public_form_tag_label = fields.Char(
        string="Libellé du choix d'étiquette",
        default="Département",
        help="Libellé affiché au-dessus du sélecteur de tags sur le formulaire public.",
    )
    public_form_tag_required = fields.Boolean(
        string="Choix d'étiquette obligatoire",
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
            # Une équipe créée avec un sondage survey garde ce mode.
            if vals.get("csat_survey_id") and "csat_mode" not in vals:
                vals["csat_mode"] = "survey"
            if not vals.get("slug") and vals.get("name"):
                vals["slug"] = self._slugify(vals["name"])
        teams = super().create(vals_list)
        teams._ensure_unique_slugs()
        return teams

    def write(self, vals):
        if vals.get("name") and not vals.get("slug"):
            # Une équipe sans adresse courte en reçoit une tirée du nom ; les
            # autres s'écrivent normalement. L'ancien code rendait True sans rien
            # écrire pour elles : renommer une équipe ne faisait rien, et les
            # autres valeurs du même enregistrement étaient perdues.
            sans_slug = self.filtered(lambda t: not t.slug)
            for team in sans_slug:
                super(HelpdeskTicketTeam, team).write(
                    dict(vals, slug=self._slugify(vals.get("name"))))
            reste = self - sans_slug
            res = super(HelpdeskTicketTeam, reste).write(vals) if reste else True
            if sans_slug:
                sans_slug._ensure_unique_slugs()
            return res
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
