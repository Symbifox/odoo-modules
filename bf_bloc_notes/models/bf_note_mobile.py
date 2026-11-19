"""Le contrat mobile du bloc-notes : ce que la page ``/notes`` et l'application
Symbifox Mobile lisent et écrivent.

Une seule couche pour les deux portes. La page (session Odoo) et l'application
(jeton d'appareil) appellent les mêmes méthodes, donc la même forme de note, les
mêmes refus et les mêmes phrases. Deux copies divergeraient au premier geste
ajouté.

Toutes les méthodes sont PRIVÉES (préfixe ``_``) : une méthode publique d'un
modèle est appelable par RPC, et rien ici n'a à l'être en dehors des deux
contrôleurs. Aucune n'élève de droit : elles tournent sous l'usager appelant,
et la règle d'enregistrement du module (auteur, ou note partagée en lecture)
s'applique telle quelle.

Deux choix qui décident de tout le reste :

* **Le téléphone écrit du texte, pas du HTML.** Une note née au téléphone est
  une suite de paragraphes ; elle fait l'aller-retour texte → HTML → texte sans
  rien perdre. Une note riche, née au bureau (image, liste, gras, lien), ne le
  ferait pas : la réécrire depuis un champ texte effacerait sa mise en forme en
  silence. Elle est donc rendue ``editable: false``, lue au téléphone et
  modifiée au bureau.
* **Une note se crée d'abord sur l'appareil.** Elle porte un ``client_uuid``
  tiré par l'appareil, et le serveur rend la même note à chaque renvoi : la file
  d'envoi hors ligne peut rejouer sans jamais dédoubler.
"""

import re
import uuid
from datetime import datetime, timedelta
from html import unescape

from lxml import html as lxml_html
from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import format_date, html2plaintext

from odoo.addons.bf_color.models.color_utils import normalize_hex

#: Plafond de `_mobile_visible_ids`. Des entiers seulement, donc on peut être
#: généreux : 5 000 identifiants pèsent environ 35 Ko. Au-delà, la réponse le
#: dit (`truncated`) et le client ne doit PAS purger sur cette base.
MAX_IDS = 5000

#: Gestes rapides offerts au téléphone et sur la carte kanban.
ACTIONS = ("archive", "unarchive", "pin", "unpin", "activity", "task", "reroute",
           # « Hors Gen ».
           "gen_exclude", "gen_include")

#: Délais de rappel offerts, en jours (aujourd'hui, demain, +2 j, +1 sem.).
ACTIVITY_DAYS = (0, 1, 2, 7)

#: Balises qu'un champ texte sait reproduire. Tout le reste rend la note riche.
PLAIN_TAGS = {"p", "div", "br", "span"}
BLOCK_TAGS = {"p", "div"}

#: Attributs qui ne portent aucune mise en forme. 🔴 `data-oe-version` est posé
#: par l'éditeur d'Odoo sur l'enveloppe de TOUTE note écrite au bureau : sans
#: lui dans cette liste, une grande partie des notes d'une base réelle étaient
#: rendues « riches », donc en lecture seule au téléphone, sans aucune raison.
HARMLESS_ATTRS = {"class", "data-oe-version"}

#: Plafonds côté serveur : une page ou une app raisonnable n'en approche jamais,
#: une charge hors sujet est refusée avant d'être écrite.
MAX_TEXT = 100_000
MAX_TITLE = 200
MAX_LIMIT = 200

#: Palette kanban d'Odoo : 0 (aucune) à 11.
MAX_COLOR = 11

#: un fichier partagé vers une note. 25 Mo couvre une photo de
#: téléphone, un PDF ou une courte vidéo ; au-delà, c'est un dépôt Nextcloud
#: qu'on veut, pas une pièce de bloc-notes.
MAX_ATTACHMENT = 25 * 1024 * 1024
#: Pièces par note : un partage en porte rarement plus de quelques-unes.
MAX_ATTACHMENTS_PER_NOTE = 20
#: Préférences d'affichage que le mobile et la page lisent et écrivent,
#: nom du contrat → champ de ``res.users``.
PREFS = {
    "layout": "bf_note_layout",
    "density": "bf_note_density",
    "color_style": "bf_note_color_style",
    "group_by_color": "bf_note_group_by_color",
}


#: ──────────────────────────────────────────────────────────────────────────
#: Lot « mise en forme ». La barre du widget de bureau écrit du
#: gras, de l'italique, du souligné, du barré et des puces. Elle doit pouvoir
#: RELIRE ce qu'elle a écrit sans rien perdre : d'où un sous-ensemble HTML
#: nommé ici, et une forme CANONIQUE, une seule orthographe par effet.
#:
#: 🔴 Qt ne rend pas le HTML qu'on lui donne. Un `TextEdit` en mode riche
#: renvoie un document COMPLET (`<!DOCTYPE>`, `<head>`, `<style>`) où le gras
#: est porté par `style="font-weight:700"` et jamais par `<b>`. Un
#: assainisseur qui ne lirait que les balises effacerait donc toute la mise en
#: forme au PREMIER aller-retour, sans un mot.
#: ──────────────────────────────────────────────────────────────────────────
EFFETS = ("b", "i", "u", "s")
#: Orthographes acceptées en entrée, ramenées à la forme canonique.
RICH_SYNONYMES = {"strong": "b", "em": "i", "del": "s", "strike": "s"}
#: Balises en ligne : elles ne coupent pas le paragraphe.
RICH_INLINE = set(EFFETS) | set(RICH_SYNONYMES) | {"span", "font"}
#: Ce qu'un corps peut porter pour rester modifiable à la barre.
RICH_TAGS = RICH_INLINE | {"p", "div", "br", "ul", "ol", "li"}
#: Déclarations de style que l'assainisseur sait traduire en balise. Une autre
#: (une couleur, une taille) serait PERDUE : la note n'est alors plus
#: modifiable à la barre, seulement affichée.
RICH_STYLES = {"font-weight", "font-style", "text-decoration", "text-decoration-line"}
#: Éléments dont même le TEXTE doit disparaître : le contenu d'un `<script>`
#: ou d'un `<style>` n'est pas de la prose, et le déplier l'écrirait dans la
#: note. Tout le reste est DÉPLIÉ : sa balise tombe, son texte reste.
RICH_MUETS = {"script", "style", "head", "title", "meta", "link", "base",
              "noscript", "iframe", "object", "embed", "template", "svg"}
#: Balises qui COUPENT le paragraphe. Hors de cette liste, une balise inconnue
#: est dépliée EN LIGNE, et c'est le bon défaut : mesuré à l'essai, traiter
#: l'inconnu comme un bloc coupait la phrase « voir <a>ici</a> » en deux
#: paragraphes. Un `<h1>` ou une cellule de tableau, eux, commencent bien une
#: ligne — d'où la liste, nommée plutôt que devinée.
RICH_BLOCS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote",
              "pre", "address", "article", "aside", "section", "nav", "main",
              "header", "footer", "figure", "figcaption", "hr", "table",
              "thead", "tbody", "tfoot", "tr", "td", "th", "caption",
              "colgroup", "col", "dl", "dt", "dd", "form", "fieldset",
              "legend", "details", "summary", "body", "html", "center"}

#: Le HTML le plus gros accepté À L'ÉCRITURE, d'un client. Qt enfle un texte
#: d'un facteur 4 à 6 avec son enveloppe ; 400 Ko laissent passer une note de
#: 100 000 caractères (MAX_TEXT) habillée par Qt, et refusent une charge hors
#: sujet AVANT de la donner à lxml.
MAX_HTML = 400_000
#: 🔴 Et un plafond SÉPARÉ, bien plus haut, pour la LECTURE. Le plafond
#: d'écriture appliqué à la lecture était un déni de service : `_mobile_payload`
#: assainit chaque note, donc UNE note de plus de 400 Ko — un collage long au
#: formulaire du bureau, une image `data:` en base64 — faisait rendre 400 à la
#: LISTE ENTIÈRE, sur les deux portes, et avec `shared` la note d'un collègue
#: suffisait à éteindre le widget de tout le monde. Rendre une donnée DÉJÀ EN
#: BASE ne doit jamais pouvoir échouer : au-delà, on sert un corps vide et
#: « non modifiable », jamais une erreur.
MAX_HTML_LECTURE = 2_000_000

_DOCTYPE = re.compile(r"(?is)<!doctype[^>]*>")
#: Un paragraphe qui ne porte que des sauts de ligne : une ligne vide.
_PARA_VIDE = re.compile(r"<p>(?:<br>)+</p>")
#: Les NOMS de balise d'un corps, lus sans analyseur. 🔴 Seul témoin qui voit
#: ce que l'analyseur avale : libxml2 abandonne vers 255 niveaux
#: d'imbrication, et un `<img>` caché sous 400 `<div>` n'est alors PAS dans
#: l'arbre — la liste blanche ne voyait que des `div` et déclarait la note
#: modifiable avec un corps vide.
#: ⚠️ Le nom va jusqu'au bord, pas jusqu'au premier caractère non
#: alphanumérique. Une première version lisait `([A-Za-z][A-Za-z0-9]*)` et
#: TRONQUAIT : `<div-onclick onclick="…">` se lisait « div », qui est dans le
#: sous-ensemble. Le témoin était donc plus faible que l'analyseur sur les
#: noms — alors qu'il existe pour les cas où l'analyseur est AVEUGLE. Mesuré :
#: 400 `<div>` imbriqués plus un `<div-onclick>` sans texte étaient déclarés
#: modifiables, l'élément caché étant hors de l'arbre tronqué et invisible au
#: témoin des lignes. Les deux bouts du nom comptent.
_NOMS_DE_BALISE = re.compile(r"<\s*/?\s*([A-Za-z][^\s>/]*)")
#: Les balises d'un corps, retirées sans analyseur. ⚠️ Linéaire exprès : une
#: première version retirait aussi le CONTENU des muets, par un `.*?` entre
#: balise ouvrante et fermante — donc quadratique, 3,4 s de processeur sur
#: 8 000 `<script ` non fermés. Ici les muets sont déjà refusés par
#: `_NOMS_DE_BALISE`, donc un simple retrait de balises suffit.
_BALISES_RE = re.compile(r"(?s)<[^>]*>")
#: Les puces que `_rich_texte_lisible` fabrique, à retirer d'un TITRE.
_PUCE = re.compile(r"^(?:• |\d+\. )")


def _rich_balise(tag):
    """Le nom canonique d'une balise, ou ``None`` si ce n'en est pas une
    (commentaire HTML, instruction de traitement)."""
    if not isinstance(tag, str):
        return None
    tag = tag.lower()
    return RICH_SYNONYMES.get(tag, tag)


def _rich_effets_du_style(style):
    """Les effets qu'un ``style`` AJOUTE, et ceux qu'il RETIRE.

    🔴 Un style peut ANNULER l'effet du parent : `font-weight:normal` dans un
    `<b>`, `text-decoration:none` dans un `<u>`. Une première version
    n'additionnait que les ajouts, et rendait donc
    `<b>gras <span style="font-weight:normal">net</span></b>` par
    `<b>gras </b><b>net</b>` : l'assainisseur AJOUTAIT de la mise en forme.
    Ajouter est pire que perdre — personne ne va vérifier ce qu'il n'a pas
    demandé.
    """
    ajouts, retraits = set(), set()
    for declaration in (style or "").lower().split(";"):
        if ":" not in declaration:
            continue
        cle, valeur = declaration.split(":", 1)
        cle, valeur = cle.strip(), valeur.strip()
        if cle == "font-weight":
            if valeur in ("bold", "bolder"):
                ajouts.add("b")
            elif valeur in ("normal", "lighter"):
                retraits.add("b")
            else:
                try:
                    # 600 est le seuil de Qt : il écrit 600 pour « gras ».
                    (ajouts if int(valeur) >= 600 else retraits).add("b")
                except ValueError:
                    pass
        elif cle == "font-style":
            if valeur in ("italic", "oblique"):
                ajouts.add("i")
            elif valeur == "normal":
                retraits.add("i")
        elif cle in ("text-decoration", "text-decoration-line"):
            if "underline" in valeur:
                ajouts.add("u")
            if "line-through" in valeur:
                ajouts.add("s")
            if "none" in valeur:
                retraits |= {"u", "s"}
    return ajouts, retraits


def _rich_style_traduisible(style):
    """Vrai si TOUTES les déclarations du ``style`` sont traduisibles.

    Une seule qui ne l'est pas (``color``, ``font-size``, ``background``)
    suffit à rendre la note non modifiable : l'assainisseur la perdrait en
    silence, et la barre réécrirait la note sans elle.
    """
    for declaration in (style or "").split(";"):
        if not declaration.strip():
            continue
        if ":" not in declaration:
            return False
        if declaration.split(":", 1)[0].strip().lower() not in RICH_STYLES:
            return False
    return True


class _Riche:
    """Réécrit un corps HTML dans le sous-ensemble de la barre.

    Deux niveaux seulement : des BLOCS (paragraphes et listes) et, dans
    chacun, du texte en ligne portant des effets. Un bloc inconnu coupe le
    paragraphe ; une balise en ligne inconnue est dépliée ; un muet disparaît
    avec son texte.

    ⚠️ Ce que l'assainisseur ne sait pas faire est nommé, et c'est
    `_rich_est_modifiable` qui refuse ces formes : une liste imbriquée est
    APLATIE, une liste enveloppée d'un `<b>` devient un paragraphe, et deux
    paragraphes dans un même `<li>` sont RECOLLÉS. Trois formes que ni
    l'éditeur d'Odoo ni Qt ne produisent, mais qu'un collage peut amener.
    """

    def __init__(self):
        self.blocs = []       # les morceaux de premier niveau, déjà rendus
        self.courant = []     # le paragraphe en construction

    # -- texte et effets ------------------------------------------------
    def _ecrire(self, texte, effets, cible):
        if not texte:
            return
        morceau = escape(texte)
        if morceau.strip():
            # Ordre FIXE : deux corps identiques rendent la même chaîne, donc
            # l'aller-retour du widget ne « modifie » pas la note pour rien.
            # Parcouru à l'envers pour que le PREMIER de `EFFETS` finisse
            # dehors : `<b><i>a</i></b>`, comme une personne l'écrirait.
            for effet in reversed(EFFETS):
                if effet in effets:
                    morceau = Markup("<%s>%s</%s>") % (effet, morceau, effet)
        cible.append(morceau)

    def _en_ligne(self, element, effets, cible):
        """Un élément en ligne — ou déplié — puis son ``tail``."""
        tag = _rich_balise(element.tag)
        if tag is None or tag in RICH_MUETS:
            pass
        elif tag == "br":
            cible.append(Markup("<br>"))
        else:
            propres = set(effets)
            if tag in RICH_INLINE:
                ajouts, retraits = _rich_effets_du_style(element.get("style"))
                propres |= ajouts
                propres -= retraits
                if tag in EFFETS:
                    propres.add(tag)
            self._ecrire(element.text, propres, cible)
            for enfant in element:
                self._en_ligne(enfant, propres, cible)
        # Le `tail` appartient au flux du PARENT : les effets de l'élément
        # qu'on vient de fermer ne le portent pas.
        self._ecrire(element.tail, effets, cible)

    # -- blocs ----------------------------------------------------------
    def _ferme(self):
        contenu = Markup("").join(self.courant).strip()
        self.courant = []
        if contenu:
            self.blocs.append(Markup("<p>%s</p>") % contenu)

    @staticmethod
    def _pousse(items, morceaux):
        contenu = Markup("").join(morceaux).strip()
        if contenu:
            items.append(contenu)

    def _items(self, liste, items, effets):
        for enfant in liste:
            tag = _rich_balise(enfant.tag)
            if tag == "li":
                self._item(enfant, items, effets)
            elif tag in ("ul", "ol"):
                self._items(enfant, items, effets)

    def _item(self, li, items, effets):
        morceaux = []
        ajouts, retraits = _rich_effets_du_style(li.get("style"))
        propres = (set(effets) | ajouts) - retraits
        self._ecrire(li.text, propres, morceaux)
        for enfant in li:
            if _rich_balise(enfant.tag) in ("ul", "ol"):
                # L'item courant se ferme avant les items imbriqués, sinon
                # leur texte se collerait au sien.
                self._pousse(items, morceaux)
                morceaux = []
                self._items(enfant, items, propres)
                self._ecrire(enfant.tail, propres, morceaux)
            else:
                self._en_ligne(enfant, propres, morceaux)
        self._pousse(items, morceaux)

    def _liste(self, element):
        self._ferme()
        items = []
        self._items(element, items, set())
        if not items:
            return
        balise = "ol" if _rich_balise(element.tag) == "ol" else "ul"
        self.blocs.append(Markup("<%s>%s</%s>") % (
            balise,
            Markup("").join(Markup("<li>%s</li>") % item for item in items),
            balise))

    def bloc(self, noeud):
        if isinstance(noeud, str):
            self._ecrire(noeud, set(), self.courant)
            return
        tag = _rich_balise(noeud.tag)
        # ⚠️ Ni les muets ni les commentaires ne sont filtrés ici : ils ne sont
        # pas des blocs, donc ils descendent dans `_en_ligne`, qui porte la
        # SEULE garde. Une deuxième garde à cet étage était du code mort —
        # mesuré par mutation : la retirer ne cassait aucun essai.
        if tag in ("ul", "ol"):
            self._liste(noeud)
            self._ecrire(noeud.tail, set(), self.courant)
            return
        if tag not in RICH_BLOCS:
            self._en_ligne(noeud, set(), self.courant)
            return
        # Un BLOC : il coupe le paragraphe avant ET après. Son propre texte
        # fait un paragraphe à lui, que les blocs enfants ne rejoignent pas.
        self._ferme()
        self._ecrire(noeud.text, set(), self.courant)
        for enfant in noeud:
            self.bloc(enfant)
        self._ferme()
        self._ecrire(noeud.tail, set(), self.courant)

    def rendu(self):
        self._ferme()
        # Les lignes vides de FIN ne sont pas gardées, comme dans
        # `_mobile_text_to_html` : sinon chaque aller-retour en ajouterait une.
        while self.blocs and _PARA_VIDE.fullmatch(self.blocs[-1]):
            self.blocs.pop()
        return Markup("").join(self.blocs)


def _rich_fragments(brut):
    """L'arbre d'un corps, ou ``None`` s'il est illisible.

    🔴 UNE analyse, partagée par l'assainisseur, par le juge et par le rendu
    du texte. Mesuré en relecture : le même corps était analysé six à huit
    fois par note, et `_mobile_payload` tourne jusqu'à 200 fois par appel de
    liste — jusqu'à onze secondes de processeur sur 200 notes un peu longues.
    """
    try:
        return lxml_html.fragments_fromstring(_DOCTYPE.sub(" ", brut))
    except Exception:  # noqa: BLE001
        return None


def _rich_assainir(brut, fragments=None):
    """Le corps réécrit dans le sous-ensemble de la barre."""
    brut = "" if not brut else str(brut)
    if not brut.strip():
        return Markup("")
    if fragments is None:
        fragments = _rich_fragments(brut)
    if fragments is None:
        raise UserError(_("This note cannot be read."))
    riche = _Riche()
    for fragment in fragments:
        riche.bloc(fragment)
    return riche.rendu()


def _rich_est_plain(fragments):
    """Vrai si l'arbre ne porte que des paragraphes de texte.

    C'est le contrat du TÉLÉPHONE, inchangé depuis la couche mobile : lu sur un arbre
    déjà analysé, pour ne pas le relire.
    """
    for fragment in fragments:
        if isinstance(fragment, str):
            continue
        for element in fragment.iter():
            if not isinstance(element.tag, str):
                # Commentaire HTML : la réécriture le perdrait.
                return False
            if element.tag not in PLAIN_TAGS:
                return False
            if element.tag == "span" and element.attrib:
                return False
            if element.tag != "span" and set(element.attrib) - HARMLESS_ATTRS:
                return False
    return True


def _rich_lignes(fragments):
    """Le texte d'un corps, UNE LIGNE PAR BLOC, espaces normalisés.

    🔴 Les deux côtés d'un témoin de perte doivent sortir de la MÊME
    fonction. Première version mesurée à l'essai : le brut par expression
    régulière (les balises retirées, les espaces autour gardées) et la forme
    assainie par l'analyseur (les blocs recollés sans séparateur). Les deux
    normalisations n'étaient pas comparables, et le témoin était faux DANS LES
    DEUX SENS :

    * `<p>a </p><p>b</p>` donnait « a b » d'un côté, « ab » de l'autre : toute
      note du bureau portant une espace au bord d'un bloc — ou un simple
      retour à la ligne entre deux balises, ce que fait tout éditeur — était
      déclarée NON modifiable. La barre se serait refermée sur les seules
      notes qu'elle avait elle-même écrites.
    * et il restait aveugle à `<ul><li><p>un</p><p>deux</p></li></ul>`, dont
      l'assainisseur recolle les mots en « undeux ».

    Ici, les deux côtés passent par l'analyseur, les bords de bloc sont
    MATÉRIALISÉS en lignes, et chaque ligne est normalisée. Une espace au bord
    ne change rien ; un mot recollé change tout.
    """
    lignes, courante = [], []

    def ferme():
        texte = " ".join("".join(courante).split())
        del courante[:]
        if texte:
            lignes.append(texte)

    def parcours(noeuds):
        for noeud in noeuds:
            if isinstance(noeud, str):
                courante.append(noeud)
                continue
            tag = _rich_balise(noeud.tag)
            if tag is None or tag in RICH_MUETS:
                courante.append(noeud.tail or "")
                continue
            # Tout ce qui n'est pas en ligne coupe : un bloc, une puce, un
            # `<br>`. La règle est la même des deux côtés, c'est tout ce qui
            # compte.
            coupe = tag not in RICH_INLINE
            if coupe:
                ferme()
            courante.append(noeud.text or "")
            parcours(list(noeud))
            if coupe:
                ferme()
            courante.append(noeud.tail or "")

    parcours(fragments)
    ferme()
    return lignes


def _rich_texte_simple(fragments):
    """Le texte d'une note SIMPLE, ligne par ligne (contrat du téléphone)."""
    lignes = []

    def en_ligne(element):
        # `<br>` à l'intérieur d'un paragraphe = saut de ligne voulu.
        parts = [element.text or ""]
        for child in element:
            parts.append("\n" if child.tag == "br" else child.text_content())
            parts.append(child.tail or "")
        texte = "".join(parts)
        # Un paragraphe qui ne contient qu'un `<br>` est une ligne vide.
        return "" if not texte.strip() else texte.rstrip("\n")

    def parcourir(noeuds, texte_de_tete=None):
        # 🔴 Un bloc peut en contenir d'autres : l'éditeur enveloppe la note
        # dans un `<div data-oe-version>` qui porte les `<p>`. Aplatir
        # l'enveloppe d'un coup collerait tous les paragraphes en une ligne.
        if texte_de_tete and texte_de_tete.strip():
            lignes.append(texte_de_tete.strip())
        for noeud in noeuds:
            if isinstance(noeud, str):
                if noeud.strip():
                    lignes.append(noeud.strip())
                continue
            if noeud.tag in BLOCK_TAGS and any(
                    isinstance(enfant.tag, str) and enfant.tag in BLOCK_TAGS
                    for enfant in noeud):
                parcourir(list(noeud), noeud.text)
            elif noeud.tag in BLOCK_TAGS:
                lignes.append(en_ligne(noeud))
            elif noeud.tag == "br":
                lignes.append("")
            else:
                lignes.append(noeud.text_content())
            if noeud.getparent() is not None and noeud.tail and noeud.tail.strip():
                lignes.append(noeud.tail.strip())

    parcourir(fragments)
    # Les lignes vides de FIN ne sont pas gardées à l'écriture : les garder
    # à la lecture rendrait un aller-retour inexact.
    while lignes and not lignes[-1].strip():
        lignes.pop()
    return "\n".join(lignes)


def _rich_texte_lisible(propre):
    """Le texte d'un corps du sous-ensemble, SANS bruit de balisage.

    🔴 `html2plaintext` rend `<b>gras</b>` par `*gras*`. C'était supportable
    pour une note riche qu'on ne faisait que lire ; ça ne l'est plus depuis
    que la barre en fabrique : mesuré à l'essai, le titre d'une note devenait
    « Des *courses* », et c'est ce titre que le téléphone affiche dans sa
    liste. Les puces, elles, GAGNENT à se voir : « • lait ».

    L'entrée est la forme canonique rendue par `_Riche`, et rien d'autre :
    seuls `p`, `ul`, `ol`, `li`, `br` et les quatre effets s'y trouvent.
    """
    lignes = []

    def en_ligne(element):
        parts = [element.text or ""]
        for enfant in element:
            if _rich_balise(enfant.tag) == "br":
                parts.append("\n")
            else:
                parts.append(en_ligne(enfant))
            parts.append(enfant.tail or "")
        return "".join(parts)

    fragments = _rich_fragments(str(propre or ""))
    if fragments is None:
        return ""
    for fragment in fragments:
        if isinstance(fragment, str):
            if fragment.strip():
                lignes.append(fragment.strip())
            continue
        tag = _rich_balise(fragment.tag)
        if tag in ("ul", "ol"):
            numero = 0
            for item in fragment:
                if _rich_balise(item.tag) != "li":
                    continue
                numero += 1
                puce = "%s. " % numero if tag == "ol" else "• "
                lignes.append(puce + en_ligne(item).strip())
        else:
            lignes.append(en_ligne(fragment).rstrip("\n"))
    while lignes and not lignes[-1].strip():
        lignes.pop()
    return "\n".join(lignes)


def _rich_est_modifiable(html, propre=None, fragments=None):
    """Vrai si le corps tient ENTIÈREMENT dans le sous-ensemble de la barre.

    C'est la promesse faite au widget : ce qu'il reçoit dans ``html``, il peut
    le renvoyer tel quel sans rien effacer. Un lien, une image, un tableau,
    une couleur, un titre, une liste imbriquée ne survivraient pas à
    l'aller-retour ; la note est alors affichée, et non modifiable.

    Quatre juges, parce qu'aucun ne voit tout :

    1. les NOMS de balise lus **sans analyseur** — seul témoin de ce que
       l'analyseur avale quand un corps est trop profond ;
    2. la liste blanche STRUCTURELLE sur l'arbre (balises, attributs, styles) ;
    3. trois formes que l'assainisseur change sans toucher au texte, donc
       qu'aucun témoin de texte ne peut voir : liste imbriquée, `<li>` hors
       liste, `<br>` hors bloc ;
    4. les LIGNES, identiques des deux côtés : le filet pour tout ce que je
       n'ai pas su énumérer.
    """
    brut = "" if not html else str(html)
    if not brut.strip():
        return True
    # 1. Sans analyseur.
    for nom in set(_NOMS_DE_BALISE.findall(brut)):
        if nom.lower() not in RICH_TAGS:
            return False
    if fragments is None:
        fragments = _rich_fragments(brut)
    if fragments is None:
        return False
    # 2 et 3.
    for fragment in fragments:
        if isinstance(fragment, str):
            continue
        for element in fragment.iter():
            if not isinstance(element.tag, str):
                return False      # commentaire : la réécriture le perdrait
            tag = element.tag.lower()
            if tag not in RICH_TAGS:
                return False
            attributs = set(element.attrib) - HARMLESS_ATTRS
            if attributs - {"style"}:
                return False
            if "style" in attributs and not _rich_style_traduisible(element.get("style")):
                return False
            ancetres = [a.tag.lower() for a in element.iterancestors()
                        if isinstance(a.tag, str)]
            if tag in ("ul", "ol") and any(a in ("ul", "ol") for a in ancetres):
                return False      # imbriquée : l'assainisseur l'aplatirait
            if tag == "li" and not any(a in ("ul", "ol") for a in ancetres):
                return False      # hors liste : la puce serait perdue
            if tag == "br" and not any(a in ("p", "div", "li") for a in ancetres):
                return False      # hors bloc : une ligne vide apparaîtrait
    # 4.
    if propre is None:
        propre = _rich_assainir(brut, fragments)
    propres = _rich_fragments(str(propre))
    if propres is None:
        return False
    lignes_propres = _rich_lignes(propres)
    # 🔴 Un dernier témoin, sans analyseur : un corps dont il ne reste RIEN
    # alors qu'il portait du texte. Mesuré à l'essai (et différemment selon la
    # version de libxml2) : mille `<div>` imbriqués sont TRONQUÉS par
    # l'analyseur, les deux côtés du témoin des lignes sont donc vides, et ils
    # sont « égaux ». Le texte brut, lui, ne mentait pas.
    if not lignes_propres and unescape(_BALISES_RE.sub("", brut)).strip():
        return False
    return _rich_lignes(fragments) == lignes_propres


def _rich_texte_de_repli(brut):
    """Le texte d'un corps que l'analyseur n'a pas su lire, sans jamais lever.

    🔴 `html2plaintext` n'est pas total : sur un corps dégénéré que lxml rend
    `None` (`<!--`, `<!`, une instruction de traitement, un CDATA nu), il fait
    un `.xpath` sur ce `None` et lève `AttributeError`. Ces corps-là
    n'arrivent pas par l'ORM — `bf.note.body` est un `fields.Html` assaini,
    qui les rend vides — mais cette fonction promet de ne jamais lever, et une
    promesse qui tient « sauf par une porte » n'en est pas une.

    Le repli est le même retrait de balises que le témoin sans analyseur.
    """
    try:
        return html2plaintext(brut).strip()
    except Exception:          # noqa: BLE001 - un chemin de LECTURE ne lève pas
        return unescape(_BALISES_RE.sub("", brut)).strip()


def _rich_rendu(html, riche=False):
    """Tout ce qu'une charge tire d'un corps, en DEUX analyses au plus.

    Un seul endroit : `_mobile_html_to_text` et `_mobile_payload` en
    dépendent tous les deux, et deux copies divergeraient au premier défaut
    corrigé d'un seul côté. ``riche`` demande en plus `html` et
    `rich_editable` — le téléphone ne les lit pas, et les lui servir doublait
    la part texte de sa charge pour rien.

    Ne lève JAMAIS : c'est un chemin de LECTURE.
    """
    brut = "" if not html else str(html)
    rendu = {"plain": True, "text": "", "html": "", "rich_editable": True}
    if not brut.strip():
        return rendu
    if len(brut) > MAX_HTML_LECTURE:
        return {"plain": False, "text": _rich_texte_de_repli(brut),
                "html": "", "rich_editable": False}
    fragments = _rich_fragments(brut)
    if fragments is None:
        return {"plain": False, "text": _rich_texte_de_repli(brut),
                "html": "", "rich_editable": False}
    plain = _rich_est_plain(fragments)
    propre = _rich_assainir(brut, fragments) if (riche or not plain) else None
    modifiable = (_rich_est_modifiable(brut, propre, fragments)
                  if propre is not None else True)
    if plain:
        texte = _rich_texte_simple(fragments)
    elif modifiable:
        texte = _rich_texte_lisible(propre)
    else:
        texte = _rich_texte_de_repli(brut)
    return {"plain": plain, "text": texte,
            "html": str(propre) if (riche and propre is not None) else "",
            "rich_editable": modifiable}


class BfNote(models.Model):
    _inherit = "bf.note"

    client_uuid = fields.Char(
        string="Device identifier",
        copy=False,
        index=True,
        readonly=True,
        help="Identifier drawn by the phone when the note was written offline. "
             "The server returns the same note each time it is sent again.",
    )

    _sql_constraints = [
        # NULL n'entre pas en collision avec NULL : les notes nées au bureau,
        # sans identifiant, ne sont pas touchées par la contrainte.
        ("client_uuid_user_unique", "unique(user_id, client_uuid)",
         "This device identifier is already used by another note."),
    ]

    # ------------------------------------------------------------------
    # Texte ↔ HTML
    # ------------------------------------------------------------------
    @api.model
    def _mobile_text_to_html(self, text):
        """Une ligne = un paragraphe ; une ligne vide garde sa place."""
        lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
        # Retirer les lignes vides de la FIN seulement : celles du milieu sont
        # l'espacement voulu par la personne.
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines:
            return ""
        return Markup("").join(
            Markup("<p>%s</p>") % line if line.strip() else Markup("<p><br></p>")
            for line in lines
        )

    @api.model
    def _mobile_html_is_plain(self, html, fragments=None):
        """Vrai si le corps ne porte que des paragraphes de texte.

        Le test est structurel (balises et attributs), pas un aller-retour de
        chaînes : Odoo réécrit le HTML à l'assainissement (espaces, ``<br>``
        auto-fermant), et une comparaison textuelle rendrait « riche » une note
        qui ne l'est pas.
        """
        if not html or not str(html).strip():
            return True
        if fragments is None:
            fragments = _rich_fragments(str(html))
        if fragments is None:  # illisible = on ne le réécrit pas
            return False
        return _rich_est_plain(fragments)
    @api.model
    def _mobile_html_to_text(self, html):
        """Le texte d'une note : ligne par ligne pour une note simple, sans
        bruit de balisage pour une note du sous-ensemble de la barre, lecture
        seule approximative (``html2plaintext``) pour tout le reste."""
        return _rich_rendu(html)["text"]
    # ------------------------------------------------------------------
    # Mise en forme
    # ------------------------------------------------------------------
    @api.model
    def _mobile_rich_sanitize(self, html):
        """ÉCRITURE : le HTML reçu d'un CLIENT, assaini. Lève sur abus.

        🔴 Exige une CHAÎNE. Une première version faisait `str(html)`, et un
        `"html": false` écrivait donc le mot « False » dans la note. Le module
        tranche déjà ainsi pour la clé voisine (`_mobile_clean_gen_excluded`).

        ⚠️ Ce plafond est celui de l'ÉCRITURE. La lecture a le sien
        (`MAX_HTML_LECTURE`) et ne lève jamais : voir `_rich_rendu`.
        """
        if not isinstance(html, str):
            raise UserError(_("“html” must be a string."))
        if len(html) > MAX_HTML:
            raise UserError(_(
                "This note is too long (%s characters of HTML at most).", MAX_HTML))
        return _rich_assainir(html)
    @api.model
    def _mobile_rich_is_safe(self, html, propre=None):
        """Vrai si ce corps peut revenir de la barre sans rien perdre.

        ``propre`` est la forme assainie quand l'appelant l'a déjà calculée.
        """
        return _rich_est_modifiable(html, propre)
    # ------------------------------------------------------------------
    # La forme d'une note sur le fil
    # ------------------------------------------------------------------
    @api.model
    def _mobile_datetime(self, value):
        """Horodatage à la microseconde : deux modifications dans la même
        seconde doivent rester distinctes pour la détection de conflit."""
        return value.strftime("%Y-%m-%d %H:%M:%S.%f") if value else None

    @api.model
    def _mobile_parse_datetime(self, value):
        if not value:
            return None
        value = str(value).strip().replace("T", " ").rstrip("Z")
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
        raise UserError(_("Unreadable date: %s", value))

    def _mobile_link_name(self, link):
        """Le nom de la fiche liée, SOUS LES DROITS DE L'APPELANT ; "" sinon.

        Jamais `link.res_name`, qui est stocké et donc calculé pour
        l'auteur de la note, pas pour qui la lit. Une fiche absente ou interdite
        rendent la même chose.
        """
        if not (link.res_model and link.res_id and link.res_model in self.env):
            return ""
        try:
            rec = self.env[link.res_model].sudo(False).browse(link.res_id).exists()
            if not rec:
                return ""
            rec.check_access("read")
            return rec.display_name or ""
        except AccessError:
            return ""

    def _mobile_touch(self):
        """Marque ces notes comme modifiées, sans rien changer de leur contenu.

        🔴 À quoi cela sert : un client suit `since` et ne redemande que ce
        dont le `write_date` a bougé. Or joindre un fichier écrit une
        `ir.attachment`, et renommer une étiquette écrit une `bf.note.tag` :
        dans les deux cas la NOTE est intacte, son `write_date` ne bouge pas,
        et la carte reste affichée avec son ancien contenu — jusqu'à ce que
        quelqu'un touche la note pour une autre raison.

        ⚠️ En `sudo` : c'est une écriture technique qui ne change aucune valeur
        métier, et celui qui joint une pièce à une note partagée en écriture
        n'a pas forcément le droit d'écrire sur tous les champs de la note.

        🔴 TROIS hypothèses fausses avant celle-ci, toutes démenties par
        l'essai :

        1. `write({})` — « l'ORM pose `write_date` sur tout `write` ». Non :
           un `vals` vide ne déclenche aucune écriture.
        2. `write({"write_date": ...})` — « il suffit de lui donner un champ,
           il écrasera la valeur ». Non : l'ORM filtre les colonnes
           d'horodatage reçues en `vals`, et retombe sur le cas 1.
        3. `UPDATE … SET write_date = now()` — et là l'essai montrait encore
           zéro microseconde d'écart. ⚠️ `now()` de PostgreSQL est l'heure de
           DÉBUT DE TRANSACTION, pas l'heure courante : dans une transaction,
           elle ne bouge jamais.

        Le point 3 n'est pas un défaut à corriger, c'est la mécanique d'Odoo :
        l'ORM écrit lui-même `write_date = self.env.cr.now()`, la même heure de
        transaction (c'est pourquoi `_mobile_next_since` s'y réfère). Deux
        écritures d'une même requête portent donc le même `write_date`, et
        c'est voulu. Ce qu'il faut, c'est que la note porte l'heure de LA
        requête qui a touché sa pièce jointe — et c'est exactement ce que fait
        `cr.now()` ici.

        ⚠️ `flush_recordset` AVANT : une écriture encore en attente dans le
        cache partirait après notre UPDATE et reposerait son propre
        `write_date`. `invalidate_recordset` APRÈS, sans quoi tout le monde
        relit l'ancienne valeur depuis le cache jusqu'à la fin de la requête.
        """
        if not self:
            return
        self.flush_recordset()
        self.env.cr.execute(
            "UPDATE bf_note SET write_date = %s WHERE id IN %s",
            (self.env.cr.now(), tuple(self.ids)))
        self.invalidate_recordset(["write_date"])

    def _mobile_payload(self, pieces=None):
        """La note, telle que la voit l'appelant.

        ``pieces`` : les pièces jointes déjà lues, quand l'appelant traite un
        lot (voir `_mobile_attachments_map`). Facultatif, pour que les dizaines
        d'appels unitaires restent inchangés.
        """
        self.ensure_one()
        links = []
        for link in self.link_ids:
            if not (link.res_model and link.res_id):
                continue
            # 🔴 le nom se résout ICI, sous les droits de
            # l'appelant. `res_name` est stocké (calculé sous les droits de
            # l'auteur de la note) : sur une note PARTAGÉE, il rendait à un
            # collègue le nom d'une fiche que lui ne peut pas ouvrir.
            nom = self._mobile_link_name(link)
            # 🔴 Et le nom caché ne suffisait pas. `model`, `id` et `url`
            # sortaient quand même, donc un collègue qui lit une note partagée
            # apprenait vers quel modèle et quelle fiche elle pointe, avec le
            # lien cliquable. Le NOM est la preuve du droit de lecture : sans
            # lui, le lien ne se décrit pas. Ce qui reste visible est qu'il
            # existe un lien — nettement moins parlant que sa cible.
            #
            # ⚠️ Les clés restent toutes présentes, vides : l'app mobile
            # publiée lit ce contrat, et en retirer une la ferait trébucher.
            lisible = bool(nom)
            links.append({
                "model": link.res_model if lisible else "",
                "id": link.res_id if lisible else 0,
                "name": nom,
                "url": "/odoo/%s/%s" % (link.res_model, link.res_id) if lisible else "",
                "restricted": not lisible,
            })
        if pieces is None:
            pieces = self._mobile_attachments()
        # 🔴 UN seul calcul pour `text`, `editable`, `html` et `rich_editable` :
        # cette méthode tourne jusqu'à 200 fois par appel de liste, et une
        # version qui analysait le corps six à huit fois mettait onze secondes
        # de processeur sur 200 notes un peu longues.
        #
        # ⚠️ `html` et `rich_editable` ne sont servis QUE sur demande
        # (`X-Symbifox-Rich`). Le téléphone et la page ne les lisent pas, et
        # les leur servir doublait la part texte de leur charge et ajoutait une
        # analyse par note, pour rien.
        rendu = _rich_rendu(self.body, riche=bool(self.env.context.get("bf_note_rich")))
        charge = {
            "id": self.id,
            "client_uuid": self.client_uuid or None,
            "title": self.name or "",
            "text": rendu["text"],
            "editable": rendu["plain"],
            "pinned": bool(self.pinned),
            # Les passes de Gen laissent cette note de côté.
            "gen_excluded": bool(self.gen_excluded),
            # `color` reste pour les versions installées de l'application ;
            # `color_hex` est la couleur résolue pour l'appelant.
            "color": self.color or 0,
            "color_hex": self.color_resolved or None,
            "text_color": self.color_text or None,
            "color_source": self.color_source or None,
            "tags": [
                {
                    "id": tag.id,
                    "name": tag.name,
                    "color_hex": tag.color_resolved or None,
                    "text_color": tag.color_text or None,
                }
                for tag in self.tag_ids
            ],
            "deadline": fields.Date.to_string(self.deadline_date) if self.deadline_date else None,
            "active": bool(self.active),
            "shared": bool(self.is_shared),
            "mine": self.user_id == self.env.user,
            "links": links,
            # Le nombre, pour les apps d'avant les pièces jointes ; la liste, pour les
            # suivantes.
            "attachments": len(pieces),
            "files": [self._mobile_attachment_payload(a) for a in pieces],
            "url": "/odoo/m-bf.note/%s" % self.id,
            "write_date": self._mobile_datetime(self.write_date),
        }
        if self.env.context.get("bf_note_rich"):
            # Barre de mise en forme. `text` et `editable` restent le
            # contrat du téléphone, qui n'écrit que du texte : une note mise en
            # forme y devient affichée, pas modifiable — la même règle qu'avant
            # ce lot.
            #
            # ⚠️ `html` est TOUJOURS assaini, même quand la note sort du
            # sous-ensemble : le widget ne rend qu'un peu de HTML, et lui
            # servir le corps brut d'une note riche — une image distante, un
            # tableau — le ferait peindre n'importe quoi, ou tirer une adresse.
            # Ce qu'il reçoit, il sait le rendre ; ce qu'il a le droit de
            # RENVOYER, c'est `rich_editable` qui le dit.
            charge["html"] = rendu["html"]
            charge["rich_editable"] = rendu["rich_editable"]
        return charge

    # ------------------------------------------------------------------
    # Lecture
    # ------------------------------------------------------------------
    @api.model
    def _mobile_scope(self, shared=False):
        """Le domaine de ce que le téléphone ou le bureau peut demander.

        🔴 Un seul endroit, et il NOMME toujours les enregistrements voulus.
        Les trois lectures de l'API (liste, identifiants, sonde) doivent avoir
        exactement la même portée : si l'une s'élargissait, son compte ou ses
        identifiants trahiraient ce que les deux autres cachent.
        """
        mien = ("user_id", "=", self.env.uid)
        if not shared:
            return [mien]
        return ["|", mien, ("is_shared", "=", True)]

    @api.model
    def _mobile_list(self, limit=50, offset=0, archived=False, query=None,
                     since=None, shared=False):
        """Les notes de l'usager, épinglées d'abord puis les plus récentes.

        Avec ``since``, rend tout ce qui a changé depuis, ARCHIVÉES COMPRISES :
        c'est ainsi qu'une note archivée ailleurs quitte la liste de l'appareil.

        Avec ``shared`` (widget de bureau), les notes que des
        collègues ont partagées s'ajoutent aux siennes. Chaque note porte déjà
        ``mine`` et ``shared``, de quoi les présenter à part et en lecture seule.

        🔴 Le domaine NOMME les deux cas, et ne se repose jamais sur la règle
        d'enregistrement. Laisser tomber le filtre sur l'auteur « pour laisser
        la règle décider » était une fuite : les règles d'un même modèle se
        combinent en OU, et ce module en a une pour ``base.group_system``
        (``bf_note_rule_admin``, domaine ``[(1, '=', 1)]``). Pour tout compte
        administrateur, un domaine vide rendait
        donc TOUTES les notes de la base, texte intégral, archivées comprises,
        y compris les notes privées des collègues. Relevé en relecture
        avant la pose.

        🔴 Une note d'autrui qui cesse d'être partagée devient INVISIBLE, donc
        ``since`` ne peut pas annoncer son départ — le domaine ne la voit plus.
        C'est ``_mobile_visible_ids`` qui règle ce cas, et un appelant qui
        demande ``shared`` doit s'en servir pour faire le ménage.
        """
        try:
            limit = max(1, min(int(limit or 50), MAX_LIMIT))
            offset = max(0, int(offset or 0))
        except (TypeError, ValueError):
            raise UserError(_("Invalid limit or offset."))
        domain = self._mobile_scope(shared)
        Note = self
        if since:
            domain.append(("write_date", ">", self._mobile_parse_datetime(since)))
            Note = self.with_context(active_test=False)
        elif archived:
            domain.append(("active", "=", False))
            Note = self.with_context(active_test=False)
        query = (query or "").strip()
        if query:
            domain += ["|", ("name", "ilike", query), ("body", "ilike", query)]
        notes = Note.search(domain, limit=limit, offset=offset,
                            order="pinned desc, write_date desc, id desc")
        # L'heure de POSTGRES : voir `_mobile_next_since`.
        maintenant = self.env.cr.now()
        # Les pièces de tout le lot en une recherche, au lieu d'une par note.
        pieces = notes._mobile_attachments_map()
        return {
            "notes": [note._mobile_payload(pieces=pieces[note.id]) for note in notes],
            "server_time": self._mobile_datetime(maintenant),
            # ⚠️ C'est CECI qu'un client doit garder comme marque d'eau, pas
            # `server_time` : voir `OVERLAP_SECONDS`.
            "next_since": self._mobile_next_since(maintenant),
        }

    @api.model
    def _mobile_visible_ids(self, shared=False):
        """Tous les identifiants que l'appelant peut voir, archivés compris.

        🔴 À quoi cela sert, et pourquoi ce n'est pas un luxe : un client qui
        suit ``since`` ne sait pas qu'une note a DISPARU de sa portée. Une note
        archivée, lui, la voit encore (``active_test=False``), mais une note
        d'autrui qu'on cesse de partager, une note qu'on lui retire, une note
        supprimée ne laissent aucune trace à lire. Sans ce ménage, elles
        resteraient sur le bureau pour toujours.

        Des entiers seulement : la liste complète d'un bloc-notes bien rempli
        tient dans quelques kilooctets, là où relire les notes coûterait cent
        fois plus.
        """
        domain = self._mobile_scope(shared)
        Notes = self.with_context(active_test=False)
        # ⚠️ Plafonné, comme tout le reste de cette API : sans borne, un
        # bloc-notes de 50 000 notes rendrait des centaines de kilooctets à
        # chaque passe. Au-delà, le client est prévenu (`truncated`) et doit
        # s'en remettre à une relecture complète plutôt que de purger sur une
        # liste partielle — purger sur un morceau de liste effacerait du
        # bureau tout ce qui est au-delà du plafond.
        notes = Notes.search(domain, order="id", limit=MAX_IDS + 1)
        tronque = len(notes) > MAX_IDS
        return {
            "ids": notes[:MAX_IDS].ids,
            "truncated": tronque,
            "server_time": self._mobile_datetime(self.env.cr.now()),
        }

    @api.model
    def _mobile_stamp(self, shared=False, since=None):
        """La sonde : « ai-je quelque chose de plus récent que ton ``since`` ? »

        C'est ce qu'un widget de bureau interroge souvent : deux
        recherches sur un index, quelques octets rendus, et le delta n'est tiré
        que si la réponse est oui.

        🔴 Pourquoi c'est le SERVEUR qui répond, et non le client qui compare
        deux empreintes : ``fields.Datetime.now()`` d'Odoo tronque les
        microsecondes, donc deux écritures dans la même seconde portent la MÊME
        ``write_date``. Une empreinte comparée côté client manquerait la
        seconde écriture — sans erreur, sans trace — jusqu'au changement
        suivant. La question posée avec ``since`` n'a pas ce trou, à condition
        de reculer la marque d'eau (voir ``OVERLAP_SECONDS``).

        ``count`` reste rendu parce qu'une SUPPRESSION ne déplace aucune
        ``write_date`` : elle ne se voit qu'au compte.
        """
        domain = self._mobile_scope(shared)
        # ⚠️ Sous les droits de l'appelant, jamais en sudo : la règle du module
        # doit filtrer ce qu'on compte, sinon l'empreinte trahirait l'activité
        # des notes privées d'autrui.
        #
        # ⚠️ Deux recherches plutôt qu'un `read_group` : la clé sous laquelle
        # ce dernier rend un agrégat « write_date:max » a changé d'une version
        # d'Odoo à l'autre, et une empreinte qui se lit `None` sans erreur
        # arrêterait toute synchronisation en silence. Les deux requêtes
        # portent sur des index.
        Notes = self.with_context(active_test=False)
        derniere = Notes.search(domain, order="write_date desc", limit=1)
        changed = True
        if since:
            marque = self._mobile_parse_datetime(since)
            changed = bool(Notes.search_count(
                domain + [("write_date", ">", marque)]))
        # L'heure de POSTGRES : voir `_mobile_next_since`.
        maintenant = self.env.cr.now()
        return {
            "changed": changed,
            "count": Notes.search_count(domain),
            "stamp": self._mobile_datetime(derniere.write_date) if derniere else "",
            "server_time": self._mobile_datetime(maintenant),
            "next_since": self._mobile_next_since(maintenant),
        }

    #: De combien la marque d'eau d'un client recule sous l'heure du serveur.
    #:
    #: 🔴 Deux raisons, et chacune suffirait :
    #:
    #: 1. Une note écrite pendant la seconde même où le serveur a rendu son
    #:    heure serait perdue : ``write_date > since`` est FAUX pour deux
    #:    horodatages égaux, et Odoo les tronque à la seconde.
    #: 2. Une écriture dont la transaction a commencé AVANT notre marque mais
    #:    s'est validée après : un téléversement de pièce jointe, un geste qui
    #:    crée une tâche et ses activités. Sa ``write_date`` est l'heure de
    #:    DÉBUT de sa transaction, donc antérieure à la marque qu'on a rendue
    #:    entre-temps. Deux secondes couvrent les gestes ordinaires ; un geste
    #:    plus long reste un angle mort, et c'est pourquoi un client qui veut
    #:    une certitude relit la liste complète de temps en temps.
    #:
    #: Le prix du recouvrement est de recevoir deux ou trois fois les mêmes
    #: notes après une écriture : elles remplacent les mêmes, le delta est
    #: idempotent. Le prix de son absence est une note qui n'arrive jamais.
    OVERLAP_SECONDS = 2

    @api.model
    def _mobile_next_since(self, maintenant=None):
        """La marque d'eau à renvoyer au client, reculée du recouvrement.

        🔴 L'heure vient du curseur, c'est-à-dire de POSTGRES — la même horloge
        qui pose ``write_date``. ``fields.Datetime.now()`` est l'horloge du
        conteneur Odoo : s'en servir ajoutait un terme de dérive entre deux
        machines au recouvrement, et une dérive de plus de deux secondes rendait
        des écritures invisibles POUR TOUJOURS. Avec l'horloge de Postgres, le
        recouvrement n'a plus qu'un seul travail : la troncature à la seconde.
        """
        maintenant = maintenant or self.env.cr.now()
        return self._mobile_datetime(
            maintenant - timedelta(seconds=self.OVERLAP_SECONDS))

    # ------------------------------------------------------------------
    # Écriture
    # ------------------------------------------------------------------
    @api.model
    def _mobile_clean_text(self, text):
        text = text if isinstance(text, str) else ("" if text is None else str(text))
        if len(text) > MAX_TEXT:
            raise UserError(_("This note is too long for the phone (%s characters at most).", MAX_TEXT))
        return text

    @api.model
    def _mobile_clean_title(self, title):
        title = (title if isinstance(title, str) else "").strip()
        return title[:MAX_TITLE]

    @api.model
    def _mobile_clean_uuid(self, value):
        """Un UUID bien formé, ou un refus. L'identifiant sert de clé
        d'idempotence : une valeur libre ouvrirait la porte aux collisions."""
        try:
            return str(uuid.UUID(str(value or "").strip()))
        except (TypeError, ValueError):
            raise UserError(_("The device identifier is not a valid UUID."))

    @api.model
    def _mobile_clean_gen_excluded(self, value):
        """« Hors Gen » : un vrai booléen JSON, ou un refus. ``bool("false")`` vaut
        ``True`` : une chaîne cocherait la note au lieu de la décocher."""
        if not isinstance(value, bool):
            raise UserError(_("“Not for Gen” must be true or false."))
        return value

    @api.model
    def _mobile_create(self, vals):
        """Crée la note, ou rend celle que ce ``client_uuid`` a déjà créée."""
        vals = vals or {}
        client_uuid = self._mobile_clean_uuid(vals.get("client_uuid"))
        existing = self.with_context(active_test=False).search([
            ("user_id", "=", self.env.uid), ("client_uuid", "=", client_uuid),
        ], limit=1)
        if existing:
            return {"note": existing._mobile_payload(), "created": False,
                    "flattened": False}

        if "text" in vals and "html" in vals:
            raise UserError(_("Send either text or html, not both."))
        aplati = False
        if "html" in vals:
            corps = self._mobile_rich_sanitize(vals["html"])
            # Voir `_mobile_update` : la perte est acceptée, et annoncée.
            aplati = not self._mobile_rich_is_safe(vals["html"], corps)
            text = self._mobile_html_to_text(corps)
        else:
            text = self._mobile_clean_text(vals.get("text"))
            corps = self._mobile_text_to_html(text)
        title = self._mobile_clean_title(vals.get("title"))
        if not text.strip() and not title:
            raise UserError(_("An empty note is not saved."))
        values = {
            "client_uuid": client_uuid,
            "user_id": self.env.uid,
            "body": corps,
            "pinned": bool(vals.get("pinned")),
            "gen_excluded": self._mobile_clean_gen_excluded(vals.get("gen_excluded", False)),
        }
        # Pas de `color` par défaut ici : posé avec `color_hex`, il empêcherait
        # l'index de suivre la couleur libre (champ par défaut : 0).
        values.update(self._mobile_color_values(vals))
        # Sans titre, la PREMIÈRE LIGNE fait le titre, comme dans Keep. Laisser
        # `_compute_name` le faire prendrait les 80 premiers caractères de tout
        # le texte, deuxième ligne comprise.
        values["name"] = title or self._mobile_first_line(text)
        note = self.create(values)
        return {"note": note._mobile_payload(), "created": True, "flattened": aplati}

    @api.model
    def _mobile_first_line(self, text):
        for line in (text or "").splitlines():
            if line.strip():
                # ⚠️ La puce ne va pas dans le TITRE. Mesuré en relecture :
                # une liste de courses sans titre s'appelait « • lait » au
                # kanban, et ce nom repart dans le résumé de l'activité et
                # dans le nom de la tâche qu'on tire de la note.
                return _PUCE.sub("", line.strip())[:80] or _("Untitled note")
        return _("Untitled note")
    @api.model
    def _mobile_color_values(self, vals):
        """Ce qu'écrire pour la couleur reçue : ``color_hex`` (libre) l'emporte
        sur ``color`` (index), qu'envoient encore les versions installées."""
        if "color_hex" in vals:
            raw = vals.get("color_hex")
            if raw in (None, False, ""):
                return {"color_hex": False, "color": 0}
            value = normalize_hex(raw if isinstance(raw, str) else "")
            if not value:
                raise UserError(_("%s is not a hex color.", raw))
            return {"color_hex": value}
        if "color" in vals:
            return {"color": self._mobile_clean_color(vals.get("color"))}
        return {}

    @api.model
    def _mobile_prefs(self):
        """Mise en page, nuanciers et palette : tout ce que l'appareil doit
        savoir pour afficher les notes comme l'usager les a réglées."""
        user = self.env.user
        prefs = {key: user[field] for key, field in PREFS.items()}
        prefs["group_by_color"] = bool(prefs["group_by_color"])
        prefs["swatches"] = self.env["bf.color.swatch"].bf_available()
        prefs["palette"] = self._bf_color_palette()
        return prefs

    @api.model
    def _mobile_set_prefs(self, vals):
        """Écrit les préférences reçues ; une valeur hors liste est refusée."""
        vals = vals or {}
        user = self.env.user
        values = {}
        for key, field in PREFS.items():
            if key not in vals:
                continue
            spec = user._fields[field]
            if spec.type == "boolean":
                values[field] = bool(vals[key])
                continue
            allowed = [code for code, _label in spec._description_selection(self.env)]
            if vals[key] not in allowed:
                raise UserError(_("Unknown value for %(pref)s: %(value)s", pref=key, value=vals[key]))
            values[field] = vals[key]
        if values:
            user.write(values)
        return self._mobile_prefs()

    @api.model
    def _mobile_clean_color(self, value):
        try:
            color = int(value or 0)
        except (TypeError, ValueError):
            return 0
        return color if 0 <= color <= MAX_COLOR else 0

    @api.model
    def _mobile_browse(self, note_id):
        """La note, si l'appelant peut la LIRE ; ``None`` sinon.

        « Introuvable » et « interdite » rendent la même réponse : distinguer les
        deux dirait qu'une note existe là où l'on n'a pas le droit de regarder.
        """
        try:
            note_id = int(note_id)
        except (TypeError, ValueError):
            return None
        note = self.with_context(active_test=False).browse(note_id).exists()
        if not note:
            return None
        # 🔴 Le domaine de portée, et pas seulement le droit de lire. Les
        # règles d'un même modèle se combinent en OU, et ce module en a une
        # pour `base.group_system` (`bf_note_rule_admin`, domaine
        # `[(1, '=', 1)]`) : pour tout compte administrateur
        # — `check_access` seul laissait LIRE et ÉCRIRE n'importe quelle note
        # de la base par son identifiant, et les identifiants sont séquentiels.
        # Les trois lectures de portée avaient été fermées au même lot ;
        # cette quatrième surface, celle qui lit ET écrit une note par son
        # identifiant, ne l'était pas. Trouvé en relecture.
        # ⚠️ Dans CET ordre. `filtered_domain` lit `user_id`, donc il lève
        # AccessError sur une note qu'on n'a pas le droit de lire : mis avant,
        # il faisait rendre 403 là où ce module rend 404 exprès (« introuvable »
        # et « interdite » doivent se ressembler). Quatre essais l'ont dit.
        try:
            note.check_access("read")
        except AccessError:
            return None
        if not note.filtered_domain(self._mobile_scope(shared=True)):
            return None
        return note

    # ------------------------------------------------------------------
    # Pièces jointes
    # ------------------------------------------------------------------
    def _mobile_attachments(self):
        """Les pièces de la note, sous les droits de l'appelant.

        ⚠️ Seulement celles rattachées à la NOTE, pas les pièces des messages
        de son fil : ces dernières portent aussi ``res_model = bf.note``, mais
        elles vivent avec le message, et le chatter les montre déjà là.
        """
        self.ensure_one()
        return self._mobile_attachments_map()[self.id]

    def _mobile_attachments_map(self):
        """Les pièces de TOUTES les notes du lot, en UNE recherche.

        ⚠️ `_mobile_payload` appelait `_mobile_attachments()` note par note :
        une liste de cinquante notes faisait cinquante recherches
        `ir.attachment`, dont la quasi-totalité ne rendait rien. Mesuré à l'essai.
        Les règles d'accès d'`ir.attachment` s'appliquent au lot comme à
        l'unité (elles vérifient le document lié), donc le résultat est le même.

        Rend un dict complet : chaque note du lot a sa clé, même sans pièce.
        """
        Attachment = self.env["ir.attachment"]
        ids_par_note = {note.id: [] for note in self}
        if not ids_par_note:
            return {}
        for piece in Attachment.search([
            ("res_model", "=", self._name), ("res_id", "in", list(ids_par_note)),
            ("res_field", "=", False),
        ], order="id asc"):
            ids_par_note[piece.res_id].append(piece.id)
        # `browse` d'une liste garde SON ordre : c'est ce qui préserve le
        # « id asc » que l'appelant attend. Une union de recordsets ne le
        # promet pas.
        return {nid: Attachment.browse(ids) for nid, ids in ids_par_note.items()}

    @api.model
    def _mobile_attachment_payload(self, attachment):
        return {
            "id": attachment.id,
            "name": attachment.name or "",
            "mimetype": attachment.mimetype or "application/octet-stream",
            "size": attachment.file_size or 0,
            "image": (attachment.mimetype or "").startswith("image/"),
        }

    def _mobile_attach(self, filename, content, mimetype=None):
        """Joint un fichier à la note ; rend la note.

        🔴 Idempotent sur (nom, empreinte) : un téléversement que le réseau a
        coupé APRÈS l'écriture est rejoué par l'app, et la pièce ne doit pas
        s'y retrouver en double.

        ⚠️ Écrire exige le droit d'écriture sur la note : une note partagée en
        lecture seule ne reçoit pas les fichiers d'un collègue. C'est l'ORM qui
        tranche, à la création de la pièce, sous l'usager réel.
        """
        self.ensure_one()
        self.check_access("write")
        if not content:
            raise UserError(_("The file is empty."))
        if len(content) > MAX_ATTACHMENT:
            raise UserError(_("The file is too large (25 MB at most)."))
        nom = (filename or "").replace("/", "_").replace("\\", "_").strip()[:MAX_TITLE] \
            or _("Shared file")
        Attachment = self.env["ir.attachment"]
        empreinte = Attachment._compute_checksum(content)
        deja = self._mobile_attachments().filtered(
            lambda a: a.checksum == empreinte and a.name == nom)
        if deja:
            return {"created": False, "file": self._mobile_attachment_payload(deja[:1]),
                    "note": self._mobile_payload()}
        if len(self._mobile_attachments()) >= MAX_ATTACHMENTS_PER_NOTE:
            raise UserError(_("This note already has %s files.", MAX_ATTACHMENTS_PER_NOTE))
        vals = {
            "name": nom,
            "raw": content,
            "res_model": self._name,
            "res_id": self.id,
        }
        if mimetype and "/" in mimetype and len(mimetype) < 128:
            vals["mimetype"] = mimetype
        piece = Attachment.create(vals)
        return {"created": True, "file": self._mobile_attachment_payload(piece),
                "note": self._mobile_payload()}

    def _mobile_attachment(self, attachment_id):
        """Une pièce de CETTE note, ou ``None`` : l'identifiant seul ne suffit pas."""
        self.ensure_one()
        try:
            attachment_id = int(attachment_id)
        except (TypeError, ValueError):
            return None
        return self._mobile_attachments().filtered(lambda a: a.id == attachment_id)[:1] or None

    def _mobile_detach(self, attachment_id):
        self.ensure_one()
        self.check_access("write")
        piece = self._mobile_attachment(attachment_id)
        if not piece:
            raise UserError(_("This file is no longer on the note."))
        piece.unlink()
        return {"note": self._mobile_payload()}

    def _mobile_update(self, vals):
        """Modifie titre, texte, épingle, « Hors Gen » ou couleur. Refuse sur conflit."""
        self.ensure_one()
        vals = vals or {}
        # 🔴 Le droit d'écrire se vérifie AVANT la forme, comme dans
        # `_mobile_action`. Sans cela, un collègue sans droit d'écriture sur
        # une note partagée dont le corps porte un lien recevait « cette note
        # a une mise en forme que la barre ne sait pas garder, modifiez-la
        # dans Symbifox » : le mauvais motif, et un conseil qui ne mène nulle
        # part. Trouvé en relecture.
        self.check_access("write")
        seen = vals.get("write_date")
        if seen and self._mobile_datetime(self.write_date) != self._mobile_datetime(
                self._mobile_parse_datetime(seen)):
            return {"conflict": True, "note": self._mobile_payload()}
        values = {}
        aplati = False
        # 🔴 Les deux à la fois, c'est le dernier écrit qui gagne — en
        # silence, et pas toujours le même : l'ordre des clés d'un objet JSON
        # n'engage personne. Un client qui envoie les deux a un défaut ; le
        # lui dire coûte moins cher que de deviner lequel il voulait.
        if "text" in vals and "html" in vals:
            raise UserError(_("Send either text or html, not both."))
        if "text" in vals:
            if not self._mobile_html_is_plain(self.body):
                raise UserError(_(
                    "This note has formatting that the phone cannot keep. "
                    "Edit it in Symbifox."))
            values["body"] = self._mobile_text_to_html(self._mobile_clean_text(vals["text"]))
        if "html" in vals:
            if not self._mobile_rich_is_safe(self.body):
                raise UserError(_(
                    "This note has formatting that the toolbar cannot keep. "
                    "Edit it in Symbifox."))
            values["body"] = self._mobile_rich_sanitize(vals["html"])
            # 🔴 Et la forme REÇUE compte autant que celle en base. La garde
            # ci-dessus ne regardait que l'ancien corps : coller un lien depuis
            # un navigateur dans l'éditeur du widget envoie un `<a href>`, que
            # l'assainisseur déplie en silence. L'adresse partait pour de bon
            # et le serveur répondait « rien n'a été perdu ». On accepte — un
            # refus bloquerait un collage légitime sans issue — mais on le DIT.
            aplati = not self._mobile_rich_is_safe(vals["html"], values["body"])
        if "title" in vals:
            title = self._mobile_clean_title(vals["title"])
            # Titre vidé : le serveur le recompose à partir du texte, comme à
            # la création. `_compute_name` saute un nom déjà posé, d'où le
            # calcul fait ici.
            if not title:
                if "text" in vals:
                    source = vals.get("text")
                elif "body" in values:
                    source = self._mobile_html_to_text(values["body"])
                else:
                    source = self._mobile_html_to_text(self.body)
                title = self._mobile_first_line(source)
            values["name"] = title
        if "pinned" in vals:
            values["pinned"] = bool(vals["pinned"])
        if "gen_excluded" in vals:
            values["gen_excluded"] = self._mobile_clean_gen_excluded(vals["gen_excluded"])
        values.update(self._mobile_color_values(vals))
        if values:
            # L'écriture passe par les droits de l'appelant : la règle
            # d'écriture du module (auteur seul) décide, pas ce code.
            self.write(values)
        return {"conflict": False, "flattened": aplati,
                "note": self._mobile_payload()}

    # ------------------------------------------------------------------
    # Gestes rapides
    # ------------------------------------------------------------------
    def _mobile_action(self, action, params=None):
        """Applique un geste rapide et rend la note et une phrase à afficher."""
        self.ensure_one()
        params = params or {}
        if action not in ACTIONS:
            raise UserError(_("Unknown action: %s", action))
        # Chaque geste modifie la note (état, épingle, activités suivies,
        # liens) : la règle d'écriture du module s'applique à tous. La
        # vérifier ici plutôt qu'au milieu d'un geste évite une activité posée
        # sur la fiche d'un autre au nom d'une note qu'on ne peut pas toucher.
        self.check_access("write")
        result = {"url": None}
        if action == "archive":
            self.active = False
            result["message"] = _("Note archived.")
        elif action == "unarchive":
            self.active = True
            result["message"] = _("Note restored.")
        elif action in ("pin", "unpin"):
            self.pinned = action == "pin"
            result["message"] = _("Note pinned.") if self.pinned else _("Note unpinned.")
        elif action in ("gen_exclude", "gen_include"):
            self.gen_excluded = action == "gen_exclude"
            result["message"] = (
                _("Not for Gen: Gen's passes over your notes will leave it alone.")
                if self.gen_excluded else _("Gen's passes over your notes can process it again."))
        elif action == "activity":
            try:
                days = int(params.get("days", 0))
            except (TypeError, ValueError):
                days = -1
            if days not in ACTIVITY_DAYS:
                raise UserError(_("Unsupported reminder delay."))
            activities = self._create_activities_for_links(offset_days=days)
            deadline = fields.Date.context_today(self) + timedelta(days=days)
            result["message"] = _(
                "Reminder set for %(date)s (%(count)s activity).",
                date=format_date(self.env, deadline), count=len(activities))
        elif action == "task":
            task = self._mobile_make_task(params)
            result["message"] = _("Task created: %s", task.display_name)
            result["url"] = "/odoo/project.task/%s" % task.id
            result["task_id"] = task.id
        elif action == "reroute":
            target = self._mobile_reroute(params)
            result["message"] = _("Note linked to %s.", target.display_name)
        result["note"] = self._mobile_payload()
        return result

    def _mobile_make_task(self, params):
        """La tâche, par l'assistant du bureau : mêmes champs, même lien retour."""
        try:
            project_id = int(params.get("project_id") or 0)
        except (TypeError, ValueError):
            project_id = 0
        project = self.env["project.project"].browse(project_id).exists() if project_id else None
        if not project:
            raise UserError(_("Choose a project for the task."))
        project.check_access("read")
        wizard = self.env["bf.note.task.wizard"].create({
            "note_id": self.id,
            "name": self._mobile_clean_title(params.get("name")) or self.name or _("Quick note"),
            "description": self.body or "",
            "project_id": project.id,
            "link_back": True,
            "archive_note": bool(params.get("archive")),
            "open_task": True,
        })
        action = wizard.action_create()
        return self.env["project.task"].browse(action["res_id"])

    def _mobile_reroute(self, params):
        """Rattache la note à une fiche, par l'assistant de re-routage."""
        model = params.get("model")
        try:
            res_id = int(params.get("id") or 0)
        except (TypeError, ValueError):
            res_id = 0
        target = self.env["bf.chatter.target"]._browse_if_allowed(model, res_id)
        if not target:
            raise UserError(_("This record no longer exists, or you do not have access to it."))
        mode = params.get("mode") or "replace"
        if mode not in ("replace", "add"):
            raise UserError(_("Unknown mode: %s", mode))
        wizard = self.env["bf.note.reroute"].create({
            "note_ids": [(6, 0, self.ids)],
            "target_reference": "%s,%s" % (target._name, target.id),
            "mode": mode,
        })
        wizard.action_confirm()
        if wizard.result_text and "ERR " in wizard.result_text:
            raise UserError(wizard.result_text.split("\n", 1)[0])
        return target

    # ------------------------------------------------------------------
    # Listes d'appui : projets et fiches
    # ------------------------------------------------------------------
    @api.model
    def _mobile_projects(self, query=None, limit=8):
        """Projets pour « En faire une tâche ».

        Sans recherche : ceux où l'usager a eu une tâche récemment, puis les
        favoris. Une liste alphabétique de centaines de projets n'aide personne au pouce.
        """
        Project = self.env["project.project"]
        query = (query or "").strip()
        if query:
            projects = Project.search([("name", "ilike", query)], limit=limit)
        else:
            tasks = self.env["project.task"].search_read(
                [("user_ids", "in", self.env.uid), ("project_id", "!=", False)],
                ["project_id"], order="write_date desc", limit=60)
            seen, ids = set(), []
            for task in tasks:
                pid = task["project_id"][0]
                if pid not in seen:
                    seen.add(pid)
                    ids.append(pid)
                if len(ids) >= limit:
                    break
            projects = Project.browse(ids).exists()
            if len(projects) < limit and "favorite_user_ids" in Project._fields:
                projects |= Project.search([
                    ("favorite_user_ids", "in", self.env.uid),
                    ("id", "not in", projects.ids),
                ], limit=limit - len(projects))
        return [{
            "id": project.id,
            "name": project.name,
            "client": project.partner_id.display_name or "",
        } for project in projects]

    @api.model
    def _mobile_targets(self, query=None):
        return self.env["bf.chatter.target"].search_targets(query, limit=5)
