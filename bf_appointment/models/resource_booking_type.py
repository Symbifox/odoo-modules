import logging
import re
import unicodedata
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

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
    collect_company = fields.Boolean(
        string="Demander le nom de l'organisation",
        # Faux par défaut À DESSEIN. Les locataires qui collectaient déjà
        # l'organisation portent la colonne et gardent leurs valeurs : un
        # déploiement n'y touche pas. Un défaut à vrai ferait, lui,
        # apparaître un champ sur des formulaires publics en service.
        default=False,
        help="Affiche un champ « Société » facultatif sur le formulaire public "
             "et le conserve sur la fiche du demandeur (nom de l'organisation). "
             "Utile quand l'organisateur doit savoir quelle organisation la "
             "personne représente, et pas seulement son nom.",
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
    allow_guests = fields.Boolean(
        string="Permettre d'inviter d'autres personnes",
        default=False,
        help="Ajoute un champ « Autres invités » au formulaire public. Rien "
             "n'est envoyé à ces personnes tant que le demandeur ne l'a pas "
             "confirmé depuis sa boîte de réception.",
    )
    max_guests = fields.Integer(
        string="Invités additionnels au maximum",
        default=5,
        help="Plafond de ce qu'un demandeur peut saisir. Sans plafond, le "
             "formulaire devient un moyen d'écrire à beaucoup de monde d'un "
             "coup — même avec la confirmation, on ne veut pas de ça.",
    )
    guests_see_intake = fields.Boolean(
        string="Les invités voient les réponses du formulaire",
        default=False,
        help="La description de l'événement d'agenda reprend les réponses du "
             "demandeur, et cette description part dans l'invitation reçue par "
             "TOUS les participants. Décoché, les réponses sont retirées de la "
             "description dès qu'un invité additionnel est confirmé : ce que "
             "le demandeur a écrit ne regarde pas forcément les autres.",
    )

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
        r"""Generate a URL-friendly slug from name.

        ⚠️ Les accents sont REPLIÉS, pas conservés. `\w` est unicode en
        Python 3 : « Rencontre découverte » sortait `rencontre-découverte`,
        donc une adresse publique accentuée, qui se recopie mal, se cite mal
        dans un courriel et se pourcent-encode dès qu'elle passe par un client
        de messagerie. Les slugs déjà en service ne bougent pas — cette
        méthode ne sert qu'à en fabriquer de nouveaux.

        ⚠️ L'unicité se vérifie avec `active_test=False`. La contrainte SQL
        `UNIQUE(slug)`, elle, ne connaît pas l'archivage : chercher sans ce
        contexte laissait choisir un slug déjà porté par un type ARCHIVÉ, et
        l'erreur ressortait en violation de contrainte au moment d'enregistrer,
        au lieu d'un suffixe silencieux.
        """
        slug = unicodedata.normalize("NFKD", name or "")
        slug = "".join(c for c in slug if not unicodedata.combining(c))
        slug = slug.lower().strip()
        slug = re.sub(r"[^a-z0-9\s-]", "", slug)
        slug = re.sub(r"[-\s]+", "-", slug)
        slug = slug.strip("-")
        # Un nom entièrement hors alphabet latin ne doit pas rendre une adresse
        # vide : la contrainte d'unicité s'en chargerait, mais en levant.
        slug = slug or "rendez-vous"
        # Ensure uniqueness
        base_slug = slug
        counter = 1
        Type = self.with_context(active_test=False)
        while Type.search_count([("slug", "=", slug)]):
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

    # ------------------------------------------------------------------
    # Lot d'ouverture (2.40.0) — surface stable pour les modules satellites
    #
    # `bf_appointment` ne connaît AUCUN de ses satellites : rien dans cette
    # section ne référence un modèle enfant, ni par un champ typé, ni par un
    # import. Un champ relationnel vers un modèle d'un autre module est une
    # dépendance DURE, résolue au chargement du registre, et le défaut ne se
    # voit que sur une installation neuve. Voir la note du même nom dans
    # `resource_booking.py` (bf_source_ref).
    # ------------------------------------------------------------------

    def _bf_candidate_slots(self, start_dt, end_dt, tz=None, limit=0):
        """Grille des créneaux libres de CE TYPE, sans réservation persistée.

        `resource.booking._get_available_slots` est une méthode d'instance :
        elle a besoin d'une réservation portant le type, la durée et la
        combinaison de ressources. Un appelant qui veut seulement *proposer*
        des créneaux (un sondage de disponibilités, un courriel de démarchage)
        n'a pas de réservation à créer, et n'en veut surtout pas : elle
        occuperait le créneau et déclencherait les courriels.

        On passe donc par un enregistrement EN MÉMOIRE (`new()`), jamais écrit
        en base. `_get_intervals` gère déjà ce cas : `self.id` d'un `NewId` est
        faux, la méthode retombe sur `booking_id = -1` et aucune réservation
        n'est exclue du calcul.

        :param start_dt: datetime aware, début de la fenêtre
        :param end_dt: datetime aware, fin de la fenêtre
        :param tz: nom de fuseau pour le regroupement par jour local. À
            défaut, le fuseau d'affichage habituel du demandeur.
        :param limit: nombre maximal de créneaux rendus (0 = tous). Les
            créneaux sont rendus en ordre chronologique, donc une limite
            garde les plus proches.
        :return: liste de datetimes aware, triée.
        """
        self.ensure_one()
        booking = self.env["resource.booking"].new({
            "type_id": self.id,
            "duration": self.duration,
        })
        if tz:
            booking = booking.with_context(tz=tz)
        grid = booking._get_available_slots(start_dt, end_dt)
        slots = sorted(slot for day_slots in grid.values() for slot in day_slots)
        return slots[:limit] if limit else slots

    def _bf_create_booking(self, start, partners=None, vals=None, confirm=True):
        """Crée une réservation pour ce type depuis une source externe.

        Point d'entrée unique pour tout module qui aboutit à un rendez-vous
        sans passer par le formulaire public : le sondage de disponibilités à
        sa clôture, un lien unique, une reprise depuis le CRM. L'appelant
        obtient une réservation qui a suivi exactement le même chemin qu'une
        réservation publique — événement d'agenda, jeton d'accès, salle visio,
        rappels programmés — sans avoir à réimplémenter la séquence.

        Les notifications de création sont supprimées comme dans le
        contrôleur public : ce sont les gabarits du module qui écrivent aux
        participants, pas le fil de discussion d'Odoo.

        :param start: datetime naïf UTC, début du rendez-vous
        :param partners: recordset res.partner des demandeurs
        :param vals: valeurs additionnelles (name, location, duration, user_id…)
        :param confirm: confirmer immédiatement (défaut) ou laisser en attente
        :return: la réservation créée
        """
        self.ensure_one()
        Booking = self.env["resource.booking"].sudo().with_context(
            no_mail_to_attendees=True,
            mail_create_nolog=True,
            mail_create_nosubscribe=True,
            tracking_disable=True,
            mail_notrack=True,
        )
        partners = partners or self.env["res.partner"]
        organizer = (vals or {}).get("user_id")
        booking_vals = {
            "type_id": self.id,
            "partner_ids": [(6, 0, partners.ids)],
            "start": start,
        }
        if not organizer:
            booking_vals["user_id"] = self.env.user.id
        if partners and "name" not in (vals or {}):
            booking_vals["name"] = Booking._bf_build_title(
                self,
                partner=partners[0],
                booker_name=partners[0].name,
                lang=partners[0].lang or self.env.user.lang,
            )
        booking_vals.update(vals or {})
        if start:
            self._bf_assert_slot_available(start, booking_vals.get("duration"))
        booking = Booking.create(booking_vals)
        booking._portal_ensure_token()
        if confirm:
            # Ce chemin n'a AUCUNE page à montrer : le sondage se clôt tout
            # seul, le satellite tourne dans un cron. Les consentements
            # manquants partent en demande par courriel depuis
            # `action_confirm`, accroche unique des quatre chemins.
            booking.action_confirm()
        return booking

    def _bf_assert_slot_available(self, start, duration=None):
        """Refuse une heure qui n'est pas réellement réservable.

        ⚠️ Le contrôle doit avoir lieu AVANT la création, et pas après.

        OCA affecte la combinaison de ressources par un calcul sur `start`.
        Hors des disponibilités, ce calcul ne trouve rien et la réservation
        naît sans ressource. Vérifier après coup ne marche pas : lire
        `combination_id` déclenche le recalcul, donc la validation OCA, qui
        lève une `ValidationError` avant qu'on ait pu dire quoi que ce soit —
        et l'enregistrement corrompu reste dans la transaction pour exploser
        plus loin, sur une opération sans rapport. Constaté au QA du
        2026-08-19 : le message pointait une création de calendrier, à des
        centaines de lignes de l'appel fautif.
        """
        self.ensure_one()
        import pytz
        heures = duration or self.duration or 1.0
        debut = start if start.tzinfo else pytz.utc.localize(start)
        fin = debut + timedelta(hours=heures + 1)
        offerts = {
            c.astimezone(pytz.utc).replace(tzinfo=None)
            for c in self._bf_candidate_slots(debut, fin)
        }
        nu = start.replace(tzinfo=None) if start.tzinfo else start
        if nu not in offerts:
            raise UserError(_(
                "Aucune ressource n'est disponible le %(quand)s pour "
                "« %(type)s ». Passer par _bf_candidate_slots() pour obtenir "
                "une heure réellement réservable.",
                quand=nu, type=self.display_name,
            ))

    def _bf_create_onetime_link(self, partner, guests=None, expires_in_days=14,
                                single_use=True, vals=None):
        """Fabrique un lien de réservation personnel et rend la réservation.

        Point d'entrée unique : l'assistant, le compositeur de courriel et la
        fiche de contact passent tous par ici. Sans ça, trois copies de la même
        séquence divergeraient au premier ajustement.

        Pas de `start` : c'est tout l'objet du lien, la personne choisit son
        créneau. On ne passe donc pas par `_bf_create_booking`, dont le
        garde-fou porte justement sur l'heure demandée.
        """
        self.ensure_one()
        if not self.is_public:
            raise UserError(_(
                "Le type « %s » n'est pas accessible publiquement : la page de "
                "choix de créneau refuserait le lien. Cochez « Page publique "
                "accessible » sur le type, et laissez « Lister sur la page "
                "d'accueil » décoché pour qu'il reste réservé à ce lien.",
                self.display_name,
            ))
        Booking = self.env["resource.booking"].with_context(
            no_mail_to_attendees=True,
            mail_create_nolog=True,
            mail_create_nosubscribe=True,
            tracking_disable=True,
            mail_notrack=True,
        )
        participants = partner | (guests or self.env["res.partner"])
        booking_vals = {
            "type_id": self.id,
            "partner_ids": [(6, 0, participants.ids)],
            "user_id": self.env.user.id,
            "name": Booking._bf_build_title(
                self, partner=partner, booker_name=partner.name,
                lang=partner.lang or self.env.user.lang,
            ),
            "bf_source": "onetime",
            "link_single_use": single_use,
        }
        if expires_in_days:
            booking_vals["link_expires_at"] = (
                fields.Datetime.now() + timedelta(days=expires_in_days))
        booking_vals.update(vals or {})
        booking = Booking.create(booking_vals)
        booking._portal_ensure_token()
        return booking
