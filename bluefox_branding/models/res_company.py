import base64

from odoo import fields, models
from odoo.tools.mimetypes import guess_mimetype

# Formats a PWA manifest may list in `icons`. An .ico is a perfectly good tab
# favicon but Chrome drops manifest entries it cannot decode, so the manifest
# keeps Odoo's icons rather than shipping a broken list.
BRAND_MANIFEST_MIMETYPES = ("image/png", "image/jpeg", "image/webp")


class ResCompany(models.Model):
    _inherit = "res.company"

    # report_brand_primary / report_brand_dark / report_brand_logo now live in
    # bf_onboarding_base (a dependency of this module) so the bf_* suite renders
    # without the white-label panel. This module styles/surfaces them but no
    # longer owns the field definitions.

    brand_email_tagline = fields.Char(
        string="Tagline de marque (courriels)",
        help=(
            "Courte phrase d'accroche affichée sous le nom de la société dans le pied "
            "des courriels brandés. Laissez vide pour utiliser l'en-tête du rapport."
        ),
    )
    brand_email_footer_html = fields.Html(
        string="Pied de page personnalisé (courriels)",
        sanitize=False,
        help=(
            "HTML qui remplace la ligne automatique courriel · téléphone · site web "
            "dans le pied des courriels brandés. Laissez vide pour utiliser les "
            "coordonnées de la société."
        ),
    )
    brand_email_signature_default = fields.Html(
        string="Signature par défaut (courriels)",
        sanitize=False,
        help=(
            "Signature HTML utilisée dans le bloc signature des courriels brandés "
            "quand l'utilisateur n'a pas de signature personnelle. Laissez vide pour "
            "le comportement Odoo standard."
        ),
    )
    brand_privacy_url = fields.Char(
        string="Lien politique de confidentialité (courriels)",
        help=(
            "URL affichée dans le pied des courriels brandés. Laissez vide pour "
            "masquer le lien."
        ),
    )
    brand_terms_url = fields.Char(
        string="Lien conditions (courriels)",
        help=(
            "URL affichée dans le pied des courriels brandés. Laissez vide pour "
            "masquer le lien."
        ),
    )
    favicon = fields.Binary(
        string="Favicon",
        attachment=True,
        help=(
            "Icône de la société : onglet du navigateur, écran d'accueil une fois "
            "l'application installée (PWA) et pages publiques. Une image carrée de "
            "512 px donne le meilleur résultat. Si vide, le logo est utilisé pour "
            "l'onglet et l'icône Odoo reste celle de l'application installée."
        ),
    )

    # -- Brand icon ---------------------------------------------------------
    # One field feeds three surfaces (tab, apple-touch, PWA manifest); the URL
    # is built here so the template, the manifest controller and any public
    # page agree on the size and the cache key.

    def _brand_icon_url(self, size=None):
        """Public URL of the company favicon, sized; empty string when unset.

        ``unique`` makes the response immutably cacheable, keyed on the
        company's ``write_date`` so a new icon lands immediately instead of
        waiting out a stale cache.
        """
        self.ensure_one()
        company = self.sudo()
        if not company.favicon:
            return ""
        url = "/web/image/res.company/%s/favicon" % company.id
        if size:
            url += "/%sx%s" % (size, size)
        if company.write_date:
            url += "?unique=%s" % int(company.write_date.timestamp())
        return url

    def _brand_tab_icon_url(self):
        """What goes in ``<link rel="icon">``: the favicon, else the logo.

        64 px covers a 2× tab and the bookmark bar; ``/web/image`` never
        upscales, so a smaller favicon is served as-is.
        """
        self.ensure_one()
        url = self._brand_icon_url(64)
        if url or not self.sudo().logo:
            return url
        return "/web/image/res.company/%s/logo/64x64" % self.id

    def _brand_manifest_icons(self):
        """Manifest ``icons`` entries, or ``()`` to keep Odoo's own."""
        self.ensure_one()
        company = self.sudo()
        if not company.favicon:
            return ()
        mimetype = guess_mimetype(base64.b64decode(company.favicon), default="")
        if mimetype not in BRAND_MANIFEST_MIMETYPES:
            return ()
        return tuple(
            {
                "src": company._brand_icon_url(size),
                "sizes": "%sx%s" % (size, size),
                "type": mimetype,
            }
            for size in (192, 512)
        )
