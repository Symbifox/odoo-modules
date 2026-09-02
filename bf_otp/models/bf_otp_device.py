"""L'appareil mobile apparié, et le code à usage unique qui l'apparie.

Le patron est celui de `bf.email.mobile.device`, éprouvé sur Symbifox Comms.
⚠️ Il est **recopié plutôt que partagé** : `bf_otp` ne dépend volontairement
d'aucun autre module maison, et surtout pas d'un module de courriel. Le coffre
de graines ne doit pas partager de rayon d'explosion avec le reste.

🔴 Ce que le jeton porteur donne, et ce qu'il ne donne pas : il ouvre l'accès
aux enregistrements de la personne, donc à du **chiffré**. Il ne donne aucune
graine, parce qu'il n'en existe aucune côté serveur. Perdre ce jeton n'ouvre
pas le coffre : il faut encore la phrase de passe.
"""
import logging
import secrets
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Le code d'appariement ne sert qu'à traverser le navigateur. Court, parce
# qu'il ne doit pas survivre à l'aller-retour.
DUREE_CODE_MINUTES = 5


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
    device_token = fields.Char(string="Jeton porteur", copy=False, index=True,
                               groups="base.group_system")
    pending_code = fields.Char(string="Code d'appariement", copy=False,
                               index=True, groups="base.group_system")
    pending_code_expiry = fields.Datetime(copy=False,
                                          groups="base.group_system")

    active = fields.Boolean(default=True)
    last_seen = fields.Datetime(string="Vu la dernière fois")

    _sql_constraints = [
        ("device_token_uniq", "unique(device_token)",
         "Ce jeton porteur existe déjà."),
    ]

    @api.model
    def _issue_pending(self, user_id, name=None, platform="android"):
        """Crée un appareil en attente et rend son code à usage unique."""
        code = secrets.token_urlsafe(24)
        self.sudo().create({
            "user_id": user_id,
            "name": name or "Appareil Android",
            "platform": platform,
            "pending_code": code,
            "pending_code_expiry": (
                fields.Datetime.now() + timedelta(minutes=DUREE_CODE_MINUTES)),
        })
        return code

    @api.model
    def _exchange(self, code):
        """Échange le code contre un jeton porteur, une seule fois.

        🔴 Le code est effacé dans le même écrit que la pose du jeton. Le
        laisser en place ferait d'un code intercepté dans un historique de
        navigateur une clé réutilisable.
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
        appareil.write({
            "device_token": secrets.token_urlsafe(48),
            "pending_code": False,
            "pending_code_expiry": False,
            "last_seen": fields.Datetime.now(),
        })
        return appareil

    @api.model
    def _resolve(self, token):
        """L'appareil derrière un jeton porteur, ou False."""
        if not token:
            return False
        return self.sudo().search(
            [("device_token", "=", token), ("active", "=", True)], limit=1)

    @api.model
    def _purger_codes_perimes(self):
        """Retire les appariements jamais terminés.

        ⚠️ Ne touche QUE les appareils sans jeton : un appareil apparié n'a
        plus de code, et le confondre avec un appariement abandonné
        déconnecterait des téléphones qui fonctionnent.
        """
        perimes = self.sudo().search([
            ("device_token", "=", False),
            ("pending_code_expiry", "<", fields.Datetime.now()),
        ])
        if perimes:
            _logger.info("Coffre de tokens : %d appariement(s) abandonné(s) "
                         "retiré(s)", len(perimes))
            perimes.unlink()
