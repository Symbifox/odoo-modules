"""La palette d'Odoo, et sa règle d'attribution, recopiées une seule fois.

⚠️ Ces valeurs ne sont pas décoratives : elles doivent donner EXACTEMENT la
couleur que la vue Calendrier affiche au bureau, sinon le téléphone et l'écran
racontent deux histoires sur la même rencontre. Elles viennent de
``web/static/src/scss/secondary_variables.scss`` (``$o-colors`` puis
``$o-colors-secondary``, joints en ``$o-colors-complete``).

La règle vient de ``web/static/src/views/calendar/colors.js`` :

    couleur = ((clé - 1) % 55) + 1

et la classe ``o_calendar_color_N`` prend la Nième couleur de la liste, en base
zéro. Une clé fausse (0, vide) ne prend aucune classe et retombe sur la
couleur 0.

Ici la clé est le champ ``color`` de l'événement, et non le premier
participant : ``calendar_nextcloud_sync`` remplace l'attribut ``color`` de la
vue. Une couleur par calendrier, donc, chaque calendrier synchronisé portant
la sienne.
"""

# $o-colors (12) puis $o-colors-secondary (44). L'ordre EST l'index.
O_COLORS_COMPLETE = (
    "#a2a2a2", "#ee2d2d", "#dc8534", "#e8bb1d", "#5794dd", "#9f628f",
    "#db8865", "#41a9a2", "#304be0", "#ee2f8a", "#61c36e", "#9872e6",
    "#aa4b6b", "#30C381", "#97743a", "#F7CD1F", "#4285F4", "#8E24AA",
    "#D6145F", "#173e43", "#348F50", "#AA3A38", "#795548", "#5e0231",
    "#6be585", "#999966", "#e9d362", "#b56969", "#bdc3c7", "#649173",
    "#ea00ff", "#ff0026", "#8bcc00", "#00bfaf", "#006aff", "#af00bf",
    "#bf001d", "#bf6300", "#8cff00", "#00f2ff", "#004ab3", "#ff00d0",
    "#ffa600", "#3acc00", "#00b6bf", "#0048ff", "#bf7c00", "#04ff00",
    "#00d0ff", "#0036bf", "#ff008c", "#00bf49", "#0092b3", "#0004ff",
    "#b20062", "#649173",
)

# Les étiquettes de kanban n'utilisent QUE les douze premières.
O_TAG_COLORS = O_COLORS_COMPLETE[:12]


def index_odoo(cle):
    """La transposition de `getColor` : une clé quelconque vers 0..55."""
    if not cle:
        return 0
    return ((int(cle) - 1) % 55) + 1


def couleur(cle):
    """Le fond plein, celui de `--o-event-bg`."""
    return O_COLORS_COMPLETE[index_odoo(cle)]


def couleur_douce(cle):
    """Le fond réellement peint dans la grille.

    La feuille de style fait ``mix($o-white, $color, 55%)``, c'est-à-dire 55 %
    de blanc et 45 % de la couleur. Rendre le ton plein ferait un agenda
    beaucoup plus saturé que celui du bureau.
    """
    return _melange_blanc(couleur(cle), 0.55)


def couleur_etiquette(cle):
    """Les étiquettes de tâches, sur les douze couleurs du kanban."""
    if not cle:
        return O_TAG_COLORS[0]
    return O_TAG_COLORS[int(cle) % len(O_TAG_COLORS)]


def _melange_blanc(hexa, part_blanche):
    brut = hexa.lstrip("#")
    canaux = [int(brut[i:i + 2], 16) for i in (0, 2, 4)]
    melanges = [
        round(255 * part_blanche + canal * (1 - part_blanche))
        for canal in canaux
    ]
    return "#%02x%02x%02x" % tuple(melanges)
