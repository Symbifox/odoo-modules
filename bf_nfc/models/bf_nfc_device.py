"""L'appareil apparié, et le code à usage unique qui l'apparie.

Le patron est celui de ``bf.otp.device``, lui-même repris de Symbifox Comms.
⚠️ Il est **recopié plutôt que partagé** : ``bf_nfc`` ne dépend d'aucun autre
module maison, et doit pouvoir se poser chez un locataire qui n'a ni messages,
ni courriel, ni coffre de tokens. Une pastille collée sur une porte n'a pas à
exiger une boîte de courriel.

🔴 **Le jeton est rangé EMPREINTÉ, jamais en clair.** Une base volée, un vidage
de table, une sauvegarde qui traîne : dans les trois cas, ce qu'on y trouve ne
rejoue pas. ``bf.otp.device`` garde encore le sien en clair ; on ne recopie pas
ce défaut-là.

🔴 Ce qu'un jeton de pastille ouvre : les gestes que la personne pourrait faire
elle-même, et rien de plus. Chaque geste s'exécute avec SES droits. Perdre ce
jeton ne donne donc pas plus que perdre son mot de passe, et ça se révoque d'un
clic depuis « Mes appareils ».
"""
import base64
import hashlib
import logging
import secrets
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)

# Le code d'appariement ne sert qu'à traverser le navigateur. Court, parce
# qu'il ne doit pas survivre à l'aller-retour.
DUREE_CODE_MINUTES = 5
PLAFOND_APPAREILS = 10
# Sans un seul appel pendant ce temps, l'appareil est désactivé. Il reste
# visible dans « Mes appareils », marqué révoqué.
JOURS_INACTIVITE = 180
# Ce qu'une désactivation efface, dans le même écrit. Voir `write`.
CHAMPS_DU_JETON = ("token_hash", "pending_code", "pending_code_expiry", "pkce_challenge")


class BfNfcDevice(models.Model):
    _name = "bf.nfc.device"
    _description = "Appareil apparié pour les pastilles NFC"
    _rec_name = "name"
    _order = "last_seen desc, id desc"

    user_id = fields.Many2one(
        "res.users", string="Personne", required=True, ondelete="cascade",
        index=True)
    name = fields.Char(string="Appareil", default="Appareil Android")
    platform = fields.Char(default="android")
    app_version = fields.Char(string="Version de l'application")

    # ⚠️ `groups` interdit la lecture par l'interface, y compris à son
    # propriétaire : rien dans l'application web n'a besoin de voir ça, et un
    # champ affiché finit par être copié quelque part.
    token_hash = fields.Char(string="Empreinte du jeton", copy=False, index=True,
                             groups="base.group_system")
    pending_code = fields.Char(string="Code d'appariement", copy=False,
                               index=True, groups="base.group_system")
    pending_code_expiry = fields.Datetime(copy=False, groups="base.group_system")
    # 🔴 Le défi PKCE, et la raison d'être de tout ce mécanisme : un schéma
    # d'application personnalisé n'est PAS exclusif sur Android. Une autre
    # application peut déclarer le même et recevoir le code d'appariement à
    # notre place. Sans PKCE elle l'échangerait contre un jeton porteur. Avec,
    # le code intercepté ne vaut rien : l'échange exige un vérificateur que
    # seule l'application qui a lancé l'appariement possède.
    pkce_challenge = fields.Char(string="Défi PKCE", copy=False,
                                 groups="base.group_system")

    active = fields.Boolean(default=True)
    last_seen = fields.Datetime(string="Vu la dernière fois")

    _sql_constraints = [
        ("token_hash_uniq", "unique(token_hash)", "Cette empreinte existe déjà."),
    ]

    # ------------------------------------------------------------------
    def write(self, vals):
        """Deux gestes que l'écriture refuse ou complète, quel que soit l'appelant.

        Relevés par une relecture adverse du modèle.

        🔴 **La personne d'un appareil ne change jamais** (sauf superutilisateur).
        Le groupe de gestion avait l'écriture sur ce modèle : un gestionnaire des
        pastilles pouvait rattacher son propre téléphone apparié à
        l'administrateur, puis jouer « Exécuter une action » avec les droits de
        l'administrateur, avec un jeton qu'il détient.

        🔴 **Désactiver efface le jeton et l'appariement.** Une révocation qui ne
        faisait que baisser ``active`` se défaisait d'un clic sur la bascule, et
        le jeton du téléphone révoqué revivait. La déconnexion, le retrait depuis
        « Mes appareils » et la désactivation pour inactivité passent tous par
        ici. L'effacement se fait en sudo : ces champs sont réservés à
        l'administration, et la personne qui révoque n'a pas à pouvoir les lire.
        """
        if "user_id" in vals and not self.env.su:
            nouvel = vals["user_id"]
            if any(appareil.user_id.id != nouvel for appareil in self):
                raise AccessError(_(
                    "La personne d'un appareil apparié ne se change pas. "
                    "Retirez l'appareil et appariez-le de nouveau."))
        resultat = super().write(vals)
        if "active" in vals and not vals["active"]:
            super(BfNfcDevice, self.sudo()).write(dict.fromkeys(CHAMPS_DU_JETON, False))
        return resultat

    @api.model
    def _hash_token(self, token):
        return hashlib.sha256((token or "").encode("utf-8")).hexdigest()

    @api.model
    def _issue_pending(self, user_id, name=None, platform="android", challenge=None):
        """Crée un appareil en attente et rend son code à usage unique."""
        actifs = self.sudo().search_count([
            ("user_id", "=", user_id), ("active", "=", True),
            ("token_hash", "!=", False)])
        if actifs >= PLAFOND_APPAREILS:
            raise UserError(_(
                "Trop d'appareils appariés (%d). Retirez-en un depuis le site.",
                actifs))
        code = secrets.token_urlsafe(24)
        # ⚠️ Né INACTIF : un appariement jamais terminé n'est pas un appareil.
        # Actif, il paraissait dans « Mes appareils » ; c'est l'échange qui l'active.
        self.sudo().create({
            "active": False,
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
        remplissage. La méthode « plain » de la norme n'est pas acceptée : elle
        laisserait passer un défi égal au vérificateur, donc ne protégerait de
        rien.
        """
        if not verificateur:
            return False
        condense = hashlib.sha256(verificateur.encode("utf-8")).digest()
        calcule = base64.urlsafe_b64encode(condense).decode().rstrip("=")
        return secrets.compare_digest(calcule, (attendu or "").strip())

    @api.model
    def _exchange(self, code, verificateur=None):
        """Échange le code contre un jeton porteur, une seule fois.

        Rend ``(appareil, jeton_en_clair)`` ou ``(vide, None)``. Le jeton en
        clair ne repasse jamais par la base : c'est la seule et unique fois
        qu'il existe quelque part.

        🔴 Le code est effacé dans le même écrit que la pose de l'empreinte.
        Le laisser en place ferait d'un code resté dans un historique de
        navigateur une clé réutilisable.
        """
        vide = self.sudo().browse()
        if not code:
            return vide, None
        appareil = self.sudo().with_context(active_test=False).search(
            [("pending_code", "=", code), ("token_hash", "=", False)], limit=1)
        if not appareil:
            return vide, None
        expiration = appareil.pending_code_expiry
        if not expiration or expiration < fields.Datetime.now():
            appareil.unlink()
            return vide, None
        if not self._verifie_pkce(appareil.pkce_challenge, verificateur):
            # ⚠️ L'appareil en attente est JETÉ, pas laissé en place : sinon un
            # code intercepté pourrait être présenté en boucle jusqu'à ce que la
            # bonne application arrive.
            _logger.warning("Pastilles : échange refusé, vérificateur PKCE absent ou faux")
            appareil.unlink()
            return vide, None
        jeton = secrets.token_urlsafe(48)
        appareil.write({
            "token_hash": self._hash_token(jeton),
            "pending_code": False,
            "pending_code_expiry": False,
            "pkce_challenge": False,
            "active": True,
            "last_seen": fields.Datetime.now(),
        })
        return appareil, jeton

    @api.model
    def _resolve(self, token):
        """L'appareil derrière un jeton porteur, ou un recordset vide.

        ⚠️ L'usager doit encore être un interne ACTIF. Archiver le compte est
        le seul geste que tout le monde fait au départ d'un employé, et il ne
        dit rien d'un jeton émis des mois plus tôt. Même garde que les autres
        registres de la maison.
        """
        if not token:
            return self.sudo().browse()
        appareil = self.sudo().search([
            ("token_hash", "=", self._hash_token(token)),
            ("active", "=", True),
        ], limit=1)
        if not appareil:
            return self.sudo().browse()
        if not appareil.user_id.active:
            return self.sudo().browse()
        limite = fields.Datetime.now() - timedelta(days=JOURS_INACTIVITE)
        if appareil.last_seen and appareil.last_seen < limite:
            appareil.write({"active": False})
            _logger.info("Pastilles : appareil %s désactivé pour inactivité", appareil.id)
            return self.sudo().browse()
        return appareil

    def _touch_last_seen(self):
        """Au plus une écriture la minute : un tapotement n'est pas un journal."""
        self.ensure_one()
        maintenant = fields.Datetime.now()
        if self.last_seen and (maintenant - self.last_seen).total_seconds() < 60:
            return
        self.sudo().write({"last_seen": maintenant})

    @api.model
    def _purger_codes_perimes(self):
        """Retire les appariements jamais terminés.

        ⚠️ Ne touche QUE les appareils sans jeton : un appareil apparié n'a plus
        de code, et le confondre avec un appariement abandonné déconnecterait
        des téléphones qui fonctionnent.
        """
        perimes = self.sudo().with_context(active_test=False).search([
            ("token_hash", "=", False),
            ("pending_code_expiry", "<", fields.Datetime.now()),
        ])
        if perimes:
            _logger.info("Pastilles : %d appariement(s) abandonné(s) retiré(s)",
                         len(perimes))
            perimes.unlink()
