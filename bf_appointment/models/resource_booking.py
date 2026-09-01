import base64
import logging
import uuid
from datetime import datetime, timedelta

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError

# bf_securetransfer owns the VoIP.ms transport. This module deliberately does
# not depend on it: the tenants carry different addon sets, and a booking page
# has no business being uninstallable because an unrelated module is missing.
# Without it the SMS channel simply never fires and every reminder leaves by
# e-mail, which is the same fallback every other failure takes.
try:
    from odoo.addons.bf_securetransfer.models import sms as sms_api
except ImportError:  # pragma: no cover — depends on the tenant's addon set
    sms_api = None

from . import _sms_text

_logger = logging.getLogger(__name__)


def _escape_ics(value):
    """Escape a string for use in ICS property values per RFC 5545."""
    if not value:
        return ""
    # Backslash must be escaped first
    value = value.replace("\\", "\\\\")
    # Semicolons and commas are special in ICS
    value = value.replace(";", "\\;")
    value = value.replace(",", "\\,")
    # Newlines must be escaped as literal \n
    value = value.replace("\r\n", "\\n")
    value = value.replace("\r", "\\n")
    value = value.replace("\n", "\\n")
    return value


def _escape_ics_param(value):
    """Assainit une chaîne destinée à un paramètre ICS ENTRE GUILLEMETS (CN="…").

    Règles différentes de _escape_ics : dans un paramètre entre guillemets, « ; »
    et « , » sont littéraux, mais le guillemet double termine la valeur. Laisser
    passer un nom contenant un guillemet permettait de fermer CN= et d'ajouter
    ses propres paramètres (SENT-BY, DIR, un second CN) sur la ligne ATTENDEE —
    le nom vient du formulaire public, où seul .strip() est appliqué. Les
    retours de ligne sont repliés en espace pour qu'aucune propriété ne puisse
    être ouverte non plus.
    """
    if not value:
        return ""
    value = value.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    value = "".join(c for c in value if c.isprintable())
    return value.replace('"', "'")


# Minimal VTIMEZONE block for America/Toronto (EST/EDT). Hard-coded because it
# covers every BF booking today, and including a VTIMEZONE is required by
# RFC 5545 when TZID references are used in DTSTART/DTEND.
_VTIMEZONE_AMERICA_TORONTO = (
    "BEGIN:VTIMEZONE\r\n"
    "TZID:America/Toronto\r\n"
    "BEGIN:STANDARD\r\n"
    "DTSTART:19701101T020000\r\n"
    "TZOFFSETFROM:-0400\r\n"
    "TZOFFSETTO:-0500\r\n"
    "TZNAME:EST\r\n"
    "RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU\r\n"
    "END:STANDARD\r\n"
    "BEGIN:DAYLIGHT\r\n"
    "DTSTART:19700308T020000\r\n"
    "TZOFFSETFROM:-0500\r\n"
    "TZOFFSETTO:-0400\r\n"
    "TZNAME:EDT\r\n"
    "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU\r\n"
    "END:DAYLIGHT\r\n"
    "END:VTIMEZONE\r\n"
)


class ResourceBooking(models.Model):
    _inherit = "resource.booking"

    video_room_token = fields.Char(
        string="Jeton de salle vidéo",
        copy=False,
        help="Jeton unique pour l'URL de la salle de vidéoconférence.",
    )
    reminder_sent = fields.Boolean(
        string="Rappel envoyé",
        default=False,
        copy=False,
        help="Indique si le courriel de rappel a été envoyé.",
    )
    sent_schedule_ids = fields.Many2many(
        "appointment.email.schedule",
        string="Courriels planifiés envoyés",
        copy=False,
    )
    intake_answer_ids = fields.One2many(
        "appointment.intake.answer",
        "booking_id",
        string="Réponses du formulaire d'accueil",
    )
    guest_ids = fields.One2many(
        "resource.booking.guest", "booking_id", string="Invités additionnels",
    )
    guest_state = fields.Selection(
        [
            ("none", "Aucun"),
            ("pending", "En attente de votre confirmation"),
            ("confirmed", "Confirmés"),
            ("declined", "Écartés"),
        ],
        string="État des invités",
        compute="_compute_guest_state",
        help="Tant que c'est « en attente », aucune invitation n'est partie.",
    )

    cancellation_reason = fields.Text(
        string="Raison de l'annulation",
        copy=False,
        help="Raison saisie par le client (ou l'organisateur) lors de "
             "l'annulation du rendez-vous. Optionnel.",
    )
    cancelled_start = fields.Datetime(
        string="Début avant annulation",
        copy=False,
        help="Date et heure prévues au moment de l'annulation. `action_cancel` "
             "déprogramme la réservation et vide `start` : sans cette copie, le "
             "courriel d'annulation ne peut plus dire QUEL rendez-vous vient "
             "d'être annulé.",
    )

    # --- Provenance (lot d'ouverture 2.40.0) -------------------------------
    # ⚠️ Volontairement NON typé. Un Many2one vers `appointment.poll` (ou vers
    # tout autre modèle satellite) ferait de ce module une dépendance DURE de
    # `bf_appointment` : Odoo résout `comodel_name` au chargement du registre,
    # bien avant qu'une garde dans un calcul ait voix au chapitre. Le défaut ne
    # se verrait QUE sur une installation neuve, chez le premier locataire qui
    # installe le module de rendez-vous sans le satellite.
    #
    # On stocke donc une référence textuelle « modele,id » et on la résout au
    # clic : une action `res_model` est une chaîne, elle ne crée pas de lien.
    bf_source = fields.Char(
        string="Origine",
        copy=False,
        index=True,
        help="Module ou mécanisme à l'origine de cette réservation "
             "(« poll », « onetime », « outreach »…). Vide pour une "
             "réservation prise sur le formulaire public.",
    )
    bf_source_ref = fields.Char(
        string="Référence d'origine",
        copy=False,
        help="Enregistrement d'origine, au format « modele,id ». Résolu à la "
             "demande — ce n'est PAS un champ relationnel, pour que "
             "bf_appointment reste installable sans ses satellites.",
    )

    # --- Lien unique (2.42.0) ----------------------------------------------
    # Une réservation « en attente » porte déjà un jeton et une page de choix de
    # créneau : c'est, tel quel, un lien de réservation personnel. Ce qui
    # manquait n'était pas le mécanisme, c'était une durée de vie, un usage
    # unique, et de quoi le fabriquer sans passer par le shell.
    link_expires_at = fields.Datetime(
        string="Le lien expire le",
        copy=False,
        help="Passé ce moment, le lien n'ouvre plus le choix de créneau. Vide "
             "= pas d'expiration. Un lien personnel qui traîne des mois dans "
             "une boîte de réception finit par être suivi au mauvais moment.",
    )
    link_single_use = fields.Boolean(
        string="Lien à usage unique",
        default=False,
        copy=False,
        help="Une fois le rendez-vous pris, le lien ne permet plus d'en "
             "choisir un autre. La personne garde l'accès à sa page de "
             "confirmation, donc elle peut toujours voir ou annuler.",
    )
    link_used_at = fields.Datetime(
        string="Lien utilisé le",
        readonly=True,
        copy=False,
    )
    link_state = fields.Selection(
        [
            ("none", "Pas un lien unique"),
            ("active", "Actif"),
            ("used", "Déjà utilisé"),
            ("expired", "Expiré"),
        ],
        string="État du lien",
        compute="_compute_link_state",
        help="Ce que verra la personne qui suit le lien.",
    )
    one_time_url = fields.Char(
        string="Lien de réservation",
        compute="_compute_one_time_url",
        help="Adresse personnelle à transmettre. Elle vaut jeton d'accès : "
             "qui l'a peut réserver.",
    )

    # --- Consentements (2.49.0) --------------------------------------------
    # La validation des consentements vivait ENTIÈREMENT dans le POST du
    # formulaire d'accueil public. Les trois autres chemins de création — lien
    # personnel, `_bf_create_booking()` d'un satellite, saisie au back-office —
    # n'y passent jamais : une réservation pouvait donc naître, se confirmer et
    # produire une rencontre enregistrée sans qu'aucun consentement ait été ni
    # vérifié ni demandé. L'état ci-dessous rend ce trou VISIBLE, et
    # `_bf_ensure_consents()` le rebouche depuis n'importe quel chemin.
    #
    # ⚠️ Non stocké, et c'est délibéré : la vérité est portée par le CONTACT,
    # pas par la réservation. Un consentement accordé ailleurs (autre
    # réservation, portail, retrait) change l'état de toutes les réservations
    # de la personne d'un coup, et aucune chaîne de `depends` ne voit ça. Un
    # champ stocké afficherait « manquant » sur une rencontre déjà couverte.
    bf_consent_state = fields.Selection(
        [
            ("none", "Sans objet"),
            ("granted", "Au dossier"),
            ("requested", "Demandé"),
            ("refused", "Refusé"),
            ("missing", "Manquant"),
        ],
        string="Consentement à l'enregistrement",
        compute="_compute_bf_consent_state",
        search="_search_bf_consent_state",
        help="État du consentement à l'enregistrement et à la transcription "
             "pour le demandeur. « Manquant » ne bloque pas le rendez-vous : "
             "il dit que la rencontre ne doit pas être enregistrée.",
    )

    # --- Libellés français des champs de base OCA (resource.booking) ---
    # Le fr.po OCA est vide → ses libellés ressortent en anglais sur un backend
    # fr_CA. Redéfinition incrémentale du seul `string` (comodel/compute/store
    # conservés par le merge ORM). La sélection `state` est aussi francisée.
    name = fields.Char(string="Nom de la réservation")
    type_id = fields.Many2one(string="Type de rendez-vous")
    partner_ids = fields.Many2many(string="Demandeur(s)")
    user_id = fields.Many2one(string="Organisateur")
    combination_id = fields.Many2one(string="Combinaison de ressources")
    combination_auto_assign = fields.Boolean(string="Attribution automatique des ressources")
    meeting_id = fields.Many2one(string="Événement d'agenda")
    location = fields.Char(string="Lieu")
    videocall_location = fields.Char(string="URL de vidéoconférence")
    description = fields.Html(string="Description")
    categ_ids = fields.Many2many(string="Étiquettes")
    start = fields.Datetime(string="Début")
    stop = fields.Datetime(string="Fin")
    duration = fields.Float(string="Durée (heures)")
    state = fields.Selection(
        [
            ("pending", "En attente"),
            ("scheduled", "Planifié"),
            ("confirmed", "Confirmé"),
            ("canceled", "Annulé"),
        ],
        string="État",
    )

    # K-of-N support: actual subset of combination resources that took the slot.
    # Equal to combination_id.resource_ids when min_required is 0 / >= N
    # (standard OCA behavior). For K-of-N (min_required = K < N), holds the K
    # resources that were free at booking time. Used by _prepare_meeting_vals
    # to add only those partners as calendar.event attendees.
    # Stored computed so it's set BEFORE _sync_meeting fires (which is
    # triggered on the same write that sets `start`).
    attendee_resource_ids = fields.Many2many(
        "resource.resource",
        "rb_attendee_resource_rel",
        "booking_id",
        "resource_id",
        string="Ressources assignées",
        compute="_compute_attendee_resources",
        store=True,
        copy=False,
        help="Subset of the combination's resources actually assigned to "
             "this booking (relevant for K-of-N combinations).",
    )

    @api.depends("start", "stop", "combination_id", "combination_id.resource_ids",
                 "combination_id.min_required")
    def _compute_attendee_resources(self):
        """Le sous-ensemble K-of-N qui prend réellement le créneau.

        ⚠️ Deux détails alignent ce calcul sur celui qui a PROPOSÉ le créneau
        (`resource.booking.combination._get_intervals`) — sans eux, les deux
        peuvent ne pas désigner les mêmes personnes :

        * `exclude_public_holidays=True`. La grille de disponibilité l'emploie ;
          ici il manquait. Un jour férié rendait donc une ressource « occupée »
          au moment d'attribuer alors qu'elle comptait comme libre au moment de
          proposer, et l'on retombait sur le repli arbitraire `[:k]`.
        * les disponibilités sont lues UNE FOIS par ressource, hors de la boucle
          des sous-ensembles. Elles ne dépendent pas du sous-ensemble : les
          relire dedans refaisait le même calcul de calendrier jusqu'à C(N-1,K-1)
          fois par ressource, sur un compute stocké déclenché à chaque écriture
          de `start`.
        """
        import pytz
        from itertools import combinations as _icombs
        from odoo.addons.resource.models.utils import Intervals
        for rec in self:
            combo = rec.combination_id
            if not combo:
                rec.attendee_resource_ids = [(5, 0, 0)]
                continue
            n = len(combo.resource_ids)
            k = combo.min_required
            if k <= 0 or k >= n or not rec.start or not rec.stop:
                rec.attendee_resource_ids = [(6, 0, combo.resource_ids.ids)]
                continue
            start_aware = pytz.utc.localize(rec.start) if rec.start.tzinfo is None else rec.start
            stop_aware = pytz.utc.localize(rec.stop) if rec.stop.tzinfo is None else rec.stop
            base = Intervals([(start_aware, stop_aware, combo)])
            sorted_resources = combo.resource_ids.sorted(lambda r: r.id)
            combo_ctx = combo.with_context(exclude_public_holidays=True)
            res_free = {}
            for res in sorted_resources:
                calendar = combo_ctx.forced_calendar_id or res.calendar_id
                res_free[res.id] = calendar.with_context(
                    exclude_public_holidays=True
                )._work_intervals_batch(start_aware, stop_aware, res)[res.id]
            picked = None
            for subset in _icombs(sorted_resources, k):
                subset_intervals = base
                for res in subset:
                    subset_intervals &= res_free[res.id]
                    if not subset_intervals:
                        break
                if subset_intervals:
                    picked = subset
                    break
            ids_picked = [r.id for r in picked] if picked else combo.resource_ids[:k].ids
            rec.attendee_resource_ids = [(6, 0, ids_picked)]

    # QWeb mail templates have non-deterministic behaviour with
    # format_datetime(tz=...) in some render paths, so we precompute the
    # localized date/time strings here for the booking type's resource calendar
    # timezone (defaults to America/Toronto).
    start_date_local = fields.Char(
        compute="_compute_start_local_strings",
        string="Date de début (locale)",
    )
    start_time_local = fields.Char(
        compute="_compute_start_local_strings",
        string="Heure de début (locale)",
    )

    @api.depends("start", "type_id.resource_calendar_id.tz",
                 "partner_id.tz")
    @api.depends_context("tz")
    def _compute_start_local_strings(self):
        """Render the booking start in the most relevant TZ for the reader.

        ``depends_context("tz")`` keys the field cache on the context tz so
        the SAME booking rendered for the booker (Montréal) and then the
        organizer (Auckland) in one transaction does not return the first
        render's cached value to the second — the bug that would otherwise
        send the organizer the booker's local time and vice-versa.

        Priority: explicit ``tz`` context (set by _send_appointment_email
        per recipient — Auckland for the organizer, the booker display tz
        for the booker) → booker's partner.tz → booking type's display
        calendar tz (Montréal) → configured default.

        The organizer's ``user_id.tz`` is deliberately NOT a fallback: with
        no context tz this method renders booker-facing content, and the
        organizer (Auckland) must never leak into it. The organizer path
        supplies its tz explicitly through the context.
        """
        for rec in self:
            rec.start_date_local, rec.start_time_local = rec._bf_local_strings(rec.start)

    def _bf_reader_tzname(self):
        """Fuseau du lecteur courant, selon la priorité décrite ci-dessus."""
        self.ensure_one()
        # A booker partner.tz of "UTC" is a spurious browser-detection
        # fallback (see _get_booker_display_tz); it would render the raw
        # UTC instant, so drop it and fall through to the display calendar.
        booker_tz = self.partner_id.tz if self.partner_id else None
        if booker_tz == "UTC":
            booker_tz = None
        return self.env["bf.timezone"].resolve([
            self.env.context.get("tz"),
            booker_tz,
            self.type_id.resource_calendar_id.tz if self.type_id else None,
        ])

    def _bf_local_strings(self, value):
        """(date, heure) locales d'un datetime naïf-UTC pour le lecteur courant.

        Factorisé pour que le créneau annulé (`cancelled_start`) se rende
        exactement comme le créneau actif, sans dupliquer la résolution de
        fuseau.
        """
        self.ensure_one()
        if not value:
            return "", ""
        import pytz
        try:
            if isinstance(value, str):
                value = fields.Datetime.from_string(value)
            aware_utc = (
                pytz.utc.localize(value) if value.tzinfo is None
                else value.astimezone(pytz.utc)
            )
            local_dt = aware_utc.astimezone(pytz.timezone(self._bf_reader_tzname()))
            return local_dt.strftime("%Y-%m-%d"), local_dt.strftime("%H:%M")
        except Exception as e:
            _logger.warning("start_local compute failed for booking %s: %s", self.id, e)
            return value.strftime("%Y-%m-%d"), value.strftime("%H:%M")

    def get_timezone_label(self):
        """Étiquette du fuseau dans lequel l'heure vient d'être rendue.

        Le MÊME rendez-vous part au client en heure de Montréal et à
        l'organisateur en heure d'Auckland — `_send_appointment_email` pose le
        fuseau par destinataire, c'est voulu. Mais sans étiquette, les deux
        courriels annoncent deux heures (et parfois deux dates) différentes
        pour une seule rencontre, et rien ne permet de trancher. Constaté au
        QA du 2026-07-30 : « 2026-08-31 18:30 » au client, « 2026-09-01 10:30 »
        à l'organisateur.

        Abréviation quand la locale en a une (« HAE » en fr_CA), nom de ville
        sinon (« Auckland ») : Babel rend « +1200 » pour la Nouvelle-Zélande,
        ce qui n'aide personne.
        """
        self.ensure_one()
        when = self.start or self.cancelled_start
        if not when:
            return ""
        import pytz
        try:
            from babel.dates import format_datetime
            if isinstance(when, str):
                when = fields.Datetime.from_string(when)
            aware_utc = pytz.utc.localize(when) if when.tzinfo is None else when
            tz = pytz.timezone(self._bf_reader_tzname())
            locale = self._appt_locale()
            local_dt = aware_utc.astimezone(tz)
            short = format_datetime(local_dt, "zzz", tzinfo=tz, locale=locale)
            if short and not short[0].isdigit() and short[0] not in "+-":
                return short
            return format_datetime(local_dt, "VVV", tzinfo=tz, locale=locale)
        except Exception as e:
            _logger.warning("tz label unavailable for booking %s: %s", self.id, e)
            return ""

    def get_cancelled_start_display(self):
        """(date, heure) du créneau perdu, pour les courriels d'annulation.

        `action_cancel` déprogramme la réservation, donc `start` est déjà vide
        quand le courriel se rend : on retombe sur la copie prise à
        l'annulation.
        """
        self.ensure_one()
        return self._bf_local_strings(self.start or self.cancelled_start)

    def get_start_display(self):
        """(date longue, heure) du créneau, dans le fuseau du LECTEUR.

        La page publique de confirmation rendait ces deux valeurs avec
        ``t-field="booking_sudo.start"``. Ça ne pouvait pas fonctionner : le
        convertisseur QWeb (``ir_qweb_fields.record_to_html``) fait
        ``record.with_context(**self.env.context)`` — il ÉCRASE le contexte du
        record par celui du rendu. Le ``with_context(tz=...)`` posé par le
        contrôleur est donc lettre morte, et ``context_timestamp`` retombe sur
        ``env.user.tz`` : l'usager ``public`` n'en a aucun, donc UTC. Un
        rendez-vous de 14:30 à Montréal s'affichait « 18:30 » juste au-dessus
        de l'étiquette « Montréal ». Invisible en interne : un employé connecté
        a un fuseau, donc la page lui semble correcte.

        ⚠️ C'est la raison de fond pour laquelle le fuseau « revenait » :
        le module a DEUX régies de rendu et une seule est sûre. Tout le reste
        (courriels, .ics, sélecteur de créneaux) passe par le modèle —
        ``start_date_local``, ``get_cancelled_start_display``,
        ``appt_format_*`` — qui résolvent le fuseau du lecteur eux-mêmes. Les
        correctifs de juin 2026 ont durci cette régie-là ; la page, elle,
        lisait encore le champ brut. Toute nouvelle surface publique doit
        passer par une méthode du modèle, jamais par ``t-field`` sur un
        Datetime.

        Rapporté le 2026-08-19 : une personne a repris trois fois la même
        réservation de 16:00 parce que la page lui répondait 20:00.
        """
        self.ensure_one()
        if not self.start:
            return "", ""
        import pytz
        from babel.dates import format_datetime
        value = self.start
        if isinstance(value, str):
            value = fields.Datetime.from_string(value)
        # Même priorité que _send_appointment_email : le fuseau explicite du
        # contexte quand le contrôleur en pose un, sinon celui du réservant.
        tz_name = self.env.context.get("tz") or self._get_booker_display_tz()
        try:
            local_dt = pytz.utc.localize(value).astimezone(pytz.timezone(tz_name))
        except Exception as e:  # pragma: no cover - fuseau invalide
            _logger.warning(
                "get_start_display: fuseau %s inutilisable sur la réservation "
                "%s (%s), rendu en UTC", tz_name, self.id, e,
            )
            local_dt = pytz.utc.localize(value)
        # Naïf volontairement : babel ne reconvertit pas, il imprime l'horloge
        # murale telle quelle. Passer le datetime averti ferait dépendre le
        # résultat du paramètre tzinfo de babel.
        naive_local = local_dt.replace(tzinfo=None)
        locale = self._appt_locale()
        return (
            format_datetime(naive_local, "EEEE d MMMM yyyy", locale=locale),
            format_datetime(naive_local, "HH:mm", locale=locale),
        )

    def _sync_meeting(self):
        """Suppress calendar.event invite/update notifications, then guarantee
        the expected attendees are on the event.

        OCA's resource_booking._sync_meeting creates and writes the linked
        calendar.event with mail_notify_author=True (set in OCA's
        calendar_event create override) and from_ui=True on reschedule.
        Both paths fire the stock Odoo "Date mise à jour" notification to
        the booker, on top of our own branded confirmation/reminder
        emails. Inject suppression context before delegating so attendees
        do not get the duplicate calendar invite.

        Puis on ré-affirme les participants. `_prepare_meeting_vals` construit
        la bonne liste, mais elle n'atterrit que si l'événement la reçoit au
        moment où il est créé : les commandes `(4, id)` sont additives, donc un
        événement né sans ses participants ne se répare jamais tout seul. Vécu
        sur le RDV « Partageons l'Espoir » (booking 387 / event 6548,
        2026-07-15), sorti avec le seul partenaire de l'organisateur — ajouté 13
        minutes plus tard par le recalcul de `partner_ids` — sans jamais voir ni
        le client ni la ressource. La fenêtre exacte nous a échappé : la même
        méthode rejoue aujourd'hui la bonne liste. On arrête donc d'en dépendre
        et on vérifie à chaque synchro.
        """
        result = super(
            ResourceBooking,
            self.with_context(
                no_mail_to_attendees=True,
                mail_notify_author=False,
                mail_create_nosubscribe=True,
                mail_create_nolog=True,
                mail_notrack=True,
                tracking_disable=True,
                dont_notify=True,
            ),
        )._sync_meeting()
        self._bf_ensure_meeting_attendees()
        return result

    def _bf_ensure_meeting_attendees(self):
        """Ajoute au calendar.event les participants attendus qui manquent.

        Purement additif : ce que quelqu'un a ajouté à la main sur l'événement
        reste en place. Le retrait des ressources non retenues d'un K-of-N reste
        la responsabilité d'`action_confirm`.
        """
        # Même garde de récursion que l'implémentation OCA.
        already_syncing = self.env.context.get("syncing_booking_ids") or []
        for one in self - self.browse(already_syncing):
            meeting = one.meeting_id
            if not (meeting and one.start):
                continue
            missing = (
                one.partner_ids | one._bf_assigned_partners()
            ) - meeting.partner_ids
            if missing:
                meeting.with_context(
                    no_mail_to_attendees=True,
                    dont_notify=True,
                ).partner_ids = [(4, partner.id, 0) for partner in missing]

    def action_confirm(self):
        """Confirmer : salle visio, participants K-of-N, puis consentements.

        ⚠️ UNE SEULE définition d'`action_confirm` dans cette classe, et ça
        n'est pas une préférence de style. Le lot 2.49.0 a ajouté l'accroche
        des consentements dans un SECOND `def action_confirm` plus bas dans le
        même corps de classe : Python garde le dernier, sans un mot. La
        génération de l'URL de vidéoconférence — dont ce corps-ci est le seul
        appelant — a donc cessé d'exister le jour du déploiement, et les
        rendez-vous confirmés ensuite sont sortis avec le lien de la salle
        GÉNÉRIQUE partagée au lieu de leur salle dédiée. Aucun test ne
        touchait la visio, d'où le silence (voir `test_video_room.py`, écrit
        pour ça, et le contrôle statique qui refuse une méthode définie deux
        fois).

        Ordre volontaire : tout ce qui peut lever ou être annulé passe AVANT
        la demande de consentement, qui peut écrire à un client. Un rollback
        ne rappelle pas un courriel.

        Le drapeau `bf_consents_handled` est posé par la page de choix de
        créneau, qui pose les questions SUR PLACE : sans lui, la demande
        partirait avant que la réponse donnée à l'écran soit consignée, et la
        personne recevrait un courriel lui redemandant ce qu'elle vient
        d'accorder.

        OCA's action_confirm unions in `combination_id.resource_ids.user_id.partner_id`
        on the meeting (re-adding ALL combination resources, including non-attendees
        for K-of-N). We post-process to keep only `attendee_resource_ids` partners.
        """
        result = super().action_confirm()
        for booking in self:
            # K-of-N: remove non-attendee resource partners from the meeting.
            combo = booking.combination_id
            if combo and 0 < combo.min_required < len(combo.resource_ids) and booking.meeting_id:
                full_partners = combo.resource_ids.filtered(
                    lambda r: r.resource_type == "user"
                ).mapped("user_id.partner_id")
                attendee_partners = booking.attendee_resource_ids.filtered(
                    lambda r: r.resource_type == "user"
                ).mapped("user_id.partner_id")
                excluded = full_partners - attendee_partners
                if excluded:
                    booking.meeting_id.partner_ids -= excluded
            if (
                booking.type_id.video_provider
                and booking.type_id.video_provider != "none"
            ):
                url = booking._generate_video_url()
                if url:
                    booking.videocall_location = url
        # Consentements en dernier : c'est le seul geste de cette méthode qui
        # sorte du système. Les quatre chemins de création finissent ici, y
        # compris la saisie au back-office, qui n'a ni page ni satellite pour
        # la représenter.
        if not self.env.context.get("bf_consents_handled"):
            for booking in self:
                try:
                    booking._bf_ensure_consents(source_note="action_confirm")
                except Exception:  # noqa: BLE001
                    _logger.exception(
                        "Consentements à la confirmation (réservation %s)", booking.id)
        return result

    def _bf_assigned_resources(self):
        """Les ressources qui prennent réellement le RDV.

        `attendee_resource_ids` (sous-ensemble K-of-N) quand il est calculé,
        sinon toute la combinaison.
        """
        self.ensure_one()
        return self.attendee_resource_ids or self.combination_id.resource_ids

    def _bf_assigned_partners(self):
        """Partenaires des ressources humaines réellement assignées."""
        self.ensure_one()
        return self._bf_assigned_resources().filtered(
            lambda res: res.resource_type == "user"
        ).mapped("user_id.partner_id")

    def _bf_assigned_user(self):
        """L'utilisateur interne unique derrière les ressources assignées.

        Vide si la combinaison n'en désigne pas exactement un (K-of-N à
        plusieurs personnes, ressources matérielles, utilisateur portail) :
        dans ce cas on laisse l'organisateur tel quel.
        """
        self.ensure_one()
        users = self._bf_assigned_resources().filtered(
            lambda res: res.resource_type == "user"
        ).mapped("user_id").filtered(lambda u: u.active and not u.share)
        return users if len(users) == 1 else self.env["res.users"].browse()

    def _prepare_meeting_vals(self):
        """Override to use attendee_resource_ids (K-of-N aware) instead of
        combination_id.resource_ids when populating calendar.event partners,
        and to pin the organizer on the resource actually assigned.

        Le contrôleur d'intake ne peut que *deviner* l'organisateur (il prend
        la 1re combinaison du type) : au moment du formulaire, le créneau n'est
        pas encore choisi. Sur un type qui offre plusieurs personnes, le RDV
        atterrissait donc toujours au nom de la 1re, même quand c'est une autre
        qui prend le créneau. Une fois la combinaison assignée, on connaît la
        réponse : on recale l'organisateur dessus.
        """
        vals = super()._prepare_meeting_vals()
        # Le sujet réel du rendez-vous, pas la consigne du formulaire.
        vals["description"] = self._bf_meeting_description()
        assigned_user = self._bf_assigned_user()
        if assigned_user:
            vals["user_id"] = assigned_user.id
        if not self.attendee_resource_ids:
            return vals
        # Replace resource_partners with K-of-N subset
        full_resource_partners = self.combination_id.resource_ids.filtered(
            lambda res: res.resource_type == "user"
        ).mapped("user_id.partner_id")
        attendee_partners = self.attendee_resource_ids.filtered(
            lambda res: res.resource_type == "user"
        ).mapped("user_id.partner_id")
        # Remove any partners from the full set that aren't in the K subset,
        # then ensure the K subset partners are present.
        partner_cmd = list(vals.get("partner_ids", []))
        # Strip OCA's add commands for non-attendee resource partners
        excluded_partner_ids = (full_resource_partners - attendee_partners).ids
        partner_cmd = [
            cmd for cmd in partner_cmd
            if not (cmd[0] == 4 and cmd[1] in excluded_partner_ids)
        ]
        # Ensure attendee subset is added
        for p in attendee_partners:
            if not any(c[0] == 4 and c[1] == p.id for c in partner_cmd):
                partner_cmd.append((4, p.id, 0))
        vals["partner_ids"] = partner_cmd
        return vals

    def action_cancel(self):
        """Override to preserve access_token AND unlink the orphan calendar.event.

        Two OCA resource_booking gotchas patched here:

        1. action_cancel clears access_token, which breaks the "Voir le
           rendez-vous" link in previously-sent confirmation emails. We
           preserve the token so the booker still lands on the confirmation
           page (showing cancelled state).
        2. action_cancel sets active=False on the booking but leaves the
           linked calendar.event behind. The orphan event keeps blocking
           slots in combinations._get_intervals(), so the same combination
           shows "no availability" on slots that should be free. We unlink
           the calendar.event after cancellation. The
           calendar_event._track_subtype override already suppresses any
           tracking notifications on these events, so the unlink is silent.
        """
        tokens = {b.id: b.access_token for b in self}
        # `action_cancel` déprogramme la réservation (start vidé) AVANT que le
        # contrôleur n'envoie les courriels d'annulation : sans cette copie,
        # ceux-ci ne peuvent plus nommer le créneau perdu.
        starts = {b.id: b.start for b in self if b.start}
        meeting_ids = [b.meeting_id.id for b in self if b.meeting_id]
        result = super().action_cancel()
        for booking in self:
            token = tokens.get(booking.id)
            if token and not booking.access_token:
                booking.sudo().access_token = token
            start = starts.get(booking.id)
            if start and not booking.cancelled_start:
                booking.sudo().cancelled_start = start
        if meeting_ids:
            # Belt + suspenders. Current OCA action_unschedule unlinks the
            # meeting before we get here, so .exists() filters those out and
            # this is usually a no-op. But if a future OCA regression or
            # an alternative cancel path leaves the event behind, this
            # ensures the slot is freed immediately.
            events = (
                self.env["calendar.event"]
                .sudo()
                .browse(meeting_ids)
                .exists()
            )
            if events:
                events.with_context(
                    no_mail_to_attendees=True,
                    tracking_disable=True,
                    mail_notrack=True,
                ).unlink()
        return result

    def _generate_video_url(self):
        """Generate video meeting URL based on the type's video provider."""
        self.ensure_one()
        provider = self.type_id.video_provider
        if not provider or provider == "none":
            return False
        if not self.video_room_token:
            self.video_room_token = uuid.uuid4().hex[:12]
        if provider == "jitsi":
            return self._generate_jitsi_url()
        if provider == "nextcloud_talk":
            return self._nc_talk_url_with_fallback()
        return False

    def _generate_jitsi_url(self):
        """Generate a Jitsi Meet URL."""
        ICP = self.env["ir.config_parameter"].sudo()
        domain = ICP.get_param(
            "bf_appointment.jitsi_domain", "meet.jit.si"
        )
        room_name = f"bf-{self.id}-{self.video_room_token}"
        return f"https://{domain}/{room_name}"

    def _generate_nc_talk_url(self):
        """Generate a Nextcloud Talk room URL via API."""
        ICP = self.env["ir.config_parameter"].sudo()
        base_url = ICP.get_param("bf_appointment.nc_talk_base_url")
        user = ICP.get_param("bf_appointment.nc_talk_user")
        password_enc = ICP.get_param("bf_appointment.nc_talk_password_encrypted")
        if not all([base_url, user, password_enc]):
            _logger.warning(
                "Nextcloud Talk not configured, falling back to type videocall_location"
            )
            return False
        password = self._decrypt_nc_talk_password(password_enc)
        if not password:
            return False
        try:
            import pytz
            import requests

            booker = self.partner_id or (self.partner_ids[:1] if self.partner_ids else False)
            booker_name = (booker.name if booker else "").strip() or "Invité"
            tz_name = self._get_booker_display_tz()
            local_start = ""
            if self.start:
                local_start = pytz.utc.localize(self.start).astimezone(
                    pytz.timezone(tz_name)
                ).strftime("%Y-%m-%d %H:%M")
            type_name = (self.type_id.name or "Rendez-vous").strip()
            room_name = " | ".join(p for p in (type_name, booker_name, local_start) if p)
            # Nextcloud caps room names at 200 chars
            room_name = room_name[:200]

            api_url = f"{base_url.rstrip('/')}/ocs/v2.php/apps/spreed/api/v4/room"
            response = requests.post(
                api_url,
                auth=(user, password),
                headers={
                    "OCS-APIREQUEST": "true",
                    "Accept": "application/json",
                },
                data={
                    "roomType": 3,  # public, anyone with the link can join as guest
                    "roomName": room_name,
                },
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
            room_token = data["ocs"]["data"]["token"]
            return f"{base_url.rstrip('/')}/index.php/call/{room_token}"
        except Exception as e:
            _logger.error("Failed to create Nextcloud Talk room: %s", e)
            return False

    def _decrypt_nc_talk_password(self, encrypted_value):
        """Decrypt Nextcloud Talk password using Fernet.

        Raises UserError if the encryption key is missing or decryption fails,
        instead of falling back to returning the raw (potentially plaintext) value.
        """
        if not encrypted_value:
            return False
        try:
            from cryptography.fernet import Fernet, InvalidToken
        except ImportError:
            _logger.error(
                "cryptography package not installed - cannot decrypt NC Talk password"
            )
            return False
        from ._crypto import get_encryption_key
        key = get_encryption_key(self.env, auto_generate=False)
        if not key:
            _logger.error(
                "bf_appointment Fernet key not set (env/odoo.conf/ICP) - cannot decrypt NC Talk password"
            )
            return False
        try:
            f = Fernet(key.encode())
            return f.decrypt(encrypted_value.encode()).decode()
        except InvalidToken:
            _logger.error(
                "NC Talk password decryption failed - key mismatch or corrupted data"
            )
            return False
        except Exception:
            _logger.exception("NC Talk password decryption error")
            return False

    def get_duration_display(self):
        """Return human-readable duration label."""
        self.ensure_one()
        minutes = int(self.duration * 60)
        if minutes >= 60:
            h = minutes // 60
            m = minutes % 60
            return f"{h}h{m:02d}" if m else f"{h}h"
        return f"{minutes} min"

    @api.depends("start")
    def _compute_is_overdue(self):
        """Verrou de modification/annulation.

        Ventilation du « Modifications Deadline » OCA : ce verrou est désormais
        piloté par `type_id.modification_lock_hours`, distinct du plancher de
        disponibilité (`type_id.modifications_deadline`, relabellé « Préavis
        minimum avant réservation »). Passé le verrou, `is_modifiable` (OCA)
        tombe à False pour le portail; l'auto-annulation OCA des réservations
        non confirmées reste inchangée.
        """
        now = fields.Datetime.now()
        for one in self:
            if not one.start:
                one.is_overdue = False
                continue
            lock_hours = one.type_id.modification_lock_hours or 0.0
            deadline = one.start - timedelta(hours=lock_hours)
            one.is_overdue = now > deadline

    # ---- Locale-aware calendar labels (public scheduling page) ----

    def _appt_locale(self):
        """Babel locale code for the current booker context.

        Defaults to fr_CA. Used instead of datetime.strftime('%A'/'%B'),
        which is driven by the server's C locale (LC_TIME) and therefore
        leaked English weekday/month names on the public slot picker even
        for a fr_CA booker.
        """
        return (self.env.context.get("lang") or self.env.lang or "fr_CA").replace("-", "_")

    def appt_format_weekday(self, day):
        """Locale-aware weekday name (e.g. 'lundi') for a slot day header.

        The template span carries `text-capitalize`, so a lowercase babel
        result is displayed capitalized without extra work here.
        """
        from babel.dates import format_date
        return format_date(day, "EEEE", locale=self._appt_locale())

    def appt_format_month(self, day):
        """Locale-aware 'month year' header (e.g. 'juillet 2026')."""
        from babel.dates import format_date
        return format_date(day, "MMMM yyyy", locale=self._appt_locale())

    # ---- Titre ----

    @api.model
    def _bf_build_title(self, booking_type, partner=None, booker_name=None, lang=None):
        """Titre lisible d'un rendez-vous : « Type - Organisation x Marque ».

        Sert au nom de la réservation, au titre de l'événement d'agenda et au
        SUMMARY du .ics — un seul endroit à changer. L'ancien « RDV - Prénom
        Nom » ne disait ni de quel type de rencontre il s'agissait ni avec qui,
        ce qui rend l'agenda illisible dès qu'on a plus d'un RDV par jour.

        L'organisation prime sur la personne quand on la connaît (contact
        rattaché à une société, ou société saisie au formulaire) : « Rencontre
        flexible - Acme x Notre marque » se lit mieux que le nom du signataire.
        La marque vient de `appointment_brand_name` pour rester juste sur les
        locataires qui n'affichent pas leur raison sociale.

        ⚠️ `name` est un Char NON traduit : la langue utilisée ici est figée
        pour toujours. Le formulaire public, lui, peut tourner en anglais sur
        un simple `Accept-Language` de navigateur — sans `lang` explicite, un
        RDV francophone ressortait « Flexible meeting - … » dans l'agenda.
        L'appelant passe donc la langue du réservant (à défaut celle de
        l'organisateur), jamais celle de la requête HTTP.
        """
        lang = lang or (partner.lang if partner else None) or self.env.lang
        if lang:
            booking_type = booking_type.with_context(lang=lang)
            self = self.with_context(lang=lang)
        who = ""
        if partner:
            who = (
                partner.parent_id.name or partner.company_name or partner.name or ""
            ).strip()
        who = who or (booker_name or "").strip()
        company = booking_type.company_id or self.env.company
        brand = (company.appointment_brand_name or company.name or "").strip()
        type_name = (booking_type.name or _("Rendez-vous")).strip()
        if who and brand:
            return f"{type_name} - {who} x {brand}"
        if who:
            return f"{type_name} - {who}"
        return type_name

    # ---- Description de l'événement d'agenda ----

    def _bf_meeting_description(self):
        """Corps de l'événement d'agenda : ce que le demandeur a écrit.

        L'OCA pose ``description = type_id.requester_advice``, c'est-à-dire le
        texte d'accompagnement du formulaire public (« Décrivez brièvement le
        sujet pour qu'on prépare la rencontre… »). C'est une consigne adressée
        au demandeur AVANT qu'il réserve : recopiée dans l'agenda, elle occupe
        exactement la place de sa réponse et n'apprend rien à personne. Et
        comme `calendar_nextcloud_sync` repousse ce champ tel quel
        (html2plaintext) vers le calendrier, l'invitation .ics reçue affiche la
        consigne au lieu du sujet du rendez-vous — constaté sur le RDV #377
        (un rendez-vous client, 2026-08-03), où « De quoi s'agit-il ? » ressortait en texte
        indicatif alors que la réponse était bien enregistrée.

        On rend donc les réponses du formulaire d'accueil — et RIEN quand il
        n'y en a pas. ⚠️ Le conseil au demandeur ne sert plus de repli : un
        lien de réservation personnel ne passe par aucun formulaire, donc la
        consigne y sortait SYSTÉMATIQUEMENT, et une description vide se lit
        mieux qu'une consigne adressée à quelqu'un d'autre, à un moment déjà
        passé. Constaté en production sur un lien personnel, où l'agenda affichait
        « Briefly describe the topic so we can prep… » à la place du sujet.

        ⚠️ Le libellé des questions (`appointment.intake.field.name`) et le
        conseil au demandeur sont des champs TRADUITS, et le formulaire public
        suit l'`Accept-Language` du navigateur. Lus dans la langue de la
        requête, ils ressortent « What is this about? » dans l'agenda d'un RDV
        par ailleurs francophone. On fige donc la langue du demandeur, à défaut
        celle de l'organisateur — même règle que `_bf_build_title`.
        """
        self.ensure_one()
        booker = self.partner_id or self.partner_ids[:1]
        lang = booker.lang or self.user_id.lang
        record = self.with_context(lang=lang) if lang else self
        # ⚠️ Confidentialité. Cette description part dans l'invitation .ics
        # reçue par TOUS les participants, et l'agenda n'en porte qu'une : on ne
        # peut pas la personnaliser par destinataire. Dès qu'un invité
        # additionnel est confirmé, les réponses du formulaire sont donc
        # retirées, sauf si le type dit explicitement de les partager. Ce que le
        # demandeur a écrit ne regarde pas forcément les gens qu'il convie.
        partage = record.type_id.guests_see_intake
        invites = record.guest_ids.filtered(lambda g: g.state == "confirmed")
        if invites and not partage:
            return ""

        parts = []
        for answer in record.intake_answer_ids:
            value = (answer.value or "").strip()
            if not value:
                continue
            parts.append(
                Markup("<p><strong>%s</strong><br/>%s</p>")
                % (
                    answer.field_name or "",
                    Markup("<br/>").join(value.splitlines()),
                )
            )
        if not parts:
            return ""
        if booker:
            who = (booker.name or "").strip()
            if booker.email:
                # Parenthèses et non chevrons : la description repasse par
                # Nextcloud, qui la renvoie en texte brut, et un `<courriel>`
                # se fait alors avaler comme une balise au retour en HTML.
                who = ("%s (%s)" % (who, booker.email)).strip()
            if who:
                parts.append(
                    Markup("<p><strong>%s</strong> : %s</p>")
                    % (record.env._("Demandeur"), who)
                )
        return Markup("").join(parts)

    def _bf_sync_meeting_description(self):
        """Ré-aligne la description de l'événement sur les réponses courantes.

        `_prepare_meeting_vals` ne tourne qu'à la synchro de l'événement. Une
        réponse ajoutée ou corrigée après coup — au backend, ou par tout flux
        qui pose `start` avant d'écrire les réponses — laisserait sinon l'agenda
        sur l'ancien texte. Écriture sans notification : le rendez-vous n'a pas
        bougé, seule sa description est rafraîchie.
        """
        for booking in self:
            meeting = booking.meeting_id
            if not meeting:
                continue
            description = booking._bf_meeting_description()
            if (meeting.description or "") == (description or ""):
                continue
            meeting.with_context(
                no_mail_to_attendees=True,
                dont_notify=True,
                tracking_disable=True,
                mail_notrack=True,
            ).description = description

    # ---- ICS Generation ----

    def _generate_ics_data(self):
        """Generate ICS calendar data for this booking."""
        self.ensure_one()
        if not self.start:
            return False
        duration_hours = self.duration or 1.0
        stop = self.start + timedelta(hours=duration_hours)
        # Localize once, in the booker display tz, and reuse for BOTH the
        # human-readable DESCRIPTION and DTSTART/DTEND below — otherwise the
        # notes text renders in naive UTC and contradicts the grid time
        # (the RDV #344 bug class, just in the .ics body instead of the email).
        tzname = self._get_ics_tzname()
        tz = ZoneInfo(tzname)
        start_local = self.start.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
        end_local = stop.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
        base_url = self.get_base_url()
        booking_url = (
            f"{base_url}/appointment/b/{self.id}/{self.access_token}"
        )
        cancel_url = f"{booking_url}/cancel"
        schedule_url = f"{booking_url}/schedule"
        # Build description with all pertinent info
        desc_parts = [self.type_id.name or _("Rendez-vous")]
        desc_parts.append("")
        # `strftime("%A %d %B %Y")` suit la locale du PROCESSUS, pas celle du
        # destinataire : un .ics par ailleurs francophone sortait « Thursday 30
        # July 2026 ». Babel, déjà utilisé par le calendrier public, rend la
        # date dans la langue du lecteur.
        from babel.dates import format_date as _babel_format_date
        desc_parts.append(
            _("Date : %s")
            % _babel_format_date(
                start_local.date(), "EEEE d MMMM yyyy", locale=self._appt_locale()
            )
        )
        desc_parts.append(
            _("Heure : %s") % start_local.strftime("%H:%M")
        )
        desc_parts.append(
            _("Dur\u00e9e : %s") % self.get_duration_display()
        )
        if self.partner_id:
            desc_parts.append(
                _("Participant : %s") % self.partner_id.name
            )
        if self.videocall_location:
            desc_parts.append("")
            desc_parts.append(
                _("Vid\u00e9oconf\u00e9rence : %s") % self.videocall_location
            )
        if self.location:
            desc_parts.append(_("Lieu : %s") % self.location)
        # Intake form answers
        if self.intake_answer_ids:
            desc_parts.append("")
            for answer in self.intake_answer_ids:
                desc_parts.append(
                    f"{answer.field_name} : {answer.value}"
                )
        desc_parts.append("")
        desc_parts.append(_("Voir mon rendez-vous : %s") % booking_url)
        desc_parts.append(_("Modifier l'horaire : %s") % schedule_url)
        desc_parts.append(_("Annuler : %s") % cancel_url)
        description = "\n".join(desc_parts)
        # Location
        location = self.videocall_location or self.location or ""
        # UID
        uid = f"bf-appointment-{self.id}@{base_url.split('//')[1] if '//' in base_url else 'odoo'}"
        # Format dates. Odoo stores datetimes naive-UTC. For America/Toronto we
        # ship a matching VTIMEZONE and render DTSTART/DTEND with TZID; for any
        # other zone we have no VTIMEZONE to ship, so we emit the instant in
        # UTC (DTSTART:...Z), which is unambiguous in every client. A bare TZID
        # with no matching VTIMEZONE is treated as floating local time
        # (RFC 5545 §3.2.19) and mis-renders in strict clients like Outlook
        # desktop. The DTSTAMP stays UTC per RFC 5545 (§3.8.7.2).
        dtstamp = fields.Datetime.now().strftime("%Y%m%dT%H%M%SZ")
        if tzname == "America/Toronto":
            vtimezone_block = _VTIMEZONE_AMERICA_TORONTO
            dtstart_line = f"DTSTART;TZID={tzname}:{start_local.strftime('%Y%m%dT%H%M%S')}\r\n"
            dtend_line = f"DTEND;TZID={tzname}:{end_local.strftime('%Y%m%dT%H%M%S')}\r\n"
        else:
            vtimezone_block = ""
            dtstart_line = f"DTSTART:{self.start.strftime('%Y%m%dT%H%M%S')}Z\r\n"
            dtend_line = f"DTEND:{stop.strftime('%Y%m%dT%H%M%S')}Z\r\n"
        summary = _escape_ics(
            self.name or self._bf_build_title(self.type_id, partner=self.partner_id)
        )
        # Organizer/Attendee: METHOD:REQUEST requires an ORGANIZER (RFC 5546);
        # ATTENDEE makes the invite RSVP-able in Outlook / Google / Apple Mail.
        organizer_email = (
            self.env.company.email
            or "service@example.com"
        )
        organizer_name = _escape_ics_param(
            self.env.company.name or "Blue Fox"
        )
        ics = (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "PRODID:-//Blue Fox Inc//BF Appointment//FR\r\n"
            "CALSCALE:GREGORIAN\r\n"
            "METHOD:REQUEST\r\n"
            + vtimezone_block
            + "BEGIN:VEVENT\r\n"
            f"UID:{uid}\r\n"
            f"DTSTAMP:{dtstamp}\r\n"
            + dtstart_line
            + dtend_line
            + f"SUMMARY:{summary}\r\n"
            f'ORGANIZER;CN="{organizer_name}":mailto:{organizer_email}\r\n'
        )
        if self.partner_id and self.partner_id.email:
            attendee_name = _escape_ics_param(self.partner_id.name or "")
            ics += (
                f'ATTENDEE;CN="{attendee_name}";ROLE=REQ-PARTICIPANT;'
                f"PARTSTAT=NEEDS-ACTION;RSVP=TRUE:"
                f"mailto:{self.partner_id.email}\r\n"
            )
        if location:
            ics += f"LOCATION:{_escape_ics(location)}\r\n"
        if self.videocall_location:
            ics += f"URL:{self.videocall_location}\r\n"
        ics += f"DESCRIPTION:{_escape_ics(description)}\r\n"
        ics += (
            "STATUS:CONFIRMED\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )
        return ics.encode("utf-8")

    def _get_booker_display_tz(self):
        """Timezone for ALL booker-facing renders: the web confirmation page,
        the ICS attachment, the booker's emails and the default slot picker.

        Priority: the booker's own ``partner_id.tz`` → the booking type's
        display calendar tz (the client-facing "display window", Montréal in
        the NZ two-layer setup) → the company calendar tz → configured
        default.

        Deliberately EXCLUDES the organizer's ``user_id.tz``. The organizer
        (the organizer) sits in Auckland; letting that leak into booker-facing
        content is exactly what made a Montréal client's confirmation and ICS
        show NZ time (RDV #344, 2026-06-17). Organizer-facing comms receive
        the organizer tz explicitly via _send_appointment_email.
        """
        self.ensure_one()
        cal_type = self.type_id.resource_calendar_id
        cal_company = self.env.company.resource_calendar_id
        # A booker partner.tz of "UTC" is almost always a stale browser-tz
        # detection fallback from the public widget, not a real location.
        # Bookings are stored naive-UTC, so honouring it renders the raw UTC
        # instant to the booker -- a 13:00 Montréal slot shows as 17:00, the
        # +4h offset reported on RDV #357. No client of a Québec-based practice
        # is legitimately in UTC, so treat it as unset and fall through to the
        # type's Montréal display calendar.
        booker_tz = self.partner_id.tz if self.partner_id else None
        if booker_tz == "UTC":
            booker_tz = None
        return self.env["bf.timezone"].resolve([
            booker_tz,
            cal_type.tz if cal_type else None,
            cal_company.tz if cal_company else None,
        ], validate=True)

    def _get_available_slots(self, start_dt, end_dt):
        """Re-bucket OCA's portal slot grid into the booker's display timezone.

        OCA builds the slot grid from intervals that each retain their own
        resource calendar's tz. In the NZ two-layer setup that means the
        Montréal display calendar (America/Toronto) and the Auckland
        availability calendar (Pacific/Auckland) contribute slots in DIFFERENT
        offsets, grouped by ``.date()``. A Québec booker then sees Auckland-time
        bubbles mislabelled under the wrong day -- they pick "19 juin 8h" and it
        lands on the 18th (booking #343, 2026-06-17).

        We convert every slot to ``_get_booker_display_tz()`` (or the explicit
        context tz the picker passes) and regroup by the LOCAL date, deduping
        identical instants, so the picker shows one consistent local grid. Only
        the labelling changes; the underlying instants -- and the confirm step,
        which reads ``slot.isoformat()`` -- are untouched.
        """
        raw = super()._get_available_slots(start_dt, end_dt)
        import pytz
        tz_name = self.env.context.get("tz") or self._get_booker_display_tz()
        try:
            tz = pytz.timezone(tz_name)
        except Exception:  # pragma: no cover - defensive: bad tz string
            return raw
        seen = set()
        regrouped = {}
        for day_slots in raw.values():
            for slot in day_slots:
                aware = slot if slot.tzinfo else pytz.utc.localize(slot)
                local = aware.astimezone(tz)
                key = local.replace(microsecond=0).isoformat()
                if key in seen:
                    continue
                seen.add(key)
                regrouped.setdefault(local.date(), []).append(local)
        for day in regrouped:
            regrouped[day].sort()
        return regrouped

    def _get_ics_tzname(self):
        """IANA TZ name used to render DTSTART/DTEND in the ICS.

        Always the booker display tz: the absolute UTC instant is preserved
        regardless of the TZID, so the organizer's calendar still shows the
        correct local time, while the booker (and our shipped
        VTIMEZONE:America/Toronto block) stay consistent.
        """
        self.ensure_one()
        return self._get_booker_display_tz()

    def _get_ics_attachment(self):
        """Return an ir.attachment record with the ICS file for email attachment."""
        self.ensure_one()
        ics_data = self._generate_ics_data()
        if not ics_data:
            return self.env["ir.attachment"]
        attachment = self.env["ir.attachment"].create({
            "name": _("rendez-vous.ics"),
            "type": "binary",
            "datas": base64.b64encode(ics_data),
            "mimetype": "text/calendar",
            "res_model": "resource.booking",
            "res_id": self.id,
        })
        return attachment

    def action_bf_resend_invitations(self):
        """Renvoie l'invitation, avec le gabarit de l'envoi automatique.

        🔴 Ce bouton ouvrait l'assistant de partage d'Odoo
        (`portal.portal_share_action`), qui expédie un texte générique :
        « Cher(e) X, Untel vous a invité à accéder au/à la **resource
        booking** ». Le nom technique du modèle, aucune date, aucune heure,
        aucun fichier d'agenda — et c'est ce qu'un client recevait. Signalé en
        production le 2026-08-26.

        Le renvoi emprunte désormais exactement le chemin de l'envoi
        automatique : même gabarit, même marque, même pièce .ics, même langue
        et même fuseau que le lecteur.
        """
        self.ensure_one()
        servis = self._bf_resend_invitations()
        if not servis:
            raise UserError(_(
                "Personne à qui renvoyer : ce rendez-vous n'a pas de "
                "participant joignable."))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": _("Invitation renvoyée à %(nb)s personne(s).",
                             nb=servis),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

    def _bf_resend_invitations(self):
        """Qui reçoit le renvoi, et par quel gabarit. Rend le nombre servi.

        Point d'extension : un module satellite dont la réservation a plusieurs
        destinataires — le sondage de disponibilités en a un par participant —
        surcharge ici plutôt que de refaire le bouton.
        """
        self.ensure_one()
        template = self.env.ref(
            "bf_appointment.mail_template_appointment_confirmation",
            raise_if_not_found=False)
        if not template or not self.partner_ids:
            return 0
        self._send_appointment_email(template, recipient="booker")
        return 1

    def _send_appointment_email(self, template, attach_ics=True, recipient=None):
        """Send an appointment email with optional ICS attachment.

        Respects the partner's language AND timezone for both the email
        template rendering and the ICS attachment content. This keeps a
        Montréal booker reading « 14:00 » while the same booking shows
        « 06:00 » to an organizer in Auckland, without storing two copies of
        start_*_local.

        ``recipient`` dit QUI lit : ``"organizer"`` ou ``"booker"``.

        ⚠️ À défaut, on retombe sur une inspection de l'expression
        ``email_to`` du gabarit — la présence de la chaîne « user_id ». Ça a
        marché jusqu'ici parce que les gabarits destinés à l'organisateur sont
        les seuls à nommer ``object.user_id``, mais c'est une heuristique, pas
        une règle : un gabarit qui mentionnerait ``object.user_id`` pour toute
        autre raison ferait basculer en silence le courriel d'un CLIENT dans le
        fuseau et la langue de l'organisateur, c'est-à-dire la famille de
        défauts que tout ce module passe son temps à corriger. Les appels du
        module sont donc explicites; le repli ne sert plus qu'aux appelants
        externes.

        Pass ``attach_ics=False`` for follow-up templates (suivi immédiat /
        1h / 2h après) where the booking has already happened, re-sending
        an ICS REQUEST for a past event would just clutter the recipient's
        calendar client.
        """
        self.ensure_one()
        # Guarantee access_token: portal links in templates render empty when
        # access_token is False, producing 404s like /appointment/b/22/. The
        # public flow calls _portal_ensure_token() at create time, but cron
        # paths and admin-confirmed bookings can still reach this method
        # without a token.
        if not self.access_token:
            self._portal_ensure_token()
        partner_lang, recipient_tz = self._bf_render_locale(template, recipient)
        booking_ctx = self.with_context(
            lang=partner_lang,
            tz=recipient_tz or False,
        )
        attachment = booking_ctx._get_ics_attachment() if attach_ics else False
        # Create the mail.mail without sending, attach the ICS explicitly,
        # then send. Going through email_values={'attachment_ids': ...} on
        # send_mail() lost attachments in production (confirmation arrived
        # without ICS in QA on 2026-04-25); writing to the record directly is
        # the only path Odoo 18 honors reliably.
        # Push lang+tz onto the template's context too, so the body_html
        # render (which lazily browses the record from the template's env)
        # picks up the same recipient-aware tz/lang as the ICS above.
        mail_id = template.with_context(
            lang=partner_lang,
            tz=recipient_tz or False,
        ).send_mail(self.id, force_send=False)
        mail = self.env["mail.mail"].browse(mail_id)
        if attachment:
            mail.write({"attachment_ids": [(4, attachment.id)]})
        mail.send()
        # La pièce .ics a fini son travail : elle est dans le message REMIS.
        # La garder attachée à la réservation en accumulait une par courriel
        # envoyé — confirmation, rappels, copie à l'organisateur — soit des
        # copies quasi identiques d'un fichier que personne ne rouvre depuis
        # Odoo (aucun gabarit ni aucune page ne la référence). On ne la retire
        # QUE sur un envoi réussi : un message resté en file a encore besoin
        # d'elle.
        if attachment and mail.exists() and mail.state == "sent":
            attachment.unlink()
        elif attachment and not mail.exists():
            # `auto_delete` a supprimé le mail après remise : c'est aussi un
            # succès, et la pièce n'a plus de porteur.
            attachment.unlink()

    def _bf_render_locale(self, template, recipient=None):
        """(langue, fuseau) dans lesquels rendre un courriel de rendez-vous.

        Extrait de `_send_appointment_email` pour être éprouvable seul : la
        propriété qui compte — une consigne explicite l'emporte sur
        l'heuristique — se vérifiait sinon en fouillant le corps d'un
        `mail.mail` déjà remis et parfois déjà effacé par `auto_delete`.

        ``recipient`` vaut ``"organizer"``, ``"booker"``, ou rien.

        ⚠️ À défaut, on inspecte l'expression ``email_to`` du gabarit et on
        cherche la chaîne « user_id ». Ça marche parce que les gabarits
        destinés à l'organisateur sont les seuls à nommer ``object.user_id``,
        mais c'est une heuristique : un gabarit qui le mentionnerait pour
        toute autre raison ferait basculer en silence le courriel d'un CLIENT
        dans le fuseau et la langue de l'organisateur — la famille de défauts
        que tout ce module passe son temps à corriger.
        """
        self.ensure_one()
        if recipient not in ("organizer", "booker"):
            recipient = (
                "organizer" if "user_id" in (template.email_to or "")
                else "booker"
            )
        if recipient == "organizer":
            tz = self.user_id.tz if self.user_id else False
            lang = self.user_id.lang if self.user_id else False
        else:
            # Booker-bound: render in the booker display tz. Never inherit the
            # organizer's (Auckland) tz, and never leave it empty — an empty tz
            # would let _compute_start_local_strings / the ICS fall through to
            # the organizer's tz again.
            tz = self._get_booker_display_tz()
            lang = self.partner_id.lang if self.partner_id else False
        return (lang or self.env.lang or "fr_CA"), tz

    # ---- SMS ----

    def _appointment_sms_phone(self):
        """The booker's number for SMS, normalized to 10 NANP digits, or None.

        The public form leaves the phone optional and stores whatever was
        typed on ``partner.phone`` — which may well be a landline, something
        no API can tell us. So this is best-effort by design: a None here, or
        a number that turns out not to receive texts, must never cost the
        booker their reminder. The caller falls back to e-mail either way.
        """
        self.ensure_one()
        partner = self.partner_id
        if not partner:
            return None
        for candidate in (partner.mobile, partner.phone):
            if not candidate:
                continue
            number = sms_api and sms_api.normalize_na(candidate)
            if number:
                return number
        return None

    def _render_appointment_sms(self, schedule):
        """Render ``schedule.sms_body`` for this booking, or None if unusable.

        Returns None (rather than a truncated message) when the rendered text
        breaks the GSM-7 budget: ``bf_securetransfer.sms.send()`` would cut it
        at 160 septets and VoIP.ms would likely refuse the result, so falling
        back to the e-mail template is both safer and more honest.
        """
        self.ensure_one()
        booker_tz = self._get_booker_display_tz()
        lang = (self.partner_id.lang if self.partner_id else False) \
            or self.env.lang or "fr_CA"
        rendered = self.env["mail.render.mixin"].with_context(
            lang=lang, tz=booker_tz or False,
        )._render_template(
            schedule.sms_body,
            "resource.booking",
            [self.id],
            engine="inline_template",
        )[self.id]
        rendered = (rendered or "").strip()
        if not rendered:
            return None
        error = _sms_text.check(rendered)
        if error:
            _logger.warning(
                "bf_appointment: SMS non envoyable pour la réservation %d "
                "(planification %d) — %s",
                self.id, schedule.id, error,
            )
            return None
        return rendered

    def _send_appointment_sms(self, schedule):
        """Try to deliver ``schedule``'s SMS to the booker. True when sent.

        Never raises: a False simply routes the caller to the e-mail template.
        """
        self.ensure_one()
        if sms_api is None or not sms_api.configured(self.env):
            return False
        phone = self._appointment_sms_phone()
        if not phone:
            return False
        body = self._render_appointment_sms(schedule)
        if not body:
            return False
        return sms_api.send(self.env, phone, body)

    # ---- Cron ----

    @api.model
    def _cron_send_appointment_reminders(self):
        """Backward compat alias for old cron."""
        return self._cron_send_appointment_emails()

    # Postgres advisory-lock key used to serialize cron execution.
    # Picked arbitrarily; only this cron uses it.
    _CRON_ADVISORY_LOCK_KEY = 0x4250414F4C434C4B  # "BPAOLCLK"

    # Tolérance de rattrapage d'un rappel « avant », en heures. Le cron passe
    # aux quarts d'heure, donc une heure absorbe largement un tick manqué sans
    # laisser partir un rappel devenu faux.
    _CRON_REMINDER_GRACE_HOURS = 1.0

    @api.model
    def _cron_reminder_grace_hours(self):
        """Tolérance de rattrapage, réglable par `ir.config_parameter`.

        ⚠️ Le réglage ABSENT — le cas de tous les locataires au déploiement —
        rend `False`, et `float(False)` vaut `0.0` sans lever : sans le premier
        test ci-dessous, la tolérance retombait silencieusement à zéro partout
        et PLUS AUCUN rappel ne partait. Ne pas confondre « non réglé » (défaut)
        et « réglé à zéro » (aucun rattrapage, choix légitime).
        """
        brut = self.env["ir.config_parameter"].sudo().get_param(
            "bf_appointment.reminder_grace_hours"
        )
        if brut in (None, False, ""):
            return self._CRON_REMINDER_GRACE_HOURS
        try:
            valeur = float(brut)
        except (TypeError, ValueError):
            return self._CRON_REMINDER_GRACE_HOURS
        return valeur if valeur >= 0 else self._CRON_REMINDER_GRACE_HOURS

    @api.model
    def _cron_send_appointment_emails(self):
        """Send scheduled appointment emails (reminders + follow-ups).

        Acquires a transaction-scoped Postgres advisory lock so two parallel
        runs (multi-worker cron, or scheduled tick + manual "Run Manually"
        click) cannot both pass the sent_schedule_ids check and double-send.
        Without this guard, QA on 2026-04-25 received the 24h reminder twice
        (38s apart) because the manual trigger raced the scheduled tick.
        """
        self.env.cr.execute(
            "SELECT pg_try_advisory_xact_lock(%s)",
            (self._CRON_ADVISORY_LOCK_KEY,),
        )
        if not self.env.cr.fetchone()[0]:
            _logger.info(
                "bf_appointment cron already running on another worker, skipping"
            )
            return
        now = fields.Datetime.now()
        # VoIP.ms stops accepting after roughly 27 messages and does not drain
        # until midnight — it is a daily quota, not a rate, so pacing inside
        # the run would buy nothing and would stall every other cron on the
        # tenant (max_cron_threads = 1). A per-run ceiling keeps a backlog from
        # burning the day's quota in one tick instead; the overflow leaves by
        # e-mail. Raise it once the limit is lifted with VoIP.ms (ticket + A2P
        # registration).
        sms_budget = int(self.env["ir.config_parameter"].sudo().get_param(
            "bf_appointment.sms_max_per_run", "25"
        ) or 0)
        # ⚠️ La recherche est BORNÉE, et pas seulement pour la charge.
        #
        # Le déclencheur « avant » porte son propre plafond (`now <
        # booking.start`). Le déclencheur « après », lui, n'en a aucun :
        # `now >= send_at` est vrai pour toujours. Une planification « après »
        # activée aujourd'hui sur un type — ou un vieux type repassé en public,
        # ce qui appelle `_create_default_email_schedules` — ne figure dans le
        # `sent_schedule_ids` d'AUCUNE réservation : au tick suivant, le suivi
        # serait parti d'un coup à toutes les réservations passées de ce type,
        # des années comprises. Sans cette borne, rien dans le code ne s'y
        # oppose.
        #
        # L'horizon est DÉDUIT des planifications, pas choisi au doigt mouillé :
        # une valeur en dur ferait taire en silence un suivi légitimement réglé
        # au-delà. On garde tout ce qui peut encore être dû, plus une marge qui
        # absorbe une instance arrêtée une fin de semaine.
        horizon_heures = max([
            planification["hours"]
            for planification in self.env["appointment.email.schedule"].sudo()
            .search_read([("active", "=", True), ("trigger", "=", "after")],
                         ["hours"])
        ] or [0.0])
        marge_jours = float(self.env["ir.config_parameter"].sudo().get_param(
            "bf_appointment.cron_lookback_grace_days", "2") or 2)
        plancher = now - timedelta(hours=horizon_heures) - timedelta(days=marge_jours)
        bookings = self.search([
            ("state", "in", ("confirmed", "scheduled")),
            ("start", "!=", False),
            ("start", ">", plancher),
            ("type_id.email_schedule_ids", "!=", False),
        ])
        for booking in bookings:
            # ⚠️ Chaque réservation a son propre point de reprise, et ce n'est
            # pas du confort : sans cette isolation, UNE réservation fautive
            # emporte la run entière, donc les rappels de tout le locataire.
            #
            # Vécu en production du 2026-08-30 au 2026-08-31. L'écriture de
            # `sent_schedule_ids` sur la réservation #414 faisait lever
            # `_check_scheduling` d'OCA (« Cannot schedule these bookings
            # because no resources are selected ») : une automatisation du
            # locataire filtrant sur `state` force un recalcul de `state` au
            # milieu de cette écriture, et la contrainte s'exécute alors sur un
            # enregistrement à moitié écrit. L'exception remontait hors de la
            # boucle — aucun rappel n'est parti pendant 24 h, pour AUCUNE
            # réservation, et le blocage ne s'est dénoué que quand l'heure de la
            # rencontre fautive est passée.
            #
            # Le verrou consultatif est pris AVANT le point de reprise : un
            # retour en arrière ne le relâche pas.
            try:
                with self.env.cr.savepoint():
                    sms_budget = booking._cron_send_for_booking(now, sms_budget)
            except Exception:
                # Le cache de l'ORM peut porter des valeurs que le retour en
                # arrière vient d'annuler.
                self.env.invalidate_all()
                _logger.exception(
                    "bf_appointment: la réservation %d a échoué, la run "
                    "continue avec les suivantes.",
                    booking.id,
                )

    def _cron_send_for_booking(self, now, sms_budget):
        """Traite une seule réservation. Rend le budget SMS restant.

        Extrait de `_cron_send_appointment_emails` pour que la boucle appelante
        puisse borner les dégâts d'une réservation fautive à cette réservation.
        """
        self.ensure_one()
        booking = self
        for schedule in booking.type_id.email_schedule_ids.filtered("active"):
            booking.invalidate_recordset(["sent_schedule_ids"])
            if schedule in booking.sent_schedule_ids:
                continue
            should_send = False
            if schedule.trigger == "before":
                send_at = booking.start - timedelta(hours=schedule.hours)
                # ⚠️ La fenêtre est BORNÉE des deux côtés. Avec le seul
                # `now >= send_at`, deux situations produisent un avis qui
                # ment :
                #  1. réserver à moins de X heures de l'échéance fait
                #     partir le rappel « X heures avant » dans la minute
                #     qui suit la réservation — relevé le 2026-08-26,
                #     une cliente a reçu « rappel : demain » 90 secondes
                #     après avoir réservé pour le lendemain ;
                #  2. une reprise après interruption du cron rejoue un
                #     rappel « 24 h avant » à deux heures de la rencontre.
                # La tolérance ne dépasse jamais le préavis lui-même,
                # sinon elle n'en serait plus une : un rappel « 30 min
                # avant » garde 30 minutes de rattrapage, pas une heure.
                tolerance = min(
                    timedelta(hours=self._cron_reminder_grace_hours()),
                    timedelta(hours=schedule.hours),
                )
                should_send = (
                    send_at <= now < booking.start
                    and now <= send_at + tolerance
                )
            elif schedule.trigger == "after":
                stop = booking.start + timedelta(
                    hours=booking.duration or 1.0
                )
                send_at = stop + timedelta(hours=schedule.hours)
                should_send = now >= send_at
            if not should_send:
                continue
            # Claim the schedule BEFORE sending so a transient send
            # failure does not retry forever, and so any concurrent path
            # that bypasses the advisory lock still sees the claim.
            booking.sent_schedule_ids = [(4, schedule.id)]
            # Skip ICS on "after" follow-ups, the meeting already
            # happened, so re-sending the calendar invite is noise.
            attach_ics = schedule.trigger != "after"
            sms_sent = False
            if schedule.channel in ("sms", "both") and sms_budget > 0:
                try:
                    sms_sent = booking._send_appointment_sms(schedule)
                except Exception as e:  # never let SMS sink the run
                    _logger.error(
                        "bf_appointment: SMS en erreur pour la "
                        "réservation %d (planification %d): %s",
                        booking.id, schedule.id, e,
                    )
                    sms_sent = False
                if sms_sent:
                    sms_budget -= 1
                elif booking._appointment_sms_phone():
                    # A booker with a number on file whose send still
                    # failed means the refusal came from VoIP.ms, not
                    # from our data — most likely the ~27/day quota,
                    # which does not drain until midnight. Retrying the
                    # rest of the batch would only hammer the account
                    # (suspension risk), so stand down for this run and
                    # let e-mail carry the remainder. The next tick
                    # tries again from scratch.
                    sms_budget = 0
                    _logger.warning(
                        "bf_appointment: SMS refusé pour la réservation "
                        "%d — bascule du reste de l'exécution sur le "
                        "courriel.", booking.id,
                    )
            # E-mail goes out unless the SMS alone was asked for and it
            # actually left: it is the fallback for every failure path
            # (no number, refused message, quota), so a reminder is never
            # silently dropped.
            if not (schedule.channel == "sms" and sms_sent):
                try:
                    # Pas de `recipient=` explicite ici, et c'est voulu :
                    # le gabarit d'une planification est choisi par un
                    # administrateur, donc lui seul sait à qui il écrit.
                    # C'est le cas où l'inspection d'`email_to` reste le
                    # seul signal disponible.
                    booking._send_appointment_email(
                        schedule.template_id, attach_ics=attach_ics
                    )
                except Exception as e:
                    _logger.error(
                        "Failed to send scheduled email for booking %d "
                        "(schedule %d): %s",
                        booking.id,
                        schedule.id,
                        e,
                    )
        return sms_budget

    # ------------------------------------------------------------------
    # Lot d'ouverture (2.40.0) — résolution de la provenance
    # ------------------------------------------------------------------

    def _bf_source_record(self):
        """Résout `bf_source_ref` en enregistrement, ou rend un recordset vide.

        Tolère tout : référence vide, modèle désinstallé, identifiant supprimé.
        Un satellite retiré ne doit jamais faire planter la fiche d'une
        réservation qu'il a créée de son vivant.
        """
        self.ensure_one()
        ref = (self.bf_source_ref or "").strip()
        if "," not in ref:
            return self.env["resource.booking"].browse()
        model_name, _, res_id = ref.partition(",")
        model_name = model_name.strip()
        if model_name not in self.env:
            return self.env["resource.booking"].browse()
        try:
            record = self.env[model_name].browse(int(res_id))
        except (TypeError, ValueError):
            return self.env["resource.booking"].browse()
        return record if record.exists() else self.env[model_name].browse()

    def action_bf_source(self):
        """Ouvre l'enregistrement d'origine. `res_model` est une CHAÎNE.

        Nom PUBLIC volontairement : Odoo 18 refuse qu'un bouton de vue appelle
        une méthode préfixée d'un souligné. Le résolveur `_bf_source_record`,
        lui, n'est appelé que depuis du code et reste privé.

        C'est ce qui permet au bouton « Voir l'origine » d'exister dans le
        parent sans qu'il connaisse le modèle visé : la chaîne n'est résolue
        qu'au clic, donc aucune dépendance au chargement du registre.
        """
        self.ensure_one()
        record = self._bf_source_record()
        if not record:
            return False
        return {
            "type": "ir.actions.act_window",
            "res_model": record._name,
            "res_id": record.id,
            "view_mode": "form",
            "target": "current",
        }

    # ------------------------------------------------------------------
    # Lien unique (2.42.0)
    # ------------------------------------------------------------------

    @api.depends("bf_source", "link_expires_at", "link_single_use",
                 "link_used_at", "state")
    def _compute_link_state(self):
        maintenant = fields.Datetime.now()
        for booking in self:
            if booking.bf_source != "onetime":
                booking.link_state = "none"
            elif booking.link_expires_at and booking.link_expires_at <= maintenant:
                booking.link_state = "expired"
            elif booking.link_single_use and booking.link_used_at:
                booking.link_state = "used"
            else:
                booking.link_state = "active"

    @api.depends("access_token")
    def _compute_one_time_url(self):
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        for booking in self:
            if booking.access_token and booking.id:
                booking.one_time_url = "%s/appointment/b/%d/%s/schedule" % (
                    base, booking.id, booking.access_token)
            else:
                booking.one_time_url = ""

    def _link_is_usable(self):
        """Le lien ouvre-t-il encore le choix d'un créneau ?

        Rendu au contrôleur, qui doit DIRE pourquoi il refuse plutôt que de
        rediriger en silence. Un lien mort qui renvoie sur la page d'accueil
        laisse la personne convaincue d'avoir mal cliqué, et elle réessaie.
        """
        self.ensure_one()
        return self.link_state in ("none", "active")

    def _mark_link_used(self):
        for booking in self:
            if booking.bf_source == "onetime" and not booking.link_used_at:
                booking.sudo().link_used_at = fields.Datetime.now()
        return True

    def action_copy_one_time_url(self):
        """Ouvre la fenêtre de copie du lien."""
        self.ensure_one()
        return self._bf_action_show_link()

    def _bf_action_show_link(self):
        """Fenêtre minimale : le lien, avec le bouton copier natif d'Odoo.

        Une notification affichait bien l'adresse, mais il fallait la
        sélectionner à la souris — sur une URL de 90 caractères, c'est raté une
        fois sur deux. Le widget `CopyClipboardURL` d'Odoo fait le travail, et
        la copie part d'un clic de l'usager, ce que les navigateurs exigent.
        """
        self.ensure_one()
        # `url` et `expires_display` se déduisent de `booking_id` : ils ne sont
        # plus recopiés dans la table de l'assistant (le lien vaut jeton).
        assistant = self.env["bf.appointment.onetime.wizard"].create({
            "type_id": self.type_id.id,
            "partner_id": self.partner_ids[:1].id or False,
            "state": "done",
            "booking_id": self.id,
        })
        return {
            "type": "ir.actions.act_window",
            "name": _("Lien de réservation"),
            "res_model": "bf.appointment.onetime.wizard",
            "res_id": assistant.id,
            "view_mode": "form",
            "target": "new",
        }

    # ------------------------------------------------------------------
    # Invités additionnels (2.45.0)
    # ------------------------------------------------------------------

    @api.depends("guest_ids.state")
    def _compute_guest_state(self):
        for booking in self:
            etats = set(booking.guest_ids.mapped("state"))
            if not etats:
                booking.guest_state = "none"
            elif "pending" in etats:
                booking.guest_state = "pending"
            elif "confirmed" in etats:
                booking.guest_state = "confirmed"
            else:
                booking.guest_state = "declined"

    def _bf_pending_guests(self):
        self.ensure_one()
        return self.guest_ids.filtered(lambda g: g.state == "pending")

    def action_bf_confirm_guests(self):
        """Confirme les invités depuis le back-office (même effet que le lien)."""
        for booking in self:
            booking._bf_pending_guests()._bf_confirm()
        return True

    def action_bf_decline_guests(self):
        for booking in self:
            booking._bf_pending_guests()._bf_decline()
        return True

    # ------------------------------------------------------------------
    # Consentements (2.49.0)
    # ------------------------------------------------------------------

    # ⚠️ L'accroche des consentements à la confirmation vit dans
    # `action_confirm` (plus haut, avec le reste de la confirmation) et NON
    # ici. Elle y a d'abord été écrite en second `def action_confirm` dans ce
    # même corps de classe : Python garde le dernier, silencieusement, et la
    # méthode d'origine — génération de la salle visio, retrait des ressources
    # non retenues d'un K-of-N — a cessé d'exister sans qu'aucun test ni
    # aucun avertissement ne le dise. Une seule définition, désormais.

    def _bf_consent_subject(self):
        """La personne dont le consentement est en jeu.

        Le premier demandeur, comme partout ailleurs dans le module
        (`_bf_action_show_link` prend la même). Les invités additionnels ne
        sont PAS inclus : ils n'ont pas choisi le créneau, ils ne voient pas
        la page, et leur poser la question suppose un canal qui n'existe pas
        encore ici.
        """
        self.ensure_one()
        return self.partner_ids[:1]

    def _bf_required_consents(self):
        """Les consentements que ce type de rendez-vous exige, en une liste.

        Une liste et non un booléen : le jour où un type exigera autre chose
        que l'enregistrement, les quatre chemins de création suivent sans
        retouche.

        Volontairement PAS l'infolettre : c'est un opt-in offert sur le
        formulaire d'accueil, pas une exigence du type. Et pas le
        consentement de collecte non plus — voir `_bf_ensure_consents`.
        """
        self.ensure_one()
        asks = []
        if self.type_id.requires_recording_consent:
            asks.append({
                "code": "recording",
                "notice": self.type_id.recording_notice_id,
            })
        return asks

    def _bf_consent_status(self, code, notice):
        """État d'un consentement pour le demandeur : au dossier, demandé,
        refusé, ou manquant.

        « Au dossier » réutilise `res.partner._bf_active_consent`, donc
        exactement la même définition d'actif que le formulaire public : ni
        expiré, ni révoqué, et rattaché à l'avis courant.
        """
        self.ensure_one()
        partner = self._bf_consent_subject()
        if not partner:
            return "missing"
        notice_id = notice.id if notice else False
        if partner._bf_active_consent(code, notice_id):
            return "granted"
        Consent = self.env["privacy.consent"].sudo()
        base = [
            ("subject_partner_id", "=", partner.id),
            ("purpose_id.code", "=", code),
            ("active", "=", True),
        ]
        if notice_id:
            base.append(("notice_id", "=", notice_id))
        if Consent.search_count(base + [("status", "in", ("draft", "pending"))]):
            return "requested"
        if Consent.search_count(base + [("status", "=", "refused")]):
            return "refused"
        return "missing"

    def _bf_missing_consents(self):
        """Ce qui reste à obtenir : ni au dossier, ni déjà demandé, ni refusé.

        Un refus est une réponse. Le redemander à chaque réservation est
        exactement le harcèlement que le consentement est censé empêcher.
        """
        self.ensure_one()
        return [
            ask for ask in self._bf_required_consents()
            if self._bf_consent_status(ask["code"], ask["notice"]) == "missing"
        ]

    @api.depends("type_id.requires_recording_consent",
                 "type_id.recording_notice_id", "partner_ids")
    def _compute_bf_consent_state(self):
        """Le pire état parmi les consentements exigés.

        « Pire » au sens de ce qui appelle une action : manquant d'abord,
        puis refusé, puis demandé. Une rencontre à deux consentements dont un
        seul manque doit se lire « manquant ».
        """
        # ⚠️ Ces dépendances sont INCOMPLÈTES, et volontairement : le reste de
        # la vérité vit dans `privacy.consent`, qui ne pointe pas vers la
        # réservation. Aucune chaîne de `depends` ne peut voir un consentement
        # accordé ailleurs. Le calcul est donc juste à la LECTURE, et les deux
        # écrivains du module invalident explicitement après écriture — sans
        # quoi la valeur reste en cache dans la même transaction, et l'on
        # relit « manquant » sur un consentement qu'on vient de consigner.
        ordre = ("missing", "refused", "requested", "granted")
        for booking in self:
            asks = booking._bf_required_consents()
            if not asks:
                booking.bf_consent_state = "none"
                continue
            etats = {
                booking._bf_consent_status(ask["code"], ask["notice"])
                for ask in asks
            }
            booking.bf_consent_state = next(e for e in ordre if e in etats)

    def _search_bf_consent_state(self, operator, value):
        """Filtrable, au prix d'un calcul en Python.

        L'état n'est pas exprimable en domaine : il croise le type, le
        contact, et des consentements qui ne pointent pas vers la réservation.

        ⚠️ Le calcul est fait une fois par COUPLE (demandeur, type), pas une
        fois par réservation. `bf_consent_state` est une fonction pure de ces
        deux-là — `_bf_required_consents` ne lit que le type,
        `_bf_consent_status` que le contact et l'avis — donc deux réservations
        du même client sur le même type donnent forcément le même état.
        Réservation par réservation, c'était deux `search_count` chacune : un
        client fidèle avec vingt rendez-vous en payait vingt fois le prix pour
        vingt fois la même réponse.
        """
        if operator not in ("=", "!=", "in", "not in"):
            raise NotImplementedError(
                "Filtre non pris en charge sur bf_consent_state : %s" % operator
            )
        cibles = set(value if isinstance(value, (list, tuple)) else [value])
        negatif = operator in ("!=", "not in")
        # Seuls les types qui exigent quelque chose peuvent sortir de « none ».
        domaine = [] if "none" in cibles or negatif else [
            ("type_id.requires_recording_consent", "=", True)
        ]
        connus = {}
        ids = []
        for booking in self.sudo().search(domaine):
            cle = (booking.partner_ids[:1].id, booking.type_id.id)
            if cle not in connus:
                connus[cle] = booking.bf_consent_state
            if (connus[cle] in cibles) != negatif:
                ids.append(booking.id)
        return [("id", "in", ids)]

    def action_bf_request_consents(self):
        """Envoyer la demande de consentement depuis le back-office.

        Doublon volontaire de ce que fait `action_confirm` : une réservation
        peut avoir été confirmée avant ce lot, ou la personne peut avoir changé
        d'adresse depuis. Le bouton n'apparaît que sur l'état « manquant », et
        la demande reste idempotente — un consentement déjà en attente ne
        repart pas.
        """
        for booking in self:
            # Le bouton est un geste humain, délibéré, sur une fiche ouverte :
            # il n'a pas à attendre l'interrupteur des envois automatiques.
            crees = booking.with_context(
                bf_force_consent_request=True)._bf_request_missing_consents()
            if not crees:
                raise UserError(_(
                    "Rien à demander pour « %s » : soit le consentement est "
                    "déjà au dossier ou en attente, soit le demandeur n'a pas "
                    "d'adresse courriel, soit le type n'a aucun modèle d'avis "
                    "rattaché.", booking.display_name))
        return True

    def _bf_consent_evidence_from_request(self):
        """Contexte de preuve tiré de la requête HTTP courante, ou vide.

        Rendu séparément pour que le modèle reste appelable hors requête (cron,
        shell, satellite) sans que la preuve devienne un mensonge : pas de
        requête, pas d'IP inventée.
        """
        try:
            from odoo.http import request
        except ImportError:  # pragma: no cover
            return {}
        if not request or not getattr(request, "httprequest", None):
            return {}
        env_meta = request.httprequest.environ
        return {
            # ⚠️ `remote_addr` et RIEN d'autre. `proxy_mode` est actif dans ce
            # déploiement : werkzeug a déjà réécrit l'adresse depuis le nombre
            # de sauts de confiance configuré. Lire soi-même
            # X-Forwarded-For / X-Real-IP rendrait la preuve FALSIFIABLE par
            # celui-là même qu'elle est censée engager — un en-tête est
            # attaquant-contrôlé dès que le point d'entrée est joignable
            # directement. Même règle que `_client_ip()` du contrôleur, dont
            # ceci est le pendant côté modèle.
            "ip_address": request.httprequest.remote_addr or "unknown",
            "user_agent": env_meta.get("HTTP_USER_AGENT", "")[:512],
            "accept_language": env_meta.get("HTTP_ACCEPT_LANGUAGE", "")[:128],
            "referer_url": env_meta.get("HTTP_REFERER", "")[:512],
            "request_method": request.httprequest.method,
            "session_id": (
                request.session.sid if hasattr(request, "session") else False
            ),
        }

    def _bf_record_consent(self, code, notice, granted, evidence=None,
                           collection_method="portal", source_note=""):
        """Écrit le `privacy.consent` et sa preuve. Écrivain UNIQUE.

        Le formulaire d'accueil, la page de choix de créneau et le back-office
        passent tous par ici. Trois copies de cette séquence divergeraient au
        premier ajustement, et une preuve qui diverge ne prouve plus rien.

        `context_ref` reste ancré sur le CONTACT : la sélection de
        `privacy.consent` n'accepte que projet ou contact. La réservation
        d'origine est tracée dans `notes`, sous un préfixe cherchable.
        """
        self.ensure_one()
        partner = self._bf_consent_subject()
        if not partner:
            return False
        Consent = self.env["privacy.consent"].sudo()
        Purpose = self.env["privacy.purpose"].sudo()
        purpose = Purpose.search([("code", "=", code)], limit=1)
        if not purpose:
            _logger.warning("privacy.purpose code=%s absent, consentement non écrit", code)
            return False
        version = self._bf_notice_version(notice)
        marqueur = "resource.booking=%d" % self.id
        maintenant = fields.Datetime.now()
        consent = Consent.create({
            "subject_partner_id": partner.id,
            "purpose_id": purpose.id,
            "notice_id": notice.id if notice else False,
            "notice_version_id": version.id if version else False,
            "status": "granted" if granted else "refused",
            "collection_method": collection_method,
            "context_ref": "res.partner,%d" % partner.id,
            "active": True,
            "granted_at": maintenant if granted else False,
            "refused_at": False if granted else maintenant,
            "notes": "Source: %s (type=%s)\n%s" % (
                marqueur, self.type_id.slug or self.type_id.id, source_note,
            ),
        })
        preuve = dict(evidence or {})
        preuve.update({
            "consent_id": consent.id,
            "evidence_type": "portal_log",
            "consent_action": "grant" if granted else "refuse",
            "timestamp_utc": maintenant,
            "consent_snapshot": (version.body if version else "")
            + "\n<!-- %s granted=%s -->" % (marqueur, granted),
            "note": preuve.pop("note", "") or "Collecté via %s (%s)" % (
                source_note or "réservation", marqueur,
            ),
        })
        self.env["privacy.consent.evidence"].sudo().create(preuve)
        self.invalidate_recordset(["bf_consent_state"])
        return consent

    def _bf_notice_version(self, notice):
        """Version courante d'un avis, ou rien."""
        if not notice:
            return False
        return self.env["privacy.notice.version"].sudo().search(
            [("notice_id", "=", notice.id)],
            order="effective_date desc, version desc",
            limit=1,
        )

    def _bf_request_missing_consents(self):
        """Repli hors bande : demander par courriel ce qu'on n'a pas pu
        demander sur place.

        Sert les chemins qui n'ont AUCUNE page à afficher — sondage, saisie au
        back-office, satellite. Le mécanisme est celui de `privacy_consent`
        (consentement en attente + courriel avec lien public de réponse), pas
        une deuxième implémentation.

        ⚠️ Jamais bloquant. Un envoi qui échoue ne doit pas empêcher une
        réservation d'exister : la trace de ce qui manque vit dans
        `bf_consent_state`, pas dans la réussite de cet appel.
        """
        self.ensure_one()
        # ⚠️ Interrupteur, par défaut FERMÉ. Ce chemin écrit à un CLIENT sans
        # que personne ne relise le courriel, et il se déclenche sur une
        # simple confirmation. Un envoi sortant automatique se gouverne : le
        # paramètre système `bf_appointment.consent_auto_request` l'ouvre,
        # locataire par locataire, quand la personne responsable l'a décidé.
        # La collecte SUR PLACE et l'état `bf_consent_state`, eux, ne
        # dépendent pas de ce réglage — ils ne sortent rien.
        if not self.env.context.get("bf_force_consent_request"):
            actif = self.env["ir.config_parameter"].sudo().get_param(
                "bf_appointment.consent_auto_request", "")
            if str(actif).strip().lower() not in ("1", "true", "yes", "on"):
                return self.env["privacy.consent"]
        partner = self._bf_consent_subject()
        if not partner or not partner.email:
            return self.env["privacy.consent"]
        Consent = self.env["privacy.consent"].sudo()
        Purpose = self.env["privacy.purpose"].sudo()
        crees = Consent
        for ask in self._bf_missing_consents():
            notice = ask["notice"]
            if not notice:
                _logger.info(
                    "Réservation %s : consentement « %s » exigé par le type mais "
                    "aucun modèle d'avis n'y est rattaché, demande impossible.",
                    self.id, ask["code"],
                )
                continue
            purpose = Purpose.search([("code", "=", ask["code"])], limit=1)
            if not purpose:
                continue
            version = self._bf_notice_version(notice)
            consent = Consent.create({
                "subject_partner_id": partner.id,
                "purpose_id": purpose.id,
                "notice_id": notice.id,
                "notice_version_id": version.id if version else False,
                "status": "pending",
                "collection_method": "email",
                "context_ref": "res.partner,%d" % partner.id,
                "requested_at": fields.Datetime.now(),
                "notes": "Demande déclenchée par resource.booking=%d" % self.id,
            })
            crees |= consent
            try:
                consent._send_consent_request_email()
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "Envoi de la demande de consentement « %s » (réservation %s)",
                    ask["code"], self.id,
                )
        if crees:
            self.invalidate_recordset(["bf_consent_state"])
        return crees

    def _bf_ensure_consents(self, collected=None, evidence=None,
                            source_note="", allow_request=True):
        """Point d'entrée UNIQUE. Les quatre chemins de création s'y branchent.

        `collected` : ce que la personne vient de répondre sur place, par code
        d'objet (`{"recording": True}`). Une valeur présente est une RÉPONSE,
        y compris `False` : une case vue et laissée décochée est un refus, pas
        une absence. C'est déjà la règle du formulaire d'accueil.

        Ce qui n'a pas été demandé sur place part en demande par courriel,
        sauf `allow_request=False` (le formulaire d'accueil, qui a déjà tout
        demandé, et les essais qui ne veulent pas d'envoi).

        ⚠️ Le consentement de COLLECTE (C1) n'est pas traité ici. Il porte sur
        les renseignements saisis dans le formulaire d'accueil; un lien
        personnel ne collecte rien de neuf, le contact et les invités ayant
        été saisis à la création du lien.
        """
        self.ensure_one()
        collected = collected or {}
        evidence = evidence if evidence is not None else self._bf_consent_evidence_from_request()
        for ask in self._bf_required_consents():
            code = ask["code"]
            if code not in collected:
                continue
            if self._bf_consent_status(code, ask["notice"]) == "granted":
                # Déjà au dossier : on n'en fabrique pas un doublon. Tous les
                # autres états cèdent devant une réponse donnée à l'instant —
                # une demande en attente à qui la personne vient de répondre
                # sur place doit être RÉPONDUE, pas ignorée.
                continue
            self._bf_record_consent(
                code, ask["notice"], bool(collected[code]),
                evidence=evidence, source_note=source_note,
            )
        if allow_request and not self.env.context.get("bf_no_consent_request"):
            try:
                self._bf_request_missing_consents()
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "Demande de consentement hors bande (réservation %s)", self.id,
                )
        return True
