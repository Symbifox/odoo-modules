# -*- coding: utf-8 -*-
"""Tracé PDF vectoriel, côté serveur, depuis les enregistrements.

Le troisième rendu. Le `.bpmn` et le `.drawio` partent déjà de `geometrie` ;
celui-ci en part aussi, et c'est tout l'intérêt — une seule géométrie, trois
sorties, aucune dérive possible entre ce qu'on voit dans Odoo, ce qu'on ouvre
dans diagrams.net et ce qu'on imprime.

Il ne réduit rien : la page est taillée sur la carte, à l'échelle 1:1, comme la
page dépliée du générateur. Les coordonnées du PDF sont donc littéralement
celles des enregistrements. Une page ne se réduit que si elle dépasse la taille
maximale d'une page PDF, et alors le facteur est écrit dans le pied de page
plutôt que subi en silence.

Deux dépendances, et pas une de plus :

* `reportlab`, déjà exigé par Odoo — donc rien à ajouter à l'image ;
* Lexend, embarqué dans `static/fonts/` sous licence OFL 1.1, parce qu'un
  tracé qui change de police change de largeurs, et que les largeurs sont
  justement ce que `mesure` a figé.

Les formes, rayons, épaisseurs, corps de texte et repli des libellés sont
ceux du tracé à l'écran et des deux exports XML : une seule géométrie, quatre
rendus. Un contrôle hors serveur superpose la sortie au rendu de référence.
"""
import io
import math
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas

from . import geometrie as geo
from . import mesure
from . import qr

POLICES = Path(__file__).resolve().parent.parent / "static" / "fonts"
REGULIER, GRAS = "BfProcessLexend", "BfProcessLexend-SemiBold"

# --- palette, celle du moteur de référence -----------------------------------
INK = (0.176, 0.188, 0.192)
GREY = (0.45, 0.47, 0.48)
HAIR = (0.72, 0.74, 0.75)
LANE_BG = (0.976, 0.980, 0.984)
POOL_HDR = (0.925, 0.937, 0.945)
WHITE = (1, 1, 1)
BLUE = (0.161, 0.671, 0.882)
BLUE_SOFT = (0.918, 0.969, 0.992)
AMBER = (0.84, 0.60, 0.13)
AMBER_SOFT = (0.996, 0.965, 0.902)
GREEN = (0.106, 0.541, 0.294)
GREEN_SOFT = (0.906, 0.965, 0.925)
RED = (0.753, 0.227, 0.169)
RED_SOFT = (0.996, 0.925, 0.914)

#: La vue delta : (fond, contour) par teinte. Une carte hors vue delta ne
#: porte aucune teinte et retombe sur le blanc et l'encre habituels, donc
#: rien ne change pour les cartes qui ne se comparent à rien.
TEINTES = {
    "vert": (GREEN_SOFT, GREEN),
    "rouge": (RED_SOFT, RED),
    "ambre": (AMBER_SOFT, AMBER),
}

MARGE = geo.MARGE
BANDEAU = 34.0          # au-dessus de la marge de la carte : le titre
PIED = 26.0             # sous la marge de la carte : la mention de source
MAX_PDF = 14000.0       # au-delà, un lecteur PDF refuse la page

# Le QR d'atelier. 30 pt tiennent dans l'écart de 32 pt entre deux rangées
# d'un même couloir ; `qr.cote_lisible` l'agrandira si les modules devenaient
# trop fins pour un téléphone, et il vaut mieux un QR qui déborde qu'un QR qui
# ne se lit pas.
QR_COTE = 30.0
QR_PENDU = 2.0          # de combien il descend sous la case

# Tabloïd, en points : c'est le format que l'atelier a sous la main.
TABLOID = (792.0, 1224.0)
# Bande commune entre deux tuiles. Elle sert d'abord au collage, mais sa
# valeur est dictée par autre chose : un code QR à cheval sur une coupe est
# tronqué sur les DEUX feuilles, donc illisible partout. Le recouvrement doit
# rester plus large qu'un QR — `test_atelier` le verrouille.
RECOUVREMENT = 72.0
TUILE_MARGE = 24.0      # ce qu'une imprimante de bureau ne sait pas encrer
TUILE_PIED = 22.0       # la bande où la tuile dit où elle se situe
# Une affiche murale se lit debout, à bout de bras : à 1:1 le libellé d'une
# case fait 9 pt et le module d'un code QR 0,27 mm. 1,6x met le libellé à 15 pt
# et le module à 0,44 mm, au-dessus du seuil où un téléphone accroche.
ECHELLE_ATELIER = 1.6

EVENEMENTS = geo.EVENEMENTS
PASSERELLES = geo.PASSERELLES


def _polices():
    """Enregistre Lexend une fois par processus Odoo."""
    if REGULIER not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(REGULIER, str(POLICES / "Lexend-Regular.ttf")))
        pdfmetrics.registerFont(TTFont(GRAS, str(POLICES / "Lexend-SemiBold.ttf")))


def _fit_label(texte, dispo, base=9.4):
    """Réduit une étiquette pivotée pour qu'elle tienne dans sa bande."""
    largeur = mesure.text_length(texte, base, gras=True)
    return base if largeur <= dispo else max(5.6, base * dispo / largeur)


class Toile:
    """Une page, et la transformation qui y pose les coordonnées du modèle.

    Même surface que le `Sheet` du moteur de référence — `rect`, `circle`,
    `poly`, `line`, `arrow`, `text`, `bloc` — pour que le code de tracé se lise
    côte à côte avec lui. Une seule différence de fond : l'origine d'une page
    PDF est en bas à gauche, et `P` retourne l'axe Y.

    Le texte est mis de côté et posé au `commit()`, après tous les
    remplissages — exactement comme le moteur de référence. Ce n'est pas un
    détail d'organisation : un fond d'annotation dessiné après une étiquette la
    recouvre, et la superposition ne le voit pas puisque le mot est bien dans
    le fichier, à la bonne place, simplement caché.
    """

    def __init__(self, c, largeur, hauteur, k=1.0, oy=0.0):
        self.c = c
        self.w, self.h = largeur, hauteur
        self.k, self.ox, self.oy = k, 0.0, oy
        self._attente = []

    # -- transformation
    def P(self, x, y):
        """Coordonnée du modèle vers coordonnée de page, l'axe Y retourné."""
        return (x * self.k + self.ox, self.h - (y * self.k + self.oy))

    def S(self, v):
        return v * self.k

    def _trait(self, w):
        return w * max(self.k, 0.55)

    def _couleurs(self, fill, color, w):
        c = self.c
        if fill is not None:
            c.setFillColorRGB(*fill)
        if color is not None:
            c.setStrokeColorRGB(*color)
            c.setLineWidth(self._trait(w))
        return fill is not None, color is not None and w > 0

    # -- primitives
    def rect(self, x0, y0, x1, y1, fill=None, color=INK, w=0.9, radius=None):
        px0, py0 = self.P(x0, y0)
        px1, py1 = self.P(x1, y1)
        lg, ht = px1 - px0, py0 - py1
        remplir, tracer = self._couleurs(fill, color, w)
        if radius:
            # le moteur de référence prend un rayon en fraction de la largeur,
            # puis l'applique au plus petit côté : le coin reste circulaire.
            r = min(radius / max(lg, 1), 0.35) * min(lg, ht)
            self.c.roundRect(px0, py1, lg, ht, r, stroke=int(tracer),
                             fill=int(remplir))
        else:
            self.c.rect(px0, py1, lg, ht, stroke=int(tracer), fill=int(remplir))

    def circle(self, cx, cy, r, fill=WHITE, color=INK, w=1.1):
        px, py = self.P(cx, cy)
        remplir, tracer = self._couleurs(fill, color, w)
        self.c.circle(px, py, self.S(r), stroke=int(tracer), fill=int(remplir))

    def poly(self, pts, fill=None, color=INK, w=0.9, close=True):
        remplir, tracer = self._couleurs(fill, color, w)
        chemin = self.c.beginPath()
        chemin.moveTo(*self.P(*pts[0]))
        for q in pts[1:]:
            chemin.lineTo(*self.P(*q))
        if close:
            chemin.close()
        self.c.drawPath(chemin, stroke=int(tracer), fill=int(remplir))

    def line(self, pts, color=INK, w=1.0, tirets=None):
        self._couleurs(None, color, w)
        if tirets:
            self.c.setDash(tirets, 0)
        chemin = self.c.beginPath()
        chemin.moveTo(*self.P(*pts[0]))
        for q in pts[1:]:
            chemin.lineTo(*self.P(*q))
        self.c.drawPath(chemin, stroke=1, fill=0)
        if tirets:
            self.c.setDash()

    def arrow(self, p0, p1, filled=True, color=INK, size=8.0):
        (x0, y0), (x1, y1) = p0, p1
        ang = math.atan2(y1 - y0, x1 - x0)
        a = (x1 - size * math.cos(ang - 0.42), y1 - size * math.sin(ang - 0.42))
        b = (x1 - size * math.cos(ang + 0.42), y1 - size * math.sin(ang + 0.42))
        self.poly([(x1, y1), a, b], fill=color if filled else WHITE,
                  color=color, w=0.9)

    def text(self, x, y, s, size=9.0, gras=False, color=INK, align=1, rotate=0):
        """Une ligne, mise de côté jusqu'au `commit()`.

        `align` : 0 à gauche, 1 centrée, 2 à droite.
        """
        if not s:
            return
        sz = max(self.S(size), 3.2)
        px, py = self.P(x, y)
        largeur = mesure.text_length(s, sz, gras=gras)
        if rotate == 90:
            # de bas en haut, centrée sur y : la bande d'un pool ou d'un couloir
            py -= largeur / 2
        elif align == 1:
            px -= largeur / 2
        elif align == 2:
            px -= largeur
        self._attente.append((px, py, s, GRAS if gras else REGULIER, sz,
                              color, rotate))

    def commit(self):
        """Pose le texte par-dessus le tracé, dans l'ordre où il est venu."""
        for px, py, s, police, sz, color, rotate in self._attente:
            self.c.setFont(police, sz)
            self.c.setFillColorRGB(*color)
            if rotate == 90:
                self.c.saveState()
                self.c.translate(px, py)
                self.c.rotate(90)
                self.c.drawString(0, 0, s)
                self.c.restoreState()
            else:
                self.c.drawString(px, py, s)
        self._attente = []

    def bloc(self, cx, cy, s, size=9.0, largeur=None, gras=False, color=INK,
             inter=1.22, align=1):
        """Texte replié, centré verticalement sur cy. Même calcul que le moteur."""
        sz = self.S(size)
        lignes = mesure.wrap(s or "", sz, self.S(largeur or 200), gras=gras)
        h = len(lignes) * sz * inter
        y = cy - (h / self.k) / 2 + size * inter * 0.78
        for ligne in lignes:
            self.text(cx, y, ligne, size=size, gras=gras, color=color, align=align)
            y += size * inter
        return len(lignes)


# --- nœuds --------------------------------------------------------------------
def _enveloppe(t, cx, cy, w, plein=False):
    h = w * 0.68
    t.rect(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2,
           fill=INK if plein else WHITE, w=0.8)
    t.line([(cx - w / 2, cy - h / 2), (cx, cy + h * 0.18), (cx + w / 2, cy - h / 2)],
           color=WHITE if plein else INK, w=0.8)


def _horloge(t, cx, cy, r):
    t.circle(cx, cy, r, fill=WHITE, w=0.9)
    t.line([(cx, cy), (cx, cy - r * 0.62)], w=0.9)
    t.line([(cx, cy), (cx + r * 0.48, cy + r * 0.2)], w=0.9)


def _bonhomme(t, cx, cy):
    t.circle(cx, cy - 3.4, 3.2, fill=WHITE, w=0.8)
    t.poly([(cx - 5.4, cy + 6), (cx - 4.2, cy + 0.5), (cx + 4.2, cy + 0.5),
            (cx + 5.4, cy + 6)], fill=WHITE, w=0.8)


def _teinte(n):
    """(fond, contour) d'un nœud : sa teinte de delta, ou le rendu habituel.

    Les annotations gardent leur ton (piste d'amélioration, fragilité) : il
    dit ce que l'annotation VEUT dire, alors que la teinte ne dit que ce qui a
    bougé. Les écraser ferait perdre le plus utile des deux.
    """
    return TEINTES.get(n.get("teinte"), (WHITE, INK))


def dessiner_noeud(t, n, cx, cy):
    k, nom = n["kind"], n.get("name") or ""
    w, h = geo.node_box(n)
    x0, y0, x1, y1 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
    fond, bord = _teinte(n)

    if k in ("start", "msgStart"):
        t.circle(cx, cy, geo.EV_R, fill=fond, color=bord, w=1.1)
        if k == "msgStart":
            _enveloppe(t, cx, cy, 15)
        t.bloc(cx, y1 + 22, nom, size=8.8, largeur=150, color=INK)
    elif k in ("timerCatch", "msgCatch"):
        t.circle(cx, cy, geo.EV_R, fill=fond, color=bord, w=1.1)
        t.circle(cx, cy, geo.EV_R - 3.4, fill=None, color=bord, w=1.0)
        if k == "timerCatch":
            _horloge(t, cx, cy, 10)
        else:
            _enveloppe(t, cx, cy, 14)
        t.bloc(cx, y1 + 22, nom, size=8.8, largeur=150)
    elif k == "end":
        t.circle(cx, cy, geo.EV_R, fill=fond, color=bord, w=2.6)
        t.bloc(cx, y1 + 22, nom, size=8.8, largeur=150, gras=True)
    elif k in PASSERELLES:
        t.poly([(cx, y0), (x1, cy), (cx, y1), (x0, cy)], fill=fond,
               color=bord, w=1.0)
        if k == "or":
            t.circle(cx, cy, 12, fill=None, w=2.0)
        elif k == "and":
            t.line([(cx - 11, cy), (cx + 11, cy)], w=1.7)
            t.line([(cx, cy - 11), (cx, cy + 11)], w=1.7)
        else:
            t.line([(cx - 8, cy - 8), (cx + 8, cy + 8)], w=1.7)
            t.line([(cx - 8, cy + 8), (cx + 8, cy - 8)], w=1.7)
        if nom:
            t.bloc(cx, y0 - 18, nom, size=8.8, largeur=150, color=GREY)
    elif k == "note":
        ton = n.get("tone")
        t.line([(x0 + 9, y0), (x0, y0), (x0, y1), (x0 + 9, y1)], color=GREY, w=1.0)
        if ton:
            t.rect(x0, y0, x1, y1,
                   fill=AMBER_SOFT if ton == "risk" else BLUE_SOFT, color=None, w=0)
            t.line([(x0 + 9, y0), (x0, y0), (x0, y1), (x0 + 9, y1)],
                   color=AMBER if ton == "risk" else BLUE, w=1.4)
        couleur = AMBER if ton == "risk" else (BLUE if ton else GREY)
        yy = y0 + 13
        for ligne in mesure.wrap(nom, t.S(mesure.NOTE_SZ), t.S(w - 20)):
            t.text(x0 + 14, yy, ligne, size=mesure.NOTE_SZ, align=0, color=couleur)
            yy += mesure.NOTE_SZ * mesure.NOTE_INTER
    elif k == "store":
        t.rect(x0, y0 + 6, x1, y1 - 4, fill=fond, color=bord, w=0.9)
        t.line([(x0, y0 + 6), (x1, y0 + 6)], w=0.9)
        t.bloc(cx, y1 + 14, nom, size=8.6, largeur=120, color=GREY)
    else:
        t.rect(x0, y0, x1, y1, fill=fond, color=bord,
               w=1.6 if n.get("teinte") else 1.0, radius=9)
        if k in ("send", "receive"):
            _enveloppe(t, x0 + 15, y0 + 13, 13, plein=(k == "send"))
        elif k == "user":
            _bonhomme(t, x0 + 14, y0 + 13)
        elif k == "sub":
            t.rect(cx - 7, y1 - 15, cx + 7, y1 - 1, fill=WHITE, w=0.9)
            t.line([(cx - 4, y1 - 8), (cx + 4, y1 - 8)], w=0.9)
            t.line([(cx, y1 - 12), (cx, y1 - 4)], w=0.9)
        pad = 10 if k in ("send", "receive", "user") else 0
        t.bloc(cx, cy - (3 if k == "sub" else 0) + pad / 2, nom,
               size=9.6 if k == "sub" else 9.2, largeur=w - 20, gras=(k == "sub"))


# --- page ---------------------------------------------------------------------
def _dessiner_niveau(t, d, etiquette_flux):
    """Pools, couloirs, flux puis nœuds — dans l'ordre d'empilement du moteur."""
    pos, lane_geo, pool, ext, noeuds, dx, dy = geo.plan(d)
    lanes = d.get("lanes") or [{"id": "_", "name": ""}]

    def X(v):
        return v + dx

    def Y(v):
        return v + dy

    for e in ext.values():
        t.rect(X(e["x0"]), Y(e["y0"]), X(e["x1"]), Y(e["y1"]), fill=WHITE, w=1.1)
        t.rect(X(e["x0"]), Y(e["y0"]), X(e["x0"]) + geo.POOL_HDR_W, Y(e["y1"]),
               fill=POOL_HDR, w=1.1)
        haut = e["y1"] - e["y0"]
        t.text(X(e["x0"]) + 18, Y((e["y0"] + e["y1"]) / 2), e["name"],
               size=_fit_label(e["name"], haut - 10), gras=True, rotate=90, align=0)

    t.rect(X(pool["x0"]), Y(pool["y0"]), X(pool["x1"]), Y(pool["y1"]),
           fill=WHITE, w=1.2)
    t.rect(X(pool["x0"]), Y(pool["y0"]), X(pool["x_lane"]), Y(pool["y1"]),
           fill=POOL_HDR, w=1.2)
    pool_h = pool["y1"] - pool["y0"]
    t.text(X(pool["x0"]) + 18, Y((pool["y0"] + pool["y1"]) / 2), d["pool"],
           size=_fit_label(d["pool"], pool_h - 12, 9.8), gras=True,
           rotate=90, align=0)

    if len(lanes) > 1 or lanes[0]["name"]:
        for ln in lanes:
            g = lane_geo[ln["id"]]
            y0 = pool["y0"] + g["y0"]
            t.rect(X(pool["x_lane"]), Y(y0), X(pool["x_lane"]) + geo.LANE_HDR,
                   Y(y0 + g["h"]), fill=LANE_BG, w=0.9)
            t.text(X(pool["x_lane"]) + geo.LANE_HDR - 8, Y(y0 + g["h"] / 2),
                   ln["name"], size=_fit_label(ln["name"], g["h"] - 14, 8.8),
                   gras=True, rotate=90, align=0)
            if ln is not lanes[-1]:
                t.line([(X(pool["x_lane"]), Y(y0 + g["h"])),
                        (X(pool["x1"]), Y(y0 + g["h"]))], color=HAIR, w=0.9)

    for f in d.get("flows", []):
        pts = [(X(x), Y(y)) for x, y in geo.points_flux(f, noeuds, pos)]
        if f.get("r") == "assoc":
            t.line(pts, color=GREY, w=0.9, tirets=[1, 3])
            continue
        t.line(pts, w=1.05)
        t.arrow(pts[-2], pts[-1], filled=True)
        if f.get("label"):
            lx, ly = f.get("lp") or (0, 0)
            mx, my = _milieu(pts, etiquette_flux)
            t.bloc(mx + lx, my + ly - 9, f["label"], size=8.8,
                   largeur=f.get("lw") or 116, color=GREY)

    for m in d.get("msgs", []):
        pts = [(X(x), Y(y)) for x, y in geo.points_message(m, noeuds, pos, ext)]
        t.line(pts, color=INK, w=1.0, tirets=[4, 3])
        t.arrow(pts[-2], pts[-1], filled=False)
        my = (pts[0][1] + pts[-1][1]) / 2 + m.get("ly", 0)
        cx, _ = pos[m["node"]]
        t.bloc(X(cx) + m.get("dx", 0) + m.get("lx", 7), my, m["label"],
               size=8.8, largeur=m.get("lw") or 124, color=GREY, align=0)

    for n in d["nodes"]:
        cx, cy = pos[n["id"]]
        dessiner_noeud(t, n, X(cx), Y(cy))

    for n in d["nodes"]:
        if n.get("qr"):
            cx, cy = pos[n["id"]]
            w, h = geo.node_box(n)
            _dessiner_qr(t, n["qr"], X(cx) + w / 2, Y(cy) + h / 2)


def _dessiner_qr(t, adresse, x_droite, y_bas):
    """Le code QR d'une étape, accroché sous son coin bas-droit.

    **Dehors, pas dedans.** La largeur disponible pour le libellé d'une case
    est de la géométrie partagée par les quatre rendus : un QR posé à
    l'intérieur passerait par-dessus le texte, ou obligerait à replier le
    libellé autrement ici et pas dans les exports XML. Il descend donc dans
    l'espace entre deux rangées, qui existe déjà.

    Il pend vers la GAUCHE depuis le coin droit : vers la droite, il entrerait
    dans la colonne suivante, dont l'écart n'est que de 16 points.
    """
    cote, module = qr.cote_lisible(adresse, QR_COTE)
    grille = qr.matrice(adresse)
    module = cote / len(grille)
    x0, y0 = x_droite - cote, y_bas + QR_PENDU
    # un fond blanc franc : le QR se pose sur le fond du couloir, et un lecteur
    # a besoin de sa zone de silence pour accrocher
    t.rect(x0 - 1.5, y0 - 1.5, x0 + cote + 1.5, y0 + cote + 1.5,
           fill=WHITE, color=None, w=0)
    for i, rangee in enumerate(grille):
        # les modules encrés d'affilée se dessinent d'un seul rectangle :
        # moins d'objets dans la page, et surtout aucun liseré blanc entre
        # deux carrés adjacents à l'affichage
        debut = None
        for j, encre in enumerate(list(rangee) + [False]):
            if encre and debut is None:
                debut = j
            elif not encre and debut is not None:
                t.rect(x0 + debut * module, y0 + i * module,
                       x0 + j * module, y0 + (i + 1) * module,
                       fill=INK, color=None, w=0)
                debut = None


def _milieu(pts, position):
    """Le point d'ancrage d'une étiquette : sur le plus long segment."""
    seg = max(zip(pts, pts[1:]),
              key=lambda ab: abs(ab[0][0] - ab[1][0]) + abs(ab[0][1] - ab[1][1]))
    return (seg[0][0] + (seg[1][0] - seg[0][0]) * position,
            seg[0][1] + (seg[1][1] - seg[0][1]) * position)


def _bornes(d):
    """Encombrement de la carte, marges comprises — la taille utile de la page."""
    pos, lane_geo, pool, ext, noeuds, dx, dy = geo.plan(d)
    droites = [pool["x1"] + dx] + [e["x1"] + dx for e in ext.values()]
    bas = [pool["y1"] + dy] + [e["y1"] + dy for e in ext.values()]
    for nid, (cx, cy) in pos.items():
        w, h = geo.node_box(noeuds[nid])
        droites.append(cx + w / 2 + dx)
        bas.append(cy + h / 2 + dy)
    return max(droites) + MARGE, max(bas) + MARGE


def to_pdf(diagrammes, titre="", sous_titre="", pied="", entete=True,
           pave=None, echelle=ECHELLE_ATELIER):
    """Le PDF de la cartographie : une page par niveau, taillée sur la carte.

    `entete=False` rend le tracé nu, sans bandeau ni mention de source — c'est
    ce que le contrôle de superposition compare au moteur de référence, et ce
    qu'il faut quand la carte va être posée dans un autre document.

    `pave=(largeur, hauteur)` change la question posée : au lieu d'une page
    taillée sur la carte, un pavage de pages de ce format, à l'échelle 1:1,
    qu'on imprime et qu'on assemble au mur. Une carte de quinze niveaux ne tient
    pas sur une feuille, et la réduire jusqu'à ce qu'elle y tienne rend un
    document qu'on ne lit pas debout.
    """
    _polices()
    tampon = io.BytesIO()
    c = rl_canvas.Canvas(tampon)
    c.setTitle(titre or "Cartographie de processus")
    c.setCreator("Blue Fox — bf_process")

    for d in diagrammes:
        largeur, hauteur = _bornes(d)
        haut = BANDEAU if entete else 0.0
        bas = PIED if entete else 0.0
        if pave:
            # le pavage ne réserve NI bandeau NI pied de carte : il a son
            # propre pied de tuile, et il tuile l'étendue réelle du tracé
            _paver(c, d, largeur, hauteur, pave, titre, sous_titre,
                   pied if entete else "", echelle=echelle)
            continue
        page_w, page_h = largeur, hauteur + haut + bas
        k = 1.0
        if page_w > MAX_PDF or page_h > MAX_PDF:
            k = min(MAX_PDF / page_w, MAX_PDF / page_h)
            page_w, page_h = page_w * k, page_h * k
        c.setPageSize((page_w, page_h))
        t = Toile(c, page_w, page_h, k=k, oy=haut * k)

        _dessiner_niveau(t, d, 0.28 if d.get("flat") else 0.5)
        t.commit()
        if entete:
            _entete(c, d, page_w, page_h, titre, sous_titre, pied, k)
        c.showPage()

    c.save()
    return tampon.getvalue()


def _paver(c, d, largeur, hauteur, pave, titre, sous_titre, pied,
           echelle=ECHELLE_ATELIER):
    """Découpe un niveau en pages assemblables, agrandies.

    Chaque tuile reprend une bande de la précédente : sans recouvrement, la
    moindre imprécision de massicot ou de marge d'imprimante coupe un trait de
    flux en deux et la carte assemblée ment. Le pied de page dit où la tuile se
    situe, parce que quinze feuilles étalées par terre se ressemblent toutes.

    **Pourquoi agrandir plutôt que tirer à 1:1.** À l'échelle de l'écran, le
    code QR d'une étape mesure 0,27 mm par module : bien formé, mais à la
    limite de ce qu'un téléphone accroche, et le libellé d'une case se lit à
    trente centimètres, pas à bout de bras devant un mur. L'agrandissement fait
    grossir la carte ET ses codes ensemble, sans toucher à la géométrie —
    c'est le facteur `k` de la toile, celui qui servait déjà à réduire une page
    trop grande. Plus de feuilles, mais une affiche qui se lit debout.
    """
    page_w, page_h = pave
    # ce que la tuile montre de la carte, une fois le pied de tuile réservé
    utile_w = page_w - 2 * TUILE_MARGE
    utile_h = page_h - 2 * TUILE_MARGE - TUILE_PIED
    # les pas sont en coordonnées du MODÈLE : c'est là que la carte est
    # découpée, alors que la page, elle, est en points agrandis
    pas_x = max((utile_w - RECOUVREMENT) / echelle, 1.0)
    pas_y = max((utile_h - RECOUVREMENT) / echelle, 1.0)
    colonnes = max(1, math.ceil(
        (largeur * echelle - RECOUVREMENT) / max(utile_w - RECOUVREMENT, 1.0)))
    rangees = max(1, math.ceil(
        (hauteur * echelle - RECOUVREMENT) / max(utile_h - RECOUVREMENT, 1.0)))

    for j in range(rangees):
        for i in range(colonnes):
            c.setPageSize((page_w, page_h))
            t = Toile(c, page_w, page_h, k=echelle)
            # la tuile (i, j) montre la carte à partir de (i·pas_x, j·pas_y),
            # posé au coin haut-gauche de la zone utile. Seule l'origine du
            # repère bouge : le tracé, lui, est exactement le même.
            t.ox = TUILE_MARGE - i * pas_x * echelle
            t.oy = TUILE_MARGE - j * pas_y * echelle
            _dessiner_niveau(t, d, 0.28 if d.get("flat") else 0.5)
            t.commit()
            _reperes_tuile(c, page_w, page_h, i, j, colonnes, rangees,
                           d, titre, sous_titre, pied)
            c.showPage()


def _reperes_tuile(c, page_w, page_h, i, j, colonnes, rangees, d,
                   titre, sous_titre, pied):
    """Le cadre de coupe et le repère d'assemblage d'une tuile."""
    c.setStrokeColorRGB(*HAIR)
    c.setLineWidth(0.5)
    c.setDash(3, 3)
    c.rect(TUILE_MARGE, TUILE_MARGE + TUILE_PIED,
           page_w - 2 * TUILE_MARGE, page_h - 2 * TUILE_MARGE - TUILE_PIED,
           stroke=1, fill=0)
    c.setDash()
    haut = " — ".join(filter(None, (d.get("level"), d.get("title")))) or titre
    c.setFont(GRAS, 8.5)
    c.setFillColorRGB(*INK)
    c.drawString(TUILE_MARGE, TUILE_MARGE + 10,
                 "Colonne %d/%d · Rangée %d/%d" % (i + 1, colonnes,
                                                   j + 1, rangees))
    c.setFont(REGULIER, 8)
    c.setFillColorRGB(*GREY)
    reste = " · ".join(filter(None, (haut, sous_titre, titre if titre != haut
                                     else "", pied)))
    largeur_dispo = page_w - 2 * TUILE_MARGE - 120
    while reste and mesure.text_length(reste, 8) > largeur_dispo:
        reste = reste[:-4] + "…"
    c.drawRightString(page_w - TUILE_MARGE, TUILE_MARGE + 10, reste)


def _entete(c, d, page_w, page_h, titre, sous_titre, pied, k):
    """Le bandeau : de quelle carte il s'agit, et d'où elle sort."""
    haut = " — ".join(filter(None, (d.get("level"), d.get("title")))) or titre
    c.setFillColorRGB(*INK)
    c.setFont(GRAS, 19)
    c.drawString(MARGE, page_h - 40, haut)
    if sous_titre:
        c.setFont(REGULIER, 10)
        c.setFillColorRGB(*GREY)
        c.drawString(MARGE, page_h - 58, sous_titre)
    if titre and titre != haut:
        c.setFont(REGULIER, 10)
        c.setFillColorRGB(*GREY)
        largeur = mesure.text_length(titre, 10)
        c.drawString(page_w - MARGE - largeur, page_h - 40, titre)
    c.setStrokeColorRGB(*BLUE)
    c.setLineWidth(1.6)
    c.line(MARGE, page_h - 66, page_w - MARGE, page_h - 66)
    mention = pied or ""
    if k < 1.0:
        # une réduction se dit : la page a buté sur la taille maximale d'un PDF
        reduit = "Réduit à %d %% pour tenir dans une page PDF." % round(k * 100)
        mention = f"{mention} {reduit}".strip()
    if mention:
        c.setFont(REGULIER, 8)
        c.setFillColorRGB(*GREY)
        c.drawString(MARGE, 12, mention)
