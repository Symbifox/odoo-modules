"""Le code de relève : un second chemin d'ouverture qui survit à l'appareil.

Pourquoi celui-ci plutôt qu'autre chose
---------------------------------------
Le coffre s'ouvre par une phrase de passe, et depuis la 18.0.3.0.0 par une clé
d'accès. La clé d'accès est une commodité, pas un recours : elle est liée à une
origine ET à un appareil, donc elle meurt avec le portable et elle ne suit pas
d'un domaine à l'autre. Restait une seule vraie porte, dans une seule tête.

Le code de relève ferme ce trou sans toucher aux graines. C'est **exactement le
scellé de la clé d'accès**, avec un code tiré au sort à la place du secret PRF :
la clé du coffre ne change pas, aucune graine n'est ré-encryptée, et le serveur
reçoit un bloc qu'il ne peut pas ouvrir. Sur un coffre bien rempli de
graines vivantes, ne pas les retoucher est la moitié de l'argument.

Ce qui entre ici
----------------
Le sel de dérivation, le nombre d'itérations, la clé du coffre scellée par le
code, et son vecteur. **Jamais le code lui-même, ni son condensat.** Un condensat
donnerait à qui lit la base un point de départ hors ligne, exactement ce que le
coffre refuse de fournir depuis le premier jour.

⚠️ **Le code n'est montré qu'une fois**, à sa création, dans la page. Il ne
repasse plus jamais, ni au serveur ni à l'écran. Le perdre revient à perdre la
relève : il reste alors la phrase de passe.

⚠️ **Un code de relève est aussi puissant que la phrase.** Qui l'a ouvre le
coffre. Sa place est un coffre-fort, une enveloppe scellée ou un gestionnaire de
mots de passe qui n'est PAS celui que ces tokens protègent.
"""

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

# Cinq suffisent : un imprimé, un au coffre-fort, un chez la relève désignée, et
# de la marge. Au-delà, chaque code est une porte de plus à surveiller, et
# personne ne tient l'inventaire de dix enveloppes.
PLAFOND_RELEVES = 5


class BfOtpRecovery(models.Model):
    _name = 'bf.otp.recovery'
    _description = "OTP vault recovery code"
    _order = 'create_date desc, id desc'

    vault_id = fields.Many2one(
        'bf.otp.vault', string="Vault", required=True,
        ondelete='cascade', index=True,
    )
    user_id = fields.Many2one(
        'res.users', related='vault_id.user_id', store=True, index=True,
    )
    name = fields.Char(
        string="Where it is stored", required=True,
        help="What will help find it, or know which one to revoke: "
             "\"envelope in the safe\", \"successors' Vaultwarden\".",
    )
    salt = fields.Char(
        string="Salt", required=True,
        help="Derivation salt for the code, drawn at random by the "
             "browser. Public by nature.",
    )
    iterations = fields.Integer(
        string="Iterations", required=True, default=600000,
        help="Stored per code, so it can be raised later without making "
             "codes already handed out unreadable.",
    )
    wrapped_secret = fields.Char(
        string="Vault key, sealed", required=True,
        help="The vault key bytes, encrypted with the recovery code. The "
             "server cannot open it.",
    )
    wrapped_iv = fields.Char(string="IV", required=True)
    last_used = fields.Datetime(string="Last unlock")


    def write(self, vals):
        """On ne déplace pas une fiche dans le coffre de quelqu'un d'autre.

        Odoo ne rejoue pas les règles d'enregistrement après un `write` : la
        règle voit la fiche de départ, pas le coffre d'arrivée."""
        if not self.env.su and vals.get('vault_id'):
            self.env['bf.otp.vault'].browse(vals['vault_id']).check_access('write')
        return super().write(vals)

    @api.constrains('wrapped_secret')
    def _check_not_a_seed(self):
        """Même garde que partout : ici, tout doit être du chiffré.

        Le jour où le navigateur cesserait de sceller, la base se remplirait de
        clés de coffre lisibles et rien ne le dirait.
        """
        from .bf_otp_token import _BASE32_NU, _URI_OTPAUTH
        for releve in self:
            v = (releve.wrapped_secret or '').strip()
            if _URI_OTPAUTH.search(v) or _BASE32_NU.match(v):
                raise ValidationError(_(
                    "The vault key reached the server in plaintext. "
                    "Sealing happens in the browser, before sending."
                ))
