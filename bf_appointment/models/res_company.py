from odoo import fields, models


# No brand is built in. A company that sets nothing sends its appointment e-mails
# under its own name, its own website and the logo its own Odoo serves; its
# privacy and terms links appear only once it sets its own (see the templates).
# Only neutral values keep a default: colours, and example.com placeholders that
# nobody can mistake for a real address.
_BF_DEFAULT_PRIMARY = "#714B67"
_BF_DEFAULT_DARK = "#212529"
_BF_DEFAULT_SUPPORT_EMAIL = "service@example.com"
_BF_DEFAULT_SUPPORT_PHONE = "+15555555555"
_BF_DEFAULT_SUPPORT_PHONE_DISPLAY = "555-555-5555"


class ResCompany(models.Model):
    _inherit = "res.company"

    appointment_quick_link_type_id = fields.Many2one(
        "resource.booking.type",
        string="Type pour les liens rapides",
        domain="[('is_public', '=', True)]",
        help="Type employé par le bouton « Lien de rendez-vous » du "
             "compositeur de courriel et de la fiche de contact. Sans ce "
             "réglage, le bouton prend le premier type public listé — ce qui "
             "marche, mais choisir explicitement évite les surprises.",
    )

    appointment_brand_name = fields.Char(
        string="Nom de marque (rendez-vous)",
        help="Nom affiché dans les courriels de rendez-vous (objet, signatures).",
    )
    appointment_brand_logo_url = fields.Char(
        string="URL du logo (rendez-vous)",
        help="URL publique du logo intégré aux courriels de rendez-vous. "
             "Doit être accessible sans authentification.",
    )
    appointment_brand_website_url = fields.Char(
        string="Site web de marque (rendez-vous)",
        help="Adresse vers laquelle pointe le logo des courriels.",
    )
    appointment_brand_primary = fields.Char(
        string="Couleur principale de marque (rendez-vous)",
        default=_BF_DEFAULT_PRIMARY,
        help="Couleur (hex) des accents (boutons, liens) sur les pages et "
             "courriels de rendez-vous.",
    )
    appointment_brand_dark = fields.Char(
        string="Couleur foncée de marque (rendez-vous)",
        default=_BF_DEFAULT_DARK,
        help="Couleur (hex) des en-têtes/pieds foncés des courriels de rendez-vous.",
    )
    appointment_brand_support_email = fields.Char(
        string="Courriel de soutien (rendez-vous)",
        default=_BF_DEFAULT_SUPPORT_EMAIL,
        help="Courriel de contact affiché dans les courriels de rendez-vous "
             "pour les questions des clients.",
    )
    appointment_brand_support_phone = fields.Char(
        string="Téléphone de soutien (format E.164)",
        default=_BF_DEFAULT_SUPPORT_PHONE,
        help="Téléphone au format E.164 (ex. +15145551212), utilisé dans les liens tel:.",
    )
    appointment_brand_support_phone_display = fields.Char(
        string="Téléphone de soutien (affichage)",
        default=_BF_DEFAULT_SUPPORT_PHONE_DISPLAY,
        help="Téléphone en format lisible (ex. 514-555-1212) montré aux lecteurs.",
    )
    appointment_brand_privacy_url = fields.Char(
        string="URL de la politique de confidentialité (rendez-vous)",
        help="URL de la politique de confidentialité affichée dans les pieds de "
             "courriel et le formulaire public.",
    )
    appointment_brand_terms_url = fields.Char(
        string="URL des conditions d'utilisation (rendez-vous)",
        help="URL des conditions d'utilisation affichée dans les pieds de courriel.",
    )
    appointment_brand_tagline = fields.Char(
        string="Slogan de marque (rendez-vous)",
        help="Court slogan affiché sous le nom de marque dans les pieds de courriel.",
    )
