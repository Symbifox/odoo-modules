"""Le compte chez un transporteur : des clés, un environnement, et une porte.

Un compte par société et par transporteur. C'est la société qui a le compte
chez UPS ou chez DHL — pas chaque syndicat : un gestionnaire d'immeubles
signe une fois et s'en sert pour tout son parc.

⚠️ **Les clés sont réservées au groupe Gestionnaire.** La Consultation voit
qu'un transporteur est branché et dans quel environnement ; elle n'a rien à
faire du secret. Et aucune clé ne paraît jamais dans un journal, un fil ou un
message d'erreur.

🔴 **Un essai et une production ne se mélangent pas.** UPS a deux hôtes
(``wwwcie`` et ``onlinetools``) et deux jeux de clés ; une clé d'essai sur
l'hôte de production répond 401, et l'inverse donne des suivis qui n'existent
pas. L'environnement est donc un champ du compte, pas un réglage global.
"""
import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from . import carrier_api as api_carrier
from . import carrier_dhl, carrier_purolator, carrier_ups

_logger = logging.getLogger(__name__)

CARRIERS = [
    ("ups", "UPS"),
    ("dhl", "DHL"),
    ("purolator", "Purolator"),
]
BACKENDS = {
    "ups": carrier_ups,
    "dhl": carrier_dhl,
    "purolator": carrier_purolator,
}
# On renouvelle le jeton un peu avant son heure : un appel qui part à la
# seconde près reviendrait 401 pour rien.
TOKEN_MARGIN = 60


class BfPropertyCarrierAccount(models.Model):
    _name = "bf.property.carrier.account"
    _description = "Compte chez un transporteur"
    _order = "carrier, environment"

    carrier = fields.Selection(CARRIERS, string="Transporteur", required=True)
    environment = fields.Selection(
        [("test", "Essai"), ("production", "Production")],
        string="Environnement", default="test", required=True,
        help="UPS a deux hôtes et deux jeux de clés. Une clé d'essai sur "
             "l'hôte de production est refusée, et l'inverse rend des suivis "
             "qui n'existent pas.",
    )
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", string="Société", required=True, index=True,
        default=lambda self: self.env.company,
    )
    locale = fields.Char(
        string="Langue des libellés", default="fr_CA",
        help="La langue dans laquelle le transporteur rend ses libellés "
             "d'état. UPS attend « fr_CA » ou « en_CA » ; DHL attend « fr » "
             "ou « en ».",
    )

    # ── UPS ──
    ups_client_id = fields.Char(
        string="UPS : identifiant client",
        groups="bf_property_core.group_bf_property_manager", copy=False)
    ups_client_secret = fields.Char(
        string="UPS : secret",
        groups="bf_property_core.group_bf_property_manager", copy=False)
    ups_account_number = fields.Char(
        string="UPS : numéro de compte",
        groups="bf_property_core.group_bf_property_manager", copy=False,
        help="Six chiffres. Facultatif : il voyage dans l'en-tête "
             "x-merchant-id de la demande de jeton.")
    ups_token = fields.Char(
        string="UPS : jeton en cours",
        groups="bf_property_core.group_bf_property_manager", copy=False,
        help="Gardé jusqu'à son expiration pour ne pas redemander un jeton à "
             "chaque colis. Il ne vaut que quelques heures.")
    ups_token_expires_at = fields.Datetime(
        string="UPS : jeton valide jusqu'à",
        groups="bf_property_core.group_bf_property_manager", copy=False)

    # ── DHL ──
    dhl_api_key = fields.Char(
        string="DHL : clé d'API",
        groups="bf_property_core.group_bf_property_manager", copy=False,
        help="L'en-tête DHL-API-Key, délivrée au portail développeur de DHL.")

    # ── Purolator ──
    purolator_key = fields.Char(
        string="Purolator : clé",
        groups="bf_property_core.group_bf_property_manager", copy=False)
    purolator_password = fields.Char(
        string="Purolator : mot de passe",
        groups="bf_property_core.group_bf_property_manager", copy=False)
    purolator_account_number = fields.Char(
        string="Purolator : numéro de compte",
        groups="bf_property_core.group_bf_property_manager", copy=False)

    hourly_limit = fields.Integer(
        string="Colis interrogés par heure",
        default=60,
        help="Plafond que ce logiciel s'impose, pour ne pas épuiser un quota "
             "ni faire une facture surprise. Il compte les COLIS interrogés "
             "dans la dernière heure : un même colis regardé deux fois compte "
             "une fois. Au-delà, les interrogations sont repoussées d'un quart "
             "d'heure, jamais abandonnées. À zéro, aucun plafond : à "
             "n'utiliser qu'avec un contrat qui le permet.",
    )
    note = fields.Text(string="Note")

    _sql_constraints = [
        ("uniq_carrier_company",
         "unique(company_id, carrier, environment)",
         "Cette société a déjà un compte pour ce transporteur et cet "
         "environnement."),
    ]

    # 🔴 Non stocké, et c'est la garde de classe du socle qui l'exige
    # (`bf_property_core/tests/test_reader_language.py`) : un texte calculé puis
    # STOCKÉ se range dans la langue de qui a déclenché le calcul, et le
    # lecteur suivant le lit tel quel. « (essai) » et « (production) » sont
    # traduits : la phrase se compose donc à la LECTURE.
    @api.depends_context("lang")
    @api.depends("carrier", "environment")
    def _compute_display_name(self):
        labels = dict(CARRIERS)
        for account in self:
            environment = _("essai") if account.environment == "test" else _("production")
            account.display_name = "%s (%s)" % (
                labels.get(account.carrier, account.carrier or ""), environment)

    # ── Ce qui manque pour interroger ────────────────────────────────────

    def _blocking_reason(self):
        """Ce qui empêche d'interroger ce transporteur, en clair, ou rien."""
        self.ensure_one()
        account = self.sudo()
        if self.carrier == "ups":
            if not (account.ups_client_id and account.ups_client_secret):
                return _("Le compte UPS n'a pas d'identifiant client ni de "
                         "secret : ils se créent au portail développeur d'UPS, "
                         "pour une application liée au compte UPS.")
        elif self.carrier == "dhl":
            if not account.dhl_api_key:
                return _("Le compte DHL n'a pas de clé d'API (en-tête "
                         "DHL-API-Key, délivrée au portail développeur de DHL).")
        elif self.carrier == "purolator":
            return _("Le suivi Purolator n'est pas branché. %s") % carrier_purolator.HELP
        return False

    def _ups_locale(self):
        locale = (self.locale or "fr_CA").strip()
        return locale if "_" in locale else "fr_CA"

    def _dhl_language(self):
        return (self.locale or "fr").strip().split("_")[0][:2] or "fr"

    # ── Jeton UPS ────────────────────────────────────────────────────────

    def _ups_token(self):
        """Le jeton en cours, ou un jeton neuf. Jamais journalisé.

        🔴 Écrit hors de la transaction de l'appelant ? Non : un jeton perdu
        par un retour en arrière n'est pas une perte (on en redemande un), et
        un jeton écrit par un commit prématuré, lui, pourrait figer une
        transaction qui devait échouer. Le jeton suit donc le sort de la
        transaction, et c'est le bon sort.
        """
        self.ensure_one()
        account = self.sudo()
        now = fields.Datetime.now()
        if account.ups_token and account.ups_token_expires_at and (
            account.ups_token_expires_at > now + timedelta(seconds=TOKEN_MARGIN)
        ):
            return account.ups_token
        token, lifetime = carrier_ups.fetch_token(account)
        account.write({
            "ups_token": token,
            "ups_token_expires_at": now + timedelta(seconds=lifetime or 3600),
        })
        return token

    def _ups_forget_token(self):
        self.sudo().write({"ups_token": False, "ups_token_expires_at": False})

    # ── Interrogation ────────────────────────────────────────────────────

    @api.model
    def _for(self, carrier, company):
        """Le compte actif de cette société pour ce transporteur, ou rien.

        La production passe avant l'essai : une organisation qui a gardé un
        compte d'essai ne doit pas suivre ses vrais colis dessus.
        """
        # 🔴 Pas `order="environment desc"` : trié sur la chaîne stockée,
        # « test » passe AVANT « production », et un parc en production aurait
        # suivi ses vrais colis sur l'hôte d'essai d'UPS (essais du
        # 2026-11-19). On cherche la production, puis l'essai.
        comptes = self.sudo().search([
            ("carrier", "=", carrier), ("company_id", "=", company.id)])
        for environment in ("production", "test"):
            for compte in comptes.filtered(lambda c, e=environment: c.environment == e):
                # Une fiche de production laissée vide ne doit pas éteindre un
                # compte d'essai qui, lui, a ses clés.
                if not compte._blocking_reason():
                    return compte
        for environment in ("production", "test"):
            trouve = comptes.filtered(lambda c, e=environment: c.environment == e)[:1]
            if trouve:
                return trouve
        return self.sudo().browse()

    def _bf_track(self, number, tz_name=None):
        """Interroge le transporteur. Rend (état, libellé, instant, code brut).

        🔴 Privée, et le tiret bas n'est pas cosmétique : toute méthode sans
        lui est appelable par `/web/dataset/call_kw`. Publique, elle laissait
        n'importe quel compte interne interroger le numéro de suivi de son
        choix avec les clés de la société — un oracle, et le quota du
        gestionnaire qui s'épuise (contrôle d'avant-publication, 2026-11-19).

        Lève ``CarrierError`` : l'appelant décide si l'échec se rejoue.

        ⚠️ Une clé qui MANQUE n'est pas un refus définitif. Un module posé le
        lundi et des clés reçues le vendredi condamnaient sinon toutes les
        annonces de la semaine à « à suivre à la main », sans retour possible
        (relecture adverse du 2026-11-19). Seul un transporteur qui n'est pas
        branché du tout l'est.
        """
        self.ensure_one()
        reason = self._blocking_reason()
        if reason:
            raise api_carrier.CarrierError(
                reason, permanent=self.carrier == "purolator")
        backend = BACKENDS.get(self.carrier)
        if not backend:
            raise api_carrier.CarrierError(
                _("Transporteur inconnu : %s") % self.carrier, permanent=True)
        if self.carrier == "ups":
            return backend.track(self.sudo(), number, tz_name=tz_name)
        return backend.track(self.sudo(), number)

    def _tracking_allowance(self):
        """Vrai s'il reste de la place sous le plafond horaire de ce compte.

        ⚠️ Compté sur les colis interrogés, pas sur un compteur à part : un
        compteur se désynchronise (retour en arrière, redémarrage), la trace
        des colis non. Elle sous-compte les appels refusés dans l'heure, et
        c'est le bon sens de l'erreur : le plafond protège d'un flot, pas
        d'une poignée.
        """
        self.ensure_one()
        if self.hourly_limit <= 0:
            return True
        since = fields.Datetime.now() - timedelta(hours=1)
        used = self.env["bf.property.parcel"].sudo().search_count([
            ("tracking_carrier", "=", self.carrier),
            ("company_id", "=", self.company_id.id),
            ("tracking_checked_at", ">=", since),
        ])
        return used < self.hourly_limit

    def action_test_credentials(self):
        """Essaie les clés sans suivre aucun colis.

        ⚠️ Rien n'est deviné : pour UPS, c'est une demande de jeton, le seul
        appel qui dit « ces clés valent quelque chose » sans interroger le
        colis de quelqu'un. Pour DHL, il n'existe pas d'appel sans numéro :
        le bouton dit alors ce qu'il ne peut pas vérifier plutôt que de
        prétendre.
        """
        self.ensure_one()
        if not (self.env.su or self.env.user.has_group(
            "bf_property_core.group_bf_property_manager"
        )):
            raise AccessError(_("Essayer un compte de transporteur relève du "
                                "syndicat."))
        reason = self._blocking_reason()
        if reason:
            raise UserError(reason)
        if self.carrier != "ups":
            raise UserError(_(
                "Ce transporteur n'a pas d'appel qui vérifie une clé sans "
                "interroger un colis. La clé se vérifiera au premier suivi, et "
                "le colis dira ce qui s'est passé."))
        try:
            self._ups_token()
        except api_carrier.CarrierError as exc:
            raise UserError(_("UPS a refusé : %s") % exc) from exc
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"type": "success", "title": _("Compte UPS joignable"),
                       "message": _("UPS a délivré un jeton pour ce compte.")},
        }

    @api.constrains("locale")
    def _check_locale(self):
        for account in self:
            if account.locale and not account.locale.replace("_", "").isalpha():
                raise ValidationError(_(
                    "La langue des libellés s'écrit « fr_CA », « en_CA », "
                    "« fr » ou « en »."))
