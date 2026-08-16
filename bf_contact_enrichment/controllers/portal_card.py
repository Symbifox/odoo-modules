"""Mobile scan page — a standalone surface over the business-card wizard.

Why a page and not an app screen: scanning a card is a ten-second, foreground,
occasional gesture. It needs no push, no offline store and no background work,
so the three things a native client buys are all unused. A page costs one
install-free URL instead of a repository, a fingerprint and a sideload.

The page is deliberately a *thin client*. It never re-implements extraction,
duplicate matching or the write to ``res.partner``: every route drives the
existing ``bf.contact.card.wizard``. Whichever backend that wizard talks to,
the page follows without a change here.

Routes are session-authenticated (``auth="user"``) and gated on the same group
as the backend wizard, so the page grants nothing the Odoo client would not.
The JSON routes use ``type="json"``: Odoo's JSON-RPC dispatcher demands an
``application/json`` body, which forces a CORS preflight on any cross-origin
attempt, so the session cookie cannot be replayed from another site.
"""

import json
import logging

from odoo import _, http
from odoo.exceptions import AccessError, UserError
from odoo.http import request

_logger = logging.getLogger(__name__)

ENRICH_GROUP = "bf_contact_enrichment.group_bf_contact_enrich"

# Mirrors the wizard's editable fields. Order is the review form's order.
CARD_FIELDS = (
    "full_name", "first_name", "last_name", "function", "company",
    "email", "phone", "mobile", "website", "street", "city", "zip", "country",
)

ICONS = "/bf_contact_enrichment/static/src/scan"

# The manifest is served from a controller rather than as a static file so
# ``start_url`` is the page itself; a static manifest would still work, but the
# icons and the scope would then be the only thing left in it.
MANIFEST = {
    # ``id`` pins the app's identity. Without it a browser derives one from
    # ``start_url``, so the day that URL moves the launcher treats it as a
    # different app and installs a second icon beside the first.
    "id": "/scan",
    "name": "Numériser une carte",
    "short_name": "Carte",
    "description": "Créer un contact à partir d'une carte d'affaires.",
    "start_url": "/scan",
    "scope": "/scan",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "#12161a",
    "theme_color": "#12161a",
    "lang": "fr-CA",
    # Both purposes at both sizes. A launcher that wants a maskable icon and
    # finds only a 512 downscales it; Android asks for 192 far more often.
    "icons": [
        {"src": f"{ICONS}/icon-192.png",
         "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": f"{ICONS}/icon-512.png",
         "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {"src": f"{ICONS}/icon-maskable-192.png",
         "sizes": "192x192", "type": "image/png", "purpose": "maskable"},
        {"src": f"{ICONS}/icon-maskable-512.png",
         "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    ],
}

# Kept inline rather than under ``static/``: a worker must be served with a
# header a static route cannot set, so it never travels through the asset
# pipeline anyway. It caches the module's own static assets and one fixed
# offline page — never an authenticated page, never a card image.
#
# The navigation branch is what makes the page installable at all. Chromium
# only offers to install a page whose start URL answers with a 200 while the
# network is down, and it asks the worker for that answer. A worker that
# declares a fetch handler but never responds to navigations — the shape this
# file had at 18.0.1.1.1 — fails that check silently: no prompt, and the
# browser degrades to a bookmark shortcut carrying whatever icon it can find.
SERVICE_WORKER = """\
const CACHE = 'bf-scan-v2';
const OFFLINE = '/bf_contact_enrichment/static/src/scan/offline.html';
const SHELL = [
  '/bf_contact_enrichment/static/src/scan/scan.css',
  '/bf_contact_enrichment/static/src/scan/scan.js',
  '/bf_contact_enrichment/static/src/scan/icon-192.png',
  OFFLINE,
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET') {
    return;
  }

  // Navigations inside /scan: always the network, because the page is
  // authenticated and personalised. Only when the network is unreachable does
  // the fixed offline shell stand in — it is the same answer for everyone, so
  // nothing about a session is ever stored on the device.
  const navigating = e.request.mode === 'navigate' ||
    (e.request.headers.get('accept') || '').includes('text/html');
  if (navigating && url.origin === self.location.origin &&
      url.pathname.startsWith('/scan')) {
    e.respondWith(fetch(e.request).catch(() => caches.match(OFFLINE).then(
      (hit) => hit || new Response(
        '<!DOCTYPE html><meta charset="utf-8"><title>Hors ligne</title>' +
        '<p>Hors ligne. Reprenez quand le r\\u00e9seau revient.',
        {status: 200, headers: {'Content-Type': 'text/html; charset=utf-8'}}))));
    return;
  }

  // The module's own static assets, cache first.
  if (url.pathname.startsWith('/bf_contact_enrichment/static/')) {
    e.respondWith(caches.match(e.request).then((hit) => hit || fetch(e.request)));
  }
});
"""


class BfContactScanPortal(http.Controller):

    # ── Helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _may_scan():
        """Both rights the flow needs, checked together.

        Holding the enrichment group is not enough: on a stock Odoo only
        ``base.group_partner_manager`` may create or write a ``res.partner``,
        and a member without it can read a card but never save it. Checking
        both up front is what keeps someone from spending a model call and
        discovering the wall afterwards.
        """
        return (request.env.user.has_group(ENRICH_GROUP)
                and request.env["res.partner"].has_access("create"))

    @classmethod
    def _check_group(cls):
        """Raise unless the caller may both spend a scan and write a contact."""
        if not request.env.user.has_group(ENRICH_GROUP):
            raise AccessError(
                _("Votre compte n'a pas accès à l'enrichissement de contacts."))
        if not request.env["res.partner"].has_access("create"):
            raise AccessError(
                _("Votre compte peut lire les contacts, mais pas en créer. "
                  "Demandez le droit « Création de contacts »."))

    @staticmethod
    def _wizard(wizard_id):
        """Re-read a wizard the caller created, or raise.

        ``sudo`` is never used: the record is read as the session user, so the
        access rules on the transient model do the gatekeeping. A stale id
        (transients get vacuumed) surfaces as a clean message, not a traceback.
        """
        try:
            wiz = request.env["bf.contact.card.wizard"].browse(int(wizard_id))
        except (TypeError, ValueError):
            raise UserError(_("Session de numérisation invalide."))
        if not wiz.exists():
            raise UserError(
                _("Cette session de numérisation a expiré. Reprenez la photo."))
        return wiz

    # ── The page ────────────────────────────────────────────────────

    @http.route("/scan", type="http", auth="user", methods=["GET"])
    def scan_page(self, **kw):
        """Render the standalone page.

        No ``website.layout``: this module has no business depending on the
        website app, and the backend chrome is exactly what makes an Odoo page
        unusable on a phone. The template carries its own shell.
        """
        return request.render("bf_contact_enrichment.scan_page", {
            "has_access": self._may_scan(),
            "user_name": request.env.user.name,
        })

    # ── Step 1: read the card ───────────────────────────────────────

    @http.route("/scan/extract", type="json", auth="user", methods=["POST"])
    def scan_extract(self, image_b64=None, filename=None, **kw):
        """Run the card through the wizard and hand back the parsed fields."""
        self._check_group()
        image_b64 = (image_b64 or "").strip()
        if not image_b64:
            return {"error": _("Aucune image reçue.")}

        wiz = request.env["bf.contact.card.wizard"].create({
            "image": image_b64,
            "image_filename": filename or "carte.jpg",
        })
        try:
            wiz.action_scan()
        except UserError as exc:
            # The wizard already phrases these for a human (unreadable card,
            # bridge down, unsupported file); pass them through as-is.
            return {"error": exc.args[0] if exc.args else _("Lecture impossible.")}
        except Exception:  # noqa: BLE001 — never leak a traceback to a phone
            _logger.exception("Card scan failed for user %s", request.env.user.login)
            return {"error": _("La lecture a échoué. Réessayez avec une photo "
                               "plus nette, ou saisissez le contact à la main.")}

        match = wiz.matched_partner_id
        return {
            "wizard_id": wiz.id,
            "confidence": wiz.confidence or 0,
            "fields": {f: (wiz[f] or "") for f in CARD_FIELDS},
            "match": {
                "id": match.id,
                "name": match.display_name,
                "email": match.email or "",
            } if match else None,
            "mode": wiz.match_mode or "create",
        }

    # ── Step 2: write the contact ───────────────────────────────────

    @http.route("/scan/save", type="json", auth="user", methods=["POST"])
    def scan_save(self, wizard_id=None, fields=None, mode=None, **kw):
        """Apply the reviewed fields through the wizard's own write path."""
        self._check_group()
        try:
            wiz = self._wizard(wizard_id)
        except UserError as exc:
            return {"error": exc.args[0]}

        edited = fields or {}
        vals = {f: (edited.get(f) or False) for f in CARD_FIELDS if f in edited}
        if mode in ("create", "update"):
            vals["match_mode"] = mode
        if mode == "update" and not wiz.matched_partner_id:
            return {"error": _("Aucun contact existant à mettre à jour.")}
        if vals:
            wiz.write(vals)

        try:
            action = wiz.action_apply()
        except UserError as exc:
            return {"error": exc.args[0] if exc.args else _("Enregistrement refusé.")}
        except AccessError:
            # A record rule can still refuse this particular contact even when
            # the model-level right is held — multi-company, most often.
            return {"error": _("Votre compte n'a pas le droit d'écrire ce contact.")}
        except Exception:  # noqa: BLE001
            _logger.exception("Card save failed for user %s", request.env.user.login)
            return {"error": _("L'enregistrement a échoué.")}

        # action_apply already resolved the partner and handed it back in its
        # act_window; read it from there rather than guessing at the last id.
        partner_id = (action or {}).get("res_id")
        if not partner_id:
            return {"error": _("Contact enregistré, mais introuvable ensuite.")}
        partner = request.env["res.partner"].browse(partner_id)
        return {
            "partner_id": partner.id,
            "name": partner.display_name,
            "url": "/odoo/contacts/%s" % partner.id,
            "updated": wiz.match_mode == "update",
        }

    # ── PWA plumbing ────────────────────────────────────────────────

    @http.route("/scan/manifest.webmanifest", type="http", auth="public",
                methods=["GET"], save_session=False)
    def scan_manifest(self, **kw):
        """Serve the web app manifest.

        Public on purpose: a manifest is fetched without credentials unless the
        link tag opts in, it holds nothing private, and an authenticated 303 to
        the login page would silently make the page non-installable.
        """
        return request.make_response(
            json.dumps(MANIFEST, ensure_ascii=False),
            headers=[("Content-Type", "application/manifest+json; charset=utf-8"),
                     ("Cache-Control", "public, max-age=3600")],
        )

    @http.route("/scan/sw.js", type="http", auth="public", methods=["GET"],
                save_session=False)
    def scan_service_worker(self, **kw):
        """Serve the service worker with a scope above its own path.

        A worker served from ``/scan/sw.js`` may only claim ``/scan/`` by
        default, which does *not* cover ``/scan`` itself — the start page would
        stay uncontrolled and the install prompt would never appear. The header
        below widens the claim to ``/scan``.
        """
        return request.make_response(
            SERVICE_WORKER,
            headers=[("Content-Type", "application/javascript; charset=utf-8"),
                     ("Service-Worker-Allowed", "/scan"),
                     ("Cache-Control", "no-cache")],
        )
