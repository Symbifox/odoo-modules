# -*- coding: utf-8 -*-
"""Une seule géométrie, pour tous les rendus.

Le PDF, le PNG, le SVG et la feuille de calcul partent tous d'ici. Aucun d'eux
ne recalcule une position : ils lisent les mêmes coordonnées, dans le même
repère, et se contentent de les tracer avec leurs outils.

Le repère est celui du dessin, pas celui du PDF : **l'origine est en haut à
gauche, y descend**. C'est le repère du SVG, du PNG et du navigateur.
`pdf.py` est le seul à retourner l'axe, une fois, à l'écriture.

Aucune dépendance : ce module s'exécute hors d'Odoo, ce qui rend les tests
rapides et le raisonnement simple.
"""
from datetime import date, timedelta

# ---------------------------------------------------------------- constantes

ECHELLES = {
    # clé: (points par jour, pas de graduation, format du libellé)
    "day": (22.0, "day", "%d"),
    "week": (7.0, "week", "%d %b"),
    "month": (2.6, "month", "%b %Y"),
}

# Le tracé est calibré pour l'impression : 1 point du PDF vaut 1 pixel à
# l'écran, ce qui est trop dense pour être lu. Le zoom n'existe donc QUE pour
# l'affichage : il étire la boîte du SVG sans toucher au repère, donc sans
# qu'aucune coordonnée change. Le PDF, lui, garde son échelle 1:1.
ZOOM_MIN, ZOOM_MAX, ZOOM_DEFAUT = 0.6, 3.0, 1.5
ZOOMS_OFFERTS = (1.0, 1.25, 1.5, 2.0, 2.5)

# 🔴 Le plafond qui compte vraiment. `PLAFOND_BARRES` borne le nombre de lignes,
# PAS l'étendue : une seule échéance mal tapée (3026 pour 2026) donnait mille ans
# de graduations, une par jour, sur une route publique et sans limitation de
# débit. Mesuré avant la garde : 2,5 s, 400 Mo et un SVG de 66 Mo par requête ;
# et à l'an 9999 un `OverflowError` non attrapé, donc un 500. La plage est donc
# ramenée de force, et le rendu le DIT plutôt que de tronquer en silence.
PLAGE_MAX_JOURS = 3660          # dix ans : au-delà, plus personne ne lit
GRADUATIONS_MAX = 2000          # garde de dernier recours sur la boucle

MARGE_PAGE = 28.0
LARGEUR_LIBELLES = 236.0
HAUTEUR_ENTETE = 54.0          # bandeau titre, sans logo
HAUTEUR_AXE = 34.0             # deux rangs de graduations
HAUTEUR_COULOIR = 26.0         # bandeau d'un couloir
HAUTEUR_LIGNE = 22.0
HAUTEUR_BARRE = 13.0
HAUTEUR_PIED = 30.0
BARRE_MINIMALE = 3.0
RAYON_JALON = 6.0
# Le logo de la société, en haut à droite du bandeau. Hauteur imposée, largeur
# déduite du fichier : un logo étiré est pire que pas de logo.
# 32 pt de haut dans un bandeau de 54 : le repère de lecture se replace tout
# seul sous le logo, et il reste 5 pt avant l'axe. Au-delà, ça se touche.
LOGO_H_MAX = 32.0
LOGO_L_MAX = 170.0

MOIS_FR = ["janv.", "févr.", "mars", "avr.", "mai", "juin",
           "juil.", "août", "sept.", "oct.", "nov.", "déc."]
JOURS_FR = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"]

# Palette de statut. Le premier ton peint la barre, le second son remplissage.
COULEURS = {
    "done":        ("#D4E7D4", "#4C9A4C"),
    "canceled":    ("#E2E2E2", "#9A9A9A"),
    "overdue":     ("#F6D6D6", "#C0392B"),
    "in_progress": ("#D6E9F6", "#1F7CB4"),
    "upcoming":    ("#E6E9EC", "#7A868F"),
}
ENCRE = "#2D3031"
GRIS = "#6B7379"
FILET = "#D8DCDF"
FOND_COULOIR = "#F4F6F8"
LIGNE_AUJOURDHUI = "#C0392B"


def _d(valeur):
    """Une chaîne ISO ou un `date` deviennent un `date`."""
    if isinstance(valeur, date):
        return valeur
    return date.fromisoformat(str(valeur)[:10])


def boite_logo(donnees_b64, hauteur_max=LOGO_H_MAX, largeur_max=LOGO_L_MAX):
    """Le logo prêt à poser : octets, base64, type MIME et taille à l'échelle.

    🔴 **Beaucoup de sociétés ont un logo SVG**, et Pillow ne sait pas l'ouvrir.
    C'est le cas de Blue Fox. Le SVG est donc reconnu à part : il passe tel quel
    dans le rendu SVG, où il est parfait, et les rendus matriciels (PDF, PNG,
    classeur) reçoivent `vectoriel: True` pour se rabattre sur le nom de la
    société écrit à la couleur de la marque. Rasteriser demanderait `cairosvg` ou
    `svglib`, aucun des deux n'est dans l'image, et ce module tient à n'y rien
    ajouter.

    Un fichier illisible rend None : un échéancier sans logo reste utile, et une
    exception ici ferait tomber les cinq sorties d'un coup.
    """
    if not donnees_b64:
        return None
    try:
        import base64
        import io

        octets = base64.b64decode(donnees_b64)
        b64 = (donnees_b64 if isinstance(donnees_b64, str)
               else donnees_b64.decode("ascii"))

        debut = octets[:200].lstrip()
        if debut.startswith(b"<?xml") or debut.startswith(b"<svg"):
            taille = _taille_svg(octets)
            if not taille:
                return None
            l, h = taille
            facteur = min(hauteur_max / h, largeur_max / l)
            return {"octets": octets, "b64": b64, "mime": "image/svg+xml",
                    "vectoriel": True,
                    "largeur": l * facteur, "hauteur": h * facteur}

        from PIL import Image

        with Image.open(io.BytesIO(octets)) as image:
            l, h = image.size
            format_ = (image.format or "PNG").lower()
        if not l or not h:
            return None
        facteur = min(hauteur_max / h, largeur_max / l)
        return {
            "octets": octets,
            "b64": b64,
            "mime": "image/jpeg" if format_ in ("jpeg", "jpg") else "image/%s" % format_,
            "vectoriel": False,
            "largeur": l * facteur,
            "hauteur": h * facteur,
        }
    except Exception:
        return None


def _taille_svg(octets):
    """(largeur, hauteur) d'un SVG, lues sur `width`/`height` ou sur `viewBox`.

    ⚠️ L'analyse se fait sans résoudre les entités : un SVG est un fichier qui
    vient du dehors, et on ne lui laisse pas ouvrir le disque.
    """
    import re

    try:
        from lxml import etree

        racine = etree.fromstring(
            octets, parser=etree.XMLParser(resolve_entities=False, no_network=True))
        attributs = racine.attrib
    except Exception:
        return None

    def nombre(valeur):
        if not valeur:
            return None
        m = re.match(r"\s*([0-9]*\.?[0-9]+)", str(valeur))
        return float(m.group(1)) if m else None

    l, h = nombre(attributs.get("width")), nombre(attributs.get("height"))
    if not l or not h:
        boite = (attributs.get("viewBox") or "").replace(",", " ").split()
        if len(boite) == 4:
            l, h = nombre(boite[2]), nombre(boite[3])
    if not l or not h:
        return None
    return l, h


def borner_zoom(valeur, defaut=ZOOM_DEFAUT):
    """Un zoom hors bornes, ou illisible, retombe sur le défaut."""
    try:
        z = float(valeur)
    except (TypeError, ValueError):
        return defaut
    return max(ZOOM_MIN, min(ZOOM_MAX, z))


def _mois_suivant(jour):
    """Le premier du mois suivant. Rend `date.max` plutôt que de déborder."""
    try:
        return (jour.replace(day=1) + timedelta(days=32)).replace(day=1)
    except (OverflowError, ValueError):
        return date.max


# ------------------------------------------------------------------- calcul

def construire(payload, echelle="week", largeur_libelles=LARGEUR_LIBELLES,
               max_lignes=None):
    """Rend la géométrie complète d'un échéancier.

    `payload` est ce que rend `bf.gantt.source.get_echeancier`.
    """
    if echelle not in ECHELLES:
        echelle = "week"
    ppj, pas, _fmt = ECHELLES[echelle]

    # Le logo se mesure AVANT tout placement : il décide de la hauteur du
    # bandeau. Sans cela, le repère de lecture qu'il pousse vers le bas vient
    # buter dans le libellé « aujourd'hui », qui vit juste au-dessus de l'axe.
    logo = boite_logo(payload.get("company", {}).get("logo"))
    hauteur_entete = HAUTEUR_ENTETE
    if logo and not logo.get("vectoriel"):
        hauteur_entete += logo["hauteur"] - 12.0

    debut = _d(payload["range"]["min"])
    fin = _d(payload["range"]["max"])
    aujourdhui = _d(payload["range"]["today"])

    # La plage est bornée AVANT tout calcul : tout ce qui suit boucle dessus.
    plage_reduite = False
    if (fin - debut).days > PLAGE_MAX_JOURS:
        fin = debut + timedelta(days=PLAGE_MAX_JOURS)
        plage_reduite = True
    if fin < debut:
        fin = debut
    jours = max(1, (fin - debut).days)

    x0 = MARGE_PAGE + largeur_libelles
    largeur_temps = jours * ppj
    largeur_totale = x0 + largeur_temps + MARGE_PAGE

    def x_de(jour):
        return x0 + (_d(jour) - debut).days * ppj

    # --- lignes, couloir par couloir ---------------------------------------
    par_couloir = {}
    for tache in payload["tasks"]:
        par_couloir.setdefault(tache["lane"], []).append(tache)

    y = MARGE_PAGE + hauteur_entete + HAUTEUR_AXE
    couloirs = []
    lignes = []
    position = {}
    posees = 0
    tronque_ici = False

    for couloir in payload["lanes"]:
        taches = par_couloir.get(couloir["key"], [])
        if not taches:
            continue
        bandeau_y = y
        y += HAUTEUR_COULOIR
        debut_lignes = y
        for tache in sorted(taches, key=lambda t: (t["start"], t["name"] or "")):
            if max_lignes is not None and posees >= max_lignes:
                tronque_ici = True
                break
            d = _d(tache["start"])
            f = _d(tache["end"])

            # La barre est ramenée DANS la fenêtre, et ce qui dépasse se dit par
            # un chevron au bord. Sans cela, une échéance hors plage se traçait
            # au-delà du cadre : invisible, alors que son nom gardait sa ligne.
            deborde_avant = d < debut
            deborde_apres = f > fin
            # ⚠️ Borner des DEUX côtés : `max(d, debut)` seul laisse passer une
            # date qui dépasse la fin, et la barre repart hors du cadre.
            d_vue = min(max(d, debut), fin)
            f_vue = min(max(f, debut), fin)
            if f_vue < d_vue:
                f_vue = d_vue
            gx = x_de(d_vue)
            gw = max(BARRE_MINIMALE, (f_vue - d_vue).days * ppj + ppj * 0.9)
            fond, plein = COULEURS.get(tache["status"], COULEURS["upcoming"])
            ligne = {
                "ref": tache["ref"],
                "name": tache["name"],
                "assignee": tache.get("assignee", ""),
                "lane": couloir["key"],
                "y": y,
                "hauteur": HAUTEUR_LIGNE,
                "bar_x": gx,
                "bar_y": y + (HAUTEUR_LIGNE - HAUTEUR_BARRE) / 2.0,
                "bar_w": gw,
                "bar_h": HAUTEUR_BARRE,
                "fill_w": gw * max(0, min(100, tache.get("progress", 0))) / 100.0,
                "couleur_fond": fond,
                "couleur_plein": plein,
                "status": tache["status"],
                "progress": tache.get("progress", 0),
                "is_milestone": tache.get("is_milestone", False),
                "start": str(d),
                "end": str(f),
                "start_origin": tache.get("start_origin", "planifie"),
                "approx": tache.get("start_origin", "planifie") != "planifie",
                "deborde_avant": deborde_avant,
                "deborde_apres": deborde_apres,
            }
            if ligne["is_milestone"]:
                ligne["bar_x"] = gx
                ligne["bar_w"] = 0.0
                ligne["diamant"] = {
                    "cx": gx + ppj * 0.45,
                    "cy": y + HAUTEUR_LIGNE / 2.0,
                    "r": RAYON_JALON,
                }
            position[tache["ref"]] = ligne
            lignes.append(ligne)
            y += HAUTEUR_LIGNE
            posees += 1
        couloirs.append({
            "key": couloir["key"],
            "name": couloir["name"],
            "pct": couloir.get("pct", 0),
            "total": couloir.get("total", 0),
            "done": couloir.get("done", 0),
            "y": bandeau_y,
            "hauteur": HAUTEUR_COULOIR,
            "y_lignes": debut_lignes,
            "y_fin": y,
        })
        if tronque_ici:
            break

    hauteur_totale = y + HAUTEUR_PIED + MARGE_PAGE

    # --- graduations --------------------------------------------------------
    graduations, bandes = _graduer(debut, fin, pas, x_de, ppj)

    # --- flèches de dépendance ---------------------------------------------
    fleches = []
    for lien in payload.get("deps", []):
        amont = position.get(lien["from"])
        aval = position.get(lien["to"])
        if not amont or not aval:
            continue
        fleches.append(_fleche(amont, aval))

    return {
        "echelle": echelle,
        "ppj": ppj,
        "x0": x0,
        "largeur_libelles": largeur_libelles,
        "largeur": largeur_totale,
        "hauteur": hauteur_totale,
        "y_axe": MARGE_PAGE + hauteur_entete,
        "hauteur_entete": hauteur_entete,
        "y_lignes": MARGE_PAGE + hauteur_entete + HAUTEUR_AXE,
        "y_fin_lignes": y,
        "debut": str(debut),
        "fin": str(fin),
        "aujourdhui": str(aujourdhui),
        "x_aujourdhui": (x_de(aujourdhui)
                         if debut <= aujourdhui <= fin else None),
        "couloirs": couloirs,
        "lignes": lignes,
        "fleches": fleches,
        "graduations": graduations,
        "bandes": bandes,
        "titre": payload.get("title", ""),
        "sous_titre": payload.get("subtitle", ""),
        "societe": payload.get("company", {}),
        "logo": logo,
        "tronque": bool(payload.get("truncated")) or tronque_ici,
        "plage_reduite": plage_reduite,
        "compte": len(lignes),
    }


def _graduer(debut, fin, pas, x_de, ppj):
    """Deux rangs : le rang large (mois ou année) et le rang fin."""
    hauts, bas, bandes = [], [], []
    # ⚠️ Près de `date.max`, ajouter un jour lève `OverflowError`. La borne
    # exclusive se calcule donc une seule fois, ici, et prudemment.
    try:
        fin_exclusive = fin + timedelta(days=1)
    except OverflowError:
        fin_exclusive = date.max

    # 🔴 Les deux boucles sortent sur l'AVANCEMENT du curseur, pas seulement sur
    # le compteur. Une borne qui bute sur `date.max` rend la même date que le
    # curseur : la boucle piétine alors sans rien ajouter, donc sans faire monter
    # le compteur, et tourne pour toujours. C'est ce qui a figé le banc.

    jour = debut
    while jour <= fin and len(bas) < GRADUATIONS_MAX:
        if pas == "day":
            suivant = _plus_un_jour(jour)
            bas.append({"x": x_de(jour), "largeur": ppj,
                        "texte": str(jour.day),
                        "weekend": jour.weekday() >= 5})
            if jour.weekday() >= 5:
                bandes.append({"x": x_de(jour), "largeur": ppj})
        elif pas == "week":
            suivant = _plus_n_jours(jour, 7 - jour.weekday())
            largeur = (min(suivant, fin_exclusive) - jour).days * ppj
            bas.append({"x": x_de(jour), "largeur": largeur,
                        "texte": "%d %s" % (jour.day, MOIS_FR[jour.month - 1][:4]),
                        "weekend": False})
        else:
            suivant = _mois_suivant(jour)
            largeur = (min(suivant, fin_exclusive) - jour).days * ppj
            bas.append({"x": x_de(jour), "largeur": largeur,
                        "texte": MOIS_FR[jour.month - 1], "weekend": False})
        if suivant <= jour:
            break
        jour = suivant

    # Rang du haut : mois pour les échelles fines, année pour l'échelle mois.
    jour = debut.replace(day=1) if pas != "month" else debut.replace(month=1, day=1)
    while jour <= fin and len(hauts) < GRADUATIONS_MAX:
        if pas != "month":
            suivant = _mois_suivant(jour)
            texte = "%s %d" % (MOIS_FR[jour.month - 1], jour.year)
        else:
            try:
                suivant = jour.replace(year=jour.year + 1)
            except (OverflowError, ValueError):
                suivant = date.max
            texte = str(jour.year)
        gauche = max(jour, debut)
        droite = min(suivant, fin_exclusive)
        if droite > gauche:
            hauts.append({"x": x_de(gauche),
                          "largeur": (droite - gauche).days * ppj,
                          "texte": texte})
        if suivant <= jour:
            break
        jour = suivant

    return {"haut": hauts, "bas": bas}, bandes


def _plus_un_jour(jour):
    return _plus_n_jours(jour, 1)


def _plus_n_jours(jour, nombre):
    """`jour + nombre` jours, ou `date.max` au bout du calendrier."""
    try:
        return jour + timedelta(days=nombre)
    except OverflowError:
        return date.max


def _fleche(amont, aval):
    """Un coude en trois segments, de la fin de l'amont au début de l'aval.

    Le tracé descend d'abord, puis va chercher l'aval : c'est le geste que fait
    la main, et il reste lisible quand l'aval remonte au-dessus de l'amont.
    """
    x1 = amont["bar_x"] + max(amont["bar_w"], 2.0)
    y1 = amont["bar_y"] + amont["bar_h"] / 2.0
    x2 = aval["bar_x"]
    y2 = aval["bar_y"] + aval["bar_h"] / 2.0
    ecart = 8.0
    if x2 >= x1 + ecart * 2:
        milieu = x1 + (x2 - x1) / 2.0
        points = [(x1, y1), (milieu, y1), (milieu, y2), (x2, y2)]
    else:
        # L'aval commence avant la fin de l'amont : on contourne par le bas.
        bas = max(y1, y2) + HAUTEUR_LIGNE / 2.0
        points = [(x1, y1), (x1 + ecart, y1), (x1 + ecart, bas),
                  (x2 - ecart, bas), (x2 - ecart, y2), (x2, y2)]
    return {"points": points, "pointe": (x2, y2),
            "from": amont["ref"], "to": aval["ref"]}


def couper(texte, largeur_max, mesurer):
    """Coupe un libellé à la largeur donnée, avec une ellipse.

    `mesurer(texte)` rend une largeur. Chaque rendu fournit la sienne : reportlab
    mesure la vraie police, le SVG et le PNG approchent. Le calcul de position,
    lui, ne dépend jamais de la mesure.
    """
    if not texte:
        return ""
    if mesurer(texte) <= largeur_max:
        return texte
    ellipse = "…"
    bas, haut = 0, len(texte)
    while bas < haut:
        milieu = (bas + haut + 1) // 2
        if mesurer(texte[:milieu] + ellipse) <= largeur_max:
            bas = milieu
        else:
            haut = milieu - 1
    return (texte[:bas] + ellipse) if bas else ellipse
