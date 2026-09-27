"""Nombres cités dans une phrase lue.

Une voix pondérée se tient à quatre décimales (une quote-part de déclaration
en porte souvent trois), et le champ garde cette précision à l'écran. Dans une
PHRASE, les zéros de remplissage ne disent rien et nuisent à la lecture :
« 585.0000 voix pour sur 502.5000 requises » se lit « 585 voix pour sur
502,5 requises ». La valeur n'est jamais arrondie au-delà de ce que le champ
porte : seuls les zéros de fin s'en vont.

⚠️ Les séparateurs viennent de la langue du LECTEUR (`formatLang` lit
`env.lang`), jamais d'un `%.4f` qui imprime le point anglais en français.
"""
from odoo.tools.misc import formatLang, get_lang


def format_decimal(env, value, digits=4):
    """Rend `value` à `digits` décimales au plus, sans zéros de fin."""
    text = formatLang(env, value or 0.0, digits=digits)
    # La même langue que `formatLang`, sinon on retire un séparateur qu'il n'a
    # pas imprimé.
    point = get_lang(env).decimal_point
    if point and point in text:
        text = text.rstrip("0").rstrip(point)
    return text
