"""L'appareil mobile apparié, et le code à usage unique qui l'apparie.

Le patron est celui de `bf.email.mobile.device`, éprouvé sur Symbifox Comms.
⚠️ Il est **recopié plutôt que partagé** : `bf_otp` ne dépend volontairement
d'aucun autre module maison, et surtout pas d'un module de courriel. Le coffre
de graines ne doit pas partager de rayon d'explosion avec le reste.

🔴 Ce que le jeton porteur donne, et ce qu'il ne donne pas : il ouvre l'accès
aux enregistrements de la personne, donc à du **chiffré**. Il ne donne aucune
graine, parce qu'il n'en existe aucune côté serveur. Perdre ce jeton n'ouvre
pas le coffre : il faut encore la phrase de passe.

🔴 Et ce qui le FERME. Les autres registres d'appareils
refusent le jeton dès que l'usager Odoo est archivé, gardent une
empreinte plutôt que le jeton, et expirent après un long silence. Celui-ci
n'avait aucune des trois. Un coffre de tokens qui survit au départ de la
personne est exactement l'inverse de ce qu'il vend.
"""
import base64
import hashlib
import logging
import secrets
from datetime import timedelta

from psycopg2 import OperationalError

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)

# Le code d'appariement ne sert qu'à traverser le navigateur. Court, parce
# qu'il ne doit pas survivre à l'aller-retour.
DUREE_CODE_MINUTES = 5
PLAFOND_APPAREILS = 10

# Un appareil qui n'a rien demandé depuis ce délai perd son jeton : un
# téléphone perdu ou remplacé ne garde pas une porte ouverte pour toujours.
# Même valeur que ``sms.archive.mobile.device`` et ``bf.email.mobile.device``.
JOURS_INACTIVITE = 90

# Fraîcheur en deçà de laquelle « vu la dernière fois » n'est pas réécrit.
HEARTBEAT_SECONDS = 60


class BfOtpDevice(models.Model):
    _name = "bf.otp.device"
    _description = "Appareil apparié au coffre de tokens"
    _rec_name = "name"
    _order = "last_seen desc, id desc"

    user_id = fields.Many2one(
        "res.users", string="Personne", required=True, ondelete="cascade",
        index=True)
    name = fields.Char(string="Appareil", default="Appareil Android")
    platform = fields.Char(default="android")
    app_version = fields.Char(string="Version de l'application")

    # ⚠️ `groups` interdit la lecture du jeton par l'interface, y compris à son
    # propriétaire : rien dans l'application web n'a besoin de le voir, et un
    # champ affiché finit par être copié quelque part.
    # ⚠️ Depuis la 18.0.12.0.0 le jeton n'est plus gardé en clair. Ce champ ne
    # le porte que le temps de le remettre à l'application (``_seal`` l'efface
    # aussitôt après la réponse) ; c'est ``token_hash`` qui reconnaît
    # l'appareil. Un dump de base ne livre donc plus de jeton utilisable.
    device_token = fields.Char(string="Token porteur", copy=False, index=True,
                               groups="base.group_system")
    token_hash = fields.Char(string="Empreinte du jeton", copy=False, index=True,
                             groups="base.group_system")
    pending_code = fields.Char(string="Code d'appariement", copy=False,
                               index=True, groups="base.group_system")
    pending_code_expiry = fields.Datetime(copy=False,
                                          groups="base.group_system")
    # 🔴 Le défi PKCE, et la raison d'être de tout ce mécanisme : un schéma
    # d'application personnalisé n'est PAS exclusif sur Android. Une autre
    # application peut déclarer le même et recevoir le code d'appariement à la
    # place de la nôtre. Sans PKCE elle l'échangerait contre un jeton porteur,
    # donc contre la liste des services protégés et les chiffrés du coffre.
    # Avec, le code intercepté ne vaut rien : l'échange exige un vérificateur
    # que seule l'application qui a lancé l'appariement possède, et qui n'est
    # jamais sorti d'elle.
    pkce_challenge = fields.Char(string="Défi PKCE", copy=False,
                                 groups="base.group_system")

    active = fields.Boolean(default=True)
    last_seen = fields.Datetime(string="Vu la dernière fois")

    _sql_constraints = [
        ("device_token_uniq", "unique(device_token)",
         "Ce jeton porteur existe déjà."),
        ("token_hash_uniq", "unique(token_hash)",
         "Cette empreinte de jeton existe déjà."),
    ]

    # Champs qu'aucune écriture hors ``sudo`` ne peut poser. Un usager du coffre
    # a create/write sur ses propres appareils (ACL + règle « les siens
    # seulement ») : sans cette garde, il pouvait s'inscrire un appareil avec un
    # jeton de son choix. La restriction ``groups`` des champs le bloquait déjà,
    # mais une garde qui se lit dans le modèle vaut mieux qu'une garde qui se
    # déduit d'un attribut. Parité avec les autres registres d'appareils.
    _PROTECTED_FIELDS = ("device_token", "token_hash", "pending_code",
                         "pending_code_expiry", "pkce_challenge", "user_id")

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
        return super().write(vals)

    @api.model
    def _issue_pending(self, user_id, name=None, platform="android",
                       challenge=None):
        """Crée un appareil en attente et rend son code à usage unique."""
        # ⚠️ Un plafond par personne. Rien ne bornait le nombre d'appareils
        # appariés : ni la mémoire d'une personne (dix téléphones ?), ni un
        # attaquant en possession d'une session, qui pourrait en semer sans fin.
        # 🔴 Compter sur ``token_hash``, pas sur ``device_token`` : depuis que
        # le jeton est scellé, un appareil apparié n'a PLUS de clair, et le
        # plafond retombait à zéro sans jamais mordre.
        actifs = self.sudo().search_count([
            ("user_id", "=", user_id), ("active", "=", True),
            ("token_hash", "!=", False)])
        if actifs >= PLAFOND_APPAREILS:
            raise UserError(
                "Trop d'appareils appariés (%d). Retirez-en un depuis le site."
                % actifs)
        code = secrets.token_urlsafe(24)
        self.sudo().create({
            "user_id": user_id,
            "name": name or "Appareil Android",
            "platform": platform,
            "pkce_challenge": challenge or False,
            "pending_code": code,
            "pending_code_expiry": (
                fields.Datetime.now() + timedelta(minutes=DUREE_CODE_MINUTES)),
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
        """Échange le code contre un jeton porteur, une seule fois.

        🔴 Le code est effacé dans le même écrit que la pose du jeton. Le
        laisser en place ferait d'un code intercepté dans un historique de
        navigateur une clé réutilisable.

        🔴 Et le code seul ne suffit PAS : il faut le vérificateur PKCE. Un
        schéma d'application personnalisé n'étant pas exclusif sur Android,
        c'est la seule chose qui distingue l'application qui a lancé
        l'appariement de celle qui a intercepté sa réponse.
        """
        if not code:
            return False
        appareil = self.sudo().search([("pending_code", "=", code)], limit=1)
        if not appareil:
            return False
        expiration = appareil.pending_code_expiry
        if not expiration or expiration < fields.Datetime.now():
            # Un code périmé se jette avec son appareil : il n'a jamais servi.
            appareil.unlink()
            return False
        if not self._verifie_pkce(appareil.pkce_challenge, verificateur):
            # ⚠️ L'appareil en attente est JETÉ, pas laissé en place : sinon un
            # code intercepté pourrait être présenté en boucle jusqu'à ce que
            # la bonne application arrive, et l'attaquant réessaierait.
            _logger.warning(
                "Coffre de tokens : échange refusé, vérificateur PKCE absent "
                "ou faux")
            appareil.unlink()
            return False
        brut = secrets.token_urlsafe(48)
        appareil.write({
            "device_token": brut,
            "token_hash": self._hash_token(brut),
            "pending_code": False,
            "pending_code_expiry": False,
            "pkce_challenge": False,
            "last_seen": fields.Datetime.now(),
        })
        return appareil

    @staticmethod
    def _hash_token(brut):
        """Empreinte SHA-256 (hex) d'un jeton porteur.

        Sans sel : le jeton est un aléa de 384 bits, une table arc-en-ciel n'a
        aucune prise dessus. Même forme que les autres registres d'appareils.
        """
        return hashlib.sha256((brut or "").encode("utf-8")).hexdigest()

    def _seal(self):
        """Efface le jeton en clair : l'application l'a reçu, l'empreinte suffit."""
        self.sudo().write({"device_token": False})

    def _touch_last_seen(self):
        """Note que l'appareil vient de parler, hors de la transaction de la requête.

        🔴 Réécrire ``last_seen`` par l'ORM à chaque appel authentifié posait un
        UPDATE de la ligne d'appareil dans la transaction de CHAQUE requête. Deux
        appels simultanés du même téléphone échouaient alors sous REPEATABLE READ
        (« could not serialize access due to concurrent update »), donc un 500 et
        un geste perdu. Les autres registres d'appareils avaient déjà corrigé ce
        défaut ; celui-ci écrivait encore en direct depuis ``_authentifie``.

        Un battement par minute, dans son propre curseur validé sur-le-champ.
        Retourne True si le battement a été écrit.
        """
        self.ensure_one()
        maintenant = fields.Datetime.now()
        vu = self.sudo().last_seen
        if vu and (maintenant - vu) < timedelta(seconds=HEARTBEAT_SECONDS):
            return False
        avant = maintenant - timedelta(seconds=HEARTBEAT_SECONDS)
        try:
            with self.env.registry.cursor() as cr:
                # SKIP LOCKED : un battement déjà en cours ailleurs n'est ni
                # attendu ni disputé.
                cr.execute(
                    "UPDATE bf_otp_device SET last_seen = %s "
                    "WHERE id = (SELECT id FROM bf_otp_device "
                    "            WHERE id = %s "
                    "              AND (last_seen IS NULL OR last_seen < %s) "
                    "            FOR UPDATE SKIP LOCKED)",
                    (maintenant, self.id, avant),
                    log_exceptions=False,
                )
                ecrit = cr.rowcount == 1
        except OperationalError:
            return False
        self.invalidate_recordset(["last_seen"])
        return ecrit

    @api.model
    def _resolve(self, token):
        """L'appareil derrière un jeton porteur, ou un recordset vide.

        Reconnu par son EMPREINTE. Une ligne d'avant la 18.0.12.0.0 porte encore
        le jeton en clair sans empreinte : on l'accepte une fois, on l'empreinte
        et on efface le clair, sans que le téléphone ait à se réapparier.

        ⚠️ L'usager doit encore être un interne ACTIF. Archiver le compte est le
        seul geste que tout le monde fait au départ d'un employé, et il ne dit
        rien d'un jeton émis des mois plus tôt : sans ce test, le téléphone
        gardait le coffre de tokens de la personne partie.

        ⚠️ Et il expire : ``JOURS_INACTIVITE`` sans un seul appel, l'appareil est
        désactivé (il reste visible dans « Mes appareils », révoqué).
        """
        vide = self.sudo().browse()
        if not token:
            return vide
        Appareil = self.sudo()
        appareil = Appareil.search(
            [("token_hash", "=", self._hash_token(token)), ("active", "=", True)],
            limit=1)
        if not appareil:
            ancien = Appareil.search(
                [("device_token", "=", token), ("token_hash", "=", False),
                 ("active", "=", True)], limit=1)
            if ancien:
                ancien.write({"token_hash": self._hash_token(token),
                              "device_token": False})
                appareil = ancien
        if not appareil:
            return vide
        if not appareil.user_id.active or appareil.user_id.share:
            _logger.info(
                "Coffre de tokens : jeton refusé, l'usager %s n'est plus un "
                "interne actif.", appareil.user_id.login)
            return vide
        vu = appareil.last_seen or appareil.create_date
        if vu and vu < fields.Datetime.now() - timedelta(days=JOURS_INACTIVITE):
            _logger.info(
                "Coffre de tokens : jeton expiré après %s jours sans appel "
                "(appareil %s), révoqué.", JOURS_INACTIVITE, appareil.id)
            appareil.write({"active": False})
            return vide
        return appareil

    @api.model
    def _purger_codes_perimes(self):
        """Retire les appariements jamais terminés.

        ⚠️ Ne touche QUE les appareils sans jeton : un appareil apparié n'a
        plus de code, et le confondre avec un appariement abandonné
        déconnecterait des téléphones qui fonctionnent.
        """
        # 🔴 ``token_hash`` explicitement faux : depuis le scellement, un
        # appareil APPARIÉ n'a plus de ``device_token`` non plus, et le seul
        # critère qui le distinguait d'un appariement abandonné a disparu.
        perimes = self.sudo().search([
            ("device_token", "=", False),
            ("token_hash", "=", False),
            ("pending_code_expiry", "<", fields.Datetime.now()),
        ])
        if perimes:
            _logger.info("Coffre de tokens : %d appariement(s) abandonné(s) "
                         "retiré(s)", len(perimes))
            perimes.unlink()
