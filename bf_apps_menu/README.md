# Menu des applications cherchable

Odoo rend le menu des applications comme une liste de noms : pas d'icône, pas de
recherche, et passé une vingtaine d'applications la liste dépasse l'écran.

Ce module la remplace par un panneau borné, en grille, avec un champ de
recherche qui a déjà le curseur.

## Ce que ça fait

| | |
|---|---|
| Grille | icônes + nom, colonnes selon la largeur disponible |
| Panneau | 40% de la largeur, 55% de la hauteur, la grille défile |
| Recherche | curseur en place à l'ouverture, **Entrée** ouvre la 1re correspondance |
| Après un choix | le champ se vide, la fois suivante le menu est complet |
| Tolérance | `fuzzyLookup`, le même que la palette de commandes d'Odoo |

## Ce que ça ne change pas

Les entrées restent des `DropdownItem` : mêmes `href`, même navigation au
clavier, même classe `o_app` et mêmes `data-menu-xmlid` / `data-section`. Les
tours et les tests d'Odoo qui s'appuient dessus continuent de passer.

## Notes d'implémentation

⚠️ **Le focus ne peut pas passer par `useAutofocus`.** Le `Dropdown` d'Odoo pose
son propre focus à l'ouverture (`useNavigation`), et il le pose *après* le
montage du contenu : un focus synchrone se fait reprendre aussitôt. D'où le
`setTimeout(0)` dans l'effet, qui repasse au tour de boucle suivant.

⚠️ **Le panneau est rendu dans `.o-overlay-container`**, hors du DOM de la barre
de navigation. Un sélecteur descendant depuis `.o_main_navbar` ne l'atteint
jamais — d'où la classe posée par la prop `menuClass` du `Dropdown`.

## Habillage

Les couleurs passent par les variables `--brand-*` quand l'instance en pose
(convention des modules d'habillage maison) et retombent sinon sur des valeurs
neutres. Les deux nombres à ajuster, en tête de `apps_menu.scss` : `40vw` et
`55vh`.
