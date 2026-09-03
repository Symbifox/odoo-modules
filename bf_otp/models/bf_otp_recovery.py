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
reçoit un bloc qu'il ne peut pas ouvrir. Sur un coffre de cent quarante-quatre
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
    _description = "Code de relève d'un coffre OTP"
    _order = 'create_date desc, id desc'

    vault_id = fields.Many2one(
        'bf.otp.vault', string='Coffre', required=True,
        ondelete='cascade', index=True,
    )
    user_id = fields.Many2one(
        'res.users', related='vault_id.user_id', store=True, index=True,
    )
    name = fields.Char(
        string='Où il est rangé', required=True,
        help="Ce qui permettra de le retrouver, ou de savoir lequel révoquer : "
             "« enveloppe au coffre-fort », « Vaultwarden de la relève ».",
    )
    salt = fields.Char(
        string='Sel', required=True,
        help="Sel de dérivation du code, tiré au sort par le navigateur. "
             "Public par nature.",
    )
    iterations = fields.Integer(
        string='Itérations', required=True, default=600000,
        help="Enregistré par code, pour pouvoir le monter plus tard sans rendre "
             "illisibles les codes déjà distribués.",
    )
    wrapped_secret = fields.Char(
        string='Clé du coffre, scellée', required=True,
        help="Les octets de la clé du coffre, chiffrés par le code de relève. "
             "Le serveur ne peut pas l'ouvrir.",
    )
    wrapped_iv = fields.Char(string='Vecteur', required=True)
    last_used = fields.Datetime(string='Dernière ouverture')

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
                    "La clé du coffre est arrivée en clair au serveur. "
                    "Le scellement se fait dans le navigateur, avant l'envoi."
                ))
