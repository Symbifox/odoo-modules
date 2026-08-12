import base64
import logging
import uuid
from datetime import datetime, timedelta

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

from odoo import _, api, fields, models

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
    """Sanitise a string for use inside a QUOTED ICS parameter value (CN="…").

    Different rules from _escape_ics: inside a quoted param, ``;`` and ``,`` are
    literal, but the double quote terminates the value. Passing a name straight
    through let a booker whose name contains ``"`` close CN= and append their own
    parameters (SENT-BY, DIR, a second CN) on the ATTENDEE line — the name comes
    from the public intake form, where only .strip() is applied. CR/LF are folded
    to a space so no property can be started either.
    """
    if not value:
        return ""
    value = value.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    value = "".join(c for c in value if c.isprintable())
    # RFC 6868 caret escaping is the standards-blessed encoding, but client
    # support is uneven; dropping the quote is lossless enough for a display name.
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
    cancellation_reason = fields.Text(
        string="Raison de l'annulation",
        copy=False,
        help="Raison saisie par le client (ou l'organisateur) lors de "
             "l'annulation du rendez-vous. Optionnel.",
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
            picked = None
            for subset in _icombs(sorted_resources, k):
                subset_intervals = base
                ok = True
                for res in subset:
                    calendar = combo.forced_calendar_id or res.calendar_id
                    free = calendar._work_intervals_batch(start_aware, stop_aware, res)[res.id]
                    subset_intervals &= free
                    if not subset_intervals:
                        ok = False
                        break
                if ok and subset_intervals:
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
        import pytz
        ctx_tz = self.env.context.get("tz")
        tz_helper = self.env["bf.timezone"]
        for rec in self:
            if not rec.start:
                rec.start_date_local = ""
                rec.start_time_local = ""
                continue
            # A booker partner.tz of "UTC" is a spurious browser-detection
            # fallback (see _get_booker_display_tz); it would render the raw
            # UTC instant, so drop it and fall through to the display calendar.
            booker_tz = rec.partner_id.tz if rec.partner_id else None
            if booker_tz == "UTC":
                booker_tz = None
            tz_name = tz_helper.resolve([
                ctx_tz,
                booker_tz,
                rec.type_id.resource_calendar_id.tz if rec.type_id else None,
            ])
            try:
                start_dt = rec.start
                if isinstance(start_dt, str):
                    start_dt = fields.Datetime.from_string(start_dt)
                aware_utc = pytz.utc.localize(start_dt) if start_dt.tzinfo is None else start_dt.astimezone(pytz.utc)
                local_dt = aware_utc.astimezone(pytz.timezone(tz_name))
                rec.start_date_local = local_dt.strftime("%Y-%m-%d")
                rec.start_time_local = local_dt.strftime("%H:%M")
            except Exception as e:
                _logger.warning("start_local compute failed for booking %s: %s", rec.id, e)
                rec.start_date_local = rec.start.strftime("%Y-%m-%d") if rec.start else ""
                rec.start_time_local = rec.start.strftime("%H:%M") if rec.start else ""

    def _sync_meeting(self):
        """Suppress calendar.event invite/update notifications.

        OCA's resource_booking._sync_meeting creates and writes the linked
        calendar.event with mail_notify_author=True (set in OCA's
        calendar_event create override) and from_ui=True on reschedule.
        Both paths fire the stock Odoo "Date mise à jour" notification to
        the booker, on top of our own branded confirmation/reminder
        emails. Inject suppression context before delegating so attendees
        do not get the duplicate calendar invite.
        """
        return super(
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

    def action_confirm(self):
        """Override to generate video URL + strip non-attendee resource partners
        for K-of-N bookings.

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
        return result

    def _prepare_meeting_vals(self):
        """Override to use attendee_resource_ids (K-of-N aware) instead of
        combination_id.resource_ids when populating calendar.event partners."""
        vals = super()._prepare_meeting_vals()
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
        meeting_ids = [b.meeting_id.id for b in self if b.meeting_id]
        result = super().action_cancel()
        for booking in self:
            token = tokens.get(booking.id)
            if token and not booking.access_token:
                booking.sudo().access_token = token
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
            return self._generate_nc_talk_url()
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
            return self.type_id.videocall_location or False
        password = self._decrypt_nc_talk_password(password_enc)
        if not password:
            return self.type_id.videocall_location or False
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
            return self.type_id.videocall_location or False

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
        # (the same bug class, just in the .ics body instead of the email).
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
        desc_parts.append(
            _("Date : %s") % start_local.strftime("%A %d %B %Y")
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
            self.name or (_("RDV - %s") % self.type_id.name)
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
        the organizer may sit in another timezone; letting that leak into booker-facing
        content is exactly what made a Montréal client's confirmation and ICS
        show the organizer's local time. Organizer-facing comms receive
        the organizer tz explicitly via _send_appointment_email.
        """
        self.ensure_one()
        cal_type = self.type_id.resource_calendar_id
        cal_company = self.env.company.resource_calendar_id
        # A booker partner.tz of "UTC" is almost always a stale browser-tz
        # detection fallback from the public widget, not a real location.
        # Bookings are stored naive-UTC, so honouring it renders the raw UTC
        # instant to the booker -- a 13:00 Montréal slot shows as 17:00, the
        # +4h offset seen in testing. No booker of a Québec-based practice
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
        lands on the 18th (observed in testing).

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

    def _send_appointment_email(self, template, attach_ics=True):
        """Send an appointment email with optional ICS attachment.

        Respects the partner's language AND timezone for both the email
        template rendering and the ICS attachment content. The recipient's
        timezone is auto-detected from the template's ``email_to`` jinja:
        when the template addresses ``object.user_id…`` we render in the
        organizer's tz; otherwise we render in the booker's tz. This keeps
        a Montréal booker reading « 14:00 » while the same booking shows
        « 06:00 » to an organizer in Auckland, without storing two copies
        of start_*_local.

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
        # Auto-detect recipient TZ from the template's email_to expression.
        # Organizer-bound templates address object.user_id…; everything else
        # is booker-bound.
        email_to_expr = (template.email_to or "")
        if "user_id" in email_to_expr:
            recipient_tz = self.user_id.tz if self.user_id else False
            recipient_lang = self.user_id.lang if self.user_id else False
        else:
            # Booker-bound: render in the booker display tz. Never inherit the
            # organizer's (Auckland) tz, and never leave it empty — an empty tz
            # would let _compute_start_local_strings / the ICS fall through to
            # the organizer's tz again.
            recipient_tz = self._get_booker_display_tz()
            recipient_lang = self.partner_id.lang if self.partner_id else False
        # Final fallbacks
        partner_lang = recipient_lang or self.env.lang or "fr_CA"
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

    # ---- Cron ----

    @api.model
    def _cron_send_appointment_reminders(self):
        """Backward compat alias for old cron."""
        return self._cron_send_appointment_emails()

    # Postgres advisory-lock key used to serialize cron execution.
    # Picked arbitrarily; only this cron uses it.
    _CRON_ADVISORY_LOCK_KEY = 0x4250414F4C434C4B  # "BPAOLCLK"

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
        bookings = self.search([
            ("state", "in", ("confirmed", "scheduled")),
            ("start", "!=", False),
            ("type_id.email_schedule_ids", "!=", False),
        ])
        for booking in bookings:
            for schedule in booking.type_id.email_schedule_ids.filtered("active"):
                booking.invalidate_recordset(["sent_schedule_ids"])
                if schedule in booking.sent_schedule_ids:
                    continue
                should_send = False
                if schedule.trigger == "before":
                    send_at = booking.start - timedelta(hours=schedule.hours)
                    should_send = now >= send_at and now < booking.start
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
                try:
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
