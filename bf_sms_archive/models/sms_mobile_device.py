"""Appareil mobile (app Android native) lié à la messagerie SMS.

Chaque enregistrement = une installation de l'app native, authentifiée par un
``device_token`` porteur (Bearer) émis à la connexion et un ``fcm_token`` pour la
notification push (Firebase Cloud Messaging). Séparé de ``sms.archive.push.subscription``
(qui, lui, sert le Web Push du navigateur/PWA).
"""
import base64
import hashlib
import logging
import secrets
from datetime import timedelta

from psycopg2 import OperationalError

from odoo import api, fields, models
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)

CODE_TTL_MINUTES = 3  # durée de vie du code d'échange unique
# Un appareil qui n'a rien demandé depuis ce délai perd son jeton : un
# téléphone perdu ou remplacé ne garde pas une porte ouverte pour toujours.
# Audit du 2026-09-08 (S-M2).
TOKEN_IDLE_DAYS = 90

# Fraîcheur en deçà de laquelle « vu la dernière fois » n'est pas réécrit.
# Voir ``_touch_last_seen`` : un battement par minute suffit, et c'est ce
# qui laisse deux appels simultanés du même téléphone sans rien à se
# disputer.
HEARTBEAT_SECONDS = 60


class SmsMobileDevice(models.Model):
    _name = "sms.archive.mobile.device"
    _description = "Appareil mobile — messagerie SMS"
    _rec_name = "name"
    _order = "last_seen desc, id desc"

    user_id = fields.Many2one(
        "res.users", string="Utilisateur", required=True, ondelete="cascade",
        index=True,
    )
    name = fields.Char(string="Appareil", default="Appareil Android")
    # ⚠️ Depuis la 5.14.0 le jeton n'est plus gardé en clair. ``device_token``
    # ne porte le jeton que le temps de le remettre à l'app (``_seal`` l'efface
    # aussitôt après la réponse) ; c'est ``token_hash`` qui sert à reconnaître
    # l'appareil. Un dump de base ne livre donc plus de jeton utilisable.
    # Audit du 2026-09-08 (S-M2).
    device_token = fields.Char(
        string="Jeton d'appareil", index=True, copy=False,
        groups="bf_sms_archive.group_sms_manager",
    )
    token_hash = fields.Char(
        string="Empreinte du jeton", index=True, copy=False,
        groups="bf_sms_archive.group_sms_manager",
    )
    fcm_token = fields.Char(string="Jeton FCM (legacy)", index=True)
    push_endpoint = fields.Char(
        string="Endpoint UnifiedPush",
        help="URL d'endpoint UnifiedPush (ntfy) vers laquelle pousser les "
             "notifications de cet appareil.")
    # Clés de l'abonnement WebPush (RFC 8291), en base64url : le point P-256 non
    # compressé de l'appareil (65 octets) et son secret d'authentification (16
    # octets). Présentes, les poussées partent chiffrées ; absentes (app ≤
    # 2.41.0), en clair. Elles appartiennent à l'ENDPOINT : ``write`` les
    # efface dès que l'endpoint change sans elles. Audit du 2026-09-08 (C-M3).
    push_p256dh = fields.Char(
        string="Clé publique WebPush", copy=False,
        groups="bf_sms_archive.group_sms_manager",
    )
    push_auth = fields.Char(
        string="Secret WebPush", copy=False,
        groups="bf_sms_archive.group_sms_manager",
    )
    platform = fields.Char(default="android")
    app_version = fields.Char()
    active = fields.Boolean(default=True)
    last_seen = fields.Datetime(string="Vu la dernière fois")
    # Code unique à usage unique pour l'échange web-login → jeton (le vrai jeton
    # ne transite jamais dans une URL/deep-link).
    pending_code = fields.Char(index=True, copy=False,
                               groups="bf_sms_archive.group_sms_manager")
    pending_code_expiry = fields.Datetime()
    # 🔴 Le défi PKCE, et la raison d'être de tout ce mécanisme : un schéma
    # d'application personnalisé n'est PAS exclusif sur Android. Une autre
    # application peut déclarer ``com.bluefoxconsultant.sms://auth`` et recevoir
    # le code d'appariement à la place de la nôtre. Sans PKCE elle l'échangerait
    # contre un jeton porteur, donc contre la messagerie SMS de la personne.
    # Avec, le code intercepté ne vaut rien : l'échange exige un vérificateur
    # que seule l'application qui a lancé l'appariement possède, et qui n'est
    # jamais sorti d'elle.
    #
    # ⚠️ L'allowlist de schémas de redirection ne remplace pas ceci : elle ferme
    # la redirection ouverte CÔTÉ SERVEUR, pas l'interception locale du code sur
    # l'appareil.
    pkce_challenge = fields.Char(string="Défi PKCE", copy=False,
                                 groups="bf_sms_archive.group_sms_manager")

    _sql_constraints = [
        ("device_token_uniq", "unique(device_token)",
         "Ce jeton d'appareil existe déjà."),
    ]

    # Champs qu'aucune écriture hors ``sudo`` ne peut poser : un gestionnaire
    # SMS pouvait sinon créer un appareil pour n'importe quel usager avec un
    # jeton de son choix, et devenir cet usager sur les sept surfaces mobiles.
    # Audit du 2026-09-08 (S-M5).
    # Les clés WebPush aussi : poser les siennes sur l'appareil d'autrui ferait
    # chiffrer ses notifications pour un tiers, qui seul pourrait les lire.
    _PROTECTED_FIELDS = ("device_token", "token_hash", "pending_code",
                         "pending_code_expiry", "pkce_challenge", "user_id",
                         "push_p256dh", "push_auth")
    _PUSH_KEY_FIELDS = ("push_p256dh", "push_auth")

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(
                "Un appareil ne se crée qu'en s'appariant depuis l'application.")
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and any(f in vals for f in self._PROTECTED_FIELDS):
            raise AccessError(
                "Le jeton et l'usager d'un appareil ne se modifient pas à la main.")
        res = super().write(vals)
        # Les clés vont avec l'endpoint. Effacé (déconnexion, endpoint mort ou
        # non public, « Mes appareils ») ou remplacé sans elles, l'endpoint les
        # emporte : des clés orphelines chiffreraient pour un abonnement qui
        # n'existe plus. En sudo, parce que l'effacement vient souvent d'un
        # geste qui n'a, lui, aucun droit sur les clés.
        if "push_endpoint" in vals and not any(f in vals for f in self._PUSH_KEY_FIELDS):
            self.sudo().write({"push_p256dh": False, "push_auth": False})
        return res

    @staticmethod
    def _hash_token(raw):
        """Empreinte SHA-256 (hex) d'un jeton porteur. Sans sel : le jeton
        est un aléa de 256 bits, une table arc-en-ciel n'a pas de prise."""
        return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()

    @api.model
    def _issue(self, user_id, name=None, platform="android"):
        """Émet un nouvel appareil + jeton porteur pour l'utilisateur donné.

        Le jeton en clair reste dans ``device_token`` jusqu'à ``_seal`` : c'est
        le contrôleur qui le remet à l'app, puis scelle."""
        raw = secrets.token_urlsafe(32)
        return self.sudo().create({
            "user_id": user_id,
            "name": (name or "Appareil Android")[:80],
            "device_token": raw,
            "token_hash": self._hash_token(raw),
            "platform": platform,
        })

    def _seal(self):
        """Efface le jeton en clair : l'app l'a reçu, l'empreinte suffit."""
        self.sudo().write({"device_token": False})

    @api.model
    def _gc_pending(self):
        """Jette les appareils dont le code d'échange a expiré sans être
        réclamé (onglet fermé) : ils portaient un jeton vivant que personne ne
        viendra chercher. Appelé à chaque nouvel appariement, ce qui suffit."""
        stale = self.sudo().search([
            ("pending_code", "!=", False),
            ("pending_code_expiry", "<", fields.Datetime.now()),
        ])
        if stale:
            stale.unlink()
        return len(stale)

    @api.model
    def _issue_pending(self, user_id, name=None, platform="android",
                       challenge=None):
        """Émet un appareil + un code d'échange unique (retourné au navigateur).
        Le jeton porteur n'est révélé qu'à l'échange HTTPS."""
        self._gc_pending()
        device = self._issue(user_id, name=name, platform=platform)
        code = secrets.token_urlsafe(24)
        device.write({
            "pending_code": code,
            "pending_code_expiry": fields.Datetime.now() + timedelta(minutes=CODE_TTL_MINUTES),
            "pkce_challenge": challenge or False,
        })
        return code

    @api.model
    def _verifie_pkce(self, attendu, verificateur):
        """Vrai quand le vérificateur correspond au défi enregistré.

        ⚠️ Le défi est le SHA-256 du vérificateur, en base64url sans
        remplissage : la méthode « plain » de la norme n'est pas acceptée ici,
        elle laisserait passer un défi égal au vérificateur et ne protégerait
        de rien.
        """
        if not verificateur:
            return False
        condense = hashlib.sha256(verificateur.encode("utf-8")).digest()
        calcule = base64.urlsafe_b64encode(condense).decode().rstrip("=")
        # Comparaison à temps constant : un défi se compare comme un secret.
        return secrets.compare_digest(calcule, (attendu or "").strip())

    @api.model
    def _exchange(self, code, verificateur=None):
        """Échange un code unique non expiré contre le jeton porteur.

        À usage unique (le code est consommé), et le code seul ne suffit PAS :
        il faut le vérificateur PKCE. Un schéma d'application personnalisé
        n'étant pas exclusif sur Android, c'est la seule chose qui distingue
        l'application qui a lancé l'appariement de celle qui a intercepté sa
        réponse. Retourne l'appareil ou vide.
        """
        if not code:
            return self.browse()
        device = self.sudo().search([("pending_code", "=", code)], limit=1)
        if not device:
            return self.browse()
        # ⚠️ L'appareil en attente est JETÉ, pas laissé en place, dans les deux
        # refus : son jeton porteur n'a jamais été révélé — seul l'échange le
        # rend — et un code présenté en boucle jusqu'à ce que la bonne
        # application arrive donnerait sa chance à celle qui l'a intercepté.
        if not device.pending_code_expiry \
                or device.pending_code_expiry < fields.Datetime.now():
            device.unlink()
            return self.browse()
        if not self._verifie_pkce(device.pkce_challenge, verificateur):
            _logger.warning(
                "Messagerie SMS : échange refusé, vérificateur PKCE absent "
                "ou faux")
            device.unlink()
            return self.browse()
        device.write({
            "pending_code": False,
            "pending_code_expiry": False,
            "pkce_challenge": False,
        })
        return device

    def _touch_last_seen(self):
        """Note que l'appareil vient de parler — hors de la transaction de la requête.

        🔴 Même défaut que ``bf.email.mobile.device`` :
        réécrire ``last_seen`` par l'ORM à chaque appel authentifié posait un
        UPDATE de la ligne d'appareil dans la transaction de CHAQUE requête,
        et deux appels simultanés du même téléphone — archiver toute une
        sélection, c'est UNE requête par fil — faisaient échouer le second
        sous REPEATABLE READ (« could not serialize access due to concurrent
        update »), donc un 500 et un geste annulé.

        Un battement par minute, dans son propre curseur validé sur-le-champ :
        la transaction de la requête ne touche plus la ligne d'appareil, et
        deux battements qui se croisent se règlent ici, en silence.

        Retourne True si le battement a été écrit.
        """
        self.ensure_one()
        now = fields.Datetime.now()
        seen = self.sudo().last_seen
        if seen and (now - seen) < timedelta(seconds=HEARTBEAT_SECONDS):
            return False
        stale_before = now - timedelta(seconds=HEARTBEAT_SECONDS)
        try:
            with self.env.registry.cursor() as cr:
                # SKIP LOCKED : un battement déjà en cours dans un autre
                # appel n'est ni attendu ni disputé, on passe. Le curseur est
                # neuf, donc son instantané date de cette ligne : un battement
                # déjà validé se lit dans ``last_seen`` et le seuil le filtre.
                cr.execute(
                    "UPDATE sms_archive_mobile_device SET last_seen = %s "
                    "WHERE id = (SELECT id FROM sms_archive_mobile_device "
                    "            WHERE id = %s "
                    "              AND (last_seen IS NULL OR last_seen < %s) "
                    "            FOR UPDATE SKIP LOCKED)",
                    (now, self.id, stale_before),
                    log_exceptions=False,
                )
                written = cr.rowcount == 1
        except OperationalError:
            # Il reste la fenêtre entre l'instantané et le verrou : l'autre
            # battement vaut le nôtre, on se tait.
            return False
        self.invalidate_recordset(["last_seen"])
        return written

    @api.model
    def _resolve(self, token):
        """Retourne l'appareil actif correspondant au jeton porteur (ou vide).

        Reconnu par son EMPREINTE. Une ligne d'avant la 5.14.0 porte encore le
        jeton en clair sans empreinte : on l'accepte une fois, on l'empreinte
        et on efface le clair, sans que le téléphone ait à se réapparier.

        ⚠️ L'usager doit encore être un interne ACTIF. Archiver le compte est
        le seul geste que tout le monde fait au départ d'un employé, et il ne
        dit rien d'un jeton émis des mois plus tôt : sans ce test, le
        téléphone gardait les SMS, le mot de passe SIP, les appels sortants,
        les alertes d'hébergement et la dictée. Même garde que
        ``bf.email.mobile.device``. Audit du 2026-09-08 (S-H1).

        ⚠️ Et il expire : ``TOKEN_IDLE_DAYS`` sans un seul appel, l'appareil
        est désactivé (il reste visible dans « Mes appareils », révoqué).
        """
        if not token:
            return self.browse()
        Device = self.sudo()
        device = Device.search(
            [("token_hash", "=", self._hash_token(token)), ("active", "=", True)],
            limit=1)
        if not device:
            legacy = Device.search(
                [("device_token", "=", token), ("token_hash", "=", False),
                 ("active", "=", True)], limit=1)
            if legacy:
                legacy.write({"token_hash": self._hash_token(token),
                              "device_token": False})
                device = legacy
        if not device:
            return self.browse()
        if not device.user_id.active or device.user_id.share:
            _logger.info("sms mobile: jeton refusé — l'utilisateur %s n'est plus "
                         "un interne actif.", device.user_id.login)
            return self.browse()
        vu = device.last_seen or device.create_date
        if vu and vu < fields.Datetime.now() - timedelta(days=TOKEN_IDLE_DAYS):
            _logger.info("sms mobile: jeton expiré après %s jours sans appel "
                         "(appareil %s), révoqué.", TOKEN_IDLE_DAYS, device.id)
            device.write({"active": False})
            return self.browse()
        return device
