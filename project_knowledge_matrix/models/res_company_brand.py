from odoo import models

# Couleurs par défaut d'Odoo. Un champ resté sur l'une d'elles n'est pas un
# choix de marque: c'est l'absence de choix, et on doit alors continuer la
# chaîne de repli plutôt que de peindre le document en mauve Odoo.
ODOO_DEFAULTS = {'#714b67', '#875a7b', '#212529'}

FALLBACK_PRIMARY = '#714B67'
FALLBACK_DARK = '#212529'


class ResCompanyBrand(models.Model):
    _inherit = 'res.company'

    def _pkm_pick_color(self, *field_names, default=None):
        """Première couleur réellement choisie parmi les champs donnés.

        Les champs sont lus défensivement: le module fonctionne sur une
        instance qui n'a ni `bluefox_branding` ni aucun autre module de marque.
        """
        self.ensure_one()
        for name in field_names:
            if name not in self._fields:
                continue
            value = (self[name] or '').strip()
            if value and value.lower() not in ODOO_DEFAULTS:
                return value
        return default

    def _pkm_brand(self):
        """Palette et logos à utiliser pour les documents de ce module."""
        self.ensure_one()
        primary = self._pkm_pick_color(
            'report_brand_primary', 'primary_color', 'email_primary_color',
            default=FALLBACK_PRIMARY,
        )
        dark = self._pkm_pick_color(
            'report_brand_dark', 'secondary_color', 'email_secondary_color',
            default=FALLBACK_DARK,
        )
        logo_dark = False
        if 'report_brand_logo' in self._fields:
            logo_dark = self.report_brand_logo or False
        return {
            'primary': primary,
            'dark': dark,
            'logo': self.logo or False,
            'logo_dark': logo_dark or self.logo or False,
            'font': self.font if 'font' in self._fields else False,
            'company_name': self.name,
        }
