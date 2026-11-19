"""Desktop surface (Plasma widget) bound to a Blue Fox OS session.

One row per widget installation. Authentication is a bearer ``device_token``
issued exclusively through the RFC 8628 device authorization flow:

  1. The widget POSTs ``/auth/device/start``. It gets back a ``device_code``
     (its own secret, which never leaves it) and a ``verification_uri_complete``.
  2. The widget opens that URI in the person's browser. The route is
     ``auth="user"``, so Odoo shows /web/login when needed: password,
     Authentik SSO and TOTP all apply untouched. The page states what is
     being allowed; its « Allow » POST is CSRF-protected.
  3. The widget, polling ``/auth/device/poll`` with its ``device_code``,
     receives the bearer token once — over HTTPS, in a response body.

⚠️ Why this flow and not the mobile one (``bf_email_management``): a QML
plasmoid has no embedded browser, no custom URL scheme it could own, and
opening a local HTTP port to catch a redirect means another process on a
multi-seat workstation could race it. Here nothing comes back to the widget
by redirection at all — it asks.

⚠️ Why no code for a person to type. RFC 8628 offers a short ``user_code``
read aloud or typed on another device. We deliberately ship only
``verification_uri_complete``: a flow that never asks anybody to type a
pairing code makes a code someone sends you suspicious by construction, which
is the one real weakness of device flows (an attacker starts a pairing and
talks the victim into approving it). The capability in the URL is therefore
full-length, not human-readable, and single-use.

⚠️ Scope. A token minted here opens the surfaces that choose to accept it —
today the notepad, and nothing else. It is NOT an Odoo session.

There is deliberately NO password route on this API.
"""
import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

from psycopg2 import OperationalError

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)

#: How long an unapproved pairing request stays usable. Long enough to log in
#: with SSO and a TOTP code, short enough that a leaked URL is inert by the
#: time anybody finds it in a proxy log.
PAIRING_TTL_SECONDS = 300

#: Seconds the widget is told to wait between two polls, and the floor the
#: server enforces. A widget that ignores it gets "slow_down", not a token.
POLL_INTERVAL_SECONDS = 2

#: A device that made no call for this long loses its token: a reinstalled or
#: reassigned workstation does not keep a door open forever. Same rule as the
#: mobile devices (audit 2026-09-08, S-M2).
TOKEN_IDLE_DAYS = 90

#: Fraîcheur en deçà de laquelle « vu la dernière fois » n'est pas réécrit.
#: Voir ``_touch_last_seen``.
HEARTBEAT_SECONDS = 60

#: Surfaces allowed to pair. A widget naming anything else is refused, so a
#: typo cannot quietly create a device nobody recognises in « My devices ».
APPS = {
    "stickynotes": "Symbifox Sticky Notes",
}


class BfDesktopDevice(models.Model):
    _name = "bf.desktop.device"
    _description = "Desktop device"
    _rec_name = "name"
    _order = "last_seen desc, id desc"

    # ⚠️ NOT required: a pairing request has no owner until somebody approves
    # it in a browser. Parking it on the technical user instead would make an
    # unapproved request look like a device belonging to root. The SQL check
    # below is what guarantees a delivered token always has an owner.
    user_id = fields.Many2one(
        "res.users", string="User", ondelete="cascade", index=True,
    )
    name = fields.Char(string="Device", default="Blue Fox OS workstation")
    app = fields.Char(string="Surface", required=True, index=True)
    app_label = fields.Char(string="Application", compute="_compute_app_label")
    app_version = fields.Char(string="Version")
    platform = fields.Char(default="linux")

    # ⚠️ Il n'y a PAS de champ pour le jeton en clair, et c'est voulu : il est
    # rendu une seule fois dans la réponse du `poll` et n'est jamais écrit en
    # base. Un vidage de la base ne livre donc aucun jeton utilisable.
    token_hash = fields.Char(
        string="Token fingerprint", index=True, copy=False,
        groups="base.group_system",
    )

    # The pairing request, before approval.
    pending_code = fields.Char(
        string="Pairing code", index=True, copy=False,
        groups="base.group_system",
    )
    pending_expiry = fields.Datetime(string="Pairing expires", copy=False)
    # The widget's own secret: it proves the poll comes from the installation
    # that started the pairing, and it never appears in a URL or on screen.
    device_secret_hash = fields.Char(
        string="Device secret fingerprint", copy=False,
        groups="base.group_system",
    )
    approved = fields.Boolean(default=False, copy=False)
    # Set when the token has been handed over, so a replayed poll gets nothing.
    delivered = fields.Boolean(default=False, copy=False)
    last_poll = fields.Datetime(copy=False)

    active = fields.Boolean(default=True)
    last_seen = fields.Datetime(string="Last seen")
    # Fingerprint of the address that OPENED the pairing request, kept only to
    # count requests in flight and cleared the moment the token is handed over.
    #
    # ⚠️ Keyed (HMAC with the database secret), not a bare hash. A plain
    # SHA-256 of an IPv4 address is 2^32 candidates — reversed in seconds — so
    # it would store the address, not a fingerprint of it, and the comment
    # claiming otherwise would be the only protection.
    ip_hash = fields.Char(string="Requester fingerprint", copy=False,
                          groups="base.group_system")

    _sql_constraints = [
        ("pending_code_uniq", "unique(pending_code)",
         "This pairing code already exists."),
        # A token that works always belongs to somebody. Enforced by the
        # database rather than by a Python constraint: this is the invariant
        # the whole module rests on.
        ("owner_once_delivered", "check(not delivered or user_id is not null)",
         "A delivered device must have an owner."),
    ]

    # Fields no non-sudo write may set: otherwise anyone with write access
    # could mint a token, or move a device to another user.
    # ⚠️ ``active`` en fait partie : sans lui, le propriétaire pouvait écrire
    # ``{"active": True}`` en RPC et rouvrir une porte que la péremption des
    # 90 jours venait de fermer. Les deux vrais « Révoquer » passent en sudo.
    _PROTECTED_FIELDS = (
        "token_hash", "pending_code", "pending_expiry",
        "device_secret_hash", "approved", "delivered", "user_id", "app",
        "active",
    )

    @api.depends("app")
    def _compute_app_label(self):
        for device in self:
            device.app_label = APPS.get(device.app) or device.app or ""

    def write(self, vals):
        if not self.env.su and any(f in vals for f in self._PROTECTED_FIELDS):
            raise AccessError(
                _("The token, the surface and the owner of a device are not "
                  "edited by hand."))
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        # Creating a row by hand would mean minting a pairing. The flow does it
        # in sudo; nobody else needs to.
        if not self.env.su:
            raise AccessError(
                _("A device is created by pairing it, not by hand."))
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # Secrets
    # ------------------------------------------------------------------
    @staticmethod
    def _hash(raw):
        """SHA-256 (hex) of a secret. Unsalted: these are 192+ bits of
        randomness, a rainbow table has nothing to bite on."""
        return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()

    @api.model
    def _fingerprint(self, raw):
        """Keyed digest of something that is NOT random — an IP address.

        ⚠️ A bare SHA-256 of an IPv4 address is 2^32 candidates, reversed in
        seconds: it stores the address. The database secret makes the digest
        useless to anybody who only has the table.
        """
        secret = (self.env["ir.config_parameter"].sudo()
                  .get_param("database.secret") or "")
        return hmac.new(secret.encode("utf-8"), (raw or "").encode("utf-8"),
                        hashlib.sha256).hexdigest()

    @api.model
    def _match_number(self, pairing_code):
        """The two digits the widget shows and the person must pick.

        🔴 Why this exists. Nothing on the consent page let somebody tell THEIR
        widget from one an attacker had started: the page shows the reader's own
        name and login whoever opened the pairing, and the workstation label
        came from the request itself, so it could read a plausible one. An
        attacker could therefore start a pairing, send the link (« your sticky
        notes need re-pairing »), and get a bearer token on the victim's
        notepad in two clicks.

        The cure honours that constraint — nothing to TYPE. The widget
        displays two digits; the page offers three numbers and asks which one
        is on screen. A failed choice destroys the request rather than letting
        them retry.

        🔴 CE QUE CETTE GARDE N'ARRÊTE PAS, et le commentaire l'a prétendu
        jusqu'en relecture. Il disait « a
        pairing the person did not start fails two times out of three ». C'est
        faux contre un attaquant PRÉPARÉ : ``/auth/device/start`` est public et
        rend ``match_number`` à l'appelant, donc celui qui ouvre l'appairage
        connaît le bon nombre et peut l'écrire dans son leurre (« ton widget
        affiche 42, clique 42 »). Les deux chances sur trois ne valent que
        contre un attaquant qui n'a pas lu la réponse qu'il a reçue.

        Ce qui reste, et c'est assumé : la page DIT en clair « si tu n'as pas
        lancé cet appairage, ferme cette page : quelqu'un demande à lire tes
        notes », et offre « je n'ai pas lancé ceci ». Un flux d'autorisation
        d'appareil sans code à taper n'a rien de mieux à offrir — c'est le prix
        du choix « rien à taper », et il est documenté ici plutôt que
        maquillé en garantie.

        Derived from the pairing code, so nothing extra is stored.
        """
        digest = hashlib.sha256((pairing_code or "").encode("utf-8")).digest()
        return digest[0] % 100

    @api.model
    def _match_choices(self, pairing_code):
        """The right number plus two decoys, in a stable order.

        Stable because the page may be reloaded: a different order on each
        render would make the honest person doubt what they saw.
        """
        bon = self._match_number(pairing_code)
        digest = hashlib.sha256(
            (b"leurres:" + (pairing_code or "").encode("utf-8"))).digest()
        choix = [bon]
        for octet in digest:
            if len(choix) == 3:
                break
            candidat = octet % 100
            if candidat not in choix:
                choix.append(candidat)
        # Ordre déterminé par le code, pas par le hasard de la requête.
        choix.sort(key=lambda n: (n * 7 + digest[0]) % 100)
        return choix

    # ------------------------------------------------------------------
    # Pairing (RFC 8628)
    # ------------------------------------------------------------------
    #: Pairing requests allowed in flight per caller. The flow is a few
    #: seconds of human attention; nobody legitimately needs more.
    MAX_PAIRINGS_PER_CALLER = 5

    #: 🔴 There is deliberately NO low global cap any more. A cap of 50 across
    #: the whole instance turned this public route into an off switch for
    #: pairing: ten source addresses (a single IPv6 /64 supplies as many as you
    #: want) times five requests, renewed with about two requests a minute, and
    #: NOBODY in the tenant can pair a widget — while the error they are shown
    #: blames their own widget. The per-caller brake is what bounds the table;
    #: this number only stops it growing without limit, and it is far above
    #: anything a tenant does.
    MAX_PAIRINGS_ABSOLUTE = 5000

    @api.model
    def _throttle_pairings(self, ip_hash):
        """True when another pairing request may be opened right now.

        🔴 The ``start`` route is public and creates a row: this is the brake.
        Expired requests are dropped first, so a quiet instance never refuses
        over rows that are already dead.
        """
        self._gc_pairings()
        Pending = self.sudo().with_context(active_test=False)
        # ⚠️ « In flight » is read on ``pending_code``, not on ``delivered``:
        # a row whose expiry is NULL would otherwise count for ever and never
        # be collected, since ``_gc_pairings`` only looks at expired ones.
        base = [("pending_code", "!=", False)]
        if Pending.search_count(base) >= self.MAX_PAIRINGS_ABSOLUTE:
            _logger.warning(
                "Desktop pairing: refused, %s requests already in flight.",
                self.MAX_PAIRINGS_ABSOLUTE)
            return False
        if ip_hash and Pending.search_count(
                base + [("ip_hash", "=", ip_hash)]) >= self.MAX_PAIRINGS_PER_CALLER:
            _logger.info("Desktop pairing: refused, caller already has %s "
                         "requests in flight.", self.MAX_PAIRINGS_PER_CALLER)
            return False
        return True

    @api.model
    def _start_pairing(self, app, name=None, version=None, ip_hash=None):
        """Open a pairing request. Returns (device, pairing_code, device_secret).

        No user yet: the row gets its owner when somebody approves it in a
        browser. Until then it is a request, not an access.
        """
        if app not in APPS:
            return self.browse(), None, None
        pairing_code = secrets.token_urlsafe(24)
        device_secret = secrets.token_urlsafe(32)
        device = self.sudo().with_context(active_test=False).create({
            # No owner yet: approval sets it. Until then the row is a request,
            # and ``_resolve`` cannot return it (no delivered token).
            "name": (name or "Blue Fox OS workstation")[:80],
            "app": app,
            "app_version": (version or "")[:40] or False,
            "pending_code": pairing_code,
            "pending_expiry":
                fields.Datetime.now() + timedelta(seconds=PAIRING_TTL_SECONDS),
            "device_secret_hash": self._hash(device_secret),
            "ip_hash": ip_hash or False,
            # A pairing request is not a device yet: it must not show up in
            # « My devices » as if it were paired.
            "active": False,
        })
        return device, pairing_code, device_secret

    @api.model
    def _pending(self, pairing_code):
        """The live pairing request behind this code, or an empty recordset."""
        if not pairing_code:
            return self.browse()
        device = self.sudo().with_context(active_test=False).search(
            [("pending_code", "=", pairing_code), ("delivered", "=", False)],
            limit=1)
        if not device:
            return self.browse()
        if not device.pending_expiry \
                or device.pending_expiry < fields.Datetime.now():
            # Dropped, not left in place: an expired request replayed until the
            # right browser shows up would give a second chance to whoever
            # found the URL.
            device.unlink()
            return self.browse()
        return device

    def _approve(self, user_id):
        """Bind this request to the person who just allowed it in a browser."""
        self.ensure_one()
        self.sudo().write({"user_id": user_id, "approved": True})

    def _refuse(self):
        """« Deny » on the consent page: the request is gone, not merely idle."""
        self.ensure_one()
        self.sudo().unlink()

    def _deliver(self, device_secret):
        """Hand over the bearer token, once, to the widget that started this.

        Returns the clear token, or None when the secret does not match.
        """
        self.ensure_one()
        device = self.sudo()
        if not device.approved or device.delivered:
            return None
        # Constant time: the device secret compares like a secret.
        if not device_secret or not secrets.compare_digest(
                self._hash(device_secret), device.device_secret_hash or ""):
            _logger.warning(
                "Desktop pairing: delivery refused, device secret missing or "
                "wrong (request %s)", device.id)
            # The whole request dies: a poll with the wrong secret is either a
            # bug or somebody else holding the pairing code.
            device.unlink()
            return None
        raw = secrets.token_urlsafe(32)
        device.write({
            "token_hash": self._hash(raw),
            "delivered": True,
            "active": True,
            "pending_code": False,
            "pending_expiry": False,
            "device_secret_hash": False,
            # The caller's fingerprint served the brake; the pairing is over.
            "ip_hash": False,
            "last_seen": fields.Datetime.now(),
        })
        return raw

    # ------------------------------------------------------------------
    # Recognition
    # ------------------------------------------------------------------
    @api.model
    def _resolve(self, token):
        """Return the active device carrying this bearer token (or empty).

        The device's *user* must still be active and internal too. Tokens do
        not expire on a clock, so without this someone who leaves keeps their
        notes on a workstation: deactivating the account is the one gesture
        everybody actually performs on departure, so it has to be the gesture
        that closes this door as well.
        """
        if not token:
            return self.browse()
        device = self.sudo().search(
            [("token_hash", "=", self._hash(token)),
             ("active", "=", True), ("delivered", "=", True)],
            limit=1)
        if not device:
            return self.browse()
        if not device.user_id.active or device.user_id.share:
            _logger.info(
                "Desktop pairing: token refused — user %s is no longer an "
                "active internal user.", device.user_id.login)
            return self.browse()
        vu = device.last_seen or device.create_date
        if vu and vu < fields.Datetime.now() - timedelta(days=TOKEN_IDLE_DAYS):
            _logger.info(
                "Desktop pairing: token expired after %s days without a call "
                "(device %s), revoked.", TOKEN_IDLE_DAYS, device.id)
            # ⚠️ L'empreinte part avec : c'est ce que font les deux gestes
            # « Révoquer », et trois chemins qui prétendent la même chose
            # doivent laisser le même enregistrement.
            #
            # ⚠️ Dans la transaction de l'appelant, comme le patron audité du
            # mobile — et c'est SÛR ici, contrairement au battement. Si cette
            # transaction est annulée, l'écriture est perdue mais la PORTE
            # RESTE FERMÉE : le refus ci-dessus est recalculé à chaque appel
            # tant que la condition d'inactivité tient. L'écriture n'est qu'un
            # marquage, pas la décision.
            #
            # 🔴 Un curseur séparé a été essayé et retiré : il ne voit pas une
            # ligne non encore validée, donc il pouvait n'affecter AUCUNE ligne
            # — un échec silencieux sur une révocation — et il rendait le geste
            # inéprouvable en essai.
            device.write({"active": False, "token_hash": False})
            return self.browse()
        return device

    def _version_a_ranger(self, version):
        """La version à écrire, ou "" s'il n'y a rien à faire.

        🔴 SÉPARÉE de l'écriture, et pas par goût de la symétrie : l'écriture
        passe par un curseur latéral (patron de `_touch_last_seen`, un
        incident connu), et un curseur latéral **ne voit pas** une ligne que la
        transaction courante n'a pas validée. En `TransactionCase`, l'appareil
        créé par l'essai n'existe donc pas pour lui : son UPDATE n'affecterait
        aucune ligne, et l'essai mesurerait zéro quoi qu'il arrive. C'est le
        piège payé à l'essai sur la révocation d'un jeton périmé.

        La DÉCISION, elle, s'éprouve : c'est ici qu'elle vit.
        """
        self.ensure_one()
        propre = (version or "").strip()[:40]
        if not propre or propre == (self.sudo().app_version or ""):
            return ""
        return propre

    def _note_app_version(self, version):
        """Ranger la version que le widget annonce, si elle a changé.

        🔴 Pourquoi : `app_version` n'était écrit qu'à la CRÉATION, donc
        « Mes appareils » affichait pour toujours la version qui avait apparié
        le poste : 1.0.5 affiché pendant que le disque en portait 1.5.0, et ce
        champ a servi de preuve à ce qu'il ne pouvait pas établir. Un champ
        figé qui a l'air vivant est pire qu'un champ absent.

        ⚠️ Même patron que `_touch_last_seen`, et pour la même raison : hors
        de la transaction de la requête, et seulement quand
        la valeur CHANGE. Une version ne change qu'à une mise à jour, donc cette
        écriture est rare — mais l'écrire à chaque appel ferait tomber deux
        appels simultanés du même poste sur « could not serialize access ».
        """
        self.ensure_one()
        propre = self._version_a_ranger(version)
        if not propre:
            return False
        with self.pool.cursor() as cr:
            cr.execute(
                """UPDATE bf_desktop_device SET app_version = %s
                   WHERE id = %s AND coalesce(app_version, '') <> %s""",
                (propre, self.id, propre))
            return bool(cr.rowcount)

    def _touch_last_seen(self):
        """Note that the device just spoke — outside the request transaction.

        🔴 Writing ``last_seen`` through the ORM on every authenticated call
        puts an UPDATE of the device row inside the transaction of EVERY
        request. Two simultaneous calls from the same workstation then write
        the same row at the same time, and under REPEATABLE READ the second
        fails: « could not serialize access due to concurrent update ». The
        mobile half paid for this already; this is the same cure.

        Returns True when the heartbeat was written.
        """
        self.ensure_one()
        now = fields.Datetime.now()
        seen = self.sudo().last_seen
        if seen and (now - seen) < timedelta(seconds=HEARTBEAT_SECONDS):
            return False
        stale_before = now - timedelta(seconds=HEARTBEAT_SECONDS)
        ecrit = False
        try:
            with self.env.registry.cursor() as cr:
                # SKIP LOCKED: a heartbeat already running in another call is
                # neither awaited nor disputed. The cursor is fresh, so a
                # heartbeat already committed is read here and the threshold
                # filters it out.
                cr.execute(
                    """
                    UPDATE bf_desktop_device SET last_seen = %s
                     WHERE id IN (
                        SELECT id FROM bf_desktop_device
                         WHERE id = %s
                           AND (last_seen IS NULL OR last_seen < %s)
                         FOR UPDATE SKIP LOCKED)
                    """,
                    (now, self.id, stale_before),
                )
                # ⚠️ SKIP LOCKED peut n'avoir rien écrit : dire « fait » alors
                # que la ligne était prise ferait mentir l'appelant et les
                # essais.
                ecrit = bool(cr.rowcount)
            if ecrit:
                self.invalidate_recordset(["last_seen"])
            return ecrit
        except OperationalError:
            # A heartbeat is never worth failing a request over.
            _logger.debug("Desktop pairing: heartbeat skipped (device %s)", self.id)
            return False

    # ------------------------------------------------------------------
    # Housekeeping
    # ------------------------------------------------------------------
    @api.model
    def _gc_pairings(self):
        """Drop pairing requests nobody approved. Called by the daily cron of
        whatever installs one; safe to call by hand."""
        # ⚠️ Les deux formes : échéance dépassée, ET échéance absente. Une
        # ligne sans échéance n'était jamais ramassée et comptait pour
        # toujours contre le frein.
        stale = self.sudo().with_context(active_test=False).search([
            ("delivered", "=", False),
            "|",
            ("pending_expiry", "=", False),
            ("pending_expiry", "<", fields.Datetime.now()),
        ])
        count = len(stale)
        stale.unlink()
        return count

    def action_revoke(self):
        """« Revoke » in « My devices »: the token stops working now."""
        for device in self:
            if device.user_id != self.env.user and not self.env.is_admin():
                raise AccessError(_("This device belongs to somebody else."))
        self.sudo().write({"active": False, "token_hash": False})
        return True
