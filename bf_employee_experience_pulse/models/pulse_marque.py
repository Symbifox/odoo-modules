"""Les couleurs de la marque, lues où elles vivent, et rendues lisibles.

Trois règles de la maison se croisent ici.

1. 🔴 **Les couleurs vivent en base**, pas dans le code : `report_brand_primary`
   et `report_brand_dark` sur la société. Un hex écrit en dur dans un gabarit
   ment le jour où un locataire change de marque.
2. ⚠️ **Le module ne dépend pas de `bluefox_branding`**, parce qu'il part au
   catalogue. Les champs de marque peuvent donc ne pas exister : on lit ce qui
   est là, et on retombe sur les champs natifs d'Odoo, puis sur un défaut.
3. 🔴 **L'accent ne se met pas sous du texte blanc tel quel.** Mesuré :
   du blanc sur un bleu d'accent courant rend **2,62:1**, très en dessous du
   4,5 exigé par WCAG AA. L'accent brut reste pour ce qui ne porte pas de texte (filet, bordure,
   pastille) ; ce qui porte du texte prend une variante assombrie, calculée
   ici plutôt que devinée.
"""

from odoo import api, models

DEFAUT_ACCENT = "#29ABE2"
DEFAUT_SOMBRE = "#2E3132"
CONTRASTE_VISE = 4.5


def _canal(valeur):
    valeur = valeur / 255
    return valeur / 12.92 if valeur <= 0.03928 else ((valeur + 0.055) / 1.055) ** 2.4


def _luminance(hexa):
    hexa = (hexa or "").lstrip("#")
    if len(hexa) != 6:
        return 0.0
    r, g, b = (int(hexa[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _canal(r) + 0.7152 * _canal(g) + 0.0722 * _canal(b)


def contraste(a, b):
    """Rapport de contraste WCAG entre deux couleurs."""
    la, lb = _luminance(a), _luminance(b)
    haut, bas = max(la, lb), min(la, lb)
    return (haut + 0.05) / (bas + 0.05)


def assombrir(hexa, facteur):
    hexa = (hexa or "").lstrip("#")
    if len(hexa) != 6:
        return "#" + hexa
    r, g, b = (int(hexa[i:i + 2], 16) for i in (0, 2, 4))
    return "#%02X%02X%02X" % (int(r * facteur), int(g * facteur), int(b * facteur))


def sur_fond_blanc(accent):
    """La variante de l'accent qui porte du texte blanc sans descendre sous AA.

    On assombrit par pas de 5 % jusqu'à passer le seuil. Une boucle plutôt
    qu'une constante : l'accent d'un locataire n'est pas le nôtre, et un pas
    codé en dur ne vaudrait que pour le bleu de Blue Fox.
    """
    facteur = 1.0
    couleur = accent or DEFAUT_ACCENT
    while facteur > 0.2:
        if contraste("#FFFFFF", couleur) >= CONTRASTE_VISE:
            return couleur
        facteur -= 0.05
        couleur = assombrir(accent or DEFAUT_ACCENT, facteur)
    return DEFAUT_SOMBRE


class PulseMarque(models.AbstractModel):
    _name = "bf.ex.pulse.marque"
    _description = "Couleurs de marque du pulse"

    @api.model
    def couleurs(self, company=None):
        """Rend les couleurs à employer, pour un courriel comme pour la page."""
        company = company or self.env.company
        accent = DEFAUT_ACCENT
        sombre = DEFAUT_SOMBRE
        if company:
            champs = company._fields
            for nom in ("report_brand_primary", "primary_color"):
                if nom in champs and company[nom]:
                    accent = company[nom]
                    break
            for nom in ("report_brand_dark", "secondary_color"):
                if nom in champs and company[nom]:
                    sombre = company[nom]
                    break
        # ⚠️ Un texte clair sur le sombre du locataire n'est pas garanti non
        # plus : si son « sombre » est pâle, on écrit dessus en anthracite.
        texte_sur_sombre = ("#E6EDF3" if contraste("#E6EDF3", sombre) >= CONTRASTE_VISE
                            else DEFAUT_SOMBRE)
        return {
            "accent": accent,
            "sombre": sombre,
            "bouton": sur_fond_blanc(accent),
            "texte_sur_sombre": texte_sur_sombre,
            "logo": "/logo.png?company=%s" % (company.id if company else ""),
        }
