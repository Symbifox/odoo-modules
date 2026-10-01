"""Couleurs de marque des courriels d'assistance, lisibles quelle que soit la marque.

La couleur vient du locataire (email_primary_color, sinon primary_color) :
Symbifox sert plusieurs marques et aucune n'est codée ici. Une couleur de
marque est rarement lisible telle quelle : le bleu Blue Fox (#29ABE2) donne
2,6:1 sur blanc. On garde la couleur pour les aplats, et on calcule à part
la couleur du texte posé dessus et celle des liens sur fond blanc, pour
atteindre 4,5:1 (WCAG 2.1 AA, cible SGQRI 008 3.0).
"""
import re

from odoo import api, fields, models

NEUTRAL = "#2D3031"
_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")


def _rgb(hex_color):
    match = _HEX.match((hex_color or "").strip())
    if not match:
        return None
    value = match.group(1)
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _hex(rgb):
    return "#%02X%02X%02X" % tuple(max(0, min(255, round(c))) for c in rgb)


def _luminance(rgb):
    def channel(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def darken_to(rgb, background, target=4.5):
    """Assombrir `rgb` jusqu'au contraste visé contre `background`."""
    color = rgb
    for _step in range(40):
        if contrast(color, background) >= target:
            return color
        color = tuple(c * 0.92 for c in color)
    return (0, 0, 0)


def brand_palette(hex_color):
    """(aplat, texte sur l'aplat, liens sur blanc), tous en #RRGGBB."""
    rgb = _rgb(hex_color) or _rgb(NEUTRAL)
    white, ink = (255, 255, 255), (0x1F, 0x23, 0x28)
    on_brand = white if contrast(rgb, white) >= contrast(rgb, ink) else ink
    if contrast(rgb, on_brand) < 4.5:
        # Couleur de milieu de gamme : ni le blanc ni l'encre n'y tiennent.
        # On assombrit l'aplat plutôt que de livrer un bouton illisible.
        rgb = darken_to(rgb, white)
        on_brand = white
    link = darken_to(_rgb(hex_color) or rgb, white)
    return _hex(rgb), _hex(on_brand), _hex(link)


class ResCompany(models.Model):
    _inherit = "res.company"

    helpdesk_brand_color = fields.Char(compute="_compute_helpdesk_brand")
    helpdesk_brand_text_color = fields.Char(compute="_compute_helpdesk_brand")
    helpdesk_brand_link_color = fields.Char(compute="_compute_helpdesk_brand")

    @api.depends("report_brand_primary", "email_primary_color", "primary_color")
    def _compute_helpdesk_brand(self):
        for company in self:
            # La couleur de la mise en page Blue Fox : celle de sa
            # barre d'accent, rendue lisible pour un texte posé dessus.
            source = (company.report_brand_primary or company.email_primary_color
                      or company.primary_color or NEUTRAL)
            brand, text, link = brand_palette(source)
            company.helpdesk_brand_color = brand
            company.helpdesk_brand_text_color = text
            company.helpdesk_brand_link_color = link
