"""Forme du cadre par société : arrondis, ombres, épaisseur des bordures.

Trois réglages sur ``res.company``, chacun avec une valeur par défaut qui
reprend exactement l'apparence d'avant. La règle de tout ce fichier : **la
valeur par défaut n'émet rien**. Une société qui ne touche pas au panneau
reçoit la même page, le même courriel et le même PDF qu'avant la pose.

Trois surfaces, trois chemins, parce qu'elles ne lisent pas le CSS pareil :

- Interface Odoo : une ``<style>`` par société (``brand_frame_backend.xml``,
  client web seulement : jamais le site, le portail ni la connexion),
  qui redéfinit les variables de Bootstrap 5.3 (``--border-radius``…) à
  l'exécution. Rien dans ``primary_variables.scss`` : il est compilé une fois
  pour tous les locataires.
- Courriels : des valeurs littérales dans les attributs ``style`` (aucun
  client de courriel ne lit une variable CSS).
- PDF : wkhtmltopdf 0.12.6 IGNORE ``var()`` (mesuré le 2026-10-21 : un bloc en
  ``var()`` sort carré, le même en valeur sort arrondi). Les règles passent
  donc par la feuille compilée par société d'Odoo (``web.styles_company_report``),
  en valeurs littérales.

Les valeurs ne viennent que des tables ci-dessous, jamais d'une saisie : la
CSS produite ne contient ni guillemet ni chevron, et peut sortir telle quelle.
"""
from markupsafe import Markup

from odoo import api, fields, models

FRAME_SHAPES = [
    ("square", "Carré"),
    ("soft", "Doux"),
    ("rounded", "Arrondi"),
]
FRAME_SHADOWS = [
    ("none", "Aucune"),
    ("subtle", "Discrète"),
    ("strong", "Marquée"),
]
FRAME_BORDERS = [
    ("thin", "Fine"),
    ("bold", "Appuyée"),
]
DEFAULT_SHAPE = "soft"
DEFAULT_SHADOW = "subtle"
DEFAULT_BORDER = "thin"

# Interface : --border-radius, -sm, -lg. « soft » n'y figure pas : c'est la
# valeur d'Odoo (0,25 / 0,1875 / 0,375rem) et on ne la réécrit pas.
_UI_RADIUS = {
    "square": ("0", "0", "0"),
    "rounded": ("0.625rem", "0.375rem", "1rem"),
}
# Interface : --box-shadow, -sm, -lg. « subtle » = Bootstrap, non réécrit.
_UI_SHADOW = {
    "none": ("none", "none", "none"),
    "strong": (
        "0 0.75rem 1.5rem rgba(0, 0, 0, 0.25)",
        "0 0.25rem 0.5rem rgba(0, 0, 0, 0.15)",
        "0 1.5rem 4rem rgba(0, 0, 0, 0.3)",
    ),
}
_UI_BORDER = {"bold": "2px"}

# Courriels : carte, boutons, filets du pied. « soft » = les valeurs codées
# jusqu'à la 18.0.3.26.0, à l'octet près.
_MAIL_RADIUS = {
    "square": {"card": "0", "button": "0", "rule": "0"},
    "soft": {"card": "16px", "button": "10px", "rule": "2px"},
    "rounded": {"card": "24px", "button": "14px", "rule": "2px"},
}
_MAIL_SHADOW = {
    "none": "none",
    "subtle": "0 4px 24px rgba(0,0,0,0.08)",
    "strong": "0 8px 32px rgba(0,0,0,0.16)",
}

# PDF : arrondi des éléments, arrondi des tableaux « boîte arrondie ».
_PDF_RADIUS = {
    "square": ("0", "0"),
    "rounded": ("0.625rem", "1rem"),
}


def _ui_css(shape, shadow, border):
    """La CSS à poser dans la page, ou ``""`` si tout est au défaut."""
    root = []
    rules = []
    if shape in _UI_RADIUS:
        base, small, large = _UI_RADIUS[shape]
        root += [
            "--border-radius: %s !important;" % base,
            "--border-radius-sm: %s !important;" % small,
            "--border-radius-lg: %s !important;" % large,
        ]
        # Ce qu'Odoo a compilé en dur au lieu de lire la variable : fenêtres,
        # popovers, boutons et utilitaires .rounded (mesuré dans
        # web.assets_web le 2026-10-21).
        rules += [
            ".modal { --modal-border-radius: var(--border-radius) !important;"
            " --modal-inner-border-radius: calc(var(--border-radius) - var(--border-width)) !important; }",
            ".popover { --popover-border-radius: var(--border-radius) !important;"
            " --popover-inner-border-radius: calc(var(--border-radius) - var(--border-width)) !important; }",
            ".btn { --btn-border-radius: var(--border-radius) !important; }",
            ".btn-sm, .btn-group-sm .btn { --btn-border-radius: var(--border-radius-sm) !important; }",
            ".btn-lg, .btn-group-lg .btn { --btn-border-radius: var(--border-radius-lg) !important; }",
            ".rounded, .rounded-1, .rounded-2 { border-radius: var(--border-radius) !important; }",
            ".rounded-3, .rounded-4 { border-radius: var(--border-radius-lg) !important; }",
            ".rounded-top { border-top-left-radius: var(--border-radius) !important;"
            " border-top-right-radius: var(--border-radius) !important; }",
            ".rounded-bottom { border-bottom-left-radius: var(--border-radius) !important;"
            " border-bottom-right-radius: var(--border-radius) !important; }",
            ".rounded-start { border-top-left-radius: var(--border-radius) !important;"
            " border-bottom-left-radius: var(--border-radius) !important; }",
            ".rounded-end { border-top-right-radius: var(--border-radius) !important;"
            " border-bottom-right-radius: var(--border-radius) !important; }",
            # Cartes kanban et feuille de formulaire : carrées d'origine dans
            # Odoo 18 (rayon 0 mesuré), elles prennent le grand rayon de la forme.
            ".o_kanban_renderer .o_kanban_record, .o_form_view .o_form_sheet"
            " { border-radius: var(--border-radius-lg) !important; }",
        ]
    if shadow in _UI_SHADOW:
        base, small, large = _UI_SHADOW[shadow]
        root += [
            "--box-shadow: %s !important;" % base,
            "--box-shadow-sm: %s !important;" % small,
            "--box-shadow-lg: %s !important;" % large,
        ]
    if border in _UI_BORDER:
        root.append("--border-width: %s !important;" % _UI_BORDER[border])
        # Trait des cartes kanban compilé à 1 px au lieu de lire la variable.
        rules.append(".o_kanban_renderer .o_kanban_record { border-width: var(--border-width) !important; }")
    if not root and not rules:
        return ""
    css = []
    if root:
        css.append(":root { %s }" % " ".join(root))
    css += rules
    return "\n".join(css)


def _pdf_scss(company_id, shape):
    """Le bloc SCSS du PDF pour une société, ou ``""`` au défaut."""
    if shape not in _PDF_RADIUS:
        return ""
    r, t = _PDF_RADIUS[shape]
    # Tableaux « boîte arrondie » (mise en page Bulle) : les six coins
    # qu'arrondit web/.../report_tables.scss, repris un à un. La classe est
    # posée sur le même div que .o_company_<id>_layout, d'où le « & ».
    boite = "&.o_table_boxed-rounded"
    table = "%s table:not(.o_ignore_layout_styling)" % boite
    total = "%s .o_total_table:not(.o_ignore_layout_styling)" % boite
    coins = [
        ("%s::before" % table, t),
        ("%s.o_has_total_table::before" % table, "%s %s 0 %s" % (t, t, t)),
        ("%s th:first-child" % table, "%s 0 0 0" % t),
        ("%s th:last-child" % table, "0 %s 0 0" % t),
        ("%s::before" % total, "0 0 %s %s" % (t, t)),
        ("%s tr:last-child td:first-child" % total, "0 0 0 %s" % t),
        ("%s tr:last-child td:last-child" % total, "0 0 %s 0" % t),
    ]
    lignes = [
        "#informations, .alert, .btn, .badge, .card, pre, code, kbd,"
        " .rounded, .rounded-1, .rounded-2, .rounded-3 { border-radius: %s !important; }" % r,
        ".rounded-top { border-top-left-radius: %s !important; border-top-right-radius: %s !important; }" % (r, r),
        ".rounded-bottom { border-bottom-left-radius: %s !important; border-bottom-right-radius: %s !important; }" % (r, r),
    ] + ["%s { border-radius: %s !important; }" % (sel, val) for sel, val in coins]
    return ".o_company_%d_layout {\n%s\n}\n" % (company_id, "\n".join("    " + l for l in lignes))


class ResCompany(models.Model):
    _inherit = "res.company"

    brand_frame_shape = fields.Selection(
        FRAME_SHAPES,
        string="Coins",
        required=True,
        default=DEFAULT_SHAPE,
        help=(
            "Forme des coins des boutons, champs, cartes et fenêtres de l'interface, "
            "de la carte et des boutons des courriels brandés, et des encadrés et "
            "tableaux des rapports PDF (bien visible avec la mise en page Bulle ; la "
            "mise en page Dossier n'a presque pas de coins). Doux est l'apparence "
            "d'origine. Le site web garde les réglages de son éditeur."
        ),
    )
    brand_frame_shadow = fields.Selection(
        FRAME_SHADOWS,
        string="Ombres",
        required=True,
        default=DEFAULT_SHADOW,
        help=(
            "Ombre portée des menus, fenêtres et cartes de l'interface, et de la carte "
            "des courriels brandés. Discrète est l'apparence d'origine."
        ),
    )
    brand_frame_border = fields.Selection(
        FRAME_BORDERS,
        string="Bordures",
        required=True,
        default=DEFAULT_BORDER,
        help=(
            "Épaisseur du trait autour des champs, cartes et boutons secondaires de "
            "l'interface. Fine est l'apparence d'origine."
        ),
    )

    _BRAND_FRAME_FIELDS = ("brand_frame_shape", "brand_frame_shadow", "brand_frame_border")

    def _brand_frame_css(self):
        """CSS de l'interface pour cette société, ``Markup("")`` au défaut."""
        self.ensure_one()
        company = self.sudo()
        return Markup(_ui_css(
            company.brand_frame_shape, company.brand_frame_shadow, company.brand_frame_border,
        ))

    def _brand_frame_mail(self):
        """Valeurs littérales pour les attributs ``style`` des courriels."""
        self.ensure_one()
        company = self.sudo()
        values = dict(_MAIL_RADIUS.get(company.brand_frame_shape) or _MAIL_RADIUS[DEFAULT_SHAPE])
        values["shadow"] = _MAIL_SHADOW.get(company.brand_frame_shadow) or _MAIL_SHADOW[DEFAULT_SHADOW]
        return values

    def _brand_frame_pdf_scss(self):
        """Bloc SCSS du PDF pour cette société, ``""`` au défaut."""
        self.ensure_one()
        # Markup : le gabarit d'Odoo produit du SCSS, pas du HTML ; échappé,
        # le « & » du sélecteur parent deviendrait « &amp; » et casserait la
        # compilation de toute la feuille (toutes les sociétés).
        return Markup(_pdf_scss(self.id, self.sudo().brand_frame_shape))

    @api.model_create_multi
    def create(self, vals_list):
        companies = super().create(vals_list)
        if any(not set(self._BRAND_FRAME_FIELDS).isdisjoint(vals) for vals in vals_list):
            companies._brand_frame_refresh_pdf()
        return companies

    def write(self, vals):
        res = super().write(vals)
        if "brand_frame_shape" in vals:
            self._brand_frame_refresh_pdf()
        return res

    def _brand_frame_refresh_pdf(self):
        # La feuille des PDF est une seule pièce jointe pour toutes les
        # sociétés (web.asset_styles_company_report), recompilée par Odoo quand
        # la police ou les couleurs changent. Même geste pour la forme.
        self._update_asset_style()
        self.env.registry.clear_cache("assets")
