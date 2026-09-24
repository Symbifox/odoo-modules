import base64
import logging
import os
from datetime import timedelta
from urllib.parse import quote

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import config

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
    #
    # La clé vit HORS de la base depuis la 18.0.3.0.0. Avant, elle était rangée
    # dans `ir.config_parameter`, donc dans le MÊME pg_dump que les secrets
    # qu'elle protège : le « chiffrement au repos » ne protégeait pas contre ce
    # à quoi on croyait qu'il protégeait.
    #
    # Deux règles tiennent tout le reste :
    #   1. On ne GÉNÈRE jamais de clé. Une clé qui apparaît toute seule, c'est
    #      une clé que personne n'a rangée, donc une clé que personne ne pourra
    #      remettre après un sinistre.
    #   2. On n'écrit jamais en clair. L'ancien code retombait sur la valeur
    #      nue quand le chiffrement échouait, avec un simple avertissement au
    #      journal : une base pouvait se remplir de clair sans un seul message
    #      d'erreur, et rien après coup ne distinguait les deux.
    # -------------------------------------------------------------------------

    #: Le paramètre système historique. Gardé en LECTURE seulement, pour deux
    #: cas : la migration 18.0.3.0.0, et la restauration d'un dump d'avant la
    #: bascule (qui porte encore sa clé). Jamais écrit, jamais généré.
    _CLE_PARAM_HERITE = 'project_credential.encryption_key'

    #: Là où la clé se range vraiment.
    _CLE_ENV = 'BF_CREDENTIALS_FERNET_KEY'
    _CLE_CONF = 'bf_credentials_fernet_key'

    #: Ce que le calcul affiche quand la valeur en base ne se déchiffre pas.
    #: Voyant, et surtout refusé à l'écriture par les inverses : réenregistrer
    #: une fiche illisible chiffrerait le chiffré une deuxième fois.
    MARQUE_ILLISIBLE = '⚠️ déchiffrement impossible'

    @api.model
    def _cle_hors_base(self):
        """La clé rangée hors de la base : variable d'environnement, puis conf.

        Rend `bytes` ou None. Ne touche jamais à la base, donc utilisable depuis
        une migration comme depuis un cron.
        """
        cle = os.environ.get(self._CLE_ENV)
        if cle:
            return cle.encode()
        cle = config.get(self._CLE_CONF)
        if cle:
            return cle.encode()
        return None

    @api.model
    def _cle_heritee(self):
        """La clé de l'ancien paramètre système, en LECTURE seule.

        Présente tant que la bascule n'est pas finie, et dans tout dump pris
        avant elle. `get_param` et rien d'autre : surtout pas `set_param`.
        """
        cle = self.env['ir.config_parameter'].sudo().get_param(
            self._CLE_PARAM_HERITE)
        return cle.encode() if cle else None

    def _get_encryption_key(self):
        """La clé de chiffrement, cherchée hors de la base d'abord.

        Ordre : variable d'environnement, `odoo.conf`, puis le paramètre système
        hérité. Rend None si aucune des trois n'existe, et ne GÉNÈRE rien : les
        appelants lèvent, ce qui est le seul comportement qui se remarque.
        """
        if not Fernet:
            return None
        return self._cle_hors_base() or self._cle_heritee()

    @api.model
    def _exige_une_cle(self, pour_ecrire=False):
        """Rend la clé, ou lève en disant où la ranger.

        `pour_ecrire` refuse la clé héritée : chiffrer du neuf avec la clé qui
        dort dans la base perpétuerait le défaut que la bascule ferme. Lire avec
        elle reste permis, sinon un dump d'avant la bascule serait illisible.
        """
        if not Fernet:
            raise UserError(_(
                "Le paquet Python « cryptography » n'est pas installé : ce "
                "module ne peut ni chiffrer ni déchiffrer. Aucun secret ne "
                "sera écrit en clair."
            ))
        cle = self._cle_hors_base()
        if cle:
            return cle
        if not pour_ecrire:
            cle = self._cle_heritee()
            if cle:
                _logger.warning(
                    "Clé de chiffrement lue dans le paramètre système hérité "
                    "%s. La bascule de la clé hors de la base n'est pas terminée sur cette "
                    "base.", self._CLE_PARAM_HERITE,
                )
                return cle
        raise UserError(_(
            "Aucune clé de chiffrement n'est configurée. Rangez-la dans la "
            "variable d'environnement %(env)s ou dans %(conf)s de odoo.conf, "
            "puis redémarrez Odoo.\n\n"
            "Elle ne doit PAS vivre dans la base : un dump emporterait la clé "
            "avec ce qu'elle protège.",
            env=self._CLE_ENV, conf=self._CLE_CONF,
        ))

    @api.private
    @api.model
    def est_un_jeton_fernet(self, valeur):
        """Dit si une valeur STOCKÉE a la forme d'un jeton Fernet.

        Ne déchiffre rien et ne lit aucune clé : c'est ce qui permet de compter
        le clair d'une base sans avoir le droit de l'ouvrir. Un jeton Fernet est
        du base64 url-safe dont le premier octet est la version 0x80.
        """
        if not valeur:
            return False
        try:
            brut = base64.urlsafe_b64decode(valeur.encode())
        except (ValueError, TypeError):
            return False
        return len(brut) > 57 and brut[0] == 0x80

    def _encrypt_value(self, value):
        """Chiffrer une valeur texte avec le chiffrement symétrique Fernet.

        Lève plutôt que de rendre la valeur nue : c'est tout l'objet de la bascule.
        """
        if not value:
            return False
        cle = self._exige_une_cle(pour_ecrire=True)
        try:
            return Fernet(cle).encrypt(value.encode()).decode()
        except Exception as e:
            _logger.error('Échec du chiffrement : %s', type(e).__name__)
            raise UserError(_(
                "Le chiffrement a échoué, rien n'a été enregistré. Vérifiez la "
                "clé de chiffrement."
            )) from e

    def _decrypt_value(self, encrypted_value):
        """Déchiffrer une valeur chiffrée avec Fernet.

        Lève sur une valeur illisible au lieu de la rendre telle quelle. Rendre
        le jeton était le piège : à l'écran on lisait « gAAAAA… » comme si
        c'était le mot de passe, et réenregistrer la fiche chiffrait le chiffré.
        """
        if not encrypted_value:
            return False
        cle = self._exige_une_cle()
        try:
            return Fernet(cle).decrypt(encrypted_value.encode()).decode()
        except InvalidToken as e:
            raise UserError(_(
                "Cette valeur ne se déchiffre pas avec la clé configurée. Soit "
                "la clé n'est pas celle qui a servi à l'écrire, soit la valeur "
                "n'a jamais été chiffrée. Dans les deux cas, ne réenregistrez "
                "pas la fiche : cela chiffrerait le contenu une deuxième fois."
            )) from e
        except Exception as e:
            _logger.error('Échec du déchiffrement : %s', type(e).__name__)
            raise UserError(_(
                "Le déchiffrement a échoué. Vérifiez la clé de chiffrement."
            )) from e

    def _dechiffre_pour_affichage(self, valeur_chiffree):
        """Le déchiffrement des calculs : une marque voyante au lieu d'une erreur.

        Un calcul qui lève rend la LISTE entière inouvrable, y compris les
        fiches saines. La fiche fautive porte donc une marque, et les inverses
        refusent de la réécrire.
        """
        if not valeur_chiffree:
            return False
        try:
            return self._decrypt_value(valeur_chiffree)
        except UserError:
            _logger.error(
                "Identifiant %s : valeur illisible avec la clé configurée.",
                self.id,
            )
            return self.MARQUE_ILLISIBLE

    @api.model
    def verifier_chiffrement(self, domaine=None):
        """Compte ce qui est chiffré, ce qui ne l'est pas, ce qui est illisible.

        Le contrôle que la bascule réclamait : savoir s'il y a déjà du clair en base.
        Aucune valeur déchiffrée n'est rendue ni journalisée, seulement des
        comptes et des identifiants.

        Publique à dessein, pour qu'un déploiement puisse la jouer par RPC et
        prouver son résultat. D'où le verrou : elle ouvre chaque secret pour
        savoir s'il s'ouvre, ce que seul un gestionnaire a le droit de faire.

        `domaine` borne le balayage. Sans lui, le bilan porte sur TOUTE la base,
        ce qui est le bon défaut pour un contrôle d'après-migration, mais faux
        dès qu'une partie du coffre a été écrite avec une autre clé.
        """
        if not self._is_credential_manager():
            raise AccessError(_(
                "Seul un gestionnaire d'identifiants peut contrôler l'état du "
                "chiffrement du coffre."
            ))
        bilan = {'total': 0, 'chiffres': 0, 'en_clair': [], 'illisibles': []}
        for cred in self.sudo().search(domaine or []):
            for champ in ('password_encrypted', 'api_key_encrypted'):
                stocke = cred[champ]
                if not stocke:
                    continue
                bilan['total'] += 1
                repere = '%s.%s' % (cred.id, champ)
                if not self.est_un_jeton_fernet(stocke):
                    bilan['en_clair'].append(repere)
                    continue
                try:
                    cred._decrypt_value(stocke)
                except UserError:
                    bilan['illisibles'].append(repere)
                else:
                    bilan['chiffres'] += 1
        return bilan

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
                record.password = record._dechiffre_pour_affichage(
                    record.password_encrypted)

    def _inverse_password(self):
        """Chiffrer le mot de passe à l'écriture.

        Le masque et la marque d'illisibilité ne sont pas des mots de passe :
        les réécrire remplacerait le secret par son propre voyant, ou
        chiffrerait une deuxième fois ce qui l'est déjà.
        """
        for record in self:
            if record.password in (False, '', '********',
                                   record.MARQUE_ILLISIBLE):
                continue
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
                record.api_key = record._dechiffre_pour_affichage(
                    record.api_key_encrypted)

    def _inverse_api_key(self):
        """Chiffrer la clé API à l'écriture. Voir `_inverse_password`."""
        for record in self:
            if record.api_key in (False, '', '********',
                                  record.MARQUE_ILLISIBLE):
                continue
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
