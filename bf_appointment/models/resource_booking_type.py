import logging
import re

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class ResourceBookingType(models.Model):
    _inherit = "resource.booking.type"

    project_id = fields.Many2one(
        "project.project",
        string="Projet associé",
        help="Projet Odoo dans lequel les tâches issues du Meeting Processor seront créées.",
    )
    is_public = fields.Boolean(
        string="Page publique accessible",
        default=False,
        help="Rend ce type accessible via son URL publique (/appointment/{slug}). "
             "Indépendant de la visibilité sur la page d'accueil — voir « Lister sur la page d'accueil ».",
    )
    listed_on_landing = fields.Boolean(
        string="Lister sur la page d'accueil",
        default=True,
        help="Affiche ce type dans la liste de la page /appointment. "
             "Désactivez pour un type « unlisted » : accessible seulement par lien direct.",
    )
    slug = fields.Char(
        string="Identifiant URL (slug)",
        index=True,
        copy=False,
        help="Identifiant lisible utilisé dans l'URL publique de la page de rendez-vous.",
    )
    public_description = fields.Html(
        string="Description publique",
        translate=True,
        sanitize=True,
        help="Description présentée aux visiteurs sur la page publique de rendez-vous.",
    )
    public_image = fields.Image(
        string="Image publique",
        max_width=512,
        max_height=512,
    )
    sequence = fields.Integer(default=10)
    video_provider = fields.Selection(
        [
            ("none", "Aucun"),
            ("jitsi", "Jitsi Meet"),
            ("nextcloud_talk", "Nextcloud Talk"),
        ],
        string="Fournisseur de vidéoconférence",
        default="nextcloud_talk",
    )
    is_in_person = fields.Boolean(
        string="Rendez-vous en personne possible",
        default=False,
        help="Indique que ce type de rendez-vous peut avoir lieu en personne.",
    )
    reminder_hours = fields.Float(
        string="Rappel avant le rendez-vous (heures)",
        default=24.0,
        help="Nombre d'heures avant le rendez-vous pour l'envoi d'un courriel de rappel.",
    )
    color_hex = fields.Char(
        string="Couleur d'accent",
        default="#714B67",
        help="Couleur (hex) de l'accent sur la page publique. Valeur Odoo par défaut; "
             "à personnaliser par type de rendez-vous. Pour les couleurs à l'échelle de "
             "l'organisation, voir Paramètres → Général → Identité de marque.",
    )
    duration_options = fields.Char(
        string="Durées proposées (minutes)",
        help="Liste de durées sélectionnables en minutes, séparées par des virgules, "
        "ex. « 15,30,45,60 ». Laisser vide pour utiliser la durée fixe.",
    )
    default_duration = fields.Float(
        string="Durée par défaut (heures)",
        help="Durée pré-sélectionnée sur la page publique. "
        "Doit correspondre à l'une des durées proposées (en heures, ex. 0,5 = 30 min).",
    )

    def get_duration_choices(self):
        """Return list of (hours_float, display_label) tuples for the template."""
        self.ensure_one()
        if not self.duration_options:
            return []
        choices = []
        for raw in self.duration_options.split(","):
            raw = raw.strip()
            if not raw:
                continue
            try:
                minutes = int(raw)
            except ValueError:
                continue
            hours = minutes / 60.0
            if minutes >= 60:
                h = minutes // 60
                m = minutes % 60
                label = f"{h}h{m:02d}" if m else f"{h}h"
            else:
                label = f"{minutes} min"
            choices.append((hours, label))
        return choices
    intake_field_ids = fields.One2many(
        "appointment.intake.field",
        "type_id",
        string="Champs du formulaire d'accueil",
    )
    email_schedule_ids = fields.One2many(
        "appointment.email.schedule",
        "type_id",
        string="Courriels planifiés",
    )
    requires_recording_consent = fields.Boolean(
        string="Demander le consentement d'enregistrement",
        default=True,
        help="Affiche une case à cocher distincte pour le consentement à l'enregistrement "
             "et à la transcription par IA. Obligatoire pour les rencontres traitées par "
             "le Meeting Processor (compte rendu auto). Désactivez pour les rendez-vous "
             "techniques courts (Synchro 2FA, support) où aucun enregistrement n'est fait.",
    )
    recording_notice_id = fields.Many2one(
        "privacy.notice",
        string="Modèle de consentement d'enregistrement",
        domain="[('purpose_id.code', 'in', ['recording', 'recording_audio'])]",
        help="Notice Loi 25 utilisée quand le consentement d'enregistrement est demandé.",
    )
    offers_newsletter_signup = fields.Boolean(
        string="Offrir l'inscription à l'infolettre",
        default=True,
        help="Affiche une case à cocher OPTIONNELLE (non pré-cochée) pour s'inscrire à "
             "l'infolettre Blue Fox. Désactivez sur les rendez-vous de support où ce serait "
             "tacky (Synchro 2FA, etc.).",
    )
    newsletter_notice_id = fields.Many2one(
        "privacy.notice",
        string="Modèle de consentement infolettre",
        domain="[('purpose_id.code', '=', 'marketing')]",
        help="Notice LCAP/Loi 25 utilisée quand l'inscription à l'infolettre est offerte.",
    )
    sends_intake_acknowledgement = fields.Boolean(
        string="Envoyer un accusé de réception",
        default=False,
        help="Envoie un courriel dès la soumission du formulaire (avant le choix "
             "du créneau). Donne au booker une trace écrite + un lien pour reprendre "
             "la sélection s'il a fermé l'onglet, et fournit une preuve horodatée "
             "du consentement aux fins d'audit. Off par défaut, à activer manuellement "
             "type par type quand l'accusé est utile.",
    )
    public_url = fields.Char(
        string="URL publique",
        compute="_compute_public_url",
        help="Lien complet à partager. Actif uniquement quand le type est publié.",
    )

    # --- Ventilation du « Modifications Deadline » OCA (champ surchargé) ---
    # L'OCA utilisait un seul champ pour DEUX comportements distincts. On les
    # sépare : `modifications_deadline` conserve le rôle de PLANCHER DE
    # DISPONIBILITÉ (préavis minimum avant réservation, lu tel quel par
    # resource_booking `_get_available_slots`), et le nouveau
    # `modification_lock_hours` pilote le VERROU DE MODIFICATION/ANNULATION
    # (voir ResourceBooking._compute_is_overdue, surchargé). On garde
    # required/default de l'OCA sur le champ existant pour ne rien casser.
    modifications_deadline = fields.Float(
        string="Préavis minimum avant réservation (heures)",
        required=True,
        default=24,
        help="Empêche la réservation d'un créneau trop rapproché : aucun créneau "
             "n'est proposé à moins de ce nombre d'heures de maintenant. "
             "Ex. : 2 = aucune disponibilité dans les 2 prochaines heures.",
    )
    modification_lock_hours = fields.Float(
        string="Délai limite de modification/annulation (heures)",
        default=2.0,
        help="Passé ce délai avant le rendez-vous, le client ne peut plus le "
             "modifier ni l'annuler lui-même (seul un gestionnaire le peut); une "
             "réservation non confirmée est annulée automatiquement.",
    )

    # --- Libellés français des champs de base OCA affichés sur le formulaire ---
    # L'OCA `resource_booking` ne livre qu'un fr.po vide : ses libellés
    # ressortent en anglais sur un backend fr_CA. On francise la source (en_US)
    # par redéfinition incrémentale du seul `string` (les autres attributs OCA —
    # comodel, relation, sélection, required — sont conservés par le merge ORM).
    name = fields.Char(string="Nom du type de rendez-vous")
    company_id = fields.Many2one(string="Société")
    duration = fields.Float(string="Durée (heures)")
    slot_duration = fields.Float(string="Intervalle entre les créneaux (heures)")
    combination_assignment = fields.Selection(string="Attribution des ressources")
    combination_rel_ids = fields.One2many(string="Combinaisons de ressources disponibles")
    categ_ids = fields.Many2many(string="Étiquettes par défaut")
    alarm_ids = fields.Many2many(string="Rappels par défaut")
    location = fields.Char(string="Lieu")
    videocall_location = fields.Char(string="URL de vidéoconférence")
    resource_calendar_id = fields.Many2one(string="Calendrier de disponibilité")
    requester_advice = fields.Text(string="Conseils au demandeur")

    @api.depends("slug")
    def _compute_public_url(self):
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        for record in self:
            record.public_url = f"{base}/appointment/{record.slug}" if record.slug else ""

    _sql_constraints = [
        (
            "slug_unique",
            "UNIQUE(slug)",
            "The URL slug must be unique.",
        ),
    ]

    @api.onchange("name")
    def _onchange_name_set_slug(self):
        for record in self:
            if record.name and not record.slug:
                record.slug = self._generate_slug(record.name)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("is_public") and not vals.get("slug") and vals.get("name"):
                vals["slug"] = self._generate_slug(vals["name"])
        records = super().create(vals_list)
        for record in records:
            if record.is_public and not record.email_schedule_ids:
                record._create_default_email_schedules()
        return records

    def write(self, vals):
        result = super().write(vals)
        if vals.get("is_public"):
            for record in self:
                if not record.email_schedule_ids:
                    record._create_default_email_schedules()
        return result

    def _create_default_email_schedules(self):
        """Create default email schedules for a public booking type.

        The 48h/2h/1h pre-reminders are intentionally absent : keeping a
        single 24h-before reminder avoids spamming the booker. Three
        post-meeting touchpoints stay on by design (immediate thanks,
        +1h check-in, +2h summary).
        """
        self.ensure_one()
        Schedule = self.env["appointment.email.schedule"]
        defaults = [
            ("before", 24, "bf_appointment.mail_template_reminder_1d"),
            ("after", 0, "bf_appointment.mail_template_followup_immediate"),
            ("after", 1, "bf_appointment.mail_template_followup_1h"),
            ("after", 2, "bf_appointment.mail_template_followup_2h"),
        ]
        for trigger, hours, xmlid in defaults:
            template = self.env.ref(xmlid, raise_if_not_found=False)
            if template:
                Schedule.create({
                    "type_id": self.id,
                    "trigger": trigger,
                    "hours": hours,
                    "template_id": template.id,
                })

    @api.model
    def _generate_slug(self, name):
        """Generate a URL-friendly slug from name."""
        slug = name.lower().strip()
        slug = re.sub(r"[^\w\s-]", "", slug)
        slug = re.sub(r"[-\s]+", "-", slug)
        slug = slug.strip("-")
        # Ensure uniqueness
        base_slug = slug
        counter = 1
        while self.search_count([("slug", "=", slug)]):
            slug = f"{base_slug}-{counter}"
            counter += 1
        return slug

    def action_view_public_page(self):
        """Open the public appointment page for this type."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": f"/appointment/{self.slug}",
            "target": "new",
        }
