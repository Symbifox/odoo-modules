# -*- coding: utf-8 -*-
"""Per-booking Shlink fallback for Nextcloud Talk video rooms.

When a dedicated Talk room cannot be created at booking time (e.g. Nextcloud
unreachable, service-account credential rejected), the booking must still go
out with a working join link instead of an empty ``videocall_location``.

Design (fallback-only):
  * Success -> ``_generate_nc_talk_url`` returns the dedicated room URL, used
    directly. Shlink is not involved.
  * Failure -> a *per-booking* short URL (slug ``rdv-<token>``) is created via
    the Shlink REST API, pointing to a permanent generic room. The booking's
    ``videocall_location`` becomes that short URL and ``video_fallback_active``
    is set.
  * Repair  -> a cron retries the dedicated room for fallback bookings and,
    on success, repoints the *same* short URL to the real room. The link the
    client already received never changes; only its target does.

All Shlink/HTTP calls are defensive: any failure degrades gracefully to the
type-level static fallback rather than raising.
"""

import logging

import requests

from odoo import fields, models

_logger = logging.getLogger(__name__)

SHLINK_TIMEOUT = 10


class ResourceBooking(models.Model):
    _inherit = "resource.booking"

    video_fallback_active = fields.Boolean(
        string="Salle visio en secours",
        default=False,
        copy=False,
        help="Vrai quand la salle Nextcloud Talk dédiée n'a pas pu être créée "
        "et que le lien de connexion pointe vers un shlink de secours. Le cron "
        "« Réparation des salles visio en secours » retente la salle dédiée et "
        "repointe le shlink vers celle-ci.",
    )

    # ------------------------------------------------------------------
    # Shlink REST helpers
    # ------------------------------------------------------------------
    def _shlink_config(self):
        """Return (base_url, api_key) or (None, None) if not configured."""
        ICP = self.env["ir.config_parameter"].sudo()
        base = (ICP.get_param("bf_appointment.shlink_base_url") or "").rstrip("/")
        key_enc = ICP.get_param("bf_appointment.shlink_api_key_encrypted")
        api_key = self._decrypt_nc_talk_password(key_enc) if key_enc else None
        if not base or not api_key:
            return (None, None)
        return (base, api_key)

    def _booking_shlink_slug(self):
        self.ensure_one()
        return "rdv-%s" % (self.video_room_token or self.id)

    def _shlink_upsert(self, slug, long_url):
        """Create the short URL ``slug`` -> ``long_url`` (or repoint if it
        already exists). Return the short URL string, or None on failure."""
        base, api_key = self._shlink_config()
        if not base or not api_key or not long_url:
            return None
        headers = {"X-Api-Key": api_key, "Accept": "application/json"}
        try:
            r = requests.post(
                "%s/rest/v3/short-urls" % base,
                headers=headers,
                json={
                    "longUrl": long_url,
                    "customSlug": slug,
                    "findIfExists": True,
                    "title": self.display_name or slug,
                },
                timeout=SHLINK_TIMEOUT,
            )
            if r.status_code in (200, 201):
                data = r.json()
                # Existing short URL returned with a stale target -> repoint.
                if (data.get("longUrl") or "").rstrip("/") != long_url.rstrip("/"):
                    self._shlink_repoint(slug, long_url)
                return data.get("shortUrl") or ("%s/%s" % (base, slug))
            # Slug already taken by a different long URL -> repoint it.
            if r.status_code == 400 and self._shlink_repoint(slug, long_url):
                return "%s/%s" % (base, slug)
            _logger.error(
                "Shlink create failed (%s): %s", r.status_code, r.text[:200]
            )
        except Exception as e:  # pragma: no cover - network defensive
            _logger.error("Shlink create error: %s", e)
        return None

    def _shlink_repoint(self, slug, long_url):
        """Point an existing short URL at ``long_url``. Return True on success."""
        base, api_key = self._shlink_config()
        if not base or not api_key:
            return False
        headers = {"X-Api-Key": api_key, "Accept": "application/json"}
        try:
            r = requests.patch(
                "%s/rest/v3/short-urls/%s" % (base, slug),
                headers=headers,
                json={"longUrl": long_url},
                timeout=SHLINK_TIMEOUT,
            )
            return r.status_code in (200, 204)
        except Exception as e:  # pragma: no cover - network defensive
            _logger.error("Shlink repoint error: %s", e)
            return False

    # ------------------------------------------------------------------
    # Fallback + repair
    # ------------------------------------------------------------------
    def _nc_talk_url_with_fallback(self):
        """Dedicated Talk room on success; per-booking shlink on failure.

        Called from ``_generate_video_url`` in place of ``_generate_nc_talk_url``.
        """
        self.ensure_one()
        room = self._generate_nc_talk_url()  # dedicated room URL, or False
        if room:
            if self.video_fallback_active:
                self.video_fallback_active = False
            return room
        return self._fallback_booking_shlink()

    def _fallback_booking_shlink(self):
        self.ensure_one()
        ICP = self.env["ir.config_parameter"].sudo()
        target = (
            ICP.get_param("bf_appointment.nc_talk_generic_room_url")
            or self.type_id.videocall_location
            or ""
        )
        short = self._shlink_upsert(self._booking_shlink_slug(), target)
        self.video_fallback_active = True
        # If Shlink itself is unavailable, degrade to the type static fallback.
        return short or self.type_id.videocall_location or target or False

    def _cron_repair_video_rooms(self):
        """Retry dedicated Talk rooms for fallback bookings and repoint their
        short URL to the real room once Nextcloud is reachable again."""
        bookings = self.search(
            [
                ("video_fallback_active", "=", True),
                ("state", "in", ("scheduled", "confirmed")),
                ("start", ">", fields.Datetime.now()),
            ]
        )
        for b in bookings:
            room = b._generate_nc_talk_url()  # retry dedicated room
            if not room:
                continue
            if b._shlink_repoint(b._booking_shlink_slug(), room):
                b.video_fallback_active = False
                _logger.info(
                    "bf_appointment: repaired video room for booking %s", b.id
                )
            self.env.cr.commit()
