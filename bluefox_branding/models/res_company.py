import base64
import re

from odoo import api, fields, models
from odoo.http import request
from odoo.tools.mimetypes import guess_mimetype

from .brand_color_mixin import _SEUIL_AA_GRAND, assombrir_pour_texte_blanc

# Formats a PWA manifest may list in `icons`. An .ico is a perfectly good tab
# favicon but Chrome drops manifest entries it cannot decode, so the manifest
# keeps Odoo's icons rather than shipping a broken list.
BRAND_MANIFEST_MIMETYPES = ("image/png", "image/jpeg", "image/webp")

# 🔴 Les deux mauves que le CŒUR pose lui-même, et que personne n'a choisis :
# `report_brand_primary` a pour DÉFAUT `#714B67` (`bf_onboarding_base`), et la
# mise en page des avis d'Odoo se replie sur `#875A7B`
# (`res.company.email_secondary_color`, un champ calculé du cœur). Une société
# restée sur l'un des deux n'a donc PAS d'accent — voir
# `_brand_email_button_color`, le seul endroit où ce départage se fait.
BRAND_ACCENTS_PAR_DEFAUT = ("#714b67", "#875a7b")


class ResCompany(models.Model):
    _inherit = "res.company"

    # report_brand_primary / report_brand_dark / report_brand_logo now live in
    # bf_onboarding_base (a dependency of this module) so the bf_* suite renders
    # without the white-label panel. This module styles/surfaces them but no
    # longer owns the field definitions.

    # Traduisibles : la mise en page commune les affiche dans la langue
    # du courriel, et un courriel anglais portait le slogan et le pied français.
    # La valeur d'avant devient la source (`en_US`), comme ailleurs dans la suite.
    brand_email_tagline = fields.Char(
        string="Tagline de marque (courriels)",
        translate=True,
        help=(
            "Courte phrase d'accroche affichée sous le nom de la société dans le pied "
            "des courriels brandés. Laissez vide pour utiliser l'en-tête du rapport."
        ),
    )
    brand_email_footer_html = fields.Html(
        string="Pied de page personnalisé (courriels)",
        sanitize=False,
        translate=True,
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

    # -- Which company the page is wearing -----------------------------------

    @api.model
    def _brand_active_company(self):
        """The company whose colours this HTTP response should wear.

        ⚠️ **`env.company` is the wrong answer here, and looks like the right
        one.** The company switcher lives entirely in the browser: it writes a
        `cids` cookie and passes `allowed_company_ids` in the *RPC* context, so
        `env.company` follows the switcher on `call_kw` but never on a
        server-rendered page. Nothing in `odoo.http` reads that cookie; only a
        handful of `mail` endpoints do it by hand, with the parsing copied
        below. A template that reads `env.company` therefore paints the user's
        *main* company for ever, whichever company they picked, and the symptom
        is a page that simply never changes colour.

        Defensive on purpose: outside a request (cron, tests, report rendering)
        and on a malformed or unauthorised cookie, this falls back to
        `env.company` rather than raising. A brand colour is never worth a
        traceback on a page load.
        """
        company = self.env.company
        if not request:
            return company
        raw = request.httprequest.cookies.get("cids") or ""
        # `-` since 17.0; older cookies used `,` and may still be in a browser.
        first = re.split(r"[-,]", raw)[0].strip()
        if not first.isdigit():
            return company
        allowed = self.env.user._get_company_ids()
        chosen = int(first)
        if chosen not in allowed:
            return company
        return self.browse(chosen)

    # -- Accent utilisable comme fond sous du texte blanc --------------------

    def _brand_primary_on_white(self):
        """L'accent, assombri juste assez pour porter du texte blanc.

        Les boutons primaires, les pastilles et les barres de progression
        peignent l'accent en FOND et écrivent dessus en blanc. Sur les marques
        claires la paire tombe sous le seuil AA (2,62:1 pour un bleu clair),
        et rien ne le signale : le bouton s'affiche, son texte est là, il est
        seulement trop pâle pour une partie des gens.

        Une marque qui passe déjà rend sa propre couleur, inchangée. Voir
        ``brand_color_mixin.assombrir_pour_texte_blanc``.
        """
        self.ensure_one()
        return assombrir_pour_texte_blanc(self.report_brand_primary or '')

    def _brand_primary_on_white_large(self):
        """L'accent assombri au seuil « gros texte » (3:1) plutôt qu'à 4,5:1.

        Réservé aux surfaces dont la règle CSS impose une étiquette ≥ 18,7 px
        en gras. Sur Blue Fox, #29ABE2 rend #1d9fd6 — ΔE 4,4 de la marque, là
        où la variante 4,5:1 s'en éloigne de 18,2 et ne se lit plus comme le
        bleu du logo.
        """
        self.ensure_one()
        return assombrir_pour_texte_blanc(self.report_brand_primary or '',
                                          seuil=_SEUIL_AA_GRAND)

    # -- Le bouton « Voir » d'un avis ----------------------------------------

    def _brand_email_button_color(self):
        """Le fond du bouton « Voir » d'un avis : l'accent de la marque, à
        défaut le champ d'Odoo, à défaut le mauve d'Odoo.

        🔴 **Un `report_brand_primary` non vide ne veut pas dire « un accent
        choisi ».** Le champ a pour DÉFAUT `#714B67`, le mauve d'Odoo
        (`bf_onboarding_base`) : il n'est jamais vide, donc tout repli écrit
        derrière lui serait INATTEIGNABLE. Mesuré le 2026-11-12 : une société
        restée au défaut, dont le champ d'Odoo portait `#000000`, aurait vu
        son bouton d'avis passer du noir au mauve d'Odoo — l'inverse de
        l'intention. Les deux mauves du cœur comptent donc pour « pas
        d'accent », à la casse et aux espaces près : le champ est un `Char`
        nu, sans contrainte, écrit à la main, par RPC ou par import.

        ⚠️ Conséquence assumée : une société qui voudrait vraiment le mauve
        d'Odoo comme accent de marque est lue comme une société sans accent.
        Un défaut ne se distingue pas d'un choix qui lui ressemble — et le
        repli la ramène au même mauve de toute façon.

        ⚠️ `email_secondary_color` (« Email Button Color ») est un champ du
        CŒUR, calculé depuis `secondary_color` et replié sur `#875A7B`. Ce
        module ne le possède pas et personne ne l'aligne sur la marque :
        mesuré sur un locataire, 930 des 997 avis encore en base portaient sa
        valeur pour la société d'en-tête, et non l'accent de sa marque.
        L'accent passe donc devant, et le champ d'Odoo garde sa place de repli
        pour une société qui n'a pas d'accent à elle.

        L'accent BRUT, pas sa variante lisible (`_brand_primary_on_white`) :
        c'est le choix du module pour les boutons de courriel depuis la
        18.0.3.19.0, et déjà celui de `bf_mail_layout`.

        Ici plutôt que dans l'expression QWeb de la vue : la décision se lit,
        s'éprouve et se mute à un seul endroit, et la vue ne garde que sa
        garde de société vide. Sûre sur un recordset VIDE pour la même raison
        — la mise en page peut recevoir une société vide, le cœur rend alors
        son propre mauve, et une couleur ne vaut jamais une trace d'erreur.
        """
        if not self:
            return '#875A7B'
        self.ensure_one()
        accent = (self.report_brand_primary or '').strip()
        if accent.lower() in BRAND_ACCENTS_PAR_DEFAUT:
            accent = ''
        return accent or (self.email_secondary_color or '').strip() or '#875A7B'

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
