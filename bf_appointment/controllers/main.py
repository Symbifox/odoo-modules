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

# Cap distinct tracked IPs so a flood of source IPs cannot grow the per-process
# limiter dicts without bound (same guard as bf_meeting).
_MAX_TRACKED_IPS = 10000

# --- Limitation de débit : UN seul mécanisme -------------------------------
#
# Il y en avait quatre : trois limiteurs écrits à la main (jetons,
# réservations, consultation de consentement) plus le seau nommé du lot
# d'ouverture, tous répétant le même motif verrou + liste d'horodatages. Le
# commentaire d'alors disait de laisser les trois en place — refactoriser des
# chemins publics vivants pour la seule élégance échange un risque réel contre
# un gain nul. C'était juste, mais ils avaient DIVERGÉ : seul le limiteur de
# jetons abandonnait les IP inactives, les deux autres attendaient le seuil de
# 10 000 pour tout vider d'un coup, ce qui remet à zéro le compteur de tout le
# monde — y compris celui qu'on est en train de plafonner.
#
# Les trois fonctions gardent leur nom et leur sémantique exacte; seul le
# stockage est mis en commun. `proxy_mode` est actif dans ce déploiement :
# `_client_ip()` lit le pair de socket déjà réécrit par ProxyFix et ne parse
# JAMAIS les en-têtes lui-même, sinon un client ferait tourner son propre seau
# à chaque requête.
_TOKEN_FAIL_MAX = 10  # max failed attempts
_TOKEN_FAIL_WINDOW = 300  # per 5 minutes
_BOOK_MAX = 5        # max bookings
_BOOK_WINDOW = 600   # per 10 minutes / IP

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
        # Same bound as the three named limiters, and it matters more here:
        # the key is (bucket, IP), so a distinct-IP flood grows this dict
        # once per bucket it touches.
        if len(_bucket_data) > _MAX_TRACKED_IPS:
            _bucket_data.clear()
        cutoff = now - window
        hits = [t for t in _bucket_data[ident] if t > cutoff]
        if len(hits) >= max_hits:
            _bucket_data[ident] = hits
            return False
        if consume:
            hits.append(now)
        if hits:
            _bucket_data[ident] = hits
        else:
            _bucket_data.pop(ident, None)  # bound memory: drop idle keys
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
    """Return True if IP is within rate limits for token validation.

    ⚠️ Vérifie SANS consommer : seuls les échecs comptent
    (`_record_token_failure`). Compter chaque lecture de page enfermerait
    dehors la personne légitime qui recharge son propre lien.
    """
    return bf_rate_limit(
        "token_fail", _TOKEN_FAIL_MAX, _TOKEN_FAIL_WINDOW, consume=False)


def _record_token_failure():
    """Record a failed token validation attempt for rate limiting."""
    bf_rate_limit_record("token_fail", _TOKEN_FAIL_WINDOW)


def _check_book_rate_limit():
    """Return True and record the hit if this IP may create another booking.

    Caps unauthenticated booking creation (and the intake-ack email it triggers)
    to _BOOK_MAX per _BOOK_WINDOW so the form can't be looped into a partner/
    booking spam or a mail-bomb aimed at an attacker-chosen address.
    """
    return bf_rate_limit("book", _BOOK_MAX, _BOOK_WINDOW)


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


def _installed_lang(code):
    """Return the active res.lang for `code`, or an empty recordset."""
    if not code:
        return request.env["res.lang"].sudo().browse()
    return request.env["res.lang"].sudo().search(
        [("code", "=", code), ("active", "=", True)], limit=1
    )


def _lang_cookie():
    """The `frontend_lang` cookie, if it names a lang installed here."""
    try:
        cookie = request.httprequest.cookies.get("frontend_lang")
    except Exception:
        return None
    return cookie if cookie and _installed_lang(cookie) else None


def _url_carries_lang():
    """True when the URL named a language other than the tenant default.

    ⚠️ Not readable from the path: Odoo strips the `/en/` prefix before
    routing, so `request.httprequest.path` is already `/appointment/...` on an
    English URL. What survives is the resolved context lang - a prefix-less URL
    always resolves to the default, so anything else means the URL said so.
    """
    return (request.env.context.get("lang") or _BF_DEFAULT_LANG) != _BF_DEFAULT_LANG


def _visitor_lang():
    """Best guess at the visitor's own language, for stamping a new contact.

    In order of trust: a language named by the URL, then the `frontend_lang`
    cookie the toggle sets, then Accept-Language.

    This exists because the booking form used to create contacts with no `lang`
    at all, so they silently inherited the request context - fr_CA on the
    prefix-less URL. That single omission then decided the language of every
    appointment email, since all of them render with
    `lang = {{ object.partner_id.lang }}`, and it outlived the booking: the
    contact stayed French for good.

    The intake form posts to a hardcoded prefix-less action, so on that request
    the URL says nothing and the cookie carries the truth - Odoo sets
    `frontend_lang` on the very page the form was served from.

    Returns a lang code that is installed and active, or the tenant default.
    """
    if _url_carries_lang():
        return request.env.context["lang"]
    cookie = _lang_cookie()
    if cookie:
        return cookie
    lang = _resolve_lang_from_accept_header()
    return lang if _installed_lang(lang) else _BF_DEFAULT_LANG


def _msg(fr, en):
    """Pick the visitor's language for a message the controller writes itself.

    The public templates carry their own `'EN' if _en else 'FR'` ternaries, but
    the validation errors the booking POST redirects with were French strings,
    full stop. An English booker who left a field empty was answered in French
    on an otherwise English page.
    """
    return en if _visitor_lang().startswith("en") else fr


def _maybe_redirect_to_visitor_lang():
    """Send a first-time visitor to their own language, once, or return None.

    Only for GET, and only on a URL that named no language of its own. An
    explicit choice always wins - that is what the 2026-05-20 regression was
    about, where an English `Accept-Language` header overrode a deliberate
    switch to Français and made the toggle unusable. So a URL with a language
    is left alone, and on a prefix-less one the `frontend_lang` cookie outranks
    the header: a visitor who toggled to Français keeps Français, while one who
    toggled to English is sent back to English instead of being stranded on the
    default page every time they return.

    Redirecting rather than merely swapping the context lang is what makes the
    choice stick: the language then lives in the URL, so every link on the page
    carries it and the next request needs no negotiation.

    ⚠️ The anti-loop guard cannot read the path - Odoo strips the `/en/` prefix
    before routing, so `request.httprequest.path` is already `/appointment/...`
    on an English URL, and matching on it sent `/en/appointment` to itself,
    forever. The resolved context lang is what survives, hence
    `_url_carries_lang()`.
    """
    if request.httprequest.method != "GET":
        return None
    if _url_carries_lang():
        return None
    wanted = _lang_cookie() or _resolve_lang_from_accept_header()
    if wanted == _BF_DEFAULT_LANG:
        return None
    lang = _installed_lang(wanted)
    if not lang or not lang.url_code:
        return None
    query = request.httprequest.query_string.decode()
    target = "/%s%s" % (lang.url_code, request.httprequest.path or "/")
    if query:
        target = "%s?%s" % (target, query)
    return request.redirect(target)


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
_CONSENT_LOOKUP_MAX = 30
_CONSENT_LOOKUP_WINDOW = 300


def _check_consent_lookup_rate_limit():
    """Seau distinct de celui des réservations : un réservant qui tape
    lentement ne doit pas se faire plafonner sa réservation."""
    return bf_rate_limit(
        "consent_lookup", _CONSENT_LOOKUP_MAX, _CONSENT_LOOKUP_WINDOW)


def _lookup_active_consent(env, partner, purpose_code, current_notice_id):
    """Return the active privacy.consent record for (partner, purpose) or False.

    Kept as a module-level helper because several routes read like this, but
    the logic itself now lives on ``res.partner._bf_active_consent`` so the
    booking model can consult it without importing a controller.
    """
    if not partner or not purpose_code:
        return False
    return partner._bf_active_consent(purpose_code, current_notice_id)


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
        redirect = _maybe_redirect_to_visitor_lang()
        if redirect:
            return redirect
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
        redirect = _maybe_redirect_to_visitor_lang()
        if redirect:
            return redirect
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
                + quote_plus(_msg(
                    "Trop de demandes. Veuillez réessayer dans quelques minutes.",
                    "Too many requests. Please try again in a few minutes.",
                ))
            )
        name = (kwargs.get("name") or "").strip()
        email = (kwargs.get("email") or "").strip()
        phone = (kwargs.get("phone") or "").strip()
        company = (kwargs.get("company") or "").strip()
        tz = (kwargs.get("tz") or "").strip()
        duration_str = (kwargs.get("duration") or "").strip()
        location_input = (kwargs.get("location") or "").strip()
        # Validate required fields
        if not name or not email:
            return request.redirect(
                f"/appointment/{slug}?error={quote_plus(_msg('Veuillez remplir tous les champs obligatoires.', 'Please fill in every required field.'))}"
            )
        # Loi 25, explicit consent required for personal information collection.
        # The form has client-side `required`, but a tampered submission could
        # bypass that, so we enforce server-side too.
        if not kwargs.get("bf_consent"):
            return request.redirect(
                f"/appointment/{slug}?error={quote_plus(_msg('Veuillez accepter la politique de confidentialité pour soumettre votre demande.', 'Please accept the privacy policy to submit your request.'))}"
            )
        # If in-person without fixed location, the booker must provide one
        if booking_type.is_in_person and not booking_type.location and not location_input:
            return request.redirect(
                f"/appointment/{slug}?error={quote_plus(_msg('Veuillez indiquer un lieu de rencontre.', 'Please give a meeting location.'))}"
            )
        # Validate the format of a booker-provided location: short or
        # letter-less strings ("abc", "123", "...") slip past the empty check
        # but are useless for the organizer.
        if location_input and not _validate_location_format(location_input):
            return request.redirect(
                f"/appointment/{slug}?error={quote_plus(_msg('Veuillez fournir une adresse ou un lieu reconnaissable.', 'Please give a recognisable address or place.'))}"
            )
        # Basic email validation
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            return request.redirect(
                f"/appointment/{slug}?error={quote_plus(_msg('Adresse courriel invalide.', 'Invalid email address.'))}"
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
                # Les deux libellés sont montés AVANT l'interpolation : imbriquer
                # une f-string entre guillemets doubles dans une f-string entre
                # guillemets doubles n'est légal qu'à partir de Python 3.12
                # (PEP 701). Odoo 18 se déclare compatible à partir de 3.10, et
                # ce fichier ne s'importait pas sous 3.10 ni 3.11.
                fr = f"Le champ « {field.name} » est obligatoire."
                en = f'The field "{field.name}" is required.'
                return request.redirect(
                    f"/appointment/{slug}?error={quote_plus(_msg(fr, en))}"
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
            # Optional organization captured on the form: stored as the
            # individual contact's company name so the organizer can see
            # which organization the invitee represents.
            if company and booking_type.collect_company:
                partner_vals["company_name"] = company
            if tz:
                partner_vals["tz"] = tz
            # Stamp the visitor's own language on the contact. Left unset, the
            # contact inherits the request context - fr_CA on the prefix-less
            # URL - and since every appointment template renders with
            # `lang = {{ object.partner_id.lang }}`, an English-speaking booker
            # gets French mail for this booking and every one after it.
            partner_vals["lang"] = _visitor_lang()
            partner = Partner.create(partner_vals)
        else:
            # Update name if currently set to the email (auto-created contacts)
            if not partner.name or partner.name.strip().lower() == partner.email.strip().lower():
                partner.name = name
            if phone and not partner.phone:
                partner.phone = phone
            # Fill the company name only when the contact has none on file,
            # to avoid clobbering a value an internal user may have curated.
            if company and booking_type.collect_company and not partner.company_name:
                partner.company_name = company
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
                msg = _msg(
                    "Le compte rendu fait partie du service pour ce type de "
                    "rencontre. Veuillez accepter l'enregistrement et la "
                    f"transcription, ou nous écrire à {fallback_email} "
                    "pour un format alternatif.",
                    "The written record is part of the service for this "
                    "meeting type. Please accept the recording and "
                    f"transcription, or write to us at {fallback_email} "
                    "for an alternative format.",
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
                    booking._send_appointment_email(
                        ack_template.sudo(), attach_ics=False, recipient="booker")
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
        """Consigne les consentements cochés sur le formulaire d'accueil.

        L'écriture elle-même est déléguée à `resource.booking._bf_record_consent`,
        écrivain unique partagé avec la page de choix de créneau et le
        back-office : trois copies de la séquence consentement + preuve
        divergeraient au premier ajustement, et une preuve qui diverge ne
        prouve plus rien.

        `allow_request=False` : ce chemin vient DE poser les questions. Faire
        partir en plus une demande par courriel pour une case que la personne
        a sous les yeux serait absurde.
        """
        evidence = booking._bf_consent_evidence_from_request()
        collected = {}

        # Enregistrement : on n'écrit que si le type l'exige ET qu'il n'y a
        # rien d'actif au dossier. Sinon on laisse le consentement existant
        # tranquille (la règle « on ne redemande pas »).
        if booking_type.requires_recording_consent and not recording_already_active:
            collected["recording"] = bool(kwargs.get("bf_consent_recording"))

        booking._bf_ensure_consents(
            collected=collected,
            evidence=evidence,
            source_note="/appointment/%s/book" % booking_type.slug,
            allow_request=False,
        )

        # Infolettre : offerte, cochée, et pas déjà au dossier. Refuser
        # l'infolettre sur un formulaire de rendez-vous n'est PAS un refus à
        # consigner — la personne n'a simplement pas adhéré. La LCAP ne
        # journalise que les consentements explicites.
        if booking_type.offers_newsletter_signup and kwargs.get("bf_consent_newsletter"):
            existing_mkt = _lookup_active_consent(
                request.env, partner, "marketing",
                booking_type.newsletter_notice_id.id if booking_type.newsletter_notice_id else False,
            )
            if not existing_mkt:
                booking._bf_record_consent(
                    "marketing",
                    booking_type.newsletter_notice_id,
                    True,
                    evidence=evidence,
                    source_note="/appointment/%s/book" % booking_type.slug,
                )
                # Au mieux : inscrire à la liste « Infolettre » si mass_mailing
                # est installé et qu'une telle liste existe. Un échec n'est pas
                # fatal — le consentement, lui, est écrit.
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
        # Consentements encore à obtenir pour CETTE personne. Calculé au
        # rendu, jamais mis en cache : un consentement accordé ailleurs entre
        # deux chargements doit faire disparaître la case, pas la répéter.
        # Rien à afficher quand tout est au dossier, ce qui est le cas le plus
        # fréquent sur un lien envoyé à un client existant.
        consent_asks = [a["code"] for a in booking_sudo._bf_missing_consents()]
        values = {
            "booking_sudo": booking_sudo,
            "access_token": token,
            "error": kwargs.get("error"),
            "visitor_tz": tz,
            "consent_asks": consent_asks,
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
            # Les consentements sont demandés SUR CETTE PAGE. Sans ce drapeau,
            # `action_confirm` enverrait la demande par courriel avant que la
            # réponse à l'écran soit consignée.
            bf_consents_handled=True,
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
        # Consentements. C'est ICI que se refermait le trou : un lien personnel
        # ne passe par aucun formulaire d'accueil, donc rien n'était ni vérifié
        # ni demandé, et la rencontre pouvait naître enregistrable sans qu'on
        # ait rien à montrer. Une case vue et laissée décochée est une réponse
        # (un refus), pas une absence : on ne transmet que ce qui a réellement
        # été affiché. Jamais bloquant — un consentement manquant empêche
        # l'ENREGISTREMENT, pas le rendez-vous.
        try:
            demandes = [a["code"] for a in booking_sudo._bf_missing_consents()]
            collected = {
                code: bool(kwargs.get("bf_consent_%s" % code))
                for code in demandes
                if kwargs.get("bf_consent_shown_%s" % code)
            }
            booking_sudo._bf_ensure_consents(
                collected=collected,
                source_note="/appointment/b/%d/.../confirm" % booking_sudo.id,
            )
        except Exception:  # noqa: BLE001
            _logger.exception(
                "Consentements à la confirmation (réservation %s)", booking_sudo.id)
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
            booking_sudo._send_appointment_email(template, recipient="booker")
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
                    org_template, attach_ics=True, recipient="organizer"
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
        # ⚠️ `!= "POST"`, PAS `== "GET"`. Werkzeug ajoute HEAD d'office à toute
        # règle qui accepte GET, mais `httprequest.method` vaut alors "HEAD" :
        # la garde écrite en `== "GET"` était donc FAUSSE sur un HEAD, et la
        # requête tombait dans la branche qui MUTE. 🔴 Vécu en production le
        # 2026-08-24 : un rendez-vous client confirmé cinq minutes plus tôt a été
        # annulé par le HEAD d'un antivirus de messagerie suivant le lien
        # « Annuler » de l'ICS. Reproduit avec un seul `curl -I`, sans aucun clic.
        # On énumère ce qui MUTE (POST), jamais ce qui ne mute pas.
        if request.httprequest.method != "POST":
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
                    client_template, attach_ics=False, recipient="booker"
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
                        org_template, attach_ics=False, recipient="organizer"
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

        # ⚠️ `!= "POST"`, PAS `== "GET"`. Werkzeug ajoute HEAD d'office à toute
        # règle qui accepte GET, mais `httprequest.method` vaut alors "HEAD" :
        # la garde écrite en `== "GET"` était donc FAUSSE sur un HEAD, et la
        # requête tombait dans la branche qui MUTE. 🔴 Vécu en production le
        # 2026-08-24 : un rendez-vous client confirmé cinq minutes plus tôt a été
        # annulé par le HEAD d'un antivirus de messagerie suivant le lien
        # « Annuler » de l'ICS. Reproduit avec un seul `curl -I`, sans aucun clic.
        # On énumère ce qui MUTE (POST), jamais ce qui ne mute pas.
        if request.httprequest.method != "POST":
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
