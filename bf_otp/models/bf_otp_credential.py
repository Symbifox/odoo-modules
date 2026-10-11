"""Une clé d'accès qui sait ouvrir le coffre, sans jamais le dire au serveur.

Le principe, et pourquoi c'est celui-là plutôt qu'un autre
----------------------------------------------------------
La clé du coffre reste **exactement celle qu'elle était** : dérivée de la phrase
de passe. On n'en change pas, donc **aucune graine n'est ré-encryptée** — ce qui
serait le vrai risque sur un coffre qui en porte déjà des centaines.

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
    _description = "OTP vault passkey"
    _order = 'create_date desc'

    vault_id = fields.Many2one(
        'bf.otp.vault', string="Vault", required=True,
        ondelete='cascade', index=True,
    )
    user_id = fields.Many2one(
        'res.users', related='vault_id.user_id', store=True, index=True,
    )
    name = fields.Char(
        string="Name", required=True,
        help="What the person will recognize: \"MacBook\", \"yellow key\".",
    )
    credential_id = fields.Char(
        string="Passkey ID", required=True,
        help="Returned by the authenticator, in base64url. Public by "
             "nature: it is used to request THE right key, it unlocks "
             "nothing.",
    )
    prf_salt = fields.Char(
        string="PRF salt", required=True,
        help="Fixed input of the PRF derivation. Public: two vaults "
             "sharing a passkey derive different secrets from it.",
    )
    wrapped_secret = fields.Char(
        string="Vault key, sealed", required=True,
        help="The vault key bytes, encrypted with the PRF secret. The "
             "server cannot open it.",
    )
    wrapped_iv = fields.Char(string="IV", required=True)
    last_used = fields.Datetime(string="Last unlock")

    _sql_constraints = [
        ('credential_uniq', 'unique(vault_id, credential_id)',
         "This passkey is already registered on this vault."),
    ]


    def write(self, vals):
        """On ne déplace pas une fiche dans le coffre de quelqu'un d'autre.

        Odoo ne rejoue pas les règles d'enregistrement après un `write` : la
        règle voit la fiche de départ, pas le coffre d'arrivée."""
        if not self.env.su and vals.get('vault_id'):
            self.env['bf.otp.vault'].browse(vals['vault_id']).check_access('write')
        return super().write(vals)

    @api.constrains('wrapped_secret')
    def _check_not_a_seed(self):
        """Même garde que sur les jetons : ici, tout doit être du chiffré."""
        from .bf_otp_token import _BASE32_NU, _URI_OTPAUTH
        for cred in self:
            v = (cred.wrapped_secret or '').strip()
            if _URI_OTPAUTH.search(v) or _BASE32_NU.match(v):
                raise ValidationError(_(
                    "The vault key reached the server in plaintext. "
                    "Sealing happens in the browser, before sending."
                ))
