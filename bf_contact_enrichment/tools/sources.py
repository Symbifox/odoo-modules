"""Les sources que Gen a lues, rendues en liste de liens.

Le même rendu sert la note au fil et la fenêtre de la proposition. Ce que Gen
rend vient du web : seules les adresses ``http(s)`` passent, et chaque valeur
est échappée en entrant dans le balisage (``Markup``), donc ni ``javascript:``
ni guillemet ne sortent de l'attribut.
"""
from markupsafe import Markup

#: Combien de sources se montrent ; le pont en rend au plus huit.
LIMITE = 8


def liens_http(sources, limite=LIMITE):
    """Les adresses http(s) d'une liste, dans l'ordre, sans doublon."""
    vues = []
    for source in sources or []:
        if (isinstance(source, str) and source.startswith(("https://", "http://"))
                and source not in vues):
            vues.append(source)
    return vues[:limite]


def items_html(sources, limite=LIMITE):
    """Les ``<li>`` d'une liste de sources ; vide s'il n'y en a aucune."""
    return Markup("").join(
        Markup('<li><a href="%s" target="_blank" rel="noopener noreferrer">'
               '%s</a></li>') % (lien, lien)
        for lien in liens_http(sources, limite))
