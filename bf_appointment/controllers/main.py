import hmac
import logging
import re
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

from dateutil.parser import isoparse

import pytz

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.http import Controller, request, route

_logger = logging.getLogger(__name__)

# Rate limiting for token validation (anti brute-force)
_token_fail_lock = threading.Lock()
_token_fail_data = defaultdict(list)  # IP -> [timestamps of failed attempts]
_TOKEN_FAIL_MAX = 10  # max failed attempts
_TOKEN_FAIL_WINDOW = 300  # per 5 minutes

# Rate limiting for booking creation (anti unauthenticated spam / intake-ack
# mail-bomb). Keyed on the ProxyFix-corrected client IP.
_book_lock = threading.Lock()
_book_data = defaultdict(list)  # IP -> [timestamps of accepted bookings]
_BOOK_MAX = 5        # max bookings
_BOOK_WINDOW = 600   # per 10 minutes / IP

# --- Lot d'ouverture (2.40.0) : seau de limitation nommé, partageable -------
#
# Les trois limiteurs ci-dessus (jetons, réservations, consultation de
# consentement) répètent le même motif verrou + liste d'horodatages. Ils sont
# laissés EN PLACE tels quels : ce sont des chemins publics vivants, et les
# refactoriser pour la seule élégance échangerait un risque réel contre un
# gain nul.
#
# Un satellite qui ouvre ses propres routes publiques a pourtant besoin du même
# service. Plutôt que de le laisser copier-coller le motif (et se tromper sur le
# verrou, ou faire confiance à X-Forwarded-For), on expose un seau nommé.
# `proxy_mode` est actif dans ce déploiement : `_client_ip()` lit le pair de
# socket déjà réécrit par ProxyFix et ne parse JAMAIS les en-têtes lui-même,
# sinon un client ferait tourner son propre seau à chaque requête.
_bucket_lock = threading.Lock()
_bucket_data = defaultdict(list)  # (seau, IP) -> [horodatages]


def bf_rate_limit(bucket, max_hits, window, key=None, consume=True):
    """Rend True si la requête passe. Consomme un jeton quand `consume`.

    ⚠️ Les deux sémantiques du module sont bien distinctes, et les confondre
    est un défaut fonctionnel, pas un détail :

    * **Consommer** (`consume=True`, défaut) convient quand chaque appel EST
      l'action à plafonner : créer une réservation, enregistrer un vote.
      C'est le comportement de `_check_book_rate_limit`.
    * **Vérifier seulement** (`consume=False`) convient au contrôle d'un jeton,
      où seuls les ÉCHECS doivent compter. Consommer à chaque lecture de page
      enfermerait dehors la personne légitime qui recharge son propre lien,
      alors qu'elle n'a rien fait de mal. C'est la raison d'être du couple
      `_check_token_rate_limit` / `_record_token_failure` du module, repris ici.

    :param bucket: nom du seau, propre à l'appelant (« poll_vote »…)
    :param max_hits: nombre de requêtes autorisées dans la fenêtre
    :param window: largeur de la fenêtre, en secondes
    :param key: clé de regroupement. Par défaut l'IP cliente ; passer un jeton
        de participant regroupe par personne plutôt que par adresse, ce qui
        vaut mieux quand plusieurs personnes partagent une sortie réseau.
    :param consume: compter cet appel, ou se contenter de vérifier
    :return: True si la requête est acceptée, False sinon
    """
    ident = (bucket, key or _client_ip())
    now = time.monotonic()
    with _bucket_lock:
        cutoff = now - window
        hits = [t for t in _bucket_data[ident] if t > cutoff]
        if len(hits) >= max_hits:
            _bucket_data[ident] = hits
            return False
        if consume:
            hits.append(now)
        _bucket_data[ident] = hits
        return True


def bf_rate_limit_record(bucket, window, key=None):
    """Compte un ÉCHEC dans un seau nommé, sans rien décider.

    Pendant de `bf_rate_limit(..., consume=False)` : on vérifie avant, on
    n'inscrit qu'après un échec avéré.
    """
    ident = (bucket, key or _client_ip())
    now = time.monotonic()
    with _bucket_lock:
        cutoff = now - window
        hits = [t for t in _bucket_data[ident] if t > cutoff]
        hits.append(now)
        _bucket_data[ident] = hits


def _client_ip():
    """Best-effort client IP for rate limiting — socket peer only.

    proxy_mode = True is set in this deployment, so werkzeug's ProxyFix has
    already rewritten ``remote_addr`` to the real client from a trusted number of
    proxy hops. We must NOT parse X-Forwarded-For / X-Real-IP ourselves: they are
    attacker-controlled when the endpoint is reached directly, which would let a
    client rotate its rate-limit / consent-lookup bucket per request. Mirrors
    bf_meeting.
    """
    try:
        return request.httprequest.remote_addr or "unknown"
    except Exception:
        return "unknown"


def _check_token_rate_limit():
    """Return True if IP is within rate limits for token validation."""
    ip = _client_ip()
    now = time.monotonic()
    with _token_fail_lock:
        attempts = _token_fail_data[ip]
        cutoff = now - _TOKEN_FAIL_WINDOW
        _token_fail_data[ip] = [t for t in attempts if t > cutoff]
        return len(_token_fail_data[ip]) < _TOKEN_FAIL_MAX


def _record_token_failure():
    """Record a failed token validation attempt for rate limiting."""
    ip = _client_ip()
    now = time.monotonic()
    with _token_fail_lock:
        _token_fail_data[ip].append(now)


def _check_book_rate_limit():
    """Return True and record the hit if this IP may create another booking.

    Caps unauthenticated booking creation (and the intake-ack email it triggers)
    to _BOOK_MAX per _BOOK_WINDOW so the form can't be looped into a partner/
    booking spam or a mail-bomb aimed at an attacker-chosen address.
    """
    ip = _client_ip()
    now = time.monotonic()
    with _book_lock:
        cutoff = now - _BOOK_WINDOW
        _book_data[ip] = [t for t in _book_data[ip] if t > cutoff]
        if len(_book_data[ip]) >= _BOOK_MAX:
            return False
        _book_data[ip].append(now)
        return True


# BF only ships fr_CA and en_CA. Anything en* maps to en_CA, everything else
# (and missing header) falls back to fr_CA.
_BF_DEFAULT_LANG = "fr_CA"


def _resolve_lang_from_accept_header():
    """Return en_CA if Accept-Language asks for English, fr_CA otherwise."""
    try:
        header = request.httprequest.headers.get("Accept-Language", "")
    except Exception:
        return _BF_DEFAULT_LANG
    if not header:
        return _BF_DEFAULT_LANG
    # Simple parse: take first non-q-flagged tag, lowercased.
    first = header.split(",")[0].split(";")[0].strip().lower()
    if first.startswith("en"):
        return "en_CA"
    return _BF_DEFAULT_LANG


def _apply_locale_from_request():
    """Switch request env lang for prefix-less booking links. Idempotent.

    The public pages (landing, type detail, intake POST) carry the language in
    the URL: `/en/appointment/...` is English, the prefix-less `/appointment/...`
    is the fr_CA default. Odoo's website middleware already resolves that
    correctly into the request context, so we leave it untouched here.

    Earlier this function applied an Accept-Language fallback to *every* route,
    which let an English browser (`Accept-Language: en-CA`) override the French
    URL and defeated the language toggle: after switching to Français the page
    stayed English because the browser still advertised English. The URL prefix
    must win on the public pages.

    The booking pages reached from confirmation/cancel emails
    (`/appointment/b/<id>/<token>/...`) are prefix-less, so the URL gives no
    language hint. There - and only there - we fall back to the booker's
    Accept-Language to choose en_CA vs fr_CA.

    Falls back to fr_CA if the resolved lang is not installed on this tenant
    (e.g. a mono-lingual tenant shipping fr_CA only - setting en_CA would 400).
    """
    # Public pages: the URL prefix is authoritative. Leave the context lang
    # exactly as the website middleware resolved it from the URL.
    if "/appointment/b/" not in (request.httprequest.path or ""):
        return
    # Email-link booking pages: no URL language signal, use Accept-Language.
    current = request.env.context.get("lang")
    if current and current.lower().startswith("en"):
        return
    lang = _resolve_lang_from_accept_header()
    if lang != _BF_DEFAULT_LANG:
        installed = request.env["res.lang"].sudo().search(
            [("code", "=", lang), ("active", "=", True)], limit=1
        )
        if not installed:
            lang = _BF_DEFAULT_LANG
    if current != lang:
        request.update_context(lang=lang)


# Security headers applied to every public /appointment* response. CSP is
# permissive on inline styles because Odoo emits inline t-att-style on widgets;
# scripts and frames are locked down. frame-ancestors 'none' + X-Frame-Options
# DENY together protect against clickjacking on legacy browsers.
_APPOINTMENT_CSP = (
    "default-src 'self'; "
    "img-src 'self' data: https:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self' 'unsafe-inline'; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


def _apply_security_headers(response):
    """Add CSP + X-Frame-Options + nosniff to a response object. No-op on redirects."""
    try:
        headers = response.headers
    except AttributeError:
        return response
    headers["Content-Security-Policy"] = _APPOINTMENT_CSP
    headers["X-Frame-Options"] = "DENY"
    headers["X-Content-Type-Options"] = "nosniff"
    headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


# Pragmatic location validation: refuse strings that are too short, lack any
# letters, or are a single short token. Rejects "abc", "123", "x", "..." while
# accepting "12, rue Exemple", "Bureau du fond", "Café du Coin".
_LOCATION_MIN_LEN = 5
_LOCATION_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]")


def _validate_location_format(value):
    """Return True when the location looks like a real lieu/adresse."""
    if not value or len(value) < _LOCATION_MIN_LEN:
        return False
    if not _LOCATION_RE.search(value):
        return False
    return True


# Rate limit for the consent-check AJAX endpoint. Per CRTC + CAI guidance, we
# never reveal whether an email is in our DB unless rate-limited; this caps
# enumeration to ~30 lookups / 5 min per IP. Same data store as token rate
# limit, but a separate counter so a slow-typing booker is not penalized.
_consent_lookup_lock = threading.Lock()
_consent_lookup_data = defaultdict(list)
_CONSENT_LOOKUP_MAX = 30
_CONSENT_LOOKUP_WINDOW = 300


def _check_consent_lookup_rate_limit():
    ip = _client_ip()
    now = time.monotonic()
    with _consent_lookup_lock:
        cutoff = now - _CONSENT_LOOKUP_WINDOW
        _consent_lookup_data[ip] = [t for t in _consent_lookup_data[ip] if t > cutoff]
        if len(_consent_lookup_data[ip]) >= _CONSENT_LOOKUP_MAX:
            return False
        _consent_lookup_data[ip].append(now)
        return True


def _lookup_active_consent(env, partner, purpose_code, current_notice_id):
    """Return the active privacy.consent record for (partner, purpose) or False.

    "Active" = status='granted', record active=True, not withdrawn, not
    expired, AND attached to a notice version whose notice matches the
    current type's notice (so a major notice rev forces re-consent).
    """
    if not partner or not purpose_code:
        return False
    Consent = env["privacy.consent"].sudo()
    domain = [
        ("subject_partner_id", "=", partner.id),
        ("status", "=", "granted"),
        ("active", "=", True),
        ("withdrawn_at", "=", False),
        ("purpose_id.code", "=", purpose_code),
        "|", ("expires_at", "=", False), ("expires_at", ">", fields.Datetime.now()),
    ]
    if current_notice_id:
        domain.append(("notice_id", "=", current_notice_id))
    consent = Consent.search(domain, order="granted_at desc", limit=1)
    return consent or False


class AppointmentController(Controller):

    @route(
        "/appointment",
        type="http",
        auth="public",
        website=True,
        sitemap=True,
    )
    def appointment_landing(self, **kwargs):
        """Public landing page listing all public booking types."""
        _apply_locale_from_request()
        BookingType = request.env["resource.booking.type"].sudo()
        types = BookingType.search(
            [("is_public", "=", True), ("listed_on_landing", "=", True)],
            order="sequence, name",
        )
        response = request.render(
            "bf_appointment.appointment_landing",
            {"booking_types": types},
        )
        return _apply_security_headers(response)

    @route(
        "/appointment/_consent_check",
        type="json",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def appointment_consent_check(self, email=None, slug=None, **kwargs):
        """Return the active-consent state for a given email under both
        purposes (recording, marketing) on the given booking type's notices.

        Lets the public intake form silently hide consent checkboxes when
        the booker has already granted (product UX rule). Always
        returns 200 + a uniform shape to avoid email enumeration via
        timing or status code differences.
        """
        out = {
            "recording": {"active": False, "granted_at": False},
            "marketing": {"active": False, "granted_at": False},
        }
        if not _check_consent_lookup_rate_limit():
            _logger.info("Consent lookup rate limit hit for IP %s", _client_ip())
            return out
        email = (email or "").strip()
        if not email or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            return out
        Partner = request.env["res.partner"].sudo()
        partner = Partner.search([("email", "=ilike", email)], limit=1)
        if not partner:
            return out
        booking_type = self._get_type_by_slug((slug or "").strip())
        rec_notice_id = (booking_type.recording_notice_id.id
                         if booking_type and booking_type.recording_notice_id else False)
        mkt_notice_id = (booking_type.newsletter_notice_id.id
                         if booking_type and booking_type.newsletter_notice_id else False)
        rec = _lookup_active_consent(request.env, partner, "recording", rec_notice_id)
        mkt = _lookup_active_consent(request.env, partner, "marketing", mkt_notice_id)
        if rec:
            out["recording"] = {
                "active": True,
                "granted_at": rec.granted_at.strftime("%Y-%m-%d") if rec.granted_at else False,
            }
        if mkt:
            out["marketing"] = {
                "active": True,
                "granted_at": mkt.granted_at.strftime("%Y-%m-%d") if mkt.granted_at else False,
            }
        return out

    @route(
        "/appointment/<string:slug>",
        type="http",
        auth="public",
        website=True,
        sitemap=True,
    )
    def appointment_type_page(self, slug, **kwargs):
        """Detail page for a specific booking type with intake form."""
        _apply_locale_from_request()
        booking_type = self._get_type_by_slug(slug)
        if not booking_type:
            return request.redirect("/appointment")
        response = request.render(
            "bf_appointment.appointment_type_page",
            {"booking_type": booking_type, "error": kwargs.get("error")},
        )
        return _apply_security_headers(response)

    @route(
        "/appointment/<string:slug>/book",
        type="http",
        auth="public",
        website=True,
        methods=["POST"],
    )
    def appointment_book(self, slug, **kwargs):
        """Create a pending booking from the intake form."""
        _apply_locale_from_request()
        booking_type = self._get_type_by_slug(slug)
        if not booking_type:
            return request.redirect("/appointment")
        # Honeypot spam check
        if kwargs.get("website_url"):
            _logger.info("Honeypot triggered on appointment form")
            return request.redirect("/appointment")
        # Per-IP throttle: cap booking creation / intake-ack emails.
        if not _check_book_rate_limit():
            return request.redirect(
                f"/appointment/{slug}?error="
                + quote_plus("Trop de demandes. Veuillez réessayer dans quelques minutes.")
            )
        name = (kwargs.get("name") or "").strip()
        email = (kwargs.get("email") or "").strip()
        phone = (kwargs.get("phone") or "").strip()
        tz = (kwargs.get("tz") or "").strip()
        duration_str = (kwargs.get("duration") or "").strip()
        location_input = (kwargs.get("location") or "").strip()
        # Validate required fields
        if not name or not email:
            return request.redirect(
                f"/appointment/{slug}?error={quote_plus('Veuillez remplir tous les champs obligatoires.')}"
            )
        # Loi 25, explicit consent required for personal information collection.
        # The form has client-side `required`, but a tampered submission could
        # bypass that, so we enforce server-side too.
        if not kwargs.get("bf_consent"):
            return request.redirect(
                f"/appointment/{slug}?error={quote_plus('Veuillez accepter la politique de confidentialité pour soumettre votre demande.')}"
            )
        # If in-person without fixed location, the booker must provide one
        if booking_type.is_in_person and not booking_type.location and not location_input:
            return request.redirect(
                f"/appointment/{slug}?error={quote_plus('Veuillez indiquer un lieu de rencontre.')}"
            )
        # Validate the format of a booker-provided location: short or
        # letter-less strings ("abc", "123", "...") slip past the empty check
        # but are useless for the organizer.
        if location_input and not _validate_location_format(location_input):
            return request.redirect(
                f"/appointment/{slug}?error={quote_plus('Veuillez fournir une adresse ou un lieu reconnaissable.')}"
            )
        # Basic email validation
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            return request.redirect(
                f"/appointment/{slug}?error={quote_plus('Adresse courriel invalide.')}"
            )
        # Validate timezone. Reject "UTC" alongside invalid zones: the browser
        # tz field can fall back to "UTC" when detection fails, and persisting
        # it on the contact below makes every later render show the raw UTC
        # instant instead of the booker's local time. A Québec-facing booker is
        # never truly UTC, so blank it and let the display calendar drive.
        if tz and (tz == "UTC" or tz not in pytz.all_timezones_set):
            tz = ""
        # Validate required intake fields. We re-run _validate_intake_value so
        # a tampered select/email/phone (non-empty but invalid) is treated as
        # missing rather than silently dropped downstream.
        for field in booking_type.intake_field_ids.filtered("required"):
            key = f"intake_{field.id}"
            raw = (kwargs.get(key) or "").strip()
            if not raw or not self._validate_intake_value(field, raw):
                return request.redirect(
                    f"/appointment/{slug}?error={quote_plus(f'Le champ « {field.name} » est obligatoire.')}"
                )
        # Find or create partner
        Partner = request.env["res.partner"].sudo()
        partner = Partner.search([("email", "=ilike", email)], limit=1)
        if not partner:
            partner_vals = {
                "name": name,
                "email": email,
                "phone": phone or False,
            }
            if tz:
                partner_vals["tz"] = tz
            partner = Partner.create(partner_vals)
        else:
            # Update name if currently set to the email (auto-created contacts)
            if not partner.name or partner.name.strip().lower() == partner.email.strip().lower():
                partner.name = name
            if phone and not partner.phone:
                partner.phone = phone
            # Capture the browser-detected TZ on the partner if missing.
            # We never overwrite an existing tz: a user who manually picked
            # a different TZ in their res.users profile (e.g. a colleague
            # travelling) shouldn't have it blasted by the booking form.
            if tz and not partner.tz:
                partner.tz = tz

        # Recording consent (Loi 25 art. 12 + 14): required for types where
        # the meeting report is part of the service offering. Exempted only
        # when the partner already has an active consent on file for the
        # same notice (no re-prompt). Types where recording is not part of
        # the deal (Sync 2FA, support) bypass this entirely via
        # requires_recording_consent=False.
        recording_active = False
        if booking_type.requires_recording_consent:
            existing_rec = _lookup_active_consent(
                request.env, partner, "recording",
                booking_type.recording_notice_id.id if booking_type.recording_notice_id else False,
            )
            recording_active = bool(existing_rec)
            checkbox_recording = bool(kwargs.get("bf_consent_recording"))
            if not recording_active and not checkbox_recording:
                fallback_email = (
                    (booking_type.company_id and booking_type.company_id.email)
                    or request.env.company.email
                    or "service@example.com"
                )
                msg = (
                    "Le compte rendu fait partie du service pour ce type de "
                    "rencontre. Veuillez accepter l'enregistrement et la "
                    f"transcription, ou nous écrire à {fallback_email} "
                    "pour un format alternatif."
                )
                return request.redirect(
                    f"/appointment/{slug}?error={quote_plus(msg)}"
                )
        # Find a real user for organizer (first resource's user or admin)
        organizer_user = (
            booking_type.combination_rel_ids[:1]
            .combination_id.resource_ids[:1]
            .filtered(lambda r: r.resource_type == "user")
            .user_id
        )
        if not organizer_user:
            organizer_user = request.env.ref("base.user_admin").sudo()
        # Create pending booking, suppress ALL notifications
        Booking = request.env["resource.booking"].sudo().with_context(
            no_mail_to_attendees=True,
            mail_create_nolog=True,
            mail_create_nosubscribe=True,
            tracking_disable=True,
            mail_notrack=True,
        )
        booking_vals = {
            "type_id": booking_type.id,
            "partner_ids": [(6, 0, [partner.id])],
            "name": Booking._bf_build_title(
                booking_type,
                partner=partner,
                booker_name=name,
                lang=partner.lang or organizer_user.lang,
            ),
            "user_id": organizer_user.id,
        }
        # Booker-provided location overrides type's blank location for in-person types
        if location_input:
            booking_vals["location"] = location_input
        # Apply custom duration if provided and valid
        if duration_str and booking_type.duration_options:
            try:
                duration_hours = float(duration_str)
                valid_durations = [
                    c[0] for c in booking_type.get_duration_choices()
                ]
                if duration_hours in valid_durations:
                    booking_vals["duration"] = duration_hours
            except (ValueError, TypeError):
                pass
        booking = Booking.create(booking_vals)
        # Save custom field answers
        self._save_intake_answers(booking, booking_type, kwargs)
        # Invités additionnels : enregistrés EN ATTENTE, rien ne leur est
        # envoyé. Seule la confirmation du demandeur, depuis sa boîte,
        # déclenchera le moindre courriel.
        self._bf_save_guests(booking, booking_type, kwargs, partner)
        # Generate access token
        booking._portal_ensure_token()

        # Persist Loi 25 / LCAP consents now that we have a partner + booking
        # to anchor them to. Errors are logged but never block the booking
        # (the consent record is the audit trail, not a hard precondition).
        try:
            self._record_booking_consents(
                booking_type=booking_type,
                booking=booking,
                partner=partner,
                kwargs=kwargs,
                recording_already_active=recording_active,
            )
        except Exception as e:
            _logger.exception(
                "Failed to persist consent records for booking %d: %s",
                booking.id, e,
            )

        # Intake acknowledgement: send the booker a branded "we got your
        # request" email with a resumable link to /schedule. Configurable
        # per type. Failure is non-fatal; the redirect to /schedule still
        # happens so the booker isn't stuck.
        if booking_type.sends_intake_acknowledgement:
            try:
                ack_template = request.env.ref(
                    "bf_appointment.mail_template_intake_acknowledgement",
                    raise_if_not_found=False,
                )
                if ack_template:
                    booking._send_appointment_email(ack_template.sudo(), attach_ics=False)
            except Exception as e:
                _logger.warning(
                    "Failed to send intake acknowledgement for booking %d: %s",
                    booking.id, e,
                )

        # Build schedule URL
        schedule_url = (
            f"/appointment/b/{booking.id}/{booking.access_token}/schedule"
        )
        if tz:
            schedule_url += f"?tz={tz}"
        return request.redirect(schedule_url)

    def _record_booking_consents(self, booking_type, booking, partner, kwargs, recording_already_active):
        """Write privacy.consent + privacy.consent.evidence records for the
        recording and newsletter consents collected on the intake form.

        Skips writing a duplicate when an active consent for the same
        (partner, purpose, notice) is already on file. A short-lived
        booking is logged in `context_ref` so the consent can be linked
        back to the originating intake submission.
        """
        Consent = request.env["privacy.consent"].sudo()
        Evidence = request.env["privacy.consent.evidence"].sudo()
        NoticeVersion = request.env["privacy.notice.version"].sudo()
        Purpose = request.env["privacy.purpose"].sudo()

        env_meta = request.httprequest.environ
        ip = _client_ip()
        ua = env_meta.get("HTTP_USER_AGENT", "")[:512]
        accept_lang = env_meta.get("HTTP_ACCEPT_LANGUAGE", "")[:128]
        referer = env_meta.get("HTTP_REFERER", "")[:512]
        # context_ref Selection only allows project.project / res.partner per
        # privacy_consent module. Anchor on the partner; trace the originating
        # booking via a structured prefix in `notes` so it's queryable.
        ctx_ref = f"res.partner,{partner.id}"
        booking_marker = f"resource.booking={booking.id}"

        def _resolve_notice_version(notice):
            if not notice:
                return False
            return NoticeVersion.search(
                [("notice_id", "=", notice.id)],
                order="effective_date desc, version desc",
                limit=1,
            )

        def _record(purpose_code, granted, notice, checkbox_value):
            """Create privacy.consent + evidence rows. Returns the consent record."""
            purpose = Purpose.search([("code", "=", purpose_code)], limit=1)
            if not purpose:
                _logger.warning("privacy.purpose code=%s missing, skipping", purpose_code)
                return False
            version = _resolve_notice_version(notice)
            consent_vals = {
                "subject_partner_id": partner.id,
                "purpose_id": purpose.id,
                "notice_id": notice.id if notice else False,
                "notice_version_id": version.id if version else False,
                "status": "granted" if granted else "refused",
                "collection_method": "portal",
                "context_ref": ctx_ref,
                "active": True,
                "granted_at": fields.Datetime.now() if granted else False,
                "refused_at": fields.Datetime.now() if not granted else False,
                "notes": (
                    f"Source: {booking_marker} (slug={booking_type.slug})\n"
                    f"Checkbox value at submission: {checkbox_value}"
                ),
            }
            consent = Consent.create(consent_vals)
            Evidence.create({
                "consent_id": consent.id,
                "evidence_type": "portal_log",
                "consent_action": "grant" if granted else "refuse",
                "ip_address": ip,
                "user_agent": ua,
                "accept_language": accept_lang,
                "referer_url": referer,
                "request_method": "POST",
                "session_id": request.session.sid if hasattr(request, "session") else False,
                "timestamp_utc": fields.Datetime.now(),
                "consent_snapshot": (version.body if version else "") + f"\n<!-- {booking_marker} checkbox={checkbox_value} -->",
                "note": f"Collected via /appointment/{booking_type.slug}/book ({booking_marker})",
            })
            return consent

        # Recording consent: only create a fresh record when the type
        # requires it AND there's no active record on file. Otherwise we
        # leave the existing consent alone (the "do not re-ask" rule).
        if booking_type.requires_recording_consent and not recording_already_active:
            checked = bool(kwargs.get("bf_consent_recording"))
            _record(
                "recording",
                granted=checked,
                notice=booking_type.recording_notice_id,
                checkbox_value=checked,
            )

        # Newsletter consent: only when offered, only when checked, and
        # only when not already on file. Refusing the newsletter on the
        # booking form is NOT a refusal record (the booker simply did not
        # opt in). LCAP only logs explicit grants.
        if booking_type.offers_newsletter_signup and kwargs.get("bf_consent_newsletter"):
            existing_mkt = _lookup_active_consent(
                request.env, partner, "marketing",
                booking_type.newsletter_notice_id.id if booking_type.newsletter_notice_id else False,
            )
            if not existing_mkt:
                _record(
                    "marketing",
                    granted=True,
                    notice=booking_type.newsletter_notice_id,
                    checkbox_value=True,
                )
                # Best-effort: subscribe to a "BF Newsletter" mailing.list if
                # the mass_mailing module is installed and a list named
                # "Infolettre Blue Fox" exists. Failure is non-fatal.
                try:
                    MailingList = request.env["mailing.list"].sudo()
                    bf_list = MailingList.search([("name", "ilike", "infolettre")], limit=1)
                    if bf_list:
                        request.env["mailing.contact"].sudo().create({
                            "email": partner.email,
                            "name": partner.name,
                            "list_ids": [(4, bf_list.id)],
                        })
                except Exception:
                    _logger.debug("mailing.list subscription skipped (module not installed?)")

    @route(
        [
            "/appointment/b/<int:booking_id>/<string:token>/schedule",
            "/appointment/b/<int:booking_id>/<string:token>/schedule/<int:year>/<int:month>",
        ],
        type="http",
        auth="public",
        website=True,
    )
    def appointment_schedule(
        self, booking_id, token, year=None, month=None, **kwargs
    ):
        """Show the scheduling calendar for the booking."""
        _apply_locale_from_request()
        booking_sudo = self._get_booking_sudo(booking_id, token)
        if not booking_sudo:
            return request.redirect("/appointment")
        # Cancelled bookings cannot be rescheduled: OCA clears the resource
        # combination on cancel, so any POST /confirm would 400. Route the
        # user straight to the confirmation page (which shows the cancelled
        # state) instead of a misleading calendar picker.
        if booking_sudo.state == "canceled":
            return request.redirect(
                f"/appointment/b/{booking_id}/{token}"
            )
        # ⚠️ Un lien mort doit DIRE pourquoi. Rediriger en silence vers la page
        # d'accueil rend 200, laisse la personne convaincue d'avoir mal cliqué,
        # et elle réessaie — c'est le défaut déjà connu sur les slugs inconnus.
        if not booking_sudo._link_is_usable():
            return _apply_security_headers(request.render(
                "bf_appointment.appointment_link_closed",
                {"booking_sudo": booking_sudo, "access_token": token},
            ))
        tz = kwargs.get("tz") or ""
        if tz and tz in pytz.all_timezones_set:
            booking_sudo = booking_sudo.with_context(tz=tz)
        calendar_ctx = booking_sudo._get_calendar_context(year, month)
        # Effective TZ for the labels next to the picker. Falls back to the
        # type's resource calendar tz so we never show an empty TZ next to
        # the slots.
        effective_tz = tz or booking_sudo._get_booker_display_tz()
        values = {
            "booking_sudo": booking_sudo,
            "access_token": token,
            "error": kwargs.get("error"),
            "visitor_tz": tz,
            "effective_tz": effective_tz,
            "effective_tz_city": request.env["bf.timezone"].sudo().tz_city(
                effective_tz
            ),
            "common_timezones": pytz.common_timezones,
            **calendar_ctx,
        }
        response = request.render(
            "bf_appointment.appointment_schedule", values
        )
        return _apply_security_headers(response)

    @route(
        "/appointment/b/<int:booking_id>/<string:token>/confirm",
        type="http",
        auth="public",
        website=True,
        methods=["POST"],
    )
    def appointment_confirm(self, booking_id, token, when, **kwargs):
        """Confirm the booking at the chosen time slot."""
        booking_sudo = self._get_booking_sudo(booking_id, token)
        if not booking_sudo:
            return request.redirect("/appointment")
        # Revérifié ICI et pas seulement à l'affichage : une page ouverte avant
        # l'expiration reste postable après, et c'est le POST qui engage.
        if not booking_sudo._link_is_usable():
            return _apply_security_headers(request.render(
                "bf_appointment.appointment_link_closed",
                {"booking_sudo": booking_sudo, "access_token": token},
            ))
        if booking_sudo.state == "canceled":
            return request.redirect(
                f"/appointment/b/{booking_id}/{token}"
            )
        try:
            when_tz_aware = isoparse(when)
            when_naive = datetime.fromtimestamp(
                when_tz_aware.timestamp(), tz=timezone.utc
            ).replace(tzinfo=None)
        except (ValueError, TypeError):
            return request.redirect(
                f"/appointment/b/{booking_id}/{token}/schedule"
                f"?error=Format de date invalide."
            )
        # Suppress ALL notifications, we send our own branded email
        booking_sudo = booking_sudo.with_context(
            no_mail_to_attendees=True,
            dont_notify=True,
            tracking_disable=True,
            mail_notrack=True,
            mail_create_nosubscribe=True,
        )
        try:
            booking_sudo.start = when_naive
        except ValidationError as error:
            tz = kwargs.get("tz", "")
            tz_param = f"&tz={tz}" if tz else ""
            return request.redirect(
                f"/appointment/b/{booking_id}/{token}"
                f"/schedule/{when_tz_aware:%Y/%m}"
                f"?error={quote_plus(str(error.args[0]))}{tz_param}"
            )
        booking_sudo.action_confirm()
        # Le créneau est fixé : c'est maintenant qu'on peut demander au
        # demandeur s'il confirme ses invités. Plus tôt, l'invitation qu'ils
        # recevraient n'aurait pas de date.
        if booking_sudo._bf_pending_guests():
            gabarit = request.env.ref(
                "bf_appointment.mail_template_guest_confirmation_request",
                raise_if_not_found=False)
            if gabarit:
                try:
                    gabarit.sudo().send_mail(booking_sudo.id, force_send=False)
                except Exception:  # noqa: BLE001
                    _logger.exception(
                        "Envoi de la demande de confirmation d'invités (réservation %s)",
                        booking_sudo.id)
        # Le lien a servi. Marqué APRÈS la confirmation : un échec de créneau
        # renvoie l'usager au calendrier, et son lien doit encore marcher.
        booking_sudo._mark_link_used()
        # Send our branded confirmation email with ICS attachment
        try:
            template = request.env.ref(
                "bf_appointment.mail_template_appointment_confirmation"
            ).sudo()
            booking_sudo._send_appointment_email(template)
        except Exception as e:
            _logger.error(
                "Failed to send confirmation email for booking %d: %s",
                booking_sudo.id,
                e,
            )
        # Notify the organizer (BF employee) so they get a heads-up. Stock
        # Odoo calendar.event invitations are suppressed by our
        # CalendarEvent._track_subtype override (avoids the duplicate "Date
        # mise à jour" notification storm), so we deliver our own branded
        # internal email instead. Skip if the organizer would just be the
        # booker (e.g. self-test where employee email == booker partner).
        try:
            organizer_partner = booking_sudo.user_id.partner_id
            booker_emails = booking_sudo.partner_ids.mapped("email")
            if (
                organizer_partner
                and organizer_partner.email
                and organizer_partner.email not in booker_emails
            ):
                org_template = request.env.ref(
                    "bf_appointment.mail_template_organizer_new_booking"
                ).sudo()
                booking_sudo._send_appointment_email(
                    org_template, attach_ics=True
                )
        except Exception as e:
            _logger.error(
                "Failed to notify organizer for booking %d: %s",
                booking_sudo.id,
                e,
            )
        # Mark past-due "before" schedules as already sent to prevent
        # the cron from sending all reminders at once for near-future bookings
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for schedule in booking_sudo.type_id.email_schedule_ids.filtered(
            lambda s: s.active and s.trigger == "before"
        ):
            send_at = booking_sudo.start - timedelta(hours=schedule.hours)
            if now >= send_at:
                booking_sudo.sent_schedule_ids = [(4, schedule.id)]
        return request.redirect(
            f"/appointment/b/{booking_id}/{token}"
        )

    @route(
        "/appointment/b/<int:booking_id>/<string:token>",
        type="http",
        auth="public",
        website=True,
    )
    def appointment_confirmation_page(self, booking_id, token, **kwargs):
        """Show the booking confirmation/details page."""
        _apply_locale_from_request()
        booking_sudo = self._get_booking_sudo(booking_id, token)
        if not booking_sudo:
            return request.redirect("/appointment")
        # Always render the confirmation in the booker display tz so the web
        # page, the confirmation email and the ICS attachment agree. We
        # intentionally do NOT honour a transient ?tz here: the picker's
        # optional override only affects slot display, not the final record.
        tz_name = booking_sudo._get_booker_display_tz()
        if tz_name in pytz.all_timezones_set:
            booking_sudo = booking_sudo.with_context(tz=tz_name)
        values = {
            "booking_sudo": booking_sudo,
            "access_token": token,
            "tz_name": tz_name,
            "tz_city_name": request.env["bf.timezone"].sudo().tz_city(tz_name),
        }
        response = request.render(
            "bf_appointment.appointment_confirmation_page", values
        )
        return _apply_security_headers(response)

    @route(
        "/appointment/b/<int:booking_id>/<string:token>/cancel",
        type="http",
        auth="public",
        website=True,
        methods=["GET", "POST"],
    )
    def appointment_cancel(self, booking_id, token, **kwargs):
        """Annuler un rendez-vous. En GET, on DEMANDE d'abord.

        ⚠️ Le GET ne doit jamais annuler, et il ne doit pas non plus rendre un
        405. Les deux se sont vérifiés :

        * Un lien d'annulation part dans la description de l'ICS
          (« Annuler : … »), donc il se clique depuis l'agenda, en GET. La
          route n'acceptait que POST : le client recevait la page brute
          « 405 Method Not Allowed » de Werkzeug. Signalé en production le
          2026-08-20, en éprouvant les liens uniques.
        * Faire annuler le GET serait pire. Les antivirus de messagerie et les
          aperçus de lien suivent les URL des courriels : un rendez-vous se
          serait annulé tout seul, sans que personne ne clique. C'est
          précisément pourquoi la mutation reste en POST.

        Le GET rend donc une page qui demande confirmation, avec un formulaire
        qui poste. La mutation n'a pas bougé d'un pouce.
        """
        _apply_locale_from_request()
        booking_sudo = self._get_booking_sudo(booking_id, token)
        if not booking_sudo:
            return request.redirect("/appointment")
        if request.httprequest.method == "GET":
            if booking_sudo.state == "canceled":
                return request.redirect(f"/appointment/b/{booking_id}/{token}")
            response = request.render(
                "bf_appointment.appointment_cancel_confirm",
                {"booking_sudo": booking_sudo, "access_token": token},
            )
            return _apply_security_headers(response)
        # Skip if already cancelled (idempotent + avoid duplicate emails)
        already_cancelled = booking_sudo.state == "canceled"
        # Capture cancellation reason before action_cancel (it may flip
        # active=False / clear writable state in some flows).
        reason = (kwargs.get("cancellation_reason") or "").strip()
        if reason and not already_cancelled:
            booking_sudo.sudo().cancellation_reason = reason[:2000]
        booking_sudo.with_context(
            no_mail_to_attendees=True,
            tracking_disable=True,
            mail_notrack=True,
        ).action_cancel()
        # Send our branded cancellation emails (suppress stock Odoo notifications above).
        if not already_cancelled:
            try:
                client_template = request.env.ref(
                    "bf_appointment.mail_template_appointment_cancellation"
                ).sudo()
                booking_sudo._send_appointment_email(
                    client_template, attach_ics=False
                )
            except Exception as e:
                _logger.error(
                    "Failed to send cancellation email for booking %d: %s",
                    booking_sudo.id, e,
                )
            try:
                organizer_partner = booking_sudo.user_id.partner_id
                booker_emails = booking_sudo.partner_ids.mapped("email")
                if (
                    organizer_partner
                    and organizer_partner.email
                    and organizer_partner.email not in booker_emails
                ):
                    org_template = request.env.ref(
                        "bf_appointment.mail_template_organizer_cancellation"
                    ).sudo()
                    booking_sudo._send_appointment_email(
                        org_template, attach_ics=False
                    )
            except Exception as e:
                _logger.error(
                    "Failed to notify organizer of cancellation for booking %d: %s",
                    booking_sudo.id, e,
                )
        response = request.render(
            "bf_appointment.appointment_cancelled", {}
        )
        return _apply_security_headers(response)

    # ---- Helpers ----

    def _get_type_by_slug(self, slug):
        """Get a public booking type by its slug."""
        BookingType = request.env["resource.booking.type"].sudo()
        return BookingType.search(
            [("slug", "=", slug), ("is_public", "=", True)],
            limit=1,
        )

    def _get_booking_sudo(self, booking_id, access_token):
        """Validate access token and return sudoed booking."""
        if not access_token:
            return False
        # Rate limit: block IPs with too many failed token attempts
        if not _check_token_rate_limit():
            _logger.warning(
                "Token rate limit exceeded for IP %s",
                _client_ip(),
            )
            return False
        booking_sudo = (
            request.env["resource.booking"]
            .sudo()
            .with_context(active_test=False)
            .browse(booking_id)
        )
        if (
            not booking_sudo.exists()
            or not booking_sudo.access_token
            or not hmac.compare_digest(booking_sudo.access_token, access_token)
        ):
            _record_token_failure()
            return False
        return booking_sudo.with_context(
            using_portal=True,
            active_test=False,
            tz=booking_sudo._get_booker_display_tz(),
        )

    @staticmethod
    def _validate_intake_value(field, value):
        """Validate an intake field value against its declared type."""
        if not value:
            return value
        if field.field_type == "email":
            if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value):
                return ""
        elif field.field_type == "phone":
            # Allow digits, spaces, dashes, parens, plus sign
            if not re.match(r"^[\d\s\-()+.]{7,20}$", value):
                return ""
        elif field.field_type == "number":
            try:
                float(value)
            except ValueError:
                return ""
        elif field.field_type == "select":
            # Validate against allowed options
            if field.select_options:
                allowed = {
                    opt.strip()
                    for opt in field.select_options.split("\n")
                    if opt.strip()
                }
                if value not in allowed:
                    return ""
        return value

    def _save_intake_answers(self, booking, booking_type, kwargs):
        """Save custom intake field answers from the form."""
        IntakeAnswer = request.env["appointment.intake.answer"].sudo()
        for field in booking_type.intake_field_ids:
            key = f"intake_{field.id}"
            value = (kwargs.get(key) or "").strip()
            value = self._validate_intake_value(field, value)
            if value:
                IntakeAnswer.create({
                    "booking_id": booking.id,
                    "field_id": field.id,
                    "value": value,
                })

    def _bf_save_guests(self, booking, booking_type, kwargs, partner):
        """Enregistre les invités saisis, en attente de confirmation."""
        if not booking_type.allow_guests:
            return False
        brut = (kwargs.get("bf_guests") or "").strip()
        if not brut:
            return False
        Guest = request.env["resource.booking.guest"].sudo()
        adresses, ecartees = Guest._bf_parse_emails(
            brut,
            exclure=[partner.email, booking.user_id.partner_id.email],
            maximum=booking_type.max_guests or 0,
        )
        for adresse in adresses:
            Guest.create({"booking_id": booking.id, "email": adresse})
        if ecartees:
            _logger.info(
                "Réservation %s : %d saisie(s) d'invité écartée(s) (format, "
                "doublon ou demandeur lui-même)", booking.id, ecartees)
        return bool(adresses)

    @route(
        [
            "/appointment/b/<int:booking_id>/<string:token>/guests/confirm",
            "/appointment/b/<int:booking_id>/<string:token>/guests/decline",
        ],
        type="http",
        auth="public",
        website=True,
        methods=["GET", "POST"],
    )
    def appointment_guests(self, booking_id, token, **kwargs):
        """Confirme ou écarte les invités saisis par le demandeur.

        🔴 Le GET ne décide RIEN, et c'est le cœur du dispositif. Ce lien part
        dans un courriel : les antivirus de messagerie et les aperçus de lien
        suivent les URL. Si le GET confirmait, les invitations partiraient
        toutes seules, sans qu'un humain ait cliqué — c'est-à-dire exactement le
        pourriel que cette confirmation existe pour empêcher. Le GET montre donc
        la liste et demande; seul le POST agit.
        """
        _apply_locale_from_request()
        booking_sudo = self._get_booking_sudo(booking_id, token)
        if not booking_sudo:
            return request.redirect("/appointment")
        ecarter = request.httprequest.path.endswith("/decline")
        en_attente = booking_sudo._bf_pending_guests()

        if request.httprequest.method == "GET":
            response = request.render(
                "bf_appointment.appointment_guests_confirm",
                {
                    "booking_sudo": booking_sudo,
                    "access_token": token,
                    "guests": en_attente,
                    "decline": ecarter,
                    "done": not en_attente,
                },
            )
            return _apply_security_headers(response)

        if not bf_rate_limit("guests_confirm", 10, 600, key=token):
            return request.redirect(f"/appointment/b/{booking_id}/{token}")
        if en_attente:
            if ecarter:
                en_attente._bf_decline()
            else:
                en_attente._bf_confirm()
        response = request.render(
            "bf_appointment.appointment_guests_confirm",
            {
                "booking_sudo": booking_sudo,
                "access_token": token,
                "guests": booking_sudo.guest_ids,
                "decline": ecarter,
                "done": True,
            },
        )
        return _apply_security_headers(response)
