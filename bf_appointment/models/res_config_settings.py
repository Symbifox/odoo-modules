import logging
import os

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Valeur d'affichage du mot de passe déjà enregistré. `get_values` la pousse
# dans le champ pour ne jamais renvoyer le secret au navigateur ; `set_values`
# doit donc l'ignorer, sinon un simple « Enregistrer » dans Paramètres
# ré-encrypte les astérisques par-dessus le vrai jeton applicatif et toutes les
# créations de salle Talk tombent en 401 (vécu sur BF le 2026-07-24).
MASKED_PASSWORD = "********"

try:
    from cryptography.fernet import Fernet
except ImportError:
    Fernet = None
    _logger.warning(
        "cryptography package not installed. "
        "Nextcloud Talk credential encryption will not be available."
    )


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_appointment_jitsi_domain = fields.Char(
        string="Domaine Jitsi",
        config_parameter="bf_appointment.jitsi_domain",
        default="meet.jit.si",
        help="Domaine du serveur Jitsi Meet (ex. meet.jit.si ou votre instance auto-hébergée).",
    )
    bf_appointment_nc_talk_base_url = fields.Char(
        string="URL de base Nextcloud Talk",
        config_parameter="bf_appointment.nc_talk_base_url",
        help="URL de base de l'instance Nextcloud (ex. https://cloud.example.com).",
    )
    bf_appointment_nc_talk_user = fields.Char(
        string="Utilisateur Nextcloud Talk",
        config_parameter="bf_appointment.nc_talk_user",
        help="Utilisateur Nextcloud pour l'authentification à l'API Talk.",
    )
    bf_appointment_nc_talk_moderators = fields.Char(
        string="Modérateurs des salles Talk",
        config_parameter="bf_appointment.nc_talk_moderators",
        help=(
            "Comptes Nextcloud promus modérateurs de chaque salle de rendez-vous. "
            "Séparés par des virgules. Une entrée « compte » vaut pour toutes les "
            "salles ; une entrée « login_odoo=compte » ne vaut que pour les "
            "rendez-vous dont ce login est l'organisateur. Laissé vide, la salle "
            "n'a que le robot pour participant et l'hôte y entre comme invité."
        ),
    )
    # ⚠️ NON STOCKÉ, délibérément. `res.config.settings` est un modèle
    # transitoire, c'est-à-dire une VRAIE table : un Char ordinaire y aurait
    # déposé le mot de passe d'application Nextcloud EN CLAIR, où il serait
    # resté jusqu'au passage du ramasse-miettes (une heure par défaut) et
    # serait parti dans toute sauvegarde prise entre-temps — alors même que
    # tout le travail de ce fichier consiste à ne le garder que chiffré.
    # Avec `compute` + `inverse` et `store=False`, la valeur ne vit que dans
    # le cache de la transaction : elle est chiffrée puis écrite dans l'ICP,
    # et il n'existe aucune colonne pour la retenir.
    bf_appointment_nc_talk_password = fields.Char(
        string="Mot de passe Nextcloud Talk",
        compute="_compute_nc_talk_password",
        inverse="_inverse_nc_talk_password",
        store=False,
        readonly=False,
        help="Mot de passe d'application Nextcloud pour l'API Talk. Stocké chiffré.",
    )

    appointment_quick_link_type_id = fields.Many2one(
        "resource.booking.type",
        related="company_id.appointment_quick_link_type_id", readonly=False,
        string="Type pour les liens rapides",
        # ⚠️ Le domaine du champ de `res.company` n'est PAS repris par le champ
        # related : `related_attrs` ne recopie que les domaines non textuels, et
        # celui-là est une chaîne. Mesuré : `_fields[...].domain` rend None. Sans
        # cette ligne, la page Paramètres offre AUSSI les types internes, dont un
        # lien public ne peut rien faire.
        domain="[('is_public', '=', True)]",
    )
    appointment_brand_name = fields.Char(
        related="company_id.appointment_brand_name", readonly=False,
    )
    appointment_brand_logo_url = fields.Char(
        related="company_id.appointment_brand_logo_url", readonly=False,
    )
    appointment_brand_website_url = fields.Char(
        related="company_id.appointment_brand_website_url", readonly=False,
    )
    appointment_brand_primary = fields.Char(
        related="company_id.appointment_brand_primary", readonly=False,
    )
    appointment_brand_dark = fields.Char(
        related="company_id.appointment_brand_dark", readonly=False,
    )
    appointment_brand_support_email = fields.Char(
        related="company_id.appointment_brand_support_email", readonly=False,
    )
    appointment_brand_support_phone = fields.Char(
        related="company_id.appointment_brand_support_phone", readonly=False,
    )
    appointment_brand_support_phone_display = fields.Char(
        related="company_id.appointment_brand_support_phone_display",
        readonly=False,
    )
    appointment_brand_privacy_url = fields.Char(
        related="company_id.appointment_brand_privacy_url", readonly=False,
    )
    appointment_brand_terms_url = fields.Char(
        related="company_id.appointment_brand_terms_url", readonly=False,
    )
    appointment_brand_tagline = fields.Char(
        related="company_id.appointment_brand_tagline", readonly=False,
    )

    @api.model
    def default_get(self, fields_list):
        """Alimente la façade du mot de passe, que le formulaire ne calcule jamais.

        🔴 La page Paramètres est bâtie par `onchange`, et la couche web sème à
        `False` tout champ de la vue que `default_get` ne rend pas
        (`addons/web/models/models.py`). Le `compute` ci-dessous ne tournait
        donc JAMAIS à l'affichage : le masque ne s'affichait pas, et rien ne
        disait plus à l'administrateur qu'un jeton applicatif était en place.
        Le secret, lui, n'a jamais été en danger — l'`inverse` ignore le vide
        depuis la morsure du 2026-07-24. Même cause que le défaut voisin de la rétention des courriels.

        Les champs voisins portent `config_parameter` ou sont `related` :
        `default_get` et la relation les rendent tout seuls.
        """
        res = super().default_get(fields_list)
        if "bf_appointment_nc_talk_password" in fields_list:
            res["bf_appointment_nc_talk_password"] = (
                MASKED_PASSWORD if self._nc_talk_password_pose() else False
            )
        return res

    @api.model
    def _nc_talk_password_pose(self):
        """Vrai si un jeton chiffré est en base. Source du `compute` aussi."""
        return bool(
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("bf_appointment.nc_talk_password_encrypted", "")
        )

    def _compute_nc_talk_password(self):
        """Ne rend JAMAIS le secret au navigateur : un masque, ou rien.

        ⚠️ Même source que `default_get`, à dessein : l'un alimente le
        formulaire, l'autre les lectures par l'ORM.
        """
        pose = self._nc_talk_password_pose()
        for reglage in self:
            reglage.bf_appointment_nc_talk_password = (
                MASKED_PASSWORD if pose else False
            )

    def _inverse_nc_talk_password(self):
        """Chiffre et range dans l'ICP. Ignore le masque.

        ⚠️ Le test sur les astérisques n'est pas cosmétique : `_compute_…`
        pousse `********` dans le champ pour ne pas renvoyer le secret, donc
        un simple « Enregistrer » dans Paramètres re-chiffrerait ces
        astérisques par-dessus le vrai jeton applicatif, et toutes les
        créations de salle Talk tomberaient en 401 (vécu sur BF le
        2026-07-24).
        """
        for reglage in self:
            # ⚠️ `.strip()` : trouvé par les essais. Une saisie
            # faite de blancs seuls est vraie et n'est pas un masque — elle
            # passait le contrôle et chiffrait des espaces par-dessus le vrai
            # jeton applicatif, avec la même conséquence que la morsure du
            # 2026-07-24 (toutes les créations de salle Talk en 401).
            saisi = (reglage.bf_appointment_nc_talk_password or "").strip()
            if not saisi or set(saisi) == {"*"}:
                continue
            self.env["ir.config_parameter"].sudo().set_param(
                "bf_appointment.nc_talk_password_encrypted",
                reglage._encrypt_value(saisi),
            )

    def _get_encryption_key(self):
        from ._crypto import get_encryption_key
        return get_encryption_key(self.env)

    def _encrypt_value(self, value):
        if not value:
            return False
        if not Fernet:
            raise UserError(
                "Le paquet 'cryptography' est requis pour stocker "
                "les identifiants de manière sécurisée. "
                "Installez-le avec: pip install cryptography"
            )
        key = self._get_encryption_key()
        f = Fernet(key.encode())
        return f.encrypt(value.encode()).decode()
