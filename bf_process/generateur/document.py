# -*- coding: utf-8 -*-
"""Le livrable : couverture, légende, sommaire, niveaux, annexes, carte dépliée.

Le PDF de `pdf.to_pdf` est un tracé : une page par niveau, taillée sur la
carte, sans un mot autour. C'est ce qu'il faut pour poser une carte dans un
autre document. Ce n'est pas un livrable : une cartographie AS-IS qui ne dit
ni d'où elle vient, ni ce qu'elle suppose, ni ce qu'elle ne sait pas encore
décrit une séquence, pas une performance.

Ce module assemble le document. Il ne redessine rien : les niveaux passent
par `_dessiner_niveau`, exactement comme le tracé nu, et la carte dépliée par
`aplatir`. Ce qui s'ajoute est la mise en page autour — et la mise en page ne
sait rien du modèle.

**Pages de taille fixe**, contrairement au tracé nu. Un document a un
sommaire, donc des numéros de page, donc des pages qui se ressemblent. Chaque
niveau est réduit pour tenir sur le tabloïd ; la carte dépliée, elle, garde
sa page taillée sur le contenu, parce qu'on la parcourt à l'écran.
"""
import io

from reportlab.lib.colors import Color
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import Frame, KeepInFrame, Paragraph, Spacer, Table, \
    TableStyle

from . import aplatir
from . import geometrie as geo
from . import mesure
from . import texte as conv
from .pdf import GRAS, REGULIER, Toile, _bornes, _dessiner_niveau, _polices, \
    AMBER, AMBER_SOFT, BLUE, BLUE_SOFT, GREY, HAIR, INK, WHITE

PAGE = (1224.0, 792.0)          # tabloïd paysage, comme le moteur de référence
MARGE = 34.0
HAUT = 74.0                     # bandeau de titre
BAS = 40.0                      # pied et pagination

ENCRE = Color(*INK)
GRIS = Color(*GREY)
FILET = Color(*HAIR)
BLEU = Color(*BLUE)
AMBRE = Color(*AMBER)
CLAIR = Color(0.85, 0.90, 0.93)
PALE = Color(0.65, 0.72, 0.76)

# Ce que la légende montre, et dans quel ordre. Les genres qui n'apparaissent
# sur aucun niveau du document sont retirés au moment du tracé : une légende
# qui explique une forme absente fait douter le lecteur de ce qu'il regarde.
LEGENDE = [
    ("start", "Événement de début\nnommé par son état"),
    ("task", "Activité\n(verbe + complément)"),
    ("send", "Activité qui émet\nun message"),
    ("receive", "Activité qui reçoit\nun message"),
    ("user", "Activité tenue\npar une personne"),
    ("sub", "Sous-processus\ndétaillé plus loin"),
    ("xor", "Choix exclusif\n(une seule voie)"),
    ("or", "Choix inclusif\n(une ou plusieurs)"),
    ("and", "Parallélisme\n(toutes les voies)"),
    ("msgCatch", "Attente d’un\névénement externe"),
    ("timerCatch", "Attente d’une\néchéance"),
    ("store", "Dépôt de données"),
    ("end", "Fin, nommée par\nl’état atteint"),
]

TITRES_ANNEXES = {
    "hypothese": "Hypothèses retenues pour dessiner la carte",
    "question": "Questions ouvertes",
    "constat": "Ce que la carte fait apparaître",
}


# ------------------------------------------------------------------- styles --
def _styles():
    return {
        "h3": ParagraphStyle("h3", fontName=GRAS, fontSize=11, leading=14,
                             spaceAfter=6, textColor=ENCRE),
        "h4": ParagraphStyle("h4", fontName=GRAS, fontSize=9.6, leading=12.5,
                             spaceBefore=6, spaceAfter=4, textColor=ENCRE),
        "p": ParagraphStyle("p", fontName=REGULIER, fontSize=8.8, leading=12.8,
                            spaceAfter=5, textColor=ENCRE),
        "puce": ParagraphStyle("puce", fontName=REGULIER, fontSize=8.8,
                               leading=12.8, spaceAfter=5, leftIndent=14,
                               bulletIndent=4, textColor=ENCRE),
        "numero": ParagraphStyle("numero", fontName=REGULIER, fontSize=8.8,
                                 leading=12.8, spaceAfter=5, leftIndent=14,
                                 textColor=ENCRE),
        "cite": ParagraphStyle("cite", fontName=REGULIER, fontSize=8.8,
                               leading=12.8, spaceAfter=5, leftIndent=12,
                               textColor=GRIS),
    }


def _fr(texte):
    """L'espacement français, posé sur le texte, jamais dans une balise."""
    import re
    balises = []

    def cacher(m):
        balises.append(m.group(0))
        return "\x01"

    masque = re.sub(r"<[^>]*>|&[A-Za-z#0-9]+;", cacher, texte)
    masque = re.sub(r"(?<=[^\s ])[ ]?(?=[?!;:](?:[\s\x01]|$))",
                    " ", masque)
    it = iter(balises)
    return re.sub("\x01", lambda _m: next(it), masque)


def _flowables(html_source, styles):
    """Le HTML d'une section, en objets de mise en page."""
    sortie = []
    for genre, contenu in conv.blocs(html_source):
        style = styles.get(genre, styles["p"])
        if genre == "puce":
            sortie.append(Paragraph(_fr(contenu), style, bulletText="•"))
        else:
            sortie.append(Paragraph(_fr(contenu), style))
    return sortie


# -------------------------------------------------------------------- chrome --
def _bandeau(c, titre, sous_titre, droite, meta, page_no, total, pied):
    t = Toile(c, PAGE[0], PAGE[1])
    t.text(MARGE, MARGE + 12, titre, size=15.5, gras=True, align=0)
    if sous_titre:
        t.text(MARGE, MARGE + 30, sous_titre, size=9.4, color=GREY, align=0)
    t.text(PAGE[0] - MARGE, MARGE + 12, droite, size=9, color=GREY, align=2)
    if meta:
        t.text(PAGE[0] - MARGE, MARGE + 27, meta, size=9, color=GREY, align=2)
    t.line([(MARGE, MARGE + 40), (PAGE[0] - MARGE, MARGE + 40)],
           color=BLUE, w=1.4)
    t.commit()
    _pied(c, page_no, total, pied)


def _pied(c, page_no, total, pied):
    t = Toile(c, PAGE[0], PAGE[1])
    t.line([(MARGE, PAGE[1] - 34), (PAGE[0] - MARGE, PAGE[1] - 34)],
           color=HAIR, w=0.7)
    if pied:
        t.text(MARGE, PAGE[1] - 22, pied, size=7.6, color=GREY, align=0)
    t.text(PAGE[0] - MARGE, PAGE[1] - 22, "%s / %s" % (page_no, total),
           size=8, color=GREY, align=2)
    t.commit()


# ---------------------------------------------------------------- couverture --
def _bloc_couverture(c, x, y, largeur, titre, corps):
    """Un bloc de la couverture. Rend l'ordonnée du bloc suivant."""
    t = Toile(c, PAGE[0], PAGE[1])
    t.text(x, y, titre, size=11, gras=True, align=0)
    t.commit()
    styles = _styles()
    style = ParagraphStyle("couv", parent=styles["p"], fontSize=9.2,
                           leading=13.3, spaceAfter=0)
    flow = [Paragraph(_fr(p), style) for _g, p in conv.blocs(corps)] or \
           [Paragraph(_fr(corps or ""), style)]
    hauteur = sum(f.wrap(largeur, 400)[1] + style.spaceAfter for f in flow)
    cadre = Frame(x, PAGE[1] - y - 16 - hauteur, largeur, hauteur + 10,
                  leftPadding=0, rightPadding=0, topPadding=0,
                  bottomPadding=0, showBoundary=0)
    cadre.addFromList(list(flow), c)
    return y + 26 + hauteur + 14


def _legende(c, x0, y0, largeur, genres):
    """La légende, dessinée avec les primitives des diagrammes.

    Elle n'est pas une image collée : chaque forme est tracée par le même
    code que la carte. Une légende qui diverge du tracé est pire qu'aucune.
    """
    t = Toile(c, PAGE[0], PAGE[1])
    t.text(x0, y0, "Légende", size=11, gras=True, align=0)
    entrees = [(g, l) for g, l in LEGENDE if g in genres]
    colonnes, larg_col = 2, largeur / 2
    from .pdf import dessiner_noeud
    for i, (genre, libelle) in enumerate(entrees):
        cx = x0 + (i % colonnes) * larg_col + 30
        cy = y0 + 36 + (i // colonnes) * 54
        dessiner_noeud(t, {"kind": genre, "name": "", "w": 50, "h": 30}, cx, cy)
        for j, ligne in enumerate(libelle.split("\n")):
            t.text(cx + 36, cy - 2 + j * 11, ligne, size=8, color=GREY, align=0)
    y = y0 + 36 + ((max(len(entrees), 1) - 1) // colonnes + 1) * 54 - 14
    lg = (largeur - 20) / 2
    for x, fond, bord, l1, l2 in (
            (x0, BLUE_SOFT, BLUE, "Piste d’amélioration", "ou d’automatisation"),
            (x0 + lg + 20, AMBER_SOFT, AMBER, "Point de fragilité ou",
             "information manquante")):
        t.rect(x, y, x + lg, y + 34, fill=fond, color=None, w=0)
        t.line([(x + 9, y), (x, y), (x, y + 34), (x + 9, y + 34)],
               color=bord, w=1.4)
        t.text(x + 12, y + 14, l1, size=8, color=bord, align=0)
        t.text(x + 12, y + 25, l2, size=8, color=bord, align=0)
    t.commit()
    return y + 50


def _sommaire(c, x0, y0, entrees, par_colonne=12, ecart=392):
    t = Toile(c, PAGE[0], PAGE[1])
    t.text(x0, y0, "Sommaire", size=11, gras=True, align=0)
    n = len(entrees)
    coupe = n if n <= par_colonne else (n + 1) // 2
    for i, (no, titre, etiquette) in enumerate(entrees):
        x = x0 + (0 if i < coupe else ecart)
        y = y0 + 24 + (i if i < coupe else i - coupe) * 22
        t.text(x, y, str(no), size=9, color=BLUE, align=0)
        t.text(x + 22, y, titre, size=9.2, align=0)
        t.text(x + 300, y, etiquette, size=8.4, color=GREY, align=0)
        t.line([(x, y + 7), (x + 360, y + 7)], color=HAIR, w=0.5)
    t.commit()


def _couverture(c, meta, blocs, sommaire, genres, total):
    t = Toile(c, PAGE[0], PAGE[1])
    t.rect(0, 0, PAGE[0], 150, fill=INK, color=None, w=0)
    t.text(MARGE, 62, meta["titre"], size=27, gras=True, color=WHITE, align=0)
    if meta.get("sous_titre"):
        t.text(MARGE, 92, meta["sous_titre"], size=15,
               color=(0.85, 0.90, 0.93), align=0)
    if meta.get("ligne_source"):
        t.text(MARGE, 122, meta["ligne_source"], size=9.6,
               color=(0.65, 0.72, 0.76), align=0)
    t.commit()

    col_largeur = 340
    y = 190
    for titre, corps in blocs[:2]:
        y = _bloc_couverture(c, MARGE, y, col_largeur, titre, corps)
    y2 = 190
    for titre, corps in blocs[2:4]:
        y2 = _bloc_couverture(c, MARGE + 380, y2, col_largeur, titre, corps)

    _legende(c, MARGE + 776, 190, 376, genres)
    _sommaire(c, MARGE, max(y, y2, 470) + 16, sommaire)
    _pied(c, 1, total, meta.get("pied"))


# ------------------------------------------------------------------ annexes --
def _page_texte(c, titre, sous_titre, meta, colonnes, page_no, total, pied,
                bas=None, hauteur_bas=0.0):
    """Une page de texte, en deux colonnes, avec un bandeau optionnel en bas."""
    _bandeau(c, titre, sous_titre, meta["droite"], meta.get("meta", ""),
             page_no, total, pied)
    haut = HAUT + 20
    dispo_h = PAGE[1] - haut - BAS - hauteur_bas
    largeur = (PAGE[0] - 2 * MARGE - 40) / 2
    for i, flow in enumerate(colonnes[:2]):
        x = MARGE + i * (largeur + 40)
        cadre = Frame(x, PAGE[1] - haut - dispo_h, largeur, dispo_h,
                      leftPadding=0, rightPadding=0, topPadding=0,
                      bottomPadding=0, showBoundary=0)
        cadre.addFromList([KeepInFrame(largeur, dispo_h, list(flow),
                                       mode="shrink")], c)
    if bas:
        cadre = Frame(MARGE, PAGE[1] - haut - dispo_h - hauteur_bas,
                      PAGE[0] - 2 * MARGE, hauteur_bas, leftPadding=0,
                      rightPadding=0, topPadding=0, bottomPadding=0,
                      showBoundary=0)
        cadre.addFromList([KeepInFrame(PAGE[0] - 2 * MARGE, hauteur_bas,
                                       list(bas), mode="shrink")], c)


def _registre(sections, styles):
    """Le registre de validation, en tableau.

    Une carte AS-IS n'a de valeur qu'une fois reconnue par ceux qui exécutent
    le travail. Le tableau arrive vide par construction : c'est un formulaire,
    pas un constat.
    """
    if not sections:
        return [], 0.0
    entete = ParagraphStyle("th", parent=styles["p"], fontName=GRAS,
                            fontSize=8.6, spaceAfter=0)
    cellule = ParagraphStyle("td", parent=styles["p"], fontSize=8.6,
                             spaceAfter=0)
    lignes = [[Paragraph("Rôle", entete), Paragraph("Nom", entete),
               Paragraph("Date", entete), Paragraph("Décision", entete)]]
    for s in sections:
        lignes.append([Paragraph(_fr(s[0] or ""), cellule),
                       Paragraph(_fr(conv.texte_nu(s[1]) or ""), cellule),
                       Paragraph("&nbsp;", cellule),
                       Paragraph("Conforme / à corriger", cellule)])
    largeur = PAGE[0] - 2 * MARGE
    table = Table(lignes, colWidths=[largeur * 0.30, largeur * 0.24,
                                     largeur * 0.16, largeur * 0.30])
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, FILET),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    hauteur = table.wrap(largeur, 400)[1]
    titre = Paragraph("Registre de validation", styles["h3"])
    intro = Paragraph(_fr(
        "Une carte AS-IS n’a de valeur qu’une fois reconnue par ceux qui "
        "exécutent le travail. À compléter et à retourner avec les "
        "corrections."), styles["p"])
    return [titre, intro, table], hauteur + 46


# ------------------------------------------------- plan de transformation --
#: Combien de lignes tiennent sur une page de plan. Mesuré sur le tabloïd
#: paysage avec le corps 8,6 : au-delà, `shrink` réduirait le tableau jusqu'à
#: l'illisible plutôt que de passer à la page suivante.
PLAN_PAR_PAGE = 16


def _pages_plan(plan):
    """Découpe le plan en pages. Une liste vide ne produit aucune page."""
    if not plan:
        return []
    return [plan[i:i + PLAN_PAR_PAGE]
            for i in range(0, len(plan), PLAN_PAR_PAGE)]


def _table_plan(lignes, styles):
    """Le plan de transformation, en tableau.

    Un écart mécanique (« l'étape X est retirée ») ne se défend pas devant un
    comité. Ce sont l'intention et le gain qui le font, et ce sont les deux
    colonnes qu'une personne remplit à la main : le tableau les met au même
    rang que l'écart lui-même plutôt que de les reléguer en note.
    """
    entete = ParagraphStyle("th_plan", parent=styles["p"], fontName=GRAS,
                            fontSize=8.6, spaceAfter=0)
    cellule = ParagraphStyle("td_plan", parent=styles["p"], fontSize=8.6,
                             spaceAfter=0)
    rangs = [[Paragraph(t, entete) for t in
              ("Niveau", "Écart", "Intention", "Gain attendu", "Effort",
               "Responsable", "État")]]
    for l in lignes:
        rangs.append([
            Paragraph(_fr(l.get("niveau") or ""), cellule),
            Paragraph(_fr(l.get("ecart") or ""), cellule),
            Paragraph(_fr(l.get("intention") or ""), cellule),
            Paragraph(_fr(l.get("gain") or ""), cellule),
            Paragraph(_fr(l.get("effort") or ""), cellule),
            Paragraph(_fr(l.get("responsable") or ""), cellule),
            Paragraph(_fr(l.get("etat") or ""), cellule),
        ])
    largeur = PAGE[0] - 2 * MARGE
    table = Table(rangs, colWidths=[largeur * x for x in
                                    (0.14, 0.26, 0.10, 0.24, 0.07, 0.11, 0.08)],
                  repeatRows=1)
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, FILET),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return table


# --------------------------------------------------------------- assemblage --
def _genres_presents(diagrammes):
    genres = set()
    for d in diagrammes:
        for n in d.get("nodes", []):
            genres.add(n.get("kind"))
    return genres


def _config_depliee(diagrammes):
    """L'ordre des couloirs et les pools de la carte dépliée, déduits.

    Le moteur de référence demandait ces trois réglages à la main dans le
    fichier de modèle. Ici ils se déduisent : l'union des couloirs dans
    l'ordre où ils apparaissent, l'union des participants externes, et le
    premier couloir pour les nœuds du niveau 1, qui n'en portent pas.
    """
    couloirs, vus = [], set()
    externes, vus_ext = [], set()
    for d in diagrammes:
        for ln in d.get("lanes") or []:
            if ln["id"] not in vus:
                vus.add(ln["id"])
                couloirs.append({"id": ln["id"], "name": ln["name"]})
        for p in d.get("ext") or []:
            if p["id"] not in vus_ext:
                vus_ext.add(p["id"])
                externes.append({"id": p["id"], "name": p["name"],
                                 "pos": p.get("pos", "top")})
    if not couloirs:
        couloirs = [{"id": "_", "name": ""}]
    return couloirs, externes


def _plan(diagrammes, sections, avec_depliee, pages_plan=0):
    """Le sommaire, calculé avant de dessiner : les numéros de page en dépendent."""
    entrees, no = [], 2
    for d in diagrammes:
        entrees.append((no, d.get("title") or "", d.get("level") or ""))
        no += 1
    if sections.get("hypothese") or sections.get("question") or \
            sections.get("validation"):
        entrees.append((no, "Hypothèses et questions ouvertes", "Annexe"))
        no += 1
    if sections.get("constat"):
        entrees.append((no, "Constats et pistes", "Annexe"))
        no += 1
    for i in range(pages_plan):
        entrees.append((no, "Plan de transformation"
                        + (" (suite)" if i else ""), "Annexe"))
        no += 1
    for titre, _corps in sections.get("annexe") or []:
        entrees.append((no, titre, "Annexe"))
        no += 1
    if avec_depliee:
        entrees.append((no, "Carte dépliée", "Une seule page"))
        no += 1
    return entrees, no - 1


def to_document(diagrammes, meta, sections, plan=None):
    """Le livrable complet, en octets.

    `sections` groupe la prose par genre : `couverture` et `annexe` portent
    des couples (titre, HTML), `hypothese`, `question` et `constat` aussi,
    `validation` porte des couples (rôle, nom).

    `plan` est le plan de transformation d'un processus souhaité : une liste
    de lignes déjà mises en mots par le modèle. Vide ou absent sur une carte
    de l'état actuel, qui n'a rien à transformer.
    """
    _polices()
    styles = _styles()
    depliee = None
    if len(diagrammes) > 1:
        couloirs, externes = _config_depliee(diagrammes)
        try:
            depliee = aplatir.flatten(
                diagrammes, couloirs, {},
                title="%s — carte dépliée" % (meta.get("titre") or ""),
                level="Tous les niveaux sur une seule page, à parcourir à l’écran",
                pool=diagrammes[0].get("pool") or "", ext=externes,
                col_w=230, row_h=220, lane_pad=56)
        except aplatir.Abandon:
            # une carte que le dépliage refuse reste un document valable : on
            # perd une page, pas le livrable. Le refus est explicite dans
            # `aplatir`, il ne se devine pas ici.
            depliee = None

    pages_plan = _pages_plan(plan)
    sommaire, total = _plan(diagrammes, sections, bool(depliee),
                            len(pages_plan))
    tampon = io.BytesIO()
    c = rl_canvas.Canvas(tampon, pagesize=PAGE)
    c.setTitle(meta.get("titre") or "Cartographie de processus")
    c.setCreator("Blue Fox — bf_process")

    _couverture(c, meta, sections.get("couverture") or [], sommaire,
                _genres_presents(diagrammes), total)
    c.showPage()

    droite = meta.get("droite") or ""
    for no, d in enumerate(diagrammes, 2):
        c.setPageSize(PAGE)
        largeur, hauteur = _bornes(d)
        dispo_w = PAGE[0] - 2 * MARGE
        dispo_h = PAGE[1] - HAUT - BAS - 16
        k = min(dispo_w / max(largeur, 1), dispo_h / max(hauteur, 1), 1.0)
        t = Toile(c, PAGE[0], PAGE[1], k=k)
        t.ox = MARGE + (dispo_w - largeur * k) / 2
        t.oy = HAUT + (dispo_h - hauteur * k) / 2
        _dessiner_niveau(t, d, 0.5)
        t.commit()
        _bandeau(c, d.get("title") or "", d.get("level") or "", droite,
                 meta.get("meta", ""), no, total, meta.get("pied"))
        c.showPage()

    no = len(diagrammes) + 2
    if sections.get("hypothese") or sections.get("question") or \
            sections.get("validation"):
        gauche = _liste(sections.get("hypothese"), TITRES_ANNEXES["hypothese"],
                        styles)
        droite_col = _liste(sections.get("question"), TITRES_ANNEXES["question"],
                            styles)
        bas, hauteur_bas = _registre(sections.get("validation") or [], styles)
        _page_texte(c, "Hypothèses et questions ouvertes",
                    "Ce que la carte suppose, et ce qu’elle ne sait pas encore",
                    {"droite": droite, "meta": meta.get("meta", "")},
                    [gauche, droite_col], no, total, meta.get("pied"),
                    bas=bas, hauteur_bas=hauteur_bas)
        c.showPage()
        no += 1

    if sections.get("constat"):
        constats = sections["constat"]
        moitie = (len(constats) + 1) // 2
        _page_texte(c, "Constats et pistes",
                    "Lecture de la carte, sans recommandation d’outil",
                    {"droite": droite, "meta": meta.get("meta", "")},
                    [_liste(constats[:moitie], TITRES_ANNEXES["constat"], styles),
                     _liste(constats[moitie:], " ", styles, depart=moitie + 1)],
                    no, total, meta.get("pied"))
        c.showPage()
        no += 1

    for i, lignes in enumerate(pages_plan):
        _page_texte(c, "Plan de transformation" + (" (suite)" if i else ""),
                    "Ce qui sépare l’état actuel du processus souhaité, "
                    "et ce que chaque changement rapporte",
                    {"droite": droite, "meta": meta.get("meta", "")},
                    [], no, total, meta.get("pied"),
                    bas=[_table_plan(lignes, styles)],
                    hauteur_bas=PAGE[1] - HAUT - BAS - 36)
        c.showPage()
        no += 1

    for titre, corps in sections.get("annexe") or []:
        flow = _flowables(corps, styles)
        moitie = (len(flow) + 1) // 2
        _page_texte(c, titre, "Annexe",
                    {"droite": droite, "meta": meta.get("meta", "")},
                    [flow[:moitie], flow[moitie:]], no, total, meta.get("pied"))
        c.showPage()
        no += 1

    if depliee:
        largeur, hauteur = _bornes(depliee)
        page_w = largeur + 2 * MARGE
        page_h = hauteur + HAUT + BAS
        c.setPageSize((page_w, page_h))
        t = Toile(c, page_w, page_h)
        t.ox, t.oy = MARGE, HAUT
        _dessiner_niveau(t, depliee, 0.28)
        t.commit()
        tt = Toile(c, page_w, page_h)
        tt.text(MARGE, 40, depliee["title"], size=19, gras=True, align=0)
        tt.text(MARGE, 58, depliee["level"], size=10, color=GREY, align=0)
        tt.line([(MARGE, 66), (page_w - MARGE, 66)], color=BLUE, w=1.6)
        if meta.get("pied"):
            tt.text(MARGE, page_h - 12, meta["pied"], size=8, color=GREY, align=0)
        tt.text(page_w - MARGE, page_h - 12, "%s / %s" % (no, total),
                size=8, color=GREY, align=2)
        tt.commit()
        c.showPage()

    c.save()
    return tampon.getvalue()


def _liste(sections, titre, styles, depart=1):
    """Une suite d'entrées numérotées : intitulé en gras, puis son corps.

    ⚠️ L'intitulé est recollé à son corps par une PONCTUATION, pas par une
    espace. Sans elle, « Un seul couloir » et « La comptabilité est portée
    par une seule personne » se lisent comme une seule phrase bancale — le
    gras seul ne suffit pas à faire la coupure à l'œil, et il disparaît si
    quelqu'un copie le texte ailleurs. Le point n'est ajouté que si
    l'intitulé n'en porte pas déjà un, et pas du tout si le corps commence
    lui-même par une ponctuation.
    """
    flow = []
    if titre and titre.strip():
        flow.append(Paragraph(_fr(titre), styles["h3"]))
    for i, (nom, corps) in enumerate(sections or [], depart):
        intitule = (nom or "").strip()
        blocs = conv.blocs(corps)
        suite = blocs[0][1].strip() if blocs else ""
        # le corps commence parfois par la ponctuation qui suivait l'intitulé
        # en gras dans la source (« …comme une étape</b>, parce que… ») : la
        # recoller avec une espace donnerait « une étape , parce que »
        colle = suite[:1] in ",;:." if suite else False
        if suite and not colle and intitule and intitule[-1] not in ".?!:»":
            intitule += "."
        texte = "<b>%d. %s</b>" % (i, _fr(intitule))
        if suite:
            texte += ("" if colle else " ") + suite
        flow.append(Paragraph(_fr(texte), styles["numero"]))
        for _genre, suite in blocs[1:]:
            flow.append(Paragraph(_fr(suite), styles["numero"]))
    return flow
