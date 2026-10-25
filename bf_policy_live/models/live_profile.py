# -*- coding: utf-8 -*-
"""Séquestre des clés de profil persistant de la clé USB live.

POURQUOI LA CLÉ VIT ICI ET NULLE PART AILLEURS
Un profil persistant est un fichier LUKS2 par personne, posé sur la clé USB.
Si sa clé de déchiffrement voyageait avec la clé USB, perdre la clé USB
reviendrait à livrer le profil. Si elle était tapée, elle serait choisie par un
humain, donc faible, et oubliée. Elle est donc tirée et gardée ici, chiffrée
avec la clé de séquestre de bf_policy (lue dans l'environnement ou odoo.conf,
jamais dans la base : un dump volé n'ouvre aucun profil), et ne sort qu'après
une authentification par QR auprès d'Authentik.

RÉVOCATION = EFFACEMENT À DISTANCE
« Révoquer » efface la clé séquestrée. Le fichier LUKS resté sur la clé USB
devient illisible pour tout le monde, l'hébergeur compris : c'est voulu. La fiche
révoquée reste en place pour que la clé live reçoive un refus explicite (403)
au lieu d'en recréer une : on ne lève une révocation qu'en supprimant la fiche,
depuis Odoo, avec le droit dédié.

QUI LIT QUOI
- Le contrôleur (sudo) : la clé, pour la personne authentifiée seulement.
- Les gestionnaires de bf_policy (base.group_system) : la fiche, jamais la clé.
- Le droit « Peut révéler les phrases de passe de disque » : révoquer et
  supprimer. Aucun bouton ne révèle la clé d'un profil : personne n'en a
  besoin, la clé live la redemande elle-même.
- Tout autre compte (interne ordinaire, portail) : rien, aucune ACL.
"""
import base64
import logging
import secrets

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.tools import clean_context

from odoo.addons.bf_policy.models import escrow

_logger = logging.getLogger(__name__)

# 32 octets = 256 bits : la clé est donnée telle quelle à LUKS (keyfile), il
# n'y a pas de dictionnaire à lui opposer, donc ni sel ni dérivation.
KEY_BYTES = 32
_ESCROW_GROUP = "bf_policy.group_disk_escrow_read"


class ProfileRevoked(Exception):
    """La fiche existe et a été révoquée : le contrôleur répond 403."""


class SubjectMismatch(Exception):
    """Le jeton ne vient pas du même compte Authentik que celui qui a créé le
    profil (revendication `sub`) : le contrôleur répond 403."""


class BfPolicyLiveProfile(models.Model):
    _name = "bf.policy.live.profile"
    _description = "Blue Fox OS — Profil persistant de clé USB live"
    _rec_name = "user_id"
    _order = "last_served_on desc, id desc"

    user_id = fields.Many2one(
        "res.users", string="Personne", required=True, ondelete="cascade",
        index=True, readonly=True)
    # groups= : même un administrateur Odoo ne lit pas cette colonne sans le
    # droit dédié, comme disk_passphrase_enc de bf_policy. Elle n'est dans
    # aucune vue : personne n'a à la voir, même chiffrée.
    key_enc = fields.Char(
        "Clé (chiffrée)", copy=False, readonly=True, groups=_ESCROW_GROUP)
    # Sujet Authentik (`sub` du jeton) de la personne qui a créé le profil.
    # Le rattachement à l'usager Odoo se fait par UNE revendication (souvent
    # le courriel), qu'un compte Authentik peut parfois modifier lui-même :
    # épingler le `sub` empêche qu'un autre compte, qui prendrait ce courriel
    # plus tard, se fasse servir la clé.
    # groups= du séquestre et non base.group_system : un
    # administrateur qui lirait ce `sub` pourrait, en pointant l'URL userinfo
    # de la politique vers un faux serveur, forger un jeton accepté. Sans le
    # `sub`, il lui faut d'abord s'attribuer le droit dédié, ce qui se voit.
    idp_subject = fields.Char(
        "Sujet Authentik", copy=False, readonly=True,
        groups=_ESCROW_GROUP,
        help="Revendication « sub » du jeton qui a créé ce profil. Un jeton "
             "d'un autre compte Authentik est refusé, même s'il désigne la "
             "même personne dans Odoo.")
    created_on = fields.Datetime(
        "Créé le", default=fields.Datetime.now, readonly=True, copy=False)
    last_served_on = fields.Datetime("Dernière remise", readonly=True, copy=False)
    serve_count = fields.Integer("Remises", default=0, readonly=True, copy=False)
    revoked = fields.Boolean(
        "Révoqué", default=False, readonly=True, copy=False,
        help="La clé a été effacée : le profil chiffré resté sur la clé USB "
             "est illisible. Supprimer la fiche lève la révocation ; la "
             "personne repart alors d'un profil neuf.")
    revoked_on = fields.Datetime("Révoqué le", readonly=True, copy=False)
    revoked_by = fields.Many2one(
        "res.users", string="Révoqué par", readonly=True, copy=False)

    _sql_constraints = [
        ("user_uniq", "unique(user_id)",
         "Une personne n'a qu'un profil persistant de clé live."),
    ]

    # ------------------------------------------------------------ écritures
    # Les ACL ne donnent ni création ni écriture à personne : tout passe par
    # le contrôleur ou par les boutons, en sudo. On le vérifie quand même ici,
    # parce qu'un groupe ajouté plus tard aux ACL rouvrirait sinon la porte au
    # RPC (réécrire user_id, poser une clé choisie, lever `revoked`).
    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(_(
                "Un profil de clé live ne se crée que par la clé live elle-même."))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            raise AccessError(_(
                "Un profil de clé live ne se modifie pas à la main."))
        return super().write(vals)

    def unlink(self):
        # Supprimer = lever la révocation (ou effacer un profil actif). Même
        # droit que la révocation, et une trace au journal.
        if not self.env.su and not self.env.user.has_group(_ESCROW_GROUP):
            raise AccessError(_(
                "Il faut le droit « Peut révéler les phrases de passe de "
                "disque » pour supprimer un profil de clé live."))
        for rec in self:
            _logger.warning(
                "[bf_policy_live] profil supprime : user=%s (revoque=%s) par %s",
                rec.user_id.login, rec.revoked, self.env.user.login)
        return super().unlink()

    # -------------------------------------------------------------- boutons
    def action_revoke(self):
        """Bouton « Révoquer » : efface la clé, donc le profil à distance.

        Publique parce que c'est un bouton ; la garde de groupe est DEDANS,
        une méthode publique s'appelant par RPC depuis n'importe quel compte.
        """
        if not self.env.user.has_group(_ESCROW_GROUP):
            raise AccessError(_(
                "Il faut le droit « Peut révéler les phrases de passe de "
                "disque » pour révoquer un profil de clé live."))
        now = fields.Datetime.now()
        for rec in self.sudo():
            if rec.revoked:
                continue
            rec.write({
                "key_enc": False,
                "revoked": True,
                "revoked_on": now,
                "revoked_by": self.env.user.id,
            })
            _logger.warning(
                "[bf_policy_live] profil revoque (cle effacee) : user=%s par %s",
                rec.user_id.login, self.env.user.login)
        return True

    def action_reset_subject(self):
        """Bouton « Réépingler le compte » : oublie le sujet Authentik retenu.

        Sert quand le fournisseur d'identité change sa façon de calculer le
        `sub` (mode de sujet du fournisseur modifié) : sans ce bouton, la
        seule issue serait de supprimer le profil, donc de le perdre. La
        prochaine remise épingle le sujet du jeton présenté.
        """
        if not self.env.user.has_group(_ESCROW_GROUP):
            raise AccessError(_(
                "Il faut le droit « Peut révéler les phrases de passe de "
                "disque » pour réépingler un profil de clé live."))
        self.sudo().write({"idp_subject": False})
        for rec in self:
            _logger.warning(
                "[bf_policy_live] sujet Authentik oublie : user=%s par %s",
                rec.sudo().user_id.login, self.env.user.login)
        return True

    # ------------------------------------------------- service (privé, RPC)
    # ⚠️ Toutes privées : une méthode sans `_` s'appelle par RPC depuis
    # n'importe quel compte connecté, portail compris, sans ACL.
    @api.model
    def _serve_key(self, user, subject, create):
        """Rend ``(clé_b64, créée)`` pour `user`, ou lève.

        - ``escrow.EscrowUnavailable`` : séquestre non configuré, rien n'est
          créé (fail-closed : jamais de clé qu'on ne peut pas ranger) ;
        - ``ProfileRevoked`` : fiche révoquée, rien n'est recréé ;
        - ``SubjectMismatch`` : autre compte Authentik que le créateur ;
        - ``LookupError`` : pas de fiche et `create` faux.

        La clé ne passe par aucun journal. L'appelant a déjà authentifié
        `user` ; cette méthode ne fait aucune vérification d'identité.
        """
        if not (self.env.su and user and len(user) == 1):
            raise AccessError("_serve_key : appel interne en sudo seulement")
        if not subject:
            raise SubjectMismatch()
        # Sans les default_* d'un contexte d'appel : ils s'ajouteraient aux
        # valeurs de create() (revoked, revoked_by…).
        self = self.with_context(clean_context(self.env.context))
        # Fail-closed AVANT tout : sans séquestre, ni création ni lecture.
        escrow._fernet()
        profile = self.with_context(active_test=False).search(
            [("user_id", "=", user.id)], limit=1)
        now = fields.Datetime.now()
        if profile:
            if profile.revoked:
                raise ProfileRevoked()
            if profile.idp_subject and profile.idp_subject != subject:
                raise SubjectMismatch()
            key = escrow.decrypt(profile.key_enc or "")
            if not key:
                # Fiche non révoquée mais vide : ne devrait pas exister. On
                # refuse plutôt que d'en tirer une autre, qui rendrait le
                # profil existant illisible sans que personne l'ait décidé.
                raise ValueError("fiche sans cle")
            vals = {"last_served_on": now, "serve_count": profile.serve_count + 1}
            if not profile.idp_subject:
                vals["idp_subject"] = subject
            profile.write(vals)
            return key, False
        if not create:
            raise LookupError()
        key = base64.b64encode(secrets.token_bytes(KEY_BYTES)).decode()
        self.create({
            "user_id": user.id,
            "key_enc": escrow.encrypt(key),
            "idp_subject": subject,
            "created_on": now,
            "last_served_on": now,
            "serve_count": 1,
        })
        return key, True


class BfPolicyOrg(models.Model):
    _inherit = "bf.policy.org"

    live_oidc_client_id = fields.Char(
        "Client OIDC de la clé live", default="blue-fox-os-live",
        help="Client OIDC public (flux d'appareil) de la clé USB live Blue Fox "
             "OS dans Authentik. Seul un jeton émis à CE client (revendication "
             "aud ou azp) obtient la clé d'un profil persistant, même quand la "
             "vérification d'audience de l'installation est coupée. Vide = "
             "aucune clé servie. Doit différer du client d'installation.")

    @api.constrains("live_oidc_client_id", "oidc_client_id",
                    "nextcloud_oidc_client_id")
    def _check_live_client_distinct(self):
        # Un même client pour l'installation et la clé live ferait d'un jeton
        # d'installation (ou de Nextcloud) un passe pour toutes les clés de
        # profil de la personne.
        for org in self:
            live = (org.live_oidc_client_id or "").strip()
            if live and live in {(org.oidc_client_id or "").strip(),
                                 (org.nextcloud_oidc_client_id or "").strip()}:
                raise ValidationError(_(
                    "Le client OIDC de la clé live doit différer du client "
                    "d'installation et du client Nextcloud."))
