"""Le coffre d'une personne : de quoi vérifier sa phrase, jamais la phrase.

Une graine TOTP doit être en clair au moment de produire le code. Ce module
choisit donc que ce moment n'arrive JAMAIS sur le serveur : la graine est
chiffrée dans le navigateur, et le code s'y calcule aussi.

Ce modèle ne garde que ce qui permet à un navigateur de retrouver la clé :
- le **sel**, public par nature, qui rend la dérivation propre à la personne ;
- le nombre d'itérations, pour pouvoir le monter plus tard sans casser
  l'existant ;
- un **témoin**, un texte connu chiffré avec la clé, qui permet de dire « ce
  n'est pas la bonne phrase » sans que le serveur sache laquelle c'est.

⚠️ Aucune phrase de passe, aucune clé, aucun condensat de phrase n'entre ici.
Un condensat donnerait à qui lit la base un point de départ pour une attaque
hors ligne meilleur que le chiffré lui-même. Le témoin n'apporte rien de plus
que ce que le chiffré donne déjà.

🔴 Conséquence assumée, à dire à qui s'en sert : **phrase perdue, graines
perdues.** Odoo ne peut pas les rendre, personne ne le peut. C'est le prix de
ne pas détenir les graines.
"""

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError


class BfOtpVault(models.Model):
    """Le coffre personnel d'un usager. Un seul par personne."""

    _name = 'bf.otp.vault'
    _description = 'Coffre de jetons OTP'
    _rec_name = 'user_id'

    user_id = fields.Many2one(
        'res.users',
        string='Usager',
        required=True,
        ondelete='cascade',
        index=True,
        default=lambda self: self.env.user,
    )
    salt = fields.Char(
        string='Sel',
        required=True,
        help="Sel de dérivation, tiré au sort par le navigateur. Public par "
             "nature : il ne protège rien à lui seul, il empêche de préparer "
             "une attaque avant d'avoir vu ce coffre-ci.",
    )
    iterations = fields.Integer(
        string="Itérations",
        required=True,
        default=600000,
        help="Nombre d'itérations PBKDF2. Enregistré par coffre pour pouvoir "
             "le monter plus tard sans rendre illisible ce qui existe.",
    )
    verifier = fields.Char(
        string='Témoin',
        required=True,
        help="Un texte connu, chiffré avec la clé. Sert au navigateur à dire "
             "« mauvaise phrase » avant de tenter de déchiffrer les jetons.",
    )
    verifier_iv = fields.Char(string='Vecteur du témoin', required=True)

    credential_ids = fields.One2many(
        'bf.otp.credential', 'vault_id', string="Clés d'accès")

    token_ids = fields.One2many('bf.otp.token', 'vault_id', string='Jetons')
    token_count = fields.Integer(
        string='Nombre de jetons', compute='_compute_token_count')

    _sql_constraints = [
        ('user_uniq', 'unique(user_id)',
         "Une personne n'a qu'un seul coffre de jetons."),
    ]

    @api.depends('token_ids')
    def _compute_token_count(self):
        comptes = dict(self.env['bf.otp.token']._read_group(
            [('vault_id', 'in', self.ids)], ['vault_id'], ['__count'],
        ))
        for vault in self:
            vault.token_count = comptes.get(vault, 0)

    # -------------------------------------------------------------------------
    # Façade appelée par le navigateur
    # -------------------------------------------------------------------------

    @api.model
    def get_my_vault(self):
        """Rend le coffre de la personne connectée, ou False s'il n'existe pas.

        Ne crée rien : c'est le navigateur qui tire le sel et fabrique le
        témoin, parce que lui seul connaît la phrase.

        ⚠️ `False` et non `None` : XML-RPC **ne sait pas encoder None** et lève
        « cannot marshal None unless allow_none is enabled », une erreur opaque
        qui ne dit rien du coffre. Le navigateur passe par JSON-RPC et s'en
        moquerait, mais un script ou une application mobile buterait dessus.
        Les deux sont faux au sens de JavaScript, donc l'interface ne change pas.
        """
        vault = self.search([('user_id', '=', self.env.uid)], limit=1)
        if not vault:
            return False
        return {
            'id': vault.id,
            'salt': vault.salt,
            'iterations': vault.iterations,
            'verifier': vault.verifier,
            'verifier_iv': vault.verifier_iv,
            'token_count': vault.token_count,
            'credentials': [
                {
                    'id': c.id,
                    'name': c.name,
                    'credential_id': c.credential_id,
                    'prf_salt': c.prf_salt,
                    'wrapped_secret': c.wrapped_secret,
                    'wrapped_iv': c.wrapped_iv,
                }
                for c in vault.credential_ids
            ],
        }

    @api.model
    def create_my_vault(self, salt, iterations, verifier, verifier_iv):
        """Pose le coffre de la personne connectée.

        ⚠️ Refuse d'écraser un coffre existant. Le faire rendrait illisibles
        tous les jetons déjà dedans, sans rien pour le signaler : le navigateur
        se contenterait d'un « mauvaise phrase » à la lecture suivante.
        """
        if self.search_count([('user_id', '=', self.env.uid)]):
            raise UserError(_(
                "Ce coffre existe déjà. Pour en changer la phrase de passe, "
                "il faut rechiffrer les jetons, ce qui se fait depuis le coffre "
                "ouvert."
            ))
        vault = self.create({
            'user_id': self.env.uid,
            'salt': salt,
            'iterations': int(iterations),
            'verifier': verifier,
            'verifier_iv': verifier_iv,
        })
        return vault.id

    # -------------------------------------------------------------------------
    # Clés d'accès
    # -------------------------------------------------------------------------

    @api.model
    def add_credential(self, name, credential_id, prf_salt,
                       wrapped_secret, wrapped_iv):
        """Enregistre une clé d'accès capable d'ouvrir MON coffre.

        Tout ce qui arrive ici a été scellé dans le navigateur. Le serveur ne
        vérifie pas la signature WebAuthn et n'a pas à le faire : il n'accorde
        aucun droit sur la foi de cette clé. Elle ne sert qu'à déchiffrer, et
        une clé fausse ne déchiffrera rien.
        """
        vault = self.search([('user_id', '=', self.env.uid)], limit=1)
        if not vault:
            raise UserError(_("Aucun coffre : il faut en créer un d'abord."))
        cred = self.env['bf.otp.credential'].create({
            'vault_id': vault.id,
            'name': name or _("Clé d'accès"),
            'credential_id': credential_id,
            'prf_salt': prf_salt,
            'wrapped_secret': wrapped_secret,
            'wrapped_iv': wrapped_iv,
        })
        return cred.id

    @api.model
    def remove_credential(self, credential_row_id):
        """Retire une clé d'accès. Le coffre reste ouvrable par la phrase."""
        cred = self.env['bf.otp.credential'].search([
            ('id', '=', int(credential_row_id)),
            ('user_id', '=', self.env.uid),
        ])
        if not cred:
            raise UserError(_("Clé d'accès introuvable."))
        cred.unlink()
        return True

    @api.model
    def touch_credential(self, credential_row_id):
        cred = self.env['bf.otp.credential'].search([
            ('id', '=', int(credential_row_id)),
            ('user_id', '=', self.env.uid),
        ])
        if cred:
            cred.sudo().last_used = fields.Datetime.now()
        return True

    def unlink(self):
        """Supprimer un coffre emporte ses jetons : on le dit, on ne le devine pas."""
        for vault in self:
            if vault.token_count:
                raise UserError(_(
                    "Ce coffre porte %(nombre)s jeton(s). Vide-le avant de le "
                    "supprimer : une fois parti, aucune phrase de passe ne les "
                    "rendra.", nombre=vault.token_count,
                ))
        return super().unlink()
