"""Ce que le connecteur Expedibox apprend ici : écouter.

Le module d'accès lui a appris à PARLER au casier (inscrire un résident, le retirer).
Ce module lui apprend à ÉCOUTER : le casier envoie un avis à chaque dépôt et à
chaque retrait, et cet avis doit atterrir quelque part de sûr.

🔴 **L'avis d'Expedibox ne porte aucune signature.** Sa spécification
(``x-webhooks: reservation_event``) ne prévoit ni secret partagé, ni empreinte,
ni horodatage signé : rien qui prouve que l'appel vient bien du casier. Deux
conséquences, et elles tiennent tout le module :

1. l'adresse de l'avis porte elle-même un secret (le jeton d'écoute), et c'est
   la seule chose qui sépare un avis d'Expedibox d'un appel de n'importe qui ;
2. **le corps reçu ne fait jamais foi.** Il nomme une réservation ; ce que nous
   en faisons est décidé après une relecture de cette réservation chez
   Expedibox, avec notre propre jeton d'application.

⚠️ Le jeton d'écoute n'est donc pas un mot de passe de confort : c'est le seul
mur. Il est réservé au groupe Gestionnaire, il se régénère d'un bouton, et le
changer coupe l'ancienne adresse à l'instant.
"""
import hmac
import logging
import secrets

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

_logger = logging.getLogger(__name__)

# 32 octets tirés au sort, rendus en base64 d'URL (`secrets.token_urlsafe`) :
# 43 caractères, alphabet A-Z a-z 0-9 avec « - » et « _ », rien à échapper dans
# une adresse, et assez d'entropie pour qu'une adresse devinée ne soit pas un
# risque sérieux.
TOKEN_BYTES = 32


class BfPropertyAccessConnector(models.Model):
    _inherit = "bf.property.access.connector"

    expedibox_notice_token = fields.Char(
        string="Jeton d'écoute des avis",
        groups="bf_property_core.group_bf_property_manager",
        copy=False,
        help="Le secret que porte l'adresse donnée à Expedibox. Il tient lieu "
             "de signature : leurs avis n'en ont pas. Le bouton « Nouveau "
             "jeton d'écoute » le remplace, et coupe l'ancienne adresse.",
    )
    expedibox_notice_url = fields.Char(
        string="Adresse à donner à Expedibox",
        compute="_compute_expedibox_notice_url",
        groups="bf_property_core.group_bf_property_manager",
        help="À coller dans « Reservation Webhook URL » au portail développeur "
             "d'Expedibox, pour cette application.",
    )
    expedibox_notice_user_id = fields.Many2one(
        "res.users",
        string="Responsable des avis",
        domain=[("share", "=", False)],
        default=lambda self: self.env.user,
        help="Reçoit les avis qu'aucun point d'accès ne réclame : un dépôt "
             "dans un casier que ce logiciel ne gère pas, un avis qu'Expedibox "
             "refuse de confirmer.",
    )
    expedibox_locker_notifies = fields.Boolean(
        string="Le casier avise lui-même le résident",
        default=True,
        help="Vrai par défaut : Expedibox écrit au destinataire dès le dépôt "
             "(champ « notify_receiver » de la réservation, vrai par défaut "
             "chez eux). Symbifox consigne alors le colis sans envoyer un "
             "second avis. Décochez-le seulement si le casier est réglé pour "
             "ne rien envoyer.",
    )

    @api.depends("expedibox_notice_token")
    def _compute_expedibox_notice_url(self):
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url") or ""
        for connector in self:
            token = connector.sudo().expedibox_notice_token
            connector.expedibox_notice_url = (
                "%s/symbifox/expedibox/avis/%s" % (base.rstrip("/"), token)
                if token else False
            )

    @api.constrains("kind", "expedibox_notice_token", "expedibox_organization_id",
                    "expedibox_notice_user_id")
    def _check_expedibox_notice(self):
        """Écouter sans savoir qui parle, ou sans personne à prévenir, n'a pas de sens.

        🔴 L'identifiant d'organisation est le second contrôle de l'avis (après
        le jeton de l'adresse) : sans lui, le module accepterait un avis au nom
        de n'importe quelle organisation et devrait deviner laquelle. Et sans
        responsable, un avis que personne ne réclame tomberait dans le vide.
        """
        for connector in self:
            if not connector.sudo().expedibox_notice_token:
                continue
            if connector.kind != "expedibox":
                raise ValidationError(_(
                    "Seul un connecteur Expedibox écoute des avis de casier : "
                    "retirez le jeton d'écoute de « %s ».") % connector.name)
            if not connector.sudo().expedibox_organization_id:
                raise ValidationError(_(
                    "« %s » écoute les avis d'Expedibox : il lui faut "
                    "l'identifiant d'organisation, qu'Expedibox envoie dans "
                    "l'en-tête de chaque avis. C'est le seul moyen de vérifier "
                    "qu'un avis parle bien de cette organisation.") % connector.name)
            if not connector.expedibox_notice_user_id:
                raise ValidationError(_(
                    "« %s » écoute les avis d'Expedibox : il lui faut un "
                    "responsable, qui reçoit les avis qu'aucun point ne "
                    "réclame.") % connector.name)

    def action_expedibox_new_notice_token(self):
        """Tire un jeton neuf. L'ancienne adresse cesse de répondre à l'instant."""
        self.ensure_one()
        # 🔴 Pas `_ensure_organisation_decides` : ce mixin est sur les modèles
        # du journal, pas sur le connecteur (essais du 2026-11-19 — le bouton
        # levait une AttributeError pour tout le monde, gestionnaire compris).
        # Et pas seulement le `groups` de la vue : un appel direct par RPC ne
        # passe pas par la vue.
        if not (self.env.su or self.env.user.has_group(
            "bf_property_core.group_bf_property_manager"
        )):
            raise AccessError(_(
                "Changer le jeton d'écoute d'un casier relève du syndicat."))
        if self.kind != "expedibox":
            raise UserError(_("« %s » n'est pas un connecteur Expedibox.") % self.name)
        self.sudo().write({"expedibox_notice_token": secrets.token_urlsafe(TOKEN_BYTES)})
        # ⚠️ Ce modèle n'a pas de fil : la trace va au journal du serveur, et
        # jamais la valeur du jeton — un journal se lit, s'exporte et se colle
        # dans un billet de soutien.
        _logger.info("Connecteur %s : jeton d'écoute des avis remplacé par %s",
                     self.id, self.env.user.login)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "warning",
                "sticky": True,
                "title": _("Nouvelle adresse d'écoute"),
                "message": _(
                    "Recopiez l'adresse au portail développeur d'Expedibox "
                    "(« Reservation Webhook URL »). L'ancienne ne répond plus : "
                    "tant que la nouvelle n'y est pas, aucun dépôt ne sera "
                    "consigné."),
            },
        }

    # ── Lecture d'une réservation ────────────────────────────────────────

    def _expedibox_notice_identity(self, organization_id):
        """Vrai si cet en-tête d'organisation est bien celui de ce connecteur.

        Comparaison en temps constant, sur des OCTETS : `compare_digest` lève
        dès qu'une des deux chaînes n'est pas ASCII, et l'en-tête est choisi
        par l'appelant. Un accent y rendait un 500 au lieu d'un 403 — et un
        identifiant d'organisation accentué, collé par un gestionnaire, aurait
        fait échouer TOUS les vrais avis.
        """
        self.ensure_one()
        mine = (self.sudo().expedibox_organization_id or "").encode("utf-8")
        try:
            offert = (organization_id or "").encode("utf-8")
        except (UnicodeEncodeError, AttributeError):
            return False
        return bool(mine) and hmac.compare_digest(mine, offert)
