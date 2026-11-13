"""Shared brand-color replacement logic.

Used by both ``mail.mail`` (at send time) and ``mail.render.mixin`` (at render
time) to swap Odoo's legacy purple for the brand primary of the company the
email belongs to.

⚠️ **The company is an argument, never ``env.company``.** The caller resolves
it from the record that sends the email; see ``mail.mail._bf_societe_de_marque``.
``env.company`` is only the last resort, and reading it here instead of
receiving it is what made a subsidiary's email wear the session's accent.

Implemented as plain module-level functions (NOT an Odoo/Python base class):
adding a foreign base class to an Odoo model breaks Odoo's ``cls.__bases__``
reassignment during registry setup ("object layout differs"). Each model keeps
a thin delegating method that calls these helpers, so the actual logic and the
OLD_COLORS list live in exactly one place.
"""

import re

from markupsafe import Markup

# Old Odoo default colors to replace with the active company's brand primary.
# NB: '#714B67' is both listed here AND the default value of
# res.company.report_brand_primary — for a company left on the default the
# replacement is a harmless no-op (purple -> same purple).
OLD_COLORS = [
    '#875A7B',  # Odoo purple (hex)
    '#875a7b',  # lowercase
    'rgb(135,90,123)',  # Odoo purple (rgb)
    'rgb(135, 90, 123)',  # with spaces
    '#714B67',  # Another Odoo purple variant
    '#714b67',  # lowercase
]

_MOTIF_VIEILLES = re.compile('|'.join(re.escape(c) for c in OLD_COLORS), re.IGNORECASE)


def entier(valeur):
    """L'identifiant, ou None. Un `NewId`, un faux ou une chaîne passent par
    ici sans lever : aucune couleur ne vaut une trace d'erreur.

    Ici plutôt que dans l'un des deux modèles, pour la même raison que
    `modele_sans_table` : les DEUX chemins en ont besoin, et ce module est le
    seul endroit qu'ils partagent déjà. Le chemin du rendu faisait un `int()`
    nu sur les identifiants que l'appelant lui passe — la même asymétrie que
    sur `mail.mail`, de l'autre côté.
    """
    try:
        return int(valeur) or None
    except (TypeError, ValueError):
        return None


def modele_sans_table(modele):
    """Ce modèle a-t-il une relation à interroger ?

    ⚠️ **`model in env` ne dit PAS qu'il y a une table.** Les 229 modèles
    ABSTRAITS de cette base sont bel et bien dans le registre, et un
    `browse(...).exists()` lance alors un `SELECT … FROM <table>` sur une
    relation qui n'existe pas. Mesuré :
    `_bf_societe_de_marque({'model': 'account.move.send', 'res_id': 1})` lève
    `UndefinedTable` — et sans savepoint PostgreSQL abandonne TOUTE la
    transaction, donc le `message_post` qui vient de passer et tout ce qui
    suit. Une couleur ne vaut jamais une trace d'erreur, encore moins une
    transaction perdue : on écarte AVANT de lire.

    Les modèles concrets à `_auto = False` (des vues SQL) sortent par la même
    porte : mesuré sur cette base, 8 des 30 — dont `sale.report` et
    `account.invoice.report` — n'ont aucune relation, et aucun des 30 n'est un
    fil de discussion, donc aucun ne peut porter un courriel.

    Ici plutôt que dans l'un des deux modèles : les deux chemins (`mail.mail`
    et `mail.render.mixin`) en ont besoin, et ce module est le seul endroit
    qu'ils partagent déjà.
    """
    return modele._abstract or not modele._auto


def couleur_de_marque(company):
    """L'accent de cette société, à défaut le mauve d'Odoo.

    Une société qui n'a jamais reçu de couleur porte la valeur par défaut du
    champ, le mauve d'Odoo : le remplacement est alors un non-geste, et c'est
    voulu — la société commande jusqu'au bout. Pour qu'une de ses lettres ne
    sorte pas en mauve, on lui donne une couleur, pas un repli.
    """
    return (company and company.report_brand_primary) or '#714B67'


def porte_une_vieille_couleur(html_content):
    """Y a-t-il quelque chose à remplacer ? Posé AVANT de chercher la société.

    La résolution de la société coûte une lecture ; la très grande majorité
    des corps ne portent aucun mauve. On ne paie donc la lecture que pour les
    corps qui vont changer.
    """
    return bool(html_content) and bool(_MOTIF_VIEILLES.search(str(html_content)))


def get_brand_button_color(env, company=None):
    """Soft-coded brand primary from res.company (owned by bluefox_branding since 18.0.2).

    ⚠️ Un recordset VIDE n'est pas « une société sans couleur » : c'est « on ne
    sait pas », et la session reprend alors la main. Sans ce départage, un
    appelant qui passerait une société vide repeindrait tout en mauve.
    """
    return couleur_de_marque(company or env.company)


def get_brand_button_color_rgb(env, company=None):
    """rgb() variant of brand primary, derived from the hex value.

    ⚠️ Le repli est le mauve d'ODOO, c'est-à-dire la couleur qu'on remplace :
    un accent illisible laisse donc le corps tel quel, le non-geste habituel
    du lot. Il valait une couleur de marque écrite EN DUR, celle de l'éditeur :
    un locataire dont une société porte un accent malformé aurait donc vu la
    marque de l'éditeur dans ses boutons.
    """
    h = get_brand_button_color(env, company=company).lstrip('#')
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f'rgb({r},{g},{b})'
    except (ValueError, IndexError):
        return 'rgb(135,90,123)'


def replace_button_colors(env, html_content, company=None):
    """Replace old Odoo button colors with the brand colors of ``company``.

    ``company`` is the company the email belongs to, resolved by the caller
    from the record that sends it. Left out, it falls back to the session's
    company — the last link of the chain, not the rule.

    Markup in, Markup out: ``re.sub()`` and ``Markup.replace()`` both return a
    plain ``str``, which would silently strip the safe-HTML marking. A caller
    like the mass-mailing layout then feeds that ``str`` to ``<t t-out="body"/>``,
    and QWeb escapes the whole body — the recipient sees ``&lt;table&gt;`` instead
    of the email. Work on a plain str internally, restore the type on the way out.
    """
    if not html_content:
        return html_content

    was_markup = isinstance(html_content, Markup)
    result = str(html_content)
    brand_color = get_brand_button_color(env, company=company)
    brand_color_rgb = get_brand_button_color_rgb(env, company=company)
    for old_color in OLD_COLORS:
        if old_color.startswith('#'):
            pattern = re.compile(re.escape(old_color), re.IGNORECASE)
            # ⚠️ Une FONCTION de remplacement, pas la couleur elle-même : en
            # second argument, `sub` lit un GABARIT où la barre oblique
            # inverse est un code. Mesuré : un accent de marque valant
            # `'#29ABE2\\'` fait lever `re.error: bad escape`, et `'\\1'`
            # `invalid group reference` — en pleine création de courriel, dans
            # un chemin qui ne doit jamais lever. Le champ
            # `report_brand_primary` est un `Char` nu, sans contrainte,
            # écrivable par RPC ou par import ; et le lot élargit qui peut
            # déclencher le défaut, puisque la couleur peut maintenant venir
            # de n'importe quelle société de la chaîne, y compris une filiale
            # que l'opérateur ne voit pas.
            #
            # Pas `re.escape(brand_color)` : `re.escape` échappe pour un
            # MOTIF, pas pour un gabarit de remplacement. Il ferait taire la
            # levée par accident et écrirait la barre oblique inverse telle
            # quelle dans le HTML. La fonction, elle, insère la chaîne sans
            # qu'aucune grammaire ne la relise : toute la classe de défaut
            # disparaît.
            result = pattern.sub(lambda _trouve: brand_color, result)
        else:
            result = result.replace(old_color, brand_color_rgb)
    return Markup(result) if was_markup else result


# ---------------------------------------------------------------- contraste
# L'accent de marque sert de FOND sous du texte blanc (boutons primaires,
# pastilles, barres de progression). Mesuré le 2026-09-11 : #29ABE2 sous du
# blanc rend 2,62:1, très en dessous du seuil AA de 4,5, et l'accent orange
# d'une seconde société 2,96:1. Ce n'est pas un défaut du mode sombre : la même
# paire rend la même chose en mode clair.
#
# ⚠️ Un assombrissement GLOBAL serait faux : trois des sept accents en
# service passent déjà (le violet d'Odoo rend 7,23:1, un vert sourd 5,47), et les
# noircir ne ferait que les abîmer. La variante se calcule donc par marque, et
# une marque qui passe déjà n'est pas touchée.

_SEUIL_AA = 4.5

# WCAG accorde un seuil de 3:1 au « gros texte » — ≥ 18,66 px en gras, ou
# ≥ 24 px en romain. Une étiquette de bouton qui respecte cette taille peut
# donc porter du blanc sur un accent nettement plus proche de la marque :
# #29ABE2 descend à #1d9fd6 (ΔE 4,4) au lieu de #1e7fa7 (ΔE 18,2).
# ⚠️ Ce seuil n'est valide QUE si la règle CSS impose aussi la taille et la
# graisse. Utiliser cette variante sur du texte normal est une régression
# d'accessibilité silencieuse — voir `.btn-primary` dans branding.scss.
_SEUIL_AA_GRAND = 3.0


def _canal_lineaire(valeur):
    v = valeur / 255.0
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def _luminance(rgb):
    r, v, b = (_canal_lineaire(c) for c in rgb)
    return 0.2126 * r + 0.7152 * v + 0.0722 * b


def _contraste(rgb_a, rgb_b):
    a, b = _luminance(rgb_a), _luminance(rgb_b)
    haut, bas = (a, b) if a > b else (b, a)
    return (haut + 0.05) / (bas + 0.05)


def _en_rgb(hexa):
    """Hex -> (r, v, b), ou None si la valeur n'est pas lisible."""
    h = (hexa or '').strip().lstrip('#')
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    if len(h) != 6:
        return None
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return None


def assombrir_pour_texte_blanc(hexa, seuil=_SEUIL_AA):
    """La même couleur si elle porte déjà du texte blanc, sinon la plus CLAIRE
    de ses versions assombries qui atteint le seuil.

    On descend par pas de 1 % plutôt que de prendre un facteur fixe : le but
    est de rester le plus près possible de la marque, pas d'obtenir un ton
    foncé. Le bleu clair y perd 26 % de luminosité, un bleu moyen 7 %, et trois
    des sept accents en service n'y perdent rien.
    """
    rgb = _en_rgb(hexa)
    if rgb is None:
        return hexa
    blanc = (255, 255, 255)
    if _contraste(rgb, blanc) >= seuil:
        return hexa
    for pourcent in range(99, 0, -1):
        candidat = tuple(c * pourcent / 100.0 for c in rgb)
        if _contraste(candidat, blanc) >= seuil:
            return '#%02x%02x%02x' % tuple(round(c) for c in candidat)
    return '#000000'


def get_brand_button_color_strong(env, company=None):
    """Variante de l'accent utilisable comme FOND sous du texte blanc."""
    return assombrir_pour_texte_blanc(get_brand_button_color(env, company=company))


def get_brand_button_color_large(env, company=None):
    """Accent utilisable sous du texte blanc GROS ET GRAS (seuil 3:1)."""
    return assombrir_pour_texte_blanc(get_brand_button_color(env, company=company),
                                      seuil=_SEUIL_AA_GRAND)
