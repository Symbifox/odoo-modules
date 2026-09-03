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
    _description = 'Token OTP'
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
             "vraiment qu'une dizaine de tokens, c'est ce qui les fait "
             "remonter sans qu'on ait rien à ranger.",
    )

    # -- sensibilité ---------------------------------------------------------
    group_name = fields.Char(
        string='Regroupement',
        help="Étiquette libre pour ranger les tokens : un client, un "
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

    # ⚠️ `active` EST la corbeille. Un token retiré passe ici à False au lieu
    # d'être détruit : le geste qui coûte cher (perdre un deuxième facteur pour
    # de bon) doit demander deux décisions, pas une. La destruction réelle
    # existe, elle s'appelle `purge_token`, et elle le dit.
    active = fields.Boolean(string='Actif', default=True)
    deleted_at = fields.Datetime(
        string='Mis à la corbeille',
        help="Posé au passage à la corbeille. Sert à dire depuis quand, pas à "
             "purger tout seul : rien ici ne détruit une graine sans qu'on le "
             "demande.",
    )

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

        # 🔴 `active_test=False` : sans lui, un token à la corbeille ne compte
        # pas comme connu, l'import en recrée un jumeau, et la restauration
        # rend ensuite deux lignes identiques. Le doublon n'apparaîtrait qu'au
        # moment où quelqu'un vide sa corbeille, des semaines plus tard.
        connus = {
            (t.issuer or '', t.name) for t in self.with_context(
                active_test=False).search([('user_id', '=', self.env.uid)])
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
                'favorite': bool(e.get('favorite')),
                # ⚠️ Les rattachements arrivent déjà RÉSOLUS en identifiants :
                # c'est la page qui a cherché le nom et n'a retenu qu'une
                # correspondance exacte et unique. Le serveur ne devine rien
                # ici, sinon un import rattacherait des tokens au mauvais
                # client sans que personne ne le voie.
                'partner_id': int(e['partner_id']) if e.get('partner_id') else False,
                'project_id': int(e['project_id']) if e.get('project_id') else False,
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
                raise ValidationError(_("Token introuvable."))
            token.write(permis)
            return token.id
        permis['vault_id'] = vault.id
        return self.create(permis).id

    @api.model
    def _mine(self, token_id, corbeille=False):
        """Le token demandé, s'il appartient à la personne connectée.

        Toutes les façades passent par ici : la règle d'enregistrement suffirait,
        mais une recherche explicite rend le refus lisible plutôt que muet.

        ⚠️ `corbeille=True` lève le filtre des archivés. Les façades qui
        restaurent ou détruisent en ont besoin ; les autres ne doivent PAS le
        lever, sinon un token à la corbeille redeviendrait copiable et
        modifiable sans jamais être ressorti.
        """
        cible = self.with_context(active_test=False) if corbeille else self
        token = cible.search(
            [('id', '=', int(token_id)), ('user_id', '=', self.env.uid)])
        if not token:
            raise ValidationError(_("Token introuvable."))
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

    # -------------------------------------------------------------------------
    # La corbeille
    # -------------------------------------------------------------------------

    @api.model
    def delete_token(self, token_id):
        """Met un token à la corbeille. Ne détruit rien.

        🔴 Avant la 18.0.10.0.0 cette façade appelait `unlink()` : un clic de
        travers effaçait pour de bon un deuxième facteur dont personne n'avait
        la graine ailleurs. L'avertissement était juste, il arrivait juste trop
        tard pour servir à quoi que ce soit.
        """
        token = self._mine(token_id)
        token.write({'active': False, 'deleted_at': fields.Datetime.now()})
        return True

    @api.model
    def load_my_trash(self):
        """Les tokens à la corbeille, chiffrés tels quels.

        Ils portent encore leur graine chiffrée : la corbeille n'est pas une
        demi-suppression, c'est un rangement. Le navigateur les affiche sans
        calculer de code, parce qu'un token à la corbeille ne doit pas servir.
        """
        tokens = self.with_context(active_test=False).search([
            ('user_id', '=', self.env.uid), ('active', '=', False),
        ])
        return tokens.read(list(self._CHAMPS_LUS) + ['deleted_at'])

    @api.model
    def restore_token(self, token_id):
        """Ressort un token de la corbeille."""
        token = self._mine(token_id, corbeille=True)
        token.write({'active': True, 'deleted_at': False})
        return True

    @api.model
    def purge_token(self, token_id):
        """Détruit un token pour de bon. Aucune phrase ne le rendra.

        ⚠️ Refuse un token qui n'est pas déjà à la corbeille : détruire doit
        toujours être le SECOND geste, jamais le premier.
        """
        token = self._mine(token_id, corbeille=True)
        if token.active:
            raise ValidationError(_(
                "Ce token n'est pas à la corbeille. Mettez-l'y d'abord : "
                "la destruction est irréversible."
            ))
        token.unlink()
        return True

    @api.model
    def empty_trash(self):
        """Vide la corbeille. Rend le nombre de tokens détruits."""
        tokens = self.with_context(active_test=False).search([
            ('user_id', '=', self.env.uid), ('active', '=', False),
        ])
        nombre = len(tokens)
        tokens.unlink()
        return nombre
