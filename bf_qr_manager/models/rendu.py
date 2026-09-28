"""Le tracé des planches : des codes QR vectoriels posés au millimètre.

⚠️ **ReportLab, pas wkhtmltopdf.** Une planche d'étiquettes ne tolère pas un
demi-millimètre de dérive : wkhtmltopdf met à l'échelle selon sa résolution et
ses réglages de rétrécissement, et la dérive s'accumule ligne après ligne jusqu'à
couper les codes du bas de la feuille. ReportLab place chaque point là où on le
lui dit, en unités d'imprimeur.

⚠️ **Des modules vectoriels, pas des images.** Un code tracé en rectangles reste
net à toutes les tailles et sur toutes les imprimantes ; une image de 400 pixels
réduite à 2 cm est rééchantillonnée par le pilote, et les bords des modules
bavent. Les modules sombres contigus d'une ligne sont tracés d'un seul trait.

Les deux règles de couleur sont celles des pages de liens (mesurées le
2026-08-30) : contraste d'au moins 4:1, et le code plus sombre que son fond. Ici
elles REFUSENT au lieu de retomber sur le noir et blanc : une planche imprimée
dans de mauvaises couleurs, c'est une feuille d'étiquettes perdue.
"""
import io
import math

from reportlab.lib.colors import HexColor
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from .bf_qr_label_format import HAUTEUR_TEXTE_MM, PAPIERS

# Part du côté utile (hors zone de silence) que le logo occupe. Les pages de
# liens ont mesuré que 34 % se lit encore en correction H et 40 % plus : 22 %
# laisse la marge que l'encre et le papier mangent.
LOGO_RATIO = 0.22
# Zone de silence exigée par ISO/IEC 18004 : 4 modules de chaque côté.
ZONE_SILENCE = 4
POLICE = "Helvetica"


class CouleurRefusee(ValueError):
    pass


def luminance(couleur):
    """Luminance relative d'une couleur hexadécimale, ou None."""
    valeur = (couleur or "").strip().lstrip("#")
    if len(valeur) == 3:
        valeur = "".join(c * 2 for c in valeur)
    if len(valeur) != 6:
        return None
    try:
        canaux = [int(valeur[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    except ValueError:
        return None
    lineaire = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in canaux]
    return 0.2126 * lineaire[0] + 0.7152 * lineaire[1] + 0.0722 * lineaire[2]


def verifier_couleurs(code, fond):
    """Rend (code, fond) normalisés, ou lève ``CouleurRefusee`` avec la raison."""
    l_code, l_fond = luminance(code), luminance(fond)
    if l_code is None or l_fond is None:
        raise CouleurRefusee("illisible")
    if l_code >= l_fond:
        raise CouleurRefusee("inverse")
    rapport = (l_fond + 0.05) / (l_code + 0.05)
    if rapport < 4.0:
        raise CouleurRefusee("contraste:%.1f" % rapport)
    return "#" + code.strip().lstrip("#"), "#" + fond.strip().lstrip("#")


def matrice(adresse, avec_logo):
    """La matrice du code, zone de silence comprise : liste de listes de booléens."""
    import qrcode
    from qrcode.constants import ERROR_CORRECT_H, ERROR_CORRECT_M

    qr = qrcode.QRCode(version=None, border=ZONE_SILENCE,
                       error_correction=ERROR_CORRECT_H if avec_logo else ERROR_CORRECT_M)
    qr.add_data(adresse)
    qr.make(fit=True)
    return qr.get_matrix()


def tracer_qr(c, x, y, cote, grille, code, fond, logo=None):
    """Trace un code carré de ``cote`` points, coin bas-gauche en (x, y)."""
    n = len(grille)
    module = cote / n
    c.setFillColor(HexColor(fond))
    c.rect(x, y, cote, cote, stroke=0, fill=1)
    c.setFillColor(HexColor(code))
    for rang, ligne in enumerate(grille):
        haut = y + cote - (rang + 1) * module
        debut = None
        for col, sombre in enumerate(ligne + [False]):
            if sombre and debut is None:
                debut = col
            elif not sombre and debut is not None:
                # Un soupçon de recouvrement évite les filets blancs entre deux
                # traits que certains visionneurs dessinent à l'anticrénelage.
                c.rect(x + debut * module, haut, (col - debut) * module + 0.02,
                       module + 0.02, stroke=0, fill=1)
                debut = None
    if logo is not None:
        utile = cote - 2 * ZONE_SILENCE * module
        cote_logo = utile * LOGO_RATIO
        plaque = cote_logo + 2 * module
        px, py = x + (cote - plaque) / 2, y + (cote - plaque) / 2
        c.setFillColor(HexColor(fond))
        c.rect(px, py, plaque, plaque, stroke=0, fill=1)
        c.drawImage(logo, px + module, py + module, cote_logo, cote_logo,
                    mask="auto", preserveAspectRatio=True, anchor="c")


def _couper(texte, police, taille, largeur):
    """Coupe ``texte`` au mot pour tenir dans ``largeur`` points ; dernière ligne abrégée."""
    mots, lignes, courante = (texte or "").split(), [], ""
    for mot in mots:
        essai = (courante + " " + mot).strip()
        if stringWidth(essai, police, taille) <= largeur:
            courante = essai
        else:
            if courante:
                lignes.append(courante)
            courante = mot
    if courante:
        lignes.append(courante)
    return [_abreger(l, police, taille, largeur) for l in lignes]


def _abreger(ligne, police, taille, largeur):
    if stringWidth(ligne, police, taille) <= largeur:
        return ligne
    while ligne and stringWidth(ligne + "…", police, taille) > largeur:
        ligne = ligne[:-1]
    return ligne + "…"


def planche(fmt, etiquettes, decalage=0, code="#000000", fond="#FFFFFF",
            logo_png=None, contours=False):
    """Le PDF des planches.

    ``fmt`` est un ``bf.qr.label.format`` ; ``etiquettes`` une liste de dicts
    ``{"url", "texte"}`` dans l'ordre d'impression ; ``decalage`` le nombre de
    places déjà utilisées sur la première feuille.
    """
    code, fond = verifier_couleurs(code, fond)
    logo = ImageReader(io.BytesIO(logo_png)) if logo_png else None
    largeur_page, hauteur_page = fmt._feuille() if fmt.papier == "custom" else PAPIERS[fmt.papier]
    tampon = io.BytesIO()
    c = canvas.Canvas(tampon, pagesize=(largeur_page * mm, hauteur_page * mm))
    c.setTitle("Étiquettes QR")
    par_page = fmt.par_feuille
    marge = (fmt.marge_interne or 0.0) * mm
    avec_texte = any(e.get("texte") for e in etiquettes)
    a_cote = avec_texte and fmt._texte_a_cote()
    cote = fmt._cote_qr(avec_texte=avec_texte) * mm
    page_courante = 0
    for i, etiquette in enumerate(etiquettes):
        rang = decalage + i
        page = rang // par_page
        if page != page_courante:
            c.showPage()
            page_courante = page
        x0, y0 = fmt._position(rang)
        x0, y0 = x0 * mm, y0 * mm
        w, h = fmt.largeur * mm, fmt.hauteur * mm
        if contours:
            c.setStrokeColor(HexColor("#BBBBBB"))
            c.setLineWidth(0.3)
            if fmt.forme == "rond":
                c.circle(x0 + w / 2, y0 + h / 2, w / 2, stroke=1, fill=0)
            else:
                c.roundRect(x0, y0, w, h, 1.5 * mm, stroke=1, fill=0)
        grille = matrice(etiquette["url"], logo is not None)
        texte = etiquette.get("texte") or ""
        taille = 7.0
        if a_cote:
            qx = x0 + marge
            qy = y0 + (h - cote) / 2
            tracer_qr(c, qx, qy, cote, grille, code, fond, logo)
            zone = w - 2 * marge - cote - 1.5 * mm
            if texte and zone > 5 * mm:
                taille = min(9.0, max(6.0, h / mm * 0.3))
                lignes = _couper(texte, POLICE, taille, zone)[:max(1, int((h - 2 * marge) / (taille * 1.25)))]
                c.setFillColor(HexColor("#000000"))
                c.setFont(POLICE, taille)
                haut = y0 + h / 2 + (len(lignes) - 1) * taille * 0.62
                for n, ligne in enumerate(lignes):
                    c.drawString(qx + cote + 1.5 * mm, haut - n * taille * 1.25 - taille * 0.35, ligne)
        else:
            reserve = HAUTEUR_TEXTE_MM * mm if texte else 0.0
            qx = x0 + (w - cote) / 2
            qy = y0 + (h - cote + reserve) / 2
            tracer_qr(c, qx, qy, cote, grille, code, fond, logo)
            if texte:
                largeur_utile = (cote if fmt.forme == "rond" else w - 2 * marge)
                ligne = _abreger(texte, POLICE, taille, largeur_utile)
                c.setFillColor(HexColor("#000000"))
                c.setFont(POLICE, taille)
                c.drawCentredString(x0 + w / 2, qy - reserve + 1.2 * mm, ligne)
    c.showPage()
    c.save()
    return tampon.getvalue()


def pages_necessaires(par_page, quantite, decalage):
    return max(1, math.ceil((decalage + quantite) / par_page))
