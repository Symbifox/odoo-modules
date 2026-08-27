"""bf.email.identity — les adresses au nom desquelles une personne peut écrire.

Une personne n'a qu'un ``res.users.email`` et qu'une ``res.users.signature``.
Quand elle porte plusieurs casquettes — deux sociétés, une marque, une adresse
héritée — tout ce qu'elle envoie part sous la seule identité que le compte Odoo
lui connaît. C'est ce modèle qui casse cette contrainte : une rangée par adresse
qu'elle peut légitimement porter, avec sa propre signature et, au besoin, son
propre serveur sortant.

Trois choses qui expliquent la forme du modèle.

**Ce n'est pas ``bf.email.account``.** Un compte est une boîte qu'on relève en
IMAP : ``login`` et ``password`` y sont obligatoires. Or l'adresse qu'on veut
porter n'est pas forcément une boîte qu'on relève — c'est exactement le cas qui
a motivé le modèle. Les deux notions se recoupent souvent, jamais toujours ;
``_sync_from_accounts`` fabrique donc l'identité correspondant à chaque compte,
et le reste se déclare à la main.

**Une identité n'est utilisable qu'une fois vérifiée.** Sans ce garde-fou,
n'importe quel usager interne pourrait écrire au nom de n'importe qui, dans un
message que le destinataire lirait comme authentique. Les identités que le
module déduit d'une preuve de possession — l'adresse du compte Odoo, le login
d'un compte IMAP — naissent vérifiées ; celles qu'on tape naissent en attente,
et seul un administrateur courriel les vérifie. Même raisonnement, et même
double garde Python, que ``forward_allow_external`` sur les règles.

**Elle ne promet pas la délivrabilité.** Poser une identité fait sortir le
courriel avec le bon ``De`` ; elle ne crée ni le serveur sortant ni les
enregistrements SPF/DKIM du domaine. ``delivery_warning`` dit lequel des deux
manque plutôt que de laisser la personne le découvrir dans les indésirables.
"""

import logging
from email.utils import formataddr, parseaddr

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import email_normalize

_logger = logging.getLogger(__name__)


class BfEmailIdentity(models.Model):
    _name = "bf.email.identity"
    _description = "Identité d'expédition"
    _order = "user_id, sequence, id"
    _rec_name = "display_name"

    sequence = fields.Integer(default=10)
    active = fields.Boolean(string="Active", default=True)

    user_id = fields.Many2one(
        "res.users",
        string="Propriétaire",
        required=True,
        ondelete="cascade",
        index=True,
        default=lambda self: self.env.user,
        help="La personne qui peut écrire sous cette identité.",
    )
    name = fields.Char(
        string="Nom affiché",
        required=True,
        help="Le nom que verra le destinataire, avant l'adresse. "
             "Ex. : Jane Doe.",
    )
    email = fields.Char(
        string="Adresse",
        required=True,
        help="L'adresse qui apparaîtra dans le « De ».",
    )
    signature_html = fields.Html(
        string="Signature",
        sanitize=False,
        help="Signature propre à cette identité. Laissée vide, c'est celle de "
             "la fiche utilisateur qui sert.",
    )
    mail_server_id = fields.Many2one(
        "ir.mail_server",
        string="Serveur sortant",
        help="Forcer un serveur précis. Laissé vide, Odoo choisit celui dont "
             "le filtre d'expéditeur couvre l'adresse.",
    )
    is_default = fields.Boolean(
        string="Par défaut",
        help="L'identité proposée quand rien d'autre ne s'impose.",
    )
    verified = fields.Boolean(
        string="Vérifiée",
        help="Une identité non vérifiée ne peut pas servir à expédier. "
             "Réservé aux administrateurs courriel.",
    )
    account_id = fields.Many2one(
        "bf.email.account",
        string="Compte IMAP",
        ondelete="set null",
        help="Le compte dont cette identité est issue, s'il y en a un.",
    )

    display_name = fields.Char(compute="_compute_display_name", store=False)
    email_normalized = fields.Char(
        compute="_compute_email_normalized", store=True, index=True)
    email_formatted = fields.Char(compute="_compute_email_formatted")
    delivery_warning = fields.Char(compute="_compute_delivery_warning")

    _sql_constraints = [
        ("bf_email_identity_uniq",
         "UNIQUE(user_id, email_normalized)",
         "Cette adresse est déjà déclarée comme identité pour cette personne."),
    ]

    # ------------------------------------------------------------------
    # Calculs
    # ------------------------------------------------------------------
    @api.depends("email")
    def _compute_email_normalized(self):
        for identity in self:
            identity.email_normalized = email_normalize(identity.email or "")

    @api.depends("name", "email")
    def _compute_email_formatted(self):
        for identity in self:
            identity.email_formatted = formataddr(
                (identity.name or "", identity.email_normalized
                 or identity.email or ""))

    @api.depends("name", "email", "verified")
    def _compute_display_name(self):
        """Lisible à l'écran, donc PAS ``email_formatted``.

        ``formataddr`` encode le nom en RFC 2047 dès qu'il porte un accent :
        « Pas vérifiée » devient « =?utf-8?b?UGFzIHbDqXJpZmnDqWU=?= ». Correct
        sur le fil, illisible dans un message d'erreur ou une liste déroulante.
        """
        for identity in self:
            address = identity.email_normalized or identity.email or ""
            if identity.name and address:
                label = f"{identity.name} <{address}>"
            else:
                label = identity.name or address or _("(incomplète)")
            if not identity.verified:
                label = _("%s — en attente de vérification", label)
            identity.display_name = label

    @api.depends("email", "mail_server_id")
    def _compute_delivery_warning(self):
        """Nommer ce qui empêchera ce « De » de sortir intact.

        Deux pannes distinctes, et il a fallu les deux : la première version ne
        regardait que si ``_find_mail_server`` réécrivait l'adresse, ce qui
        rate le cas le plus courant.

        1. **Aucun ``from_filter`` ne couvre l'adresse, et Odoo la réécrit.**
           Le courriel part sous l'identité de notification. Intention perdue.
        2. **Aucun ``from_filter`` ne la couvre, mais Odoo la laisse passer.**
           C'est ce qui arrive à l'étape 4 du sélecteur quand aucune adresse de
           notification n'est configurée : le « De » survit, mais il sort par
           un serveur que le domaine n'autorise pas. SPF et DKIM échouent chez
           le destinataire, et personne ne le voit d'ici.

        Le cas 2 n'était pas couvert, et c'est le plus fréquent — un locataire
        sans ``mail.default.from``, exactement la configuration observée.
        """
        Server = self.env["ir.mail_server"].sudo()
        for identity in self:
            identity.delivery_warning = False
            address = identity.email_normalized
            if not address or identity.mail_server_id:
                continue
            if Server._bf_covers_from(address):
                continue
            _server, resolved = Server._find_mail_server(address)
            if email_normalize(resolved or "") != address:
                identity.delivery_warning = _(
                    "Aucun serveur sortant ne couvre %(addr)s : Odoo "
                    "remplacerait le « De » par %(sub)s. Créez un serveur "
                    "dont le filtre d'expéditeur porte cette adresse ou son "
                    "domaine, et publiez SPF/DKIM pour ce domaine.",
                    addr=address,
                    sub=resolved or _("l'adresse de notification"),
                )
            else:
                identity.delivery_warning = _(
                    "Aucun serveur sortant ne déclare %(addr)s dans son "
                    "filtre d'expéditeur. Le « De » sortira intact, mais par "
                    "un serveur que ce domaine n'autorise pas : SPF et DKIM "
                    "échoueront chez le destinataire. Créez un serveur pour "
                    "ce domaine et publiez ses enregistrements DNS.",
                    addr=address,
                )


    # ------------------------------------------------------------------
    # Garde-fous
    # ------------------------------------------------------------------
    @api.constrains("email")
    def _check_email(self):
        for identity in self:
            if not email_normalize(identity.email or ""):
                raise ValidationError(_(
                    "« %s » n'est pas une adresse courriel valide.",
                    identity.email or ""))

    @api.constrains("verified", "user_id")
    def _check_verified_is_admin_granted(self):
        """Se vérifier soi-même viderait la garde de son sens.

        La règle d'enregistrement laisse chacun écrire ses propres identités,
        ce qui est voulu : on déclare ses adresses soi-même. Mais cocher
        « vérifiée » est ce qui autorise l'expédition sous ce nom, donc ça
        ne peut pas relever de la même main.
        """
        if self.env.su:
            return
        if self.env.user.has_group("bf_email_management.group_email_admin"):
            return
        if self.env.user.has_group("base.group_system"):
            return
        for identity in self:
            if identity.verified:
                raise ValidationError(_(
                    "Vérifier une identité d'expédition est réservé aux "
                    "administrateurs courriel : c'est cette case qui autorise "
                    "à écrire sous ce nom."))

    @api.constrains("is_default", "user_id", "active")
    def _check_single_default(self):
        for identity in self.filtered(lambda i: i.is_default and i.active):
            others = self.search([
                ("user_id", "=", identity.user_id.id),
                ("is_default", "=", True),
                ("id", "!=", identity.id),
            ])
            if others:
                others.write({"is_default": False})

    # ------------------------------------------------------------------
    # Résolution
    # ------------------------------------------------------------------
    def _signature_for(self):
        """La signature à poser pour cette identité.

        Repli sur celle de la fiche utilisateur : une identité neuve ne doit
        pas faire disparaître la signature que la personne avait déjà.
        Appelable sur un ensemble vide — le composeur s'ouvre parfois sans
        identité, et il lui faut quand même une signature.
        """
        identity = self[:1]
        if identity.signature_html and identity.signature_html.strip():
            return identity.signature_html
        user = identity.user_id or self.env.user
        return user.signature or ""

    @api.model
    def _usable_for(self, user):
        """Les identités dont ``user`` peut réellement se servir."""
        return self.sudo().search([
            ("user_id", "=", user.id),
            ("verified", "=", True),
            ("active", "=", True),
        ])

    @api.model
    def _default_for(self, user):
        """L'identité à proposer quand rien ne désigne mieux."""
        usable = self._usable_for(user)
        return usable.filtered("is_default")[:1] or usable[:1]

    @api.model
    def _for_account(self, account):
        """L'identité correspondant à la boîte où le courriel est arrivé.

        Répondre depuis la boîte qui a reçu est le seul défaut qui ne surprend
        personne — c'est déjà ce que fait le répondeur d'absence, qui expédie
        depuis ``account_id.login``.
        """
        if not account:
            return self.browse()
        usable = self._usable_for(account.user_id)
        by_account = usable.filtered(lambda i: i.account_id == account)
        if by_account:
            return by_account[:1]
        login = email_normalize(account.login or "")
        if login:
            match = usable.filtered(lambda i: i.email_normalized == login)
            if match:
                return match[:1]
        return self.browse()

    # ------------------------------------------------------------------
    # Semis
    # ------------------------------------------------------------------
    @api.model
    def _sync_from_accounts(self, users=None):
        """Fabriquer les identités que le module peut prouver.

        Deux sources de preuve : l'adresse de la fiche utilisateur, et le login
        de chaque compte IMAP que la personne a su configurer. Les deux sont
        des possessions démontrées, donc elles naissent vérifiées.

        Idempotent : on ne crée que ce qui manque, et on ne retouche jamais une
        rangée existante — un nom affiché ou une signature ajustés à la main ne
        doivent pas être écrasés au prochain passage.
        """
        Users = self.env["res.users"].sudo()
        if users is None:
            users = Users.search([("share", "=", False), ("active", "=", True)])
        created = self.browse()

        for user in users:
            existing = self.sudo().with_context(active_test=False).search([
                ("user_id", "=", user.id),
            ])
            known = set(existing.mapped("email_normalized"))
            vals_list = []

            own = email_normalize(user.email or "")
            if own and own not in known:
                vals_list.append({
                    "user_id": user.id,
                    "name": user.name or own,
                    "email": own,
                    "verified": True,
                    "is_default": not existing,
                    "sequence": 1,
                })
                known.add(own)

            accounts = self.env["bf.email.account"].sudo().search([
                ("user_id", "=", user.id),
            ])
            for account in accounts:
                login = email_normalize(account.login or "")
                if not login or login in known:
                    continue
                vals_list.append({
                    "user_id": user.id,
                    "name": user.name or login,
                    "email": login,
                    "account_id": account.id,
                    "verified": True,
                    "is_default": not existing and not vals_list,
                    "sequence": 10,
                })
                known.add(login)

            if vals_list:
                created |= self.sudo().create(vals_list)

        if created:
            _logger.info(
                "bf.email.identity : %s identité(s) semée(s) depuis les "
                "comptes et les fiches utilisateur.", len(created))
        return created


class IrMailServerFromCoverage(models.Model):
    """Savoir si un ``from_filter`` couvre une adresse, sans redire Odoo."""

    _inherit = "ir.mail_server"

    @api.model
    def _bf_covers_from(self, address):
        """Un serveur actif déclare-t-il cette adresse, ou son domaine ?

        Même lecture que les étapes 1 et 2 de ``_find_mail_server`` : le filtre
        est une liste séparée par des virgules, et chaque entrée vaut soit une
        adresse complète, soit un domaine nu.
        """
        address = email_normalize(address or "")
        if not address:
            return False
        domain = address.split("@")[-1]
        for server in self.sudo().search([]):
            for entry in (server.from_filter or "").split(","):
                entry = entry.strip().lower()
                if not entry:
                    continue
                if entry == address or entry == domain:
                    return True
        return False


class BfEmailAccountIdentity(models.Model):
    """Le compte IMAP gagne son identité dès qu'il naît."""

    _inherit = "bf.email.account"

    identity_ids = fields.One2many(
        "bf.email.identity", "account_id", string="Identités d'expédition")

    @api.model_create_multi
    def create(self, vals_list):
        accounts = super().create(vals_list)
        for account in accounts:
            try:
                self.env["bf.email.identity"]._sync_from_accounts(
                    account.user_id)
            except Exception:  # noqa: BLE001
                # Un compte doit pouvoir se créer même si le semis échoue :
                # l'identité se rattrape, la configuration IMAP non.
                _logger.exception(
                    "bf.email.identity : semis impossible pour le compte %s",
                    account.id)
        return accounts


def _parse_display(value):
    """``"Nom" <adresse>`` → ``(nom, adresse)``, tolérant l'adresse nue."""
    name, address = parseaddr(value or "")
    return name, email_normalize(address) or ""
