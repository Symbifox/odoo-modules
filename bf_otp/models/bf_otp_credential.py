"""Une clé d'accès qui sait ouvrir le coffre, sans jamais le dire au serveur.

Le principe, et pourquoi c'est celui-là plutôt qu'un autre
----------------------------------------------------------
La clé du coffre reste **exactement celle qu'elle était** : dérivée de la phrase
de passe. On n'en change pas, donc **aucune graine n'est ré-encryptée** — ce qui
serait le vrai risque sur un coffre qui en porte déjà cent quarante-quatre.

Ce qu'on range ici est une **copie scellée de cette clé** : ses octets, chiffrés
par un secret que l'extension PRF de WebAuthn dérive de la clé d'accès. Le
serveur reçoit un bloc qu'il ne peut pas ouvrir, un identifiant de clé d'accès
qui n'est pas un secret, et un sel qui n'en est pas un non plus.

Ouvrir devient : toucher le capteur → l'authentificateur rend 32 octets stables
→ ces octets déchiffrent la copie scellée → on a la clé du coffre.

⚠️ **La phrase de passe reste le chemin de secours, et ce n'est pas optionnel.**
Une clé d'accès est liée à une origine et à un appareil : elle ne suit pas d'un
domaine à l'autre, et un appareil se perd. Retirer la phrase transformerait une
commodité en point unique de défaillance.

🔴 **Ce que le serveur peut faire, et qu'il faut avoir en tête** : il ne peut pas
lire le coffre, mais il peut mentir sur la LISTE des clés d'accès — en cacher
une, en proposer une autre. Ça ne lui donne aucune graine (il faudrait
l'authentificateur), mais ça peut empêcher d'ouvrir. Le remède est le même que
partout ici : la phrase de passe, qui ne dépend d'aucune liste.
"""

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class BfOtpCredential(models.Model):
    _name = 'bf.otp.credential'
    _description = "Clé d'accès d'un coffre OTP"
    _order = 'create_date desc'

    vault_id = fields.Many2one(
        'bf.otp.vault', string='Coffre', required=True,
        ondelete='cascade', index=True,
    )
    user_id = fields.Many2one(
        'res.users', related='vault_id.user_id', store=True, index=True,
    )
    name = fields.Char(
        string='Nom', required=True,
        help="Ce que la personne reconnaîtra : « MacBook », « clé jaune ».",
    )
    credential_id = fields.Char(
        string="Identifiant de la clé d'accès", required=True,
        help="Rendu par l'authentificateur, en base64url. Public par nature : "
             "il sert à demander LA bonne clé, il n'ouvre rien.",
    )
    prf_salt = fields.Char(
        string='Sel PRF', required=True,
        help="Entrée fixe de la dérivation PRF. Public : deux coffres qui "
             "partageraient une clé d'accès en tirent des secrets différents.",
    )
    wrapped_secret = fields.Char(
        string='Clé du coffre, scellée', required=True,
        help="Les octets de la clé du coffre, chiffrés par le secret PRF. "
             "Le serveur ne peut pas l'ouvrir.",
    )
    wrapped_iv = fields.Char(string='Vecteur', required=True)
    last_used = fields.Datetime(string='Dernière ouverture')

    _sql_constraints = [
        ('credential_uniq', 'unique(vault_id, credential_id)',
         "Cette clé d'accès est déjà enregistrée sur ce coffre."),
    ]

    @api.constrains('wrapped_secret')
    def _check_not_a_seed(self):
        """Même garde que sur les jetons : ici, tout doit être du chiffré."""
        from .bf_otp_token import _BASE32_NU, _URI_OTPAUTH
        for cred in self:
            v = (cred.wrapped_secret or '').strip()
            if _URI_OTPAUTH.search(v) or _BASE32_NU.match(v):
                raise ValidationError(_(
                    "La clé du coffre est arrivée en clair au serveur. "
                    "Le scellement se fait dans le navigateur, avant l'envoi."
                ))
