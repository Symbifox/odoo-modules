"""Parquer les images distantes d'un corps de courriel.

Une image chargée depuis le serveur de l'expéditeur lui dit que le message a
été lu, quand, et depuis quelle adresse IP. Ce module retire du HTML tout ce
qu'un navigateur irait chercher ailleurs, et le compte.

⚠️ On parcourt l'ARBRE, pas le texte. Une version précédente parquait par
expressions régulières : un `url(` sans parenthèse fermante avalait le
texte et les balises jusqu'à la prochaine `)`, effaçant un tableau et son
montant ; et le navigateur lit `https:\\\\hôte`, `\\68ttps`, `u\\72l(` ou
`srcset` là où une expression ne voit rien. Ici chaque attribut est lu seul,
normalisé comme un navigateur le ferait, et une déclaration CSS fautive est
retirée sans toucher aux voisines : rien ne déborde hors de l'attribut.

Ce qui reste local ne sort pas du message et n'annonce rien : `cid:`,
`data:`, `#ancre`, et une adresse relative (résolue contre la page d'Odoo
elle-même, jamais chez un tiers).
"""
import re
from email.utils import parseaddr

from lxml import etree
from lxml import html as lxml_html

from odoo import tools

# Les attributs qu'un navigateur charge tout seul, sans geste du lecteur.
_LOADING_ATTRS = ("src", "srcset", "poster", "background", "lowsrc",
                  "dynsrc", "data", "imagesrcset", "ping")
# `href` ne charge que sur ces balises (une feuille, une image SVG).
# (`img` : l'assainisseur réécrit `<svg><image href>` en `<img href>`.)
_HREF_LOADING_TAGS = ("link", "image", "img", "use", "feimage")
_LOCAL_PREFIXES = ("cid:", "data:", "#")
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.\-]*:")
# Ce qu'un navigateur retire d'une URL avant de la lire : blancs et contrôles.
_URL_STRIP_RE = re.compile(r"[\x00-\x20\x7f]+")
_CSS_ESCAPE_RE = re.compile(r"\\(?:([0-9a-fA-F]{1,6})[ \t\r\n\f]?|(.))", re.S)
_CSS_COMMENT_RE = re.compile(r"/\*.*?(?:\*/|$)", re.S)
# Dans une valeur CSS déjà décodée : l'argument d'un url(...) (fermé ou non),
# et toute chaîne entre guillemets (image-set('…'), @import "…").
_CSS_URL_ARG_RE = re.compile(r"url\s*\(\s*(['\"]?)([^'\")]*)", re.I)
# Un document entier (`<html>`, `<head>`, `<body background=…>`) : le parseur
# de fragments jette ces enveloppes ET leurs attributs. On resérialise alors
# toujours, sinon l'original repartirait avec son `<body background>`.
_DOCUMENT_TAG_RE = re.compile(r"<\s*(?:html|head|body)\b", re.I)
_CSS_STRING_RE = re.compile(r"(['\"])(.*?)(?:\1|$)", re.S)
# Les attributs qu'on ne juge pas comme du CSS : du texte, un lien qu'il faut
# toucher pour suivre, ou un attribut que le navigateur ne charge jamais.
_TEXT_ATTRS = frozenset({"alt", "title", "href", "name", "id", "class",
                         "lang", "dir", "target", "rel", "type", "value",
                         "placeholder", "summary", "abbr", "label", "content",
                         "datetime", "cite", "action", "width", "height",
                         "align", "valign", "colspan", "rowspan", "border",
                         "cellpadding", "cellspacing", "bgcolor", "color",
                         "face", "size"})


def is_remote_url(value):
    """Vrai si un navigateur irait chercher cette URL chez un tiers."""
    url = _URL_STRIP_RE.sub("", value or "").replace("\\", "/").lower()
    if not url or url.startswith(_LOCAL_PREFIXES):
        return False
    # « //hôte » et tout schéma (http, https, ftp…) sortent ; une adresse
    # relative reste chez Odoo.
    return url.startswith("//") or bool(_SCHEME_RE.match(url))


def _css_unescape(text):
    """La valeur CSS avec ses échappements décodés (`u\\72l(` → `url(`)."""
    def _unescape(match):
        if match.group(1):
            try:
                return chr(int(match.group(1), 16))
            except (ValueError, OverflowError):
                return "�"
        return match.group(2)
    return _CSS_ESCAPE_RE.sub(_unescape, text or "")


def _css_text_is_remote(decoded):
    if "@import" in decoded.lower():
        return True
    for match in _CSS_URL_ARG_RE.finditer(decoded):
        if is_remote_url(match.group(2)):
            return True
    # Toute chaîne qui EST une URL distante, quelle que soit la propriété :
    # `image-set('…')`, mais aussi `--x:'…'` relu plus loin par
    # `image-set(var(--x))`, que rien ne relie à la chaîne.
    for match in _CSS_STRING_RE.finditer(decoded):
        if is_remote_url(match.group(2)):
            return True
    return False


def css_is_remote(text):
    """Vrai si ce fragment de CSS chargerait une ressource chez un tiers.

    Jugé deux fois, avec et sans les commentaires : un `/*` dans une chaîne
    (`url('/*'),url(http://…)`) n'ouvre pas de commentaire pour le
    navigateur, et le retirer à l'aveugle masquait l'URL qui suit. Un faux
    positif retire une déclaration ; un faux négatif
    annonce la lecture.
    """
    decoded = _css_unescape(text)
    return (_css_text_is_remote(decoded)
            or _css_text_is_remote(_CSS_COMMENT_RE.sub("", decoded)))


def _park_style(value):
    """Retire les déclarations qui chargent chez un tiers. Rend ``(style, n)``."""
    kept, removed = [], 0
    for declaration in (value or "").split(";"):
        if css_is_remote(declaration):
            removed += 1
        else:
            kept.append(declaration)
    if not removed:
        return value, 0
    return ";".join(kept), removed


def _srcset_is_remote(value):
    return any(is_remote_url(candidate.strip().split(" ")[0])
               for candidate in (value or "").split(",") if candidate.strip())


def _park_tree(root):
    count = 0
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        tag = el.tag.lower()
        if tag == "meta" and "refresh" in (el.get("http-equiv") or "").lower():
            el.attrib.clear()
            count += 1
            continue
        if tag == "style":
            if css_is_remote(el.text or ""):
                el.text = ""
                count += 1
            continue
        for attr, value in list(el.attrib.items()):
            name = attr.lower()
            if name.startswith("data-blocked-"):
                continue
            local = name.rsplit(":", 1)[-1]  # xlink:href
            if name == "style":
                parked, n = _park_style(value)
                if n:
                    el.set("style", parked)
                    count += n
                continue
            if local in ("srcset", "imagesrcset"):
                remote = _srcset_is_remote(value)
            elif local in _LOADING_ATTRS or (
                    local == "href" and tag in _HREF_LOADING_TAGS):
                remote = is_remote_url(value)
            elif local in _TEXT_ATTRS or name.startswith(("data-", "aria-")):
                continue
            else:
                # Attributs de présentation SVG (`filter`, `mask`, `cursor`,
                # `fill`, `marker-start`…) : l'assainisseur les garde, et
                # Chromium charge `filter="url(http://…)"` sans geste. Tout autre
                # attribut est jugé comme du CSS.
                remote = css_is_remote(value)
            if remote:
                del el.attrib[attr]
                el.set("data-blocked-" + name.replace(":", "-"), value)
                count += 1
    return count


def park_remote_images(body):
    """Parque les images distantes d'un corps HTML. Rend ``(html, n)``.

    Un corps sans rien à parquer revient TEL QUEL (pas resérialisé) : le
    comportement de lecture des messages sans pisteur ne bouge pas.
    """
    body = body or ""
    if not body.strip():
        return body, 0
    try:
        root = lxml_html.fragment_fromstring(body, create_parent="div")
    except (etree.ParserError, ValueError):
        return body, 0
    count = _park_tree(root)
    if not count and not _DOCUMENT_TAG_RE.search(body):
        return body, 0
    out = lxml_html.tostring(root, encoding="unicode")
    # Retirer l'enveloppe <div>…</div> posée par fragment_fromstring.
    return out[len("<div>"):-len("</div>")], count


def count_remote_images(body):
    """Combien d'images distantes ce corps chargerait, sans le toucher."""
    return park_remote_images(body)[1]


def strict_sender(email_from):
    """L'adresse de l'expéditeur, ou ``""`` si l'en-tête ``From`` est ambigu.

    La liste d'autorisation décide sur cette adresse : un ``From`` que deux
    lecteurs liraient différemment ne doit pas la faire jouer :
    ``tools.email_normalize`` rend ``alertes@exemple.com`` pour
    ``alertes@exemple.com@evil.example``.
    Refusés : zéro ou plusieurs adresses, plus d'un ``@``, ou un désaccord
    entre ``email_split`` d'Odoo et ``parseaddr`` de Python.
    """
    addresses = tools.email_split(email_from or "")
    if len(addresses) != 1:
        return ""
    address = (addresses[0] or "").strip().lower()
    parsed = (parseaddr(email_from or "")[1] or "").strip().lower()
    if address.count("@") != 1 or parsed != address or address.endswith("."):
        return ""
    return tools.email_normalize(address) or ""
