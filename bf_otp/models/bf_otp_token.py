"""Un jeton OTP : ses métadonnées en clair, sa graine jamais.

Le serveur porte de quoi RECONNAÎTRE un jeton (émetteur, compte, algorithme,
période) et un bloc opaque qui ne lui sert à rien. Il ne peut pas produire de
code, et ce n'est pas une limitation : c'est la propriété qu'on achète.

⚠️ Le champ `secret_cipher` doit TOUJOURS contenir du chiffré. Une graine en
base32 qui y arriverait voudrait dire que le navigateur n'a pas chiffré, et
personne ne s'en apercevrait avant longtemps : la contrainte
`_check_cipher_is_not_a_seed` refuse ce cas.
"""

import re

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

# Une graine en base32, exactement ce qui ne doit JAMAIS atterrir dans le champ
# de chiffré. On est strict ici, à l'inverse du registre de `bf_credentials` :
# le contenu légitime est du base64 de chiffré, jamais du texte écrit par une
# personne, donc il n'y a pas de faux positif à craindre.
_BASE32_NU = re.compile(r'\A[A-Z2-7]{8,}=*\Z')
_URI_OTPAUTH = re.compile(r'\botpauth(-migration)?://', re.IGNORECASE)


class BfOtpToken(models.Model):
    _name = 'bf.otp.token'
    _description = 'Jeton OTP'
    _order = 'favorite desc, sequence, issuer, name, id'

    # -- rattachement --------------------------------------------------------
    vault_id = fields.Many2one(
        'bf.otp.vault', string='Coffre', required=True,
        ondelete='cascade', index=True,
    )
    user_id = fields.Many2one(
        'res.users', string='Propriétaire',
        related='vault_id.user_id', store=True, index=True,
    )

    # -- métadonnées, en clair et assumées -----------------------------------
    # Elles ne sont pas secrètes : elles disent QUEL compte, pas comment y
    # entrer. Les garder lisibles est ce qui permet de chercher, de trier et de
    # savoir ce que le coffre contient sans l'ouvrir.
    name = fields.Char(string='Compte', required=True)
    issuer = fields.Char(string='Émetteur', index=True)
    sequence = fields.Integer(string='Séquence', default=10)

    otp_type = fields.Selection([
        ('totp', 'TOTP (par le temps)'),
        ('hotp', 'HOTP (par compteur)'),
    ], string='Type', required=True, default='totp')
    algorithm = fields.Selection([
        ('SHA1', 'SHA-1'),
        ('SHA256', 'SHA-256'),
        ('SHA512', 'SHA-512'),
    ], string='Algorithme', required=True, default='SHA1')
    digits = fields.Integer(string='Chiffres', required=True, default=6)
    period = fields.Integer(string='Période (s)', required=True, default=30)
    counter = fields.Integer(
        string='Compteur', default=0,
        help="Pour un HOTP seulement. Avancé par le navigateur à chaque code "
             "produit.",
    )

    # -- à qui ça appartient -------------------------------------------------
    # Un jeton sert à entrer quelque part, et ce quelque part appartient à
    # quelqu'un. Sans ce rattachement, un coffre de 144 jetons n'est qu'une
    # liste : on ne peut ni préparer un départ, ni rendre ce qui revient à un
    # client, ni savoir ce qu'on détient pour lui.
    #
    # ⚠️ `project_id` rend l'app Projet OBLIGATOIRE pour installer ce module :
    # un champ typé vers un modèle rend sa dépendance non négociable. Choix
    # assumé le 2026-08-30 ; si un client du catalogue veut le coffre sans
    # Projet, ce champ déménagera dans un module pont.
    partner_id = fields.Many2one(
        'res.partner', string='Client', index=True, ondelete='set null',
    )
    project_id = fields.Many2one(
        'project.project', string='Projet', index=True, ondelete='set null',
    )

    # -- confort ---------------------------------------------------------------
    favorite = fields.Boolean(
        string='Favori', default=False, index=True,
        help="Remonte en tête, au-dessus des regroupements.",
    )
    last_used = fields.Datetime(
        string='Dernière utilisation',
        help="Posée à chaque code copié. Sur un coffre où l'on n'utilise "
             "vraiment qu'une dizaine de jetons, c'est ce qui les fait "
             "remonter sans qu'on ait rien à ranger.",
    )

    # -- sensibilité ---------------------------------------------------------
    group_name = fields.Char(
        string='Regroupement',
        help="Étiquette libre pour ranger les jetons : un client, un "
             "environnement, une équipe.",
    )
    sensitive = fields.Boolean(
        string='Sensible',
        default=False,
        help="Le code reste masqué jusqu'à ce qu'on le demande, et n'est pas "
             "copié au presse-papiers par mégarde.",
    )

    # -- la graine, chiffrée ailleurs ----------------------------------------
    secret_cipher = fields.Char(
        string='Graine chiffrée', required=True,
        help="Chiffré AES-GCM produit par le navigateur. Le serveur ne peut "
             "pas le lire et n'essaie pas.",
    )
    secret_iv = fields.Char(string='Vecteur', required=True)

    active = fields.Boolean(string='Actif', default=True)

    # -------------------------------------------------------------------------
    # Le garde : du chiffré, jamais une graine
    # -------------------------------------------------------------------------

    @api.constrains('secret_cipher')
    def _check_cipher_is_not_a_seed(self):
        """Refuse une graine en clair dans le champ de chiffré.

        Ce module existe pour que le serveur ne détienne aucune graine. Si le
        navigateur cessait de chiffrer — bogue, page tierce, appel direct au
        RPC — la base se remplirait de graines lisibles et rien ne le dirait.
        Cette contrainte fait du bruit tout de suite.
        """
        for token in self:
            valeur = (token.secret_cipher or '').strip()
            if _URI_OTPAUTH.search(valeur) or _BASE32_NU.match(valeur):
                raise ValidationError(_(
                    "La graine est arrivée en clair au serveur.\n\n"
                    "Ce coffre ne détient aucune graine lisible : le "
                    "chiffrement se fait dans le navigateur, avant l'envoi. "
                    "Refuser ici est volontaire, parce qu'une graine acceptée "
                    "en clair ne se remarquerait plus jamais."
                ))

    @api.constrains('digits', 'period')
    def _check_shape(self):
        for token in self:
            if token.digits not in (6, 7, 8):
                raise ValidationError(_(
                    "Un code OTP fait 6, 7 ou 8 chiffres."))
            if token.otp_type == 'totp' and token.period < 1:
                raise ValidationError(_(
                    "La période d'un TOTP doit être d'au moins une seconde."))

    # -------------------------------------------------------------------------
    # Façade pour le navigateur
    # -------------------------------------------------------------------------

    _CHAMPS_LUS = (
        'name', 'issuer', 'otp_type', 'algorithm', 'digits', 'period',
        'counter', 'group_name', 'sensitive', 'secret_cipher', 'secret_iv',
        'sequence', 'partner_id', 'project_id', 'favorite', 'last_used',
    )

    @api.model
    def load_my_tokens(self):
        """Rend les jetons de la personne connectée, chiffrés tels quels."""
        tokens = self.search([('user_id', '=', self.env.uid)])
        return tokens.read(list(self._CHAMPS_LUS))

    @api.model
    def import_tokens(self, entries):
        """Enregistre des jetons déjà chiffrés par le navigateur.

        ⚠️ Le déchiffrement de l'export venu d'ailleurs, et le rechiffrement
        pour ce coffre-ci, se font AVANT cet appel, dans la page. Le serveur ne
        voit passer que du chiffré, à l'import comme au reste du temps.
        """
        vault = self.env['bf.otp.vault'].search(
            [('user_id', '=', self.env.uid)], limit=1)
        if not vault:
            raise ValidationError(_(
                "Aucun coffre : il faut en créer un avant d'importer."))

        connus = {
            (t.issuer or '', t.name) for t in self.search(
                [('user_id', '=', self.env.uid)])
        }
        a_creer, ignores = [], 0
        for e in entries:
            cle = (e.get('issuer') or '', e.get('name') or '')
            if cle in connus:
                # Réimporter le même export deux fois est le geste le plus
                # probable de quelqu'un qui doute que ça ait marché. Le doublon
                # silencieux serait pire que le refus.
                ignores += 1
                continue
            connus.add(cle)
            a_creer.append({
                'vault_id': vault.id,
                'name': e.get('name') or _('Sans nom'),
                'issuer': e.get('issuer') or False,
                'otp_type': e.get('otp_type') or 'totp',
                'algorithm': (e.get('algorithm') or 'SHA1').upper(),
                'digits': int(e.get('digits') or 6),
                'period': int(e.get('period') or 30),
                'counter': int(e.get('counter') or 0),
                'group_name': e.get('group_name') or False,
                'sensitive': bool(e.get('sensitive')),
                'secret_cipher': e['secret_cipher'],
                'secret_iv': e['secret_iv'],
            })
        crees = self.create(a_creer) if a_creer else self.browse()
        return {'created': len(crees), 'skipped': ignores}

    @api.model
    def save_token(self, values, token_id=None):
        """Crée ou met à jour un jeton, chiffré par la page."""
        vault = self.env['bf.otp.vault'].search(
            [('user_id', '=', self.env.uid)], limit=1)
        if not vault:
            raise ValidationError(_("Aucun coffre."))
        permis = {
            k: v for k, v in (values or {}).items() if k in self._CHAMPS_LUS
        }
        if token_id:
            token = self.search(
                [('id', '=', int(token_id)), ('user_id', '=', self.env.uid)])
            if not token:
                raise ValidationError(_("Jeton introuvable."))
            token.write(permis)
            return token.id
        permis['vault_id'] = vault.id
        return self.create(permis).id

    @api.model
    def _mine(self, token_id):
        """Le jeton demandé, s'il appartient à la personne connectée.

        Toutes les façades passent par ici : la règle d'enregistrement suffirait,
        mais une recherche explicite rend le refus lisible plutôt que muet.
        """
        token = self.search(
            [('id', '=', int(token_id)), ('user_id', '=', self.env.uid)])
        if not token:
            raise ValidationError(_("Jeton introuvable."))
        return token

    @api.model
    def toggle_favorite(self, token_id):
        token = self._mine(token_id)
        token.favorite = not token.favorite
        return token.favorite

    @api.model
    def touch_token(self, token_id):
        """Note qu'un code vient d'être utilisé.

        ⚠️ Écrit avec `sudo()` sur le seul champ de date : `last_used` doit se
        poser même si l'enregistrement est par ailleurs en lecture seule pour
        une raison qu'on n'a pas prévue. La propriété du jeton est déjà
        vérifiée par `_mine`.
        """
        token = self._mine(token_id)
        token.sudo().last_used = fields.Datetime.now()
        return True

    @api.model
    def name_search_targets(self, model, term, limit=12):
        """Cherche un client ou un projet pour le champ de rattachement.

        ⚠️ Liste blanche stricte des modèles : cette méthode est appelable par
        RPC, et sans elle elle deviendrait un `name_search` universel sur
        n'importe quel modèle, sous l'identité de qui appelle.
        """
        if model not in ('res.partner', 'project.project'):
            raise ValidationError(_("Modèle non autorisé : %s", model))
        return self.env[model].name_search(name=term or '', limit=int(limit))

    @api.model
    def bump_counter(self, token_id, counter):
        """Avance le compteur d'un HOTP. Le seul état que le serveur tient."""
        token = self._mine(token_id)
        token.counter = int(counter)
        return True

    @api.model
    def delete_token(self, token_id):
        self._mine(token_id).unlink()
        return True
