# Menu des applications cherchable

Odoo rend le menu des applications comme une liste de noms : pas d'icône, pas de
recherche, et passé une vingtaine d'applications la liste dépasse l'écran.

Ce module la remplace par un panneau borné, en grille, avec un champ de
recherche qui a déjà le curseur — et il porte la même recherche dans le tiroir
du téléphone, que le panneau n'atteint pas.

## Ce que ça fait

| | |
|---|---|
| Grille | icônes + nom, colonnes selon la largeur disponible |
| Panneau | 40% de la largeur, 55% de la hauteur, la grille défile |
| Recherche | curseur en place à l'ouverture, **Entrée** ouvre la 1re correspondance |
| Après un choix | le champ se vide, la fois suivante le menu est complet |
| Tolérance | `fuzzyLookup`, le même que la palette de commandes d'Odoo |

## Sur téléphone

Odoo ne monte **jamais** ce panneau sur petit écran : quand `ui.isSmall` est
vrai, il rend son tiroir latéral (`web.NavBar.AppsMenu.Sidebar`), qui a deux
listes — les menus de l'application ouverte, et « Toutes les applications »
derrière le bouton du haut. C'est cette seconde liste qui se parcourait à l'œil.

| | |
|---|---|
| Le champ | épinglé (`sticky`) en tête de la zone défilante : il ne part pas avec la liste |
| Le curseur | s'y pose quand on touche « Toutes les applications », et là seulement |
| À la simple ouverture | le champ peut être là (si on y était resté), mais sans le curseur |
| Entrée | ouvre la 1re correspondance et referme ; ne fait rien sur un champ vide |
| À la sortie | le champ se vide : X, fond, balayage, choix d'une ligne, rotation de l'écran |
| Forme des lignes | celle d'Odoo : une app par ligne, grande icône, cible large |
| Le clavier | `type="search"` + `enterkeyhint="go"` : la touche « aller » au lieu de « retour » |
| Glisser dans le champ | sélectionne du texte ; il ne ferme plus le tiroir |

⚠️ **Android lève le clavier, iOS non.** Safari ne lève le clavier que sur un
geste direct de l'usager ; un focus posé après un rendu n'en est plus un. Sur
iPhone, le curseur est donc dans le champ et le clavier monte au premier
toucher.

🔴 **Aucune couleur en dur dans le tiroir, et c'est tout le dessin.** Le tiroir
est blanc sur un Odoo nu et **sombre sous un habillage** — un habillage sombre met
tout le tiroir à `--brand-dark`. D'où le champ placé dans un `<ul>` du
`<nav>` : le `<ul>` prend le fond du `<nav>`, notre ligne prend celui du `<ul>`,
et l'`<input>` prend la couleur de texte que la règle `li, button` d'Odoo donne
déjà aux lignes. Sa pastille vient d'un `color-mix` sur `currentColor`. Rien à
tenir à jour quand la marque change.

⚠️ **La pastille a un repli, et il compte.** `color-mix` manque à Chrome < 111,
Safari < 16.2 et Firefox < 113 ; sans repli, la seule déclaration de fond de
l'`<input>` tombe et il revient au blanc de l'agent utilisateur — soit du blanc
sur blanc dans un tiroir sombre. Le repli est un gris neutre translucide
(`rgba(128, 128, 128, .25)`) : il assombrit un tiroir clair et éclaircit un
tiroir sombre. Les deux déclarations sont nécessaires, dans cet ordre.

🔴 **Le champ a son propre `<ul>`, frère de celui des applications.** Deux
raisons. Les couleurs, ci-dessus : hors d'un `<ul>` du `<nav>`, il n'hérite plus
de rien. Et la position des lignes : un `<li>` de plus dans le `<ul>` des
applications décalerait les sélecteurs d'Odoo — `li:nth-of-type(2)` désignerait
la **première** application, et trois essais d'Odoo visent cette position
(`web/static/tests/webclient/mobile/burger_menu.test.js`, dont
`.o_burger_menu_app > ul > li:nth-of-type(2)`). C'est aussi le `<ul>` qui porte
le `sticky`, pas le `<li>` : un élément collant ne sort jamais de la boîte de son
parent, et un `<ul>` à la hauteur de son unique `<li>` ne lui laisserait aucune
course.

⚠️ Corollaire : les variables `--brand-bg`, `--brand-line`, `--brand-mute`,
`--brand-charcoal` **ne sont pas posées partout** : un habillage peut n'en poser
que `--brand-dark` et `--brand-primary`. Le panneau du poste de
travail tourne donc sur ses valeurs de repli. Il est clair sur fond clair, donc
lisible — mais il ne suit pas la marque, contrairement à ce que laisse croire le
reste de ce fichier.

🔴 **Jamais moins de 16 px dans le champ** : en deçà, Safari iOS zoome la page à
la mise au point et le tiroir sort de l'écran.

⚠️ **Un habillage qui détourne le tiroir rend ce champ invisible.** Le module
tiers `main_menu` (auto_install) remplace le `t-on-click` du bouton « Toutes les
applications » par le sien : la liste des applications d'Odoo ne s'ouvre jamais,
donc le champ n'est jamais rendu. Aucun conflit, aucune erreur — simplement rien.
Vérifier `main_menu` avant de conclure que le module ne marche pas.

### Ce que le tiroir ne donne pas (et que ce module ne corrige pas)

Les lignes du tiroir d'Odoo n'ont ni `tabindex`, ni `role`, ni lien : **aucune
n'est atteignable au clavier**, et le tiroir ne confine pas le focus. Taper dans
le champ n'annonce donc rien à un lecteur d'écran, hormis le message « aucune
application » (`role="status"`). Corriger cela voudrait dire réécrire le tiroir
d'Odoo, pas l'étendre.

## Ce que ça ne change pas

Au poste de travail, les entrées restent des `DropdownItem` : mêmes `href`, même
navigation au clavier, même classe `o_app` et mêmes `data-menu-xmlid` /
`data-section`.

Dans le tiroir, la forme des lignes d'Odoo est intouchée, et les trois
sélecteurs de position de `burger_menu.test.js` désignent les mêmes applications
qu'avant le module — **mesuré dans le DOM du tiroir**, pas déduit. Les gestes du
tiroir restent ceux d'Odoo, à une exception près : un glissement né **dans le
champ** ne déclenche plus la fermeture par balayage (il sert à sélectionner du
texte). Partout ailleurs, le balayage ferme comme avant.

## Notes d'implémentation

⚠️ **Le focus ne peut passer ni par `useAutofocus`, ni par un `setTimeout(0)`.**
Le `Dropdown` d'Odoo pose son propre focus à l'ouverture (`useNavigation`), et
il passe par `throttleForAnimation`, donc par un `requestAnimationFrame` : tout
focus posé plus tôt se fait reprendre à la frame suivante, en silence. Le vrai
correctif est dans le gabarit (le champ est enveloppé dans un `.o-navigable`, ce
qui en fait le premier élément navigable du menu) ; le double
`requestAnimationFrame` du JS ne reste qu'en filet.

⚠️ **Le panneau est rendu dans `.o-overlay-container`**, hors du DOM de la barre
de navigation. Un sélecteur descendant depuis `.o_main_navbar` ne l'atteint
jamais — d'où la classe posée par la prop `menuClass` du `Dropdown`. Le tiroir
du téléphone est lui aussi hors de la barre : Odoo le porte dans le `body`
(`t-portal`). Dans les deux cas, le curseur se pose par une requête DOM et non
par un `t-ref` à travers le portail.

## Habillage

**Panneau du poste de travail** : les couleurs passent par les variables
`--brand-*` quand l'instance en pose (convention des modules d'habillage maison)
et retombent sinon sur des valeurs neutres. ⚠️ Là où aucune de celles qu'il utilise
n'est posée, il tourne entièrement sur ses replis. Les deux
nombres à ajuster, en tête de `apps_menu.scss` : `40vw` et `55vh`.

**Tiroir du téléphone** : rien à ajuster, et rien à nommer. Tout vient par
héritage du tiroir lui-même (voir plus haut). La seule couleur écrite dans le
bloc du tiroir est le repli de la pastille, un gris neutre qui ne suppose ni
tiroir clair ni tiroir sombre.

## Languages (v18.0.1.1.0)

Labels and messages are written in English in the source; the French ships in `i18n/fr_CA.po`. Before this version they read in French for every user, including users set to English.
