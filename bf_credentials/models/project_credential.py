import logging
from datetime import timedelta
from urllib.parse import quote

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError

from .otp_secret_guard import otp_secret_reason

_logger = logging.getLogger(__name__)

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:
    Fernet = None
    InvalidToken = Exception
    _logger.warning("Le paquet cryptography n'est pas installé. Le chiffrement des identifiants ne sera pas disponible.")


class ProjectCredential(models.Model):
    """Stockage sécurisé d'identifiants liés aux projets."""
    _name = 'project.credential'
    _description = 'Identifiant de projet'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'project_id, type_id, name'

    # Identification
    name = fields.Char(
        string='Nom',
        required=True,
        tracking=True,
        help='Nom descriptif pour cet identifiant',
    )
    reference = fields.Char(
        string='Référence',
        tracking=True,
        help='Référence externe optionnelle ou numéro de billet',
    )

    # Relations
    project_id = fields.Many2one(
        'project.project',
        string='Projet',
        required=True,
        ondelete='cascade',
        tracking=True,
        index=True,
    )
    type_id = fields.Many2one(
        'project.credential.type',
        string='Type',
        required=True,
        tracking=True,
        index=True,
    )
    company_id = fields.Many2one(
        'res.company',
        string='Société',
        related='project_id.company_id',
        store=True,
        readonly=True,
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Contact',
        tracking=True,
        help='Personne-ressource pour cet identifiant',
    )

    # Données d'identifiant - Texte en clair
    url = fields.Char(
        string='URL',
        tracking=True,
    )
    username = fields.Char(
        string="Nom d'utilisateur",
        tracking=True,
    )
    domain = fields.Char(
        string='Domaine',
        tracking=True,
        help="Domaine ou locataire pour l'authentification (ex. : domaine AD)",
    )

    # Données d'identifiant - Chiffré
    password = fields.Char(
        string='Mot de passe',
        compute='_compute_password',
        inverse='_inverse_password',
        store=False,
        # Le calcul masque le secret selon les groupes de l'utilisateur courant.
        # Sans cette clé, la valeur reste en cache d'une lecture à l'autre dans
        # une même transaction : un secret lu par un gestionnaire ressort en
        # clair à la lecture suivante, quel que soit l'utilisateur.
        depends_context=('uid',),
    )
    password_encrypted = fields.Char(
        string='Mot de passe (chiffré)',
        groups='base.group_system',
    )
    api_key = fields.Char(
        string='Clé API',
        compute='_compute_api_key',
        inverse='_inverse_api_key',
        store=False,
        depends_context=('uid',),
    )
    api_key_encrypted = fields.Char(
        string='Clé API (chiffrée)',
        groups='base.group_system',
    )

    # Support de fichier de clé
    key_file = fields.Binary(
        string='Fichier de clé',
        attachment=True,
        help='Clé SSH, certificat ou autre fichier de clé (.pem, .key, .p12, .ppk)',
    )
    key_filename = fields.Char(
        string='Nom du fichier de clé',
    )

    # Environnement
    environment = fields.Selection([
        ('production', 'Production'),
        ('staging', 'Pré-production'),
        ('development', 'Développement'),
        ('testing', 'Tests'),
    ], string='Environnement', default='production', tracking=True)

    # Cycle de vie
    state = fields.Selection([
        ('active', 'Actif'),
        ('expiring', 'Expire bientôt'),
        ('expired', 'Expiré'),
        ('revoked', 'Révoqué'),
    ], string='Statut', default='active', tracking=True, index=True)

    expiration_date = fields.Date(
        string="Date d'expiration",
        tracking=True,
    )
    last_verified = fields.Datetime(
        string='Dernière vérification',
        tracking=True,
        help='Date de la dernière vérification du bon fonctionnement de cet identifiant',
    )
    last_rotated = fields.Datetime(
        string='Dernière rotation',
        tracking=True,
        help='Date du dernier changement de mot de passe',
    )

    # Contrôle d'accès
    restricted = fields.Boolean(
        string='Restreint',
        default=False,
        tracking=True,
        help="Si coché, le mot de passe n'est visible que par les gestionnaires d'identifiants",
    )

    # -------------------------------------------------------------------------
    # Deuxième facteur — le registre, jamais la graine
    #
    # Le coffre porte le mot de passe. Son deuxième facteur vit ailleurs, et ces
    # champs disent seulement OÙ et CHEZ QUI. Aucun d'eux ne contient de secret,
    # et `_check_no_otp_secret` refuse ce qui ressemble à une graine : mettre le
    # mot de passe et son deuxième facteur dans la même base laisserait UN
    # facteur au client, pas deux.
    # -------------------------------------------------------------------------

    mfa_type = fields.Selection([
        ('unknown', 'À documenter'),
        ('none', 'Aucun'),
        ('totp', 'Code à durée limitée (TOTP)'),
        ('hardware', 'Clé matérielle (FIDO2, YubiKey)'),
        ('app', "Notification d'une application"),
        ('sms', 'SMS'),
        ('email', 'Courriel'),
        ('backup_codes', 'Codes de secours seulement'),
        ('other', 'Autre'),
    ],
        string='Deuxième facteur',
        # « À documenter » et non « Aucun » : les identifiants qui existaient
        # avant ce lot n'ont jamais été interrogés là-dessus. Les déclarer sans
        # deuxième facteur inventerait une réponse, et une réponse fausse dans
        # le sens rassurant. « À documenter » les fait apparaître comme du
        # travail à faire, ce qu'ils sont.
        default='unknown',
        required=True,
        tracking=True,
        index=True,
    )
    mfa_vault_id = fields.Many2one(
        'project.credential.vault',
        string='Porteur du facteur',
        tracking=True,
        help="Où vit le deuxième facteur. Jamais son contenu.",
    )
    mfa_reference = fields.Char(
        string='Référence chez le porteur',
        tracking=True,
        help="L'étiquette du facteur chez son porteur, de quoi le retrouver sans "
             "le lire.\n"
             "⚠️ Jamais la graine elle-même : ce champ la refuse.",
    )
    mfa_holder_ids = fields.Many2many(
        'res.users',
        'project_credential_mfa_holder_rel', 'credential_id', 'user_id',
        string='Peuvent produire un code',
        tracking=True,
    )
    mfa_holder_note = fields.Char(
        string='Autre porteur',
        tracking=True,
        help="Quand la personne qui détient le facteur n'a pas de compte ici : "
             "un jeton dans un tiroir, quelqu'un chez le client.",
    )
    mfa_recovery = fields.Selection([
        ('unknown', 'À documenter'),
        ('none', 'Aucune'),
        ('backup_codes', 'Codes de secours'),
        ('second_holder', 'Une deuxième personne peut produire un code'),
        ('vault', 'Le facteur est dans un porteur partagé'),
        ('provider', 'Réémission par le fournisseur'),
    ],
        string='Reprise',
        default='unknown',
        required=True,
        tracking=True,
        help="Ce qui se passe si le porteur disparaît. C'est la question que "
             "personne ne se pose avant un départ.",
    )
    mfa_last_reviewed = fields.Date(
        string='Dernière revue du facteur',
        tracking=True,
    )
    mfa_state = fields.Selection([
        ('unknown', 'À documenter'),
        ('absent', 'Aucun deuxième facteur'),
        ('at_risk', 'Deuxième facteur sans relève'),
        ('covered', 'Couvert'),
    ],
        string='État du deuxième facteur',
        compute='_compute_mfa_state',
        store=True,
        index=True,
    )
    mfa_item_url = fields.Char(
        string="Lien vers l'élément",
        compute='_compute_mfa_item_url',
    )

    # Notes
    notes = fields.Html(
        string='Notes',
        help='Informations ou instructions supplémentaires',
    )

    # Champs calculés
    is_expiring_soon = fields.Boolean(
        string='Expire bientôt',
        compute='_compute_expiration_status',
        store=True,
    )
    is_expired = fields.Boolean(
        string='Est expiré',
        compute='_compute_expiration_status',
        store=True,
    )

    # Champs de visibilité de type (calculés depuis type_id)
    show_domain = fields.Boolean(
        related='type_id.show_domain',
    )
    show_url = fields.Boolean(
        related='type_id.show_url',
    )
    show_api_key = fields.Boolean(
        related='type_id.show_api_key',
    )
    show_key_file = fields.Boolean(
        related='type_id.show_key_file',
    )

    # -------------------------------------------------------------------------
    # Méthodes de chiffrement
    # -------------------------------------------------------------------------

    def _get_encryption_key(self):
        """Obtenir ou générer la clé de chiffrement depuis les paramètres système."""
        if not Fernet:
            return None
        ICP = self.env['ir.config_parameter'].sudo()
        key = ICP.get_param('project_credential.encryption_key')
        if not key:
            key = Fernet.generate_key().decode()
            ICP.set_param('project_credential.encryption_key', key)
        return key.encode()

    def _encrypt_value(self, value):
        """Chiffrer une valeur texte avec le chiffrement symétrique Fernet."""
        if not value:
            return False
        key = self._get_encryption_key()
        if not key:
            _logger.warning('Clé de chiffrement non disponible, stockage en clair')
            return value
        try:
            f = Fernet(key)
            return f.encrypt(value.encode()).decode()
        except Exception as e:
            _logger.error('Échec du chiffrement : %s', e)
            return value

    def _decrypt_value(self, encrypted_value):
        """Déchiffrer une valeur chiffrée avec Fernet."""
        if not encrypted_value:
            return False
        key = self._get_encryption_key()
        if not key:
            return encrypted_value
        try:
            f = Fernet(key)
            return f.decrypt(encrypted_value.encode()).decode()
        except InvalidToken:
            _logger.debug("La valeur semble non chiffrée, retour en l'état")
            return encrypted_value
        except Exception as e:
            _logger.error('Échec du déchiffrement : %s', e)
            return encrypted_value

    # -------------------------------------------------------------------------
    # Champ mot de passe
    # -------------------------------------------------------------------------

    def _compute_password(self):
        """Déchiffrer le mot de passe pour l'affichage."""
        is_manager = self.env.user.has_group(
            'bf_credentials.group_credential_manager'
        )
        for record in self:
            if record.restricted and not is_manager:
                record.password = '********'
            else:
                record.password = record._decrypt_value(record.password_encrypted)

    def _inverse_password(self):
        """Chiffrer le mot de passe à l'écriture."""
        for record in self:
            if record.password and record.password != '********':
                record.password_encrypted = record._encrypt_value(record.password)

    # -------------------------------------------------------------------------
    # Champ clé API
    # -------------------------------------------------------------------------

    def _compute_api_key(self):
        """Déchiffrer la clé API pour l'affichage."""
        is_manager = self.env.user.has_group(
            'bf_credentials.group_credential_manager'
        )
        for record in self:
            if record.restricted and not is_manager:
                record.api_key = '********'
            else:
                record.api_key = record._decrypt_value(record.api_key_encrypted)

    def _inverse_api_key(self):
        """Chiffrer la clé API à l'écriture."""
        for record in self:
            if record.api_key and record.api_key != '********':
                record.api_key_encrypted = record._encrypt_value(record.api_key)

    # -------------------------------------------------------------------------
    # Contrôle d'accès — verrou « Restreint »
    # -------------------------------------------------------------------------

    def _is_credential_manager(self):
        return self.env.su or self.env.user.has_group(
            'bf_credentials.group_credential_manager'
        )

    def write(self, vals):
        # Le drapeau « Restreint » décide si un non-gestionnaire voit le secret
        # déchiffré (_compute_password/_compute_api_key). Sans ce verrou, un
        # simple `write({'restricted': False})` par un utilisateur d'identifiants
        # (droit d'écriture via la règle « membres du projet ») dévoilerait un
        # secret qu'un gestionnaire avait volontairement masqué. On bloque donc
        # tout CHANGEMENT réel de `restricted` par un non-gestionnaire (une
        # ré-écriture à l'identique — sauvegarde de formulaire — reste permise).
        if 'restricted' in vals and not self._is_credential_manager():
            new_value = bool(vals['restricted'])
            if any(bool(rec.restricted) != new_value for rec in self):
                raise AccessError(_(
                    "Seul un gestionnaire d'identifiants peut modifier l'état "
                    "« Restreint » d'un identifiant."
                ))

        # Toucher au deuxième facteur, c'est l'avoir revu : la date se pose
        # toute seule. Sans ça, `mfa_last_reviewed` resterait vide à jamais, et
        # une « dernière revue » toujours vide est pire qu'absente — elle laisse
        # croire que personne n'a jamais regardé.
        #
        # ⚠️ On estampille sur un changement RÉEL, pas sur la présence du champ
        # dans les valeurs : enregistrer un formulaire réécrit tous les champs,
        # et estampiller là-dessus ferait passer pour une revue le simple fait
        # d'avoir ouvert la fiche.
        if (not self._MFA_CHAMPS_DE_REVUE.intersection(vals)
                or 'mfa_last_reviewed' in vals
                or self.env.context.get('bf_cred_sans_horodatage')):
            return super().write(vals)

        avant = {rec.id: rec._mfa_empreinte() for rec in self}
        resultat = super().write(vals)
        changees = self.filtered(lambda r: r._mfa_empreinte() != avant[r.id])
        if changees:
            changees.with_context(bf_cred_sans_horodatage=True).write(
                {'mfa_last_reviewed': fields.Date.context_today(self)})
        return resultat

    # Ce qui, changé, vaut une revue. `mfa_state` en est absent : il est calculé,
    # il suit ces champs-là et ne se touche pas à la main.
    _MFA_CHAMPS_DE_REVUE = {
        'mfa_type', 'mfa_vault_id', 'mfa_reference', 'mfa_holder_ids',
        'mfa_holder_note', 'mfa_recovery',
    }

    def _mfa_empreinte(self):
        """Ce que le registre dit du facteur, sous une forme comparable.

        Les identifiants du many2many sont TRIÉS : sans ça, un simple
        réordonnancement se lirait comme un changement et poserait une date de
        revue que personne n'a faite.
        """
        self.ensure_one()
        return (
            self.mfa_type, self.mfa_vault_id.id, self.mfa_reference or '',
            tuple(sorted(self.mfa_holder_ids.ids)), self.mfa_holder_note or '',
            self.mfa_recovery,
        )

    # -------------------------------------------------------------------------
    # Deuxième facteur — calculs et garde-fou
    # -------------------------------------------------------------------------

    # Les types qui désignent un vrai facteur, celui qu'il faut pouvoir produire
    # le jour où on en a besoin. « none » et « unknown » n'en sont pas, et les
    # codes de secours seuls sont déjà leur propre relève.
    _MFA_TYPES_REELS = ('totp', 'hardware', 'app', 'sms', 'email', 'other')

    @api.depends('mfa_type', 'mfa_vault_id', 'mfa_holder_ids',
                 'mfa_holder_note', 'mfa_recovery')
    def _compute_mfa_state(self):
        """Résume en un mot ce que le registre sait du deuxième facteur.

        L'état ne juge pas la sécurité du facteur, il juge ce qu'on en SAIT. Un
        facteur que personne n'est nommé pour produire, ou dont la reprise n'est
        pas écrite, est un facteur qui bloquera quelqu'un un jour, quelle que
        soit sa qualité cryptographique.
        """
        for record in self:
            if record.mfa_type == 'unknown':
                record.mfa_state = 'unknown'
            elif record.mfa_type == 'none':
                record.mfa_state = 'absent'
            elif record.mfa_type not in record._MFA_TYPES_REELS:
                # Codes de secours seuls : documenté, et sa propre relève.
                record.mfa_state = 'covered'
            else:
                porteur_nomme = bool(
                    record.mfa_vault_id or record.mfa_holder_ids
                    or record.mfa_holder_note
                )
                reprise_ecrite = record.mfa_recovery not in ('unknown', 'none')
                record.mfa_state = (
                    'covered' if porteur_nomme and reprise_ecrite else 'at_risk'
                )

    @api.depends('mfa_vault_id.item_url_pattern', 'mfa_reference')
    def _compute_mfa_item_url(self):
        """Un lien vers l'élément chez son porteur, construit sans rien lire.

        C'est tout le raccordement : Odoo sait où aller, il n'y va pas. La
        référence est encodée pour l'URL, jamais interpolée telle quelle.
        """
        for record in self:
            gabarit = record.mfa_vault_id.item_url_pattern
            if gabarit and record.mfa_reference and '{ref}' in gabarit:
                record.mfa_item_url = gabarit.replace(
                    '{ref}', quote(record.mfa_reference, safe=''))
            else:
                record.mfa_item_url = False

    # Les champs surveillés, et avec quelle sévérité.
    #
    # `notes` est le VRAI chemin de fuite : personne ne colle une graine dans
    # « Référence chez le porteur », mais on colle volontiers les instructions
    # d'enrôlement en entier dans les notes, adresse otpauth:// comprise.
    #
    # ⚠️ Les notes sont du texte libre, donc on n'y applique que les deux règles
    # CERTAINES. La règle du base32 nu y ferait des faux positifs, et un faux
    # positif sur un champ de rédaction bloquerait une sauvegarde sans porte de
    # sortie.
    _CHAMPS_SANS_GRAINE = {
        'mfa_reference': True,
        'mfa_holder_note': True,
        'notes': False,
    }

    @api.constrains('mfa_reference', 'mfa_holder_note', 'notes')
    def _check_no_otp_secret(self):
        """Refuse une graine dans les champs du registre.

        La promesse du module est qu'il ne détient aucune graine. Sans cette
        contrainte, la promesse ne tiendrait qu'à la discipline de qui saisit,
        et le premier collage d'une adresse otpauth:// la briserait en silence.
        """
        etiquettes = {
            'mfa_reference': _('Référence chez le porteur'),
            'mfa_holder_note': _('Autre porteur'),
            'notes': _('Notes'),
        }
        for record in self:
            for champ, strict in self._CHAMPS_SANS_GRAINE.items():
                etiquette = etiquettes[champ]
                raison = otp_secret_reason(record[champ], strict=strict)
                if raison:
                    raise ValidationError(_(
                        "Le champ « %(champ)s » a reçu %(raison)s.\n\n"
                        "Ce registre ne garde aucune graine : il dit seulement "
                        "où vit le deuxième facteur et qui peut produire un "
                        "code. Range la graine chez son porteur, et mets ici "
                        "l'étiquette qui permet de la retrouver.",
                        champ=etiquette, raison=raison,
                    ))

    # -------------------------------------------------------------------------
    # Statut d'expiration
    # -------------------------------------------------------------------------

    @api.depends('expiration_date', 'state')
    def _compute_expiration_status(self):
        """Calculer le statut d'expiration basé sur la date d'expiration."""
        today = fields.Date.today()
        warning_date = today + timedelta(days=30)
        for record in self:
            if record.state == 'revoked':
                record.is_expired = False
                record.is_expiring_soon = False
            elif record.expiration_date:
                record.is_expired = record.expiration_date < today
                record.is_expiring_soon = (
                    not record.is_expired and
                    record.expiration_date <= warning_date
                )
            else:
                record.is_expired = False
                record.is_expiring_soon = False

    # -------------------------------------------------------------------------
    # Actions d'état
    # -------------------------------------------------------------------------

    def action_verify(self):
        """Marquer l'identifiant comme vérifié."""
        self.write({
            'last_verified': fields.Datetime.now(),
            'state': 'active',
        })
        self.message_post(body=_('Identifiant vérifié comme fonctionnel.'))

    def action_revoke(self):
        """Marquer l'identifiant comme révoqué."""
        self.write({'state': 'revoked'})
        self.message_post(body=_('Identifiant révoqué.'))

    def action_reactivate(self):
        """Réactiver un identifiant révoqué."""
        self.write({'state': 'active'})
        self.message_post(body=_('Identifiant réactivé.'))

    def action_rotate_password(self):
        """Ouvrir l'assistant de rotation de mot de passe."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Rotation de mot de passe'),
            'res_model': 'project.credential.rotate.wizard',
            'views': [[False, 'form']],
            'target': 'new',
            'context': {
                'default_credential_id': self.id,
            },
        }

    # -------------------------------------------------------------------------
    # Méthodes de tâche planifiée
    # -------------------------------------------------------------------------

    @api.model
    def _cron_check_expiring_credentials(self):
        """Vérifier les identifiants expirants/expirés et mettre à jour les statuts."""
        today = fields.Date.today()
        warning_date = today + timedelta(days=30)

        # Trouver les identifiants expirés
        expired = self.search([
            ('expiration_date', '<', today),
            ('state', 'not in', ['expired', 'revoked']),
        ])
        if expired:
            expired.write({'state': 'expired'})
            for cred in expired:
                cred.message_post(
                    body=_('Identifiant expiré.'),
                    message_type='notification',
                )

        # Trouver les identifiants qui expirent bientôt
        expiring = self.search([
            ('expiration_date', '>=', today),
            ('expiration_date', '<=', warning_date),
            ('state', '=', 'active'),
        ])
        if expiring:
            expiring.write({'state': 'expiring'})
            for cred in expiring:
                cred.message_post(
                    body=_("L'identifiant expirera le %s.") % cred.expiration_date,
                    message_type='notification',
                )

        _logger.info(
            "Vérification d'expiration des identifiants : %d expirés, %d expirant bientôt",
            len(expired), len(expiring)
        )

    # -------------------------------------------------------------------------
    # Nom d'affichage
    # -------------------------------------------------------------------------

    def name_get(self):
        """Inclure le projet et le type dans le nom d'affichage."""
        result = []
        for record in self:
            name = record.name
            if record.project_id:
                name = f'[{record.project_id.name}] {name}'
            result.append((record.id, name))
        return result
