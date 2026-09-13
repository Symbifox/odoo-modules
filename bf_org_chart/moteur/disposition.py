# -*- coding: utf-8 -*-
"""De la carte au plan : les coordonnées, calculées une seule fois.

Deux dispositions, choisies par la forme des données et non par le module
appelant :

* **arbre** quand chaque boîte a au plus un parent. Parent centré sur ses
  enfants, c'est l'organigramme que tout le monde reconnaît.
* **couches** dès qu'une boîte a plusieurs parents. Une détention partagée
  (60 / 40) n'est pas un arbre : elle se range en niveaux, et les arêtes se
  croisent le moins possible.

Une boucle ne fait pas tomber le dessin : les arêtes qui la referment sont
écartées et nommées dans `Plan.avertissements`, pour que l'écran puisse le
dire. Un organigramme saisi à la main finit toujours par en contenir une.
"""
from dataclasses import dataclass, field

from . import mesure

# --- métrique du dessin, en points PDF ---------------------------------------
BOITE_L = 190.0
MARGE_INT = 9.0
ESPACE_H = 24.0
ESPACE_V = 58.0
MARGE_PAGE = 36.0
T_TITRE, T_SOUS, T_NOTE = 10.5, 8.5, 8.0
INTERLIGNE = 1.28
H_ENTETE = 54.0
H_PIED = 34.0
#: Un relais est la place RÉSERVÉE, à un niveau intermédiaire, pour laisser
#: passer une arête qui saute ce niveau. Sans lui, le trait traverse une boîte.
RELAIS_L = 14.0
RELAIS_H = 6.0


@dataclass
class BoitePlan:
    cle: str
    x: float
    y: float
    w: float
    h: float
    teinte: str
    lien: str | None
    accent: bool = False
    lignes_titre: list = field(default_factory=list)
    lignes_sous: list = field(default_factory=list)
    lignes_note: list = field(default_factory=list)

    @property
    def cx(self):
        return self.x + self.w / 2.0

    @property
    def bas(self):
        return self.y + self.h


@dataclass
class AretePlan:
    de: str
    vers: str
    points: list
    etiquette: str = ""
    etiquette_xy: tuple = (0.0, 0.0)
    pointille: bool = False


@dataclass
class Plan:
    boites: list = field(default_factory=list)
    aretes: list = field(default_factory=list)
    largeur: float = 0.0
    hauteur: float = 0.0
    titre: str = ""
    sous_titre: str = ""
    legende: list = field(default_factory=list)
    pied: str = ""
    mode: str = "arbre"
    avertissements: list = field(default_factory=list)

    def boite(self, cle):
        for b in self.boites:
            if b.cle == cle:
                return b
        return None


def _mesurer(carte):
    """Replie les libellés et donne sa hauteur à chaque boîte."""
    plans, utile = {}, BOITE_L - 2 * MARGE_INT
    for b in carte.boites:
        titre = mesure.replier(b.titre, T_TITRE, utile, 2, gras=True)
        sous = mesure.replier(b.sous_titre, T_SOUS, utile, 2)
        note = mesure.replier(b.note, T_NOTE, utile, 1)
        h = 2 * MARGE_INT + len(titre) * T_TITRE * INTERLIGNE
        h += len(sous) * T_SOUS * INTERLIGNE + len(note) * T_NOTE * INTERLIGNE
        plans[b.cle] = BoitePlan(
            cle=b.cle, x=0.0, y=0.0, w=BOITE_L, h=round(h, 2),
            teinte=b.teinte, lien=b.lien, accent=b.accent,
            lignes_titre=titre, lignes_sous=sous, lignes_note=note,
        )
    return plans


def _graphe(carte):
    """Rend (enfants, parents) en conservant l'ordre d'arrivée des boîtes."""
    enfants = {c: [] for c in carte.cles()}
    parents = {c: [] for c in carte.cles()}
    for a in carte.aretes:
        enfants[a.de].append(a.vers)
        parents[a.vers].append(a.de)
    return enfants, parents


def _couper_les_boucles(carte, enfants, parents):
    """Écarte les arêtes de retour, et rend la liste de ce qui a été coupé.

    Parcours en profondeur avec trois couleurs. Une arête vers un nœud encore
    gris referme un cycle : on la retire du graphe ET de la carte, pour que les
    deux rendus ne la voient jamais.
    """
    couleur, coupees = {c: 0 for c in enfants}, []

    def descendre(n):
        couleur[n] = 1
        for f in list(enfants[n]):
            if couleur[f] == 1:
                coupees.append((n, f))
                enfants[n].remove(f)
                parents[f].remove(n)
            elif couleur[f] == 0:
                descendre(f)
        couleur[n] = 2

    for n in list(enfants):
        if couleur[n] == 0:
            descendre(n)
    if coupees:
        perdues = {(de, vers) for de, vers in coupees}
        carte.aretes = [a for a in carte.aretes if (a.de, a.vers) not in perdues]
    return coupees


def _niveaux(enfants, parents):
    """Niveau = plus long chemin depuis une racine. Graphe déjà acyclique."""
    niveau = {c: 0 for c in enfants}
    reste = {c: len(parents[c]) for c in enfants}
    file = [c for c, n in reste.items() if n == 0]
    ordre = []
    while file:
        n = file.pop(0)
        ordre.append(n)
        for f in enfants[n]:
            niveau[f] = max(niveau[f], niveau[n] + 1)
            reste[f] -= 1
            if reste[f] == 0:
                file.append(f)
    return niveau


def _y_des_niveaux(plans, niveau):
    """Chaque niveau démarre sous le plus haut de ceux qui le précèdent.

    Rend (haut du niveau, bas du niveau) : le bas sert à placer les couloirs
    d'arêtes exactement au milieu de l'espace libre.
    """
    hauteurs = {}
    for cle, n in niveau.items():
        hauteurs[n] = max(hauteurs.get(n, 0.0), plans[cle].h)
    y, ys, bas = MARGE_PAGE + H_ENTETE, {}, {}
    for n in sorted(hauteurs):
        ys[n] = y
        bas[n] = y + hauteurs[n]
        y += hauteurs[n] + ESPACE_V
    return ys, bas


def _disposer_arbre(carte, plans, enfants, parents, niveau):
    """Parent centré sur ses enfants, sous-arbres posés côte à côte."""
    ys, _bas = _y_des_niveaux(plans, niveau)
    racines = [c for c in carte.cles() if not parents[c]]

    largeurs = {}

    def largeur_sous_arbre(n, vus):
        if n in largeurs:
            return largeurs[n]
        vus = vus | {n}
        fils = [f for f in enfants[n] if f not in vus]
        if not fils:
            largeurs[n] = plans[n].w
        else:
            total = sum(largeur_sous_arbre(f, vus) for f in fils)
            total += ESPACE_H * (len(fils) - 1)
            largeurs[n] = max(plans[n].w, total)
        return largeurs[n]

    for r in racines:
        largeur_sous_arbre(r, set())

    def poser(n, gauche, vus):
        vus = vus | {n}
        fils = [f for f in enfants[n] if f not in vus]
        plans[n].y = ys[niveau[n]]
        if not fils:
            plans[n].x = gauche + (largeurs[n] - plans[n].w) / 2.0
            return
        curseur = gauche
        for f in fils:
            poser(f, curseur, vus)
            curseur += largeurs[f] + ESPACE_H
        premier, dernier = plans[fils[0]], plans[fils[-1]]
        plans[n].x = (premier.cx + dernier.cx) / 2.0 - plans[n].w / 2.0

    curseur = MARGE_PAGE
    for r in racines:
        poser(r, curseur, set())
        curseur += largeurs[r] + ESPACE_H * 2


def _relayer(carte, plans, niveau):
    """Réserve une colonne à chaque niveau qu'une arête saute.

    C'est la seule façon honnête de faire passer un lien du niveau 0 au niveau
    2 sans traverser une boîte du niveau 1 : lui donner, au niveau 1, une place
    aussi réelle que celle d'une boîte. Les relais ne sont jamais dessinés, ils
    ne font qu'occuper de l'espace et porter le tracé.
    """
    enfants = {c: [] for c in plans}
    parents = {c: [] for c in plans}
    chaines, compteur = {}, 0
    for a in carte.aretes:
        u, v = a.de, a.vers
        if niveau[v] - niveau[u] <= 1:
            enfants[u].append(v)
            parents[v].append(u)
            continue
        chaine, precedent = [], u
        for etage in range(niveau[u] + 1, niveau[v]):
            cle = "\u00a7relais-%d" % compteur
            compteur += 1
            plans[cle] = BoitePlan(cle=cle, x=0.0, y=0.0, w=RELAIS_L, h=RELAIS_H,
                                   teinte="neutre", lien=None)
            niveau[cle] = etage
            enfants[cle], parents[cle] = [], []
            enfants[precedent].append(cle)
            parents[cle].append(precedent)
            chaine.append(cle)
            precedent = cle
        enfants[precedent].append(v)
        parents[v].append(precedent)
        chaines[(u, v)] = chaine
    return enfants, parents, chaines


def _disposer_couches(carte, plans, enfants, parents, niveau):
    """Niveaux, relais, puis barycentre : on range pour croiser le moins possible.

    Rend (chaines de relais, y du bas de chaque niveau), dont `_tracer_aretes`
    a besoin pour poser les couloirs.
    """
    enfants, parents, chaines = _relayer(carte, plans, niveau)
    ys, bas = _y_des_niveaux(plans, niveau)
    par_niveau = {}
    for cle in plans:
        par_niveau.setdefault(niveau[cle], []).append(cle)

    for _ in range(4):
        for n in sorted(par_niveau)[1:]:
            rang = {c: i for i, c in enumerate(par_niveau[n - 1])}

            def bary(c, rang=rang):
                pos = [rang[p] for p in parents[c] if p in rang]
                return sum(pos) / len(pos) if pos else 1e9

            par_niveau[n].sort(key=bary)
        for n in sorted(par_niveau, reverse=True)[1:]:
            suivant = par_niveau.get(n + 1, [])
            rang = {c: i for i, c in enumerate(suivant)}

            def bary_bas(c, rang=rang):
                pos = [rang[f] for f in enfants[c] if f in rang]
                return sum(pos) / len(pos) if pos else 1e9

            par_niveau[n].sort(key=bary_bas)

    for n in sorted(par_niveau):
        x = MARGE_PAGE
        for cle in par_niveau[n]:
            plans[cle].x, plans[cle].y = x, ys[n]
            x += plans[cle].w + ESPACE_H

    # Une passe de recentrage : chaque boîte glisse vers ses parents, sans
    # jamais chevaucher sa voisine de gauche.
    for n in sorted(par_niveau)[1:]:
        gauche_min = MARGE_PAGE
        for cle in par_niveau[n]:
            cibles = [plans[p].cx for p in parents[cle] if p in plans]
            if cibles:
                vise = sum(cibles) / len(cibles) - plans[cle].w / 2.0
                plans[cle].x = max(gauche_min, vise)
            gauche_min = plans[cle].x + plans[cle].w + ESPACE_H
    return chaines, bas


def _tracer_aretes(carte, plans, niveau, chaines, bas):
    """Un tracé orthogonal par arête, en passant par les relais réservés.

    Le couloir horizontal d'un saut est posé au milieu de l'espace libre entre
    deux niveaux, jamais à mi-chemin entre deux boîtes éloignées : c'est ce qui
    faisait traverser les boîtes intermédiaires.
    """
    aretes = []
    for a in carte.aretes:
        d, v = plans[a.de], plans[a.vers]
        colonnes = [d.cx] + [plans[r].cx for r in chaines.get((a.de, a.vers), [])] + [v.cx]
        etages = list(range(niveau[a.de], niveau[a.vers]))
        points = [(colonnes[0], d.bas)]
        for i, etage in enumerate(etages):
            couloir = bas.get(etage, d.bas) + ESPACE_V / 2.0
            if abs(colonnes[i] - colonnes[i + 1]) < 0.5 and len(etages) == 1:
                continue
            points.append((colonnes[i], couloir))
            points.append((colonnes[i + 1], couloir))
        points.append((colonnes[-1], v.y))
        if len(points) >= 4:
            etiq = ((points[1][0] + points[2][0]) / 2.0, points[1][1])
        else:
            etiq = (points[0][0] + 5, (points[0][1] + points[-1][1]) / 2.0)
        aretes.append(AretePlan(
            de=a.de, vers=a.vers, points=points, etiquette=a.etiquette,
            etiquette_xy=etiq, pointille=a.pointille,
        ))
    _decoller_les_etiquettes(aretes)
    return aretes


def _decoller_les_etiquettes(aretes):
    """Deux pourcentages qui se chevauchent valent un pourcentage illisible.

    Les étiquettes d'un même couloir sont parcourues de gauche à droite; celle
    qui mordrait sur la précédente monte d'un cran. Le trait, lui, ne bouge pas.
    """
    portees = [a for a in aretes if a.etiquette]
    couloirs = {}
    for a in portees:
        couloirs.setdefault(round(a.etiquette_xy[1], 1), []).append(a)
    for _y, lot in couloirs.items():
        lot.sort(key=lambda a: a.etiquette_xy[0])
        droite_precedente, cran = None, 0
        for a in lot:
            demi = mesure.largeur(a.etiquette, 7.5, gras=True) / 2.0 + 6
            gauche = a.etiquette_xy[0] - demi
            if droite_precedente is not None and gauche < droite_precedente:
                cran = 1 if cran == 0 else 0
                a.etiquette_xy = (a.etiquette_xy[0], a.etiquette_xy[1] - 15 * (cran + 1))
            else:
                cran = 0
            droite_precedente = a.etiquette_xy[0] + demi


def disposer(carte):
    """Rend le `Plan` complet, coordonnées comprises. Ne lève jamais."""
    carte.valider()
    plans = _mesurer(carte)
    plan = Plan(titre=carte.titre, sous_titre=carte.sous_titre,
                legende=list(carte.legende), pied=carte.pied)
    if not carte.boites:
        plan.largeur = 420.0
        plan.hauteur = MARGE_PAGE * 2 + H_ENTETE + H_PIED
        return plan

    enfants, parents = _graphe(carte)
    coupees = _couper_les_boucles(carte, enfants, parents)
    for de, vers in coupees:
        plan.avertissements.append(
            "Lien circulaire écarté du dessin : %s vers %s." % (de, vers))
    niveau = _niveaux(enfants, parents)
    plan.mode = "arbre" if all(len(p) <= 1 for p in parents.values()) else "couches"
    if plan.mode == "arbre":
        _disposer_arbre(carte, plans, enfants, parents, niveau)
        _, bas = _y_des_niveaux(plans, niveau)
        chaines = {}
    else:
        chaines, bas = _disposer_couches(carte, plans, enfants, parents, niveau)

    plan.boites = [plans[c] for c in carte.cles()]
    plan.aretes = _tracer_aretes(carte, plans, niveau, chaines, bas)
    droite = max(b.x + b.w for b in plan.boites)
    bas = max(b.bas for b in plan.boites)
    plan.largeur = round(droite + MARGE_PAGE, 2)
    plan.hauteur = round(bas + MARGE_PAGE + H_PIED, 2)
    return plan
