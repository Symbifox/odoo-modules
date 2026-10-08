# -*- coding: utf-8 -*-
"""Lire un fichier BPMN 2.0 et en faire une carte.

C'est le sens du retour, et il n'est pas symétrique de l'aller. Un `.bpmn`
porte des coordonnées absolues ; le modèle, lui, raisonne en grille (colonne =
avancement, rangée = décalage dans le couloir). La lecture reconstitue donc la
grille depuis la mise en page, ce qui suppose une mise en page régulière.

**La garantie tenue est celle-ci** : relire un fichier que ce module a produit
reconstitue exactement les mêmes enregistrements. Un fichier venu d'un autre
éditeur passe aussi, mais sa grille est déduite au mieux — le résultat est à
relire avant d'être validé.

Deux choses valent d'être sues avant de toucher à ce fichier.

**Le préfixe de niveau se retrouve, il ne se devine pas.** L'export préfixe
tout identifiant d'un niveau par le `bpmn_id` de celui-ci : `d3_process`,
`d3_plane`, `d3_lane_f`, `d3_t1`. Le retirer par une expression régulière qui
suppose « d suivi d'un nombre » marchait tant qu'on ne faisait que créer des
cartes neuves ; dès qu'il s'agit de reconnaître un niveau déjà stocké, il faut
le préfixe exact, et il est écrit dans le fichier.

**Les coordonnées lues sont relatives, et c'est voulu.** `geometrie` cale le
bord gauche du pool sur le nœud le plus à gauche, et la hauteur d'un couloir
sur son propre contenu : un niveau entier décalé de trois colonnes se dessine
exactement comme le même niveau collé à zéro. Une position absolue n'a donc
aucun sens hors de sa page. La lecture rend la forme canonique — colonne la
plus à gauche à 0, rangée la plus haute de chaque couloir à 0 — et le calage
est recalculé depuis les données plutôt que recopié de `geometrie`, pour que
les deux ne puissent pas diverger en silence. C'est `fusion` qui reporte
ensuite cette forme relative sur le repère de la carte visée.

**Rien ne s'écarte en silence.** Un élément que la lecture ne peut pas
reprendre — sans forme dans la partie DI, d'un type BPMN que le module ne
trace pas, dessiné hors du processus lu, ou un lien dont une extrémité est
dans ce cas — est consigné sous `ecartes`, avec son motif, et l'appelant le
dit. Un `continue` muet rendait
« aucun écart » sur un fichier qui portait pourtant du neuf. Or l'absence de
DI est valide en BPMN 2.0 : la partie graphique y est facultative.
⚠️ Reste muet : le plan de descente qu'un éditeur écrit à part pour un
sous-processus replié (un `BPMNPlane` posé sur le sous-processus). Ses liens
intérieurs sont nommés, ses étapes non.
"""
import base64
import math
import re
import xml.etree.ElementTree as ET

from markupsafe import Markup

from odoo import _, fields, models
from odoo.exceptions import UserError

from .erreurs import refus_lisible

from ..generateur import geometrie as geo
from ..generateur.bpmn import ELEMENT
from .structure import KINDS

B = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"
DI = "{http://www.omg.org/spec/BPMN/20100524/DI}"
DC = "{http://www.omg.org/spec/DD/20100524/DC}"

# l'inverse de la table d'export : balise BPMN + définition d'événement -> genre
VERS_GENRE = {}
for genre, (balise, evdef) in ELEMENT.items():
    VERS_GENRE[(balise, evdef)] = genre

AUTRES_GENRES = {"textAnnotation": "note", "dataStoreReference": "store"}

# Ce qu'un éditeur BPMN dessine : activités, événements, portes, artefacts et
# données. Un élément de ces familles que la lecture laisse de côté est une
# perte, et elle se dit. Le reste d'un <bpmn:process> — documentation,
# extensions, dataObject, ioSpecification — n'a rien à tracer.
SE_DESSINE = ("task", "subProcess", "callActivity", "transaction",
              "textAnnotation", "group", "dataObjectReference",
              "dataStoreReference")
SE_DESSINE_SUFFIXES = ("Task", "Event", "Gateway", "SubProcess")


class BfProcessLecture(models.AbstractModel):
    """Lecture d'un `.bpmn` vers la forme d'échange.

    Modèle abstrait plutôt que fonctions libres : l'import et la fusion en ont
    besoin tous les deux, et un `AbstractModel` se surcharge par héritage
    comme le reste du module.
    """

    _name = "bf.process.lecture"
    _description = "Lecture d'un BPMN 2.0"

    # ------------------------------------------------------------------ entrée
    def _lire_fichier(self, contenu, grilles=None):
        """Rend les niveaux d'un `.bpmn`, sous la forme d'échange.

        `grilles` impose le pas d'un niveau déjà connu, par `bpmn_id` — c'est
        ce qui évite de redéduire une grille qu'on connaît déjà, et donc de la
        déduire faux sur une carte réarrangée à la main.
        """
        try:
            racine = ET.fromstring(contenu)
        except ET.ParseError as e:
            raise UserError(_("Ce fichier n'est pas du XML lisible : %s") % e)
        if not racine.tag.endswith("definitions"):
            raise UserError(_("La racine attendue est <bpmn:definitions>."))
        diagrammes = self._lire(racine, grilles or {})
        if not diagrammes:
            raise UserError(_(
                "Aucun diagramme exploitable : il faut la partie DI (les "
                "coordonnées), pas seulement la sémantique."))
        return diagrammes

    def _lire(self, racine, grilles):
        procs = {p.get("id"): p for p in racine.iter(B + "process")}
        # Les collaborations s'indexent une fois. Les chercher par un prédicat
        # ElementPath composé en f-string faisait entrer un identifiant du
        # FICHIER dans la syntaxe de la requête : une simple apostrophe dans un
        # identifiant — qu'un éditeur tiers peut produire sans malice — cassait
        # le prédicat et sortait en trace de 500.
        colabs = {c.get("id"): c for c in racine.iter(B + "collaboration")}
        diagrammes = []
        for plan in racine.iter(DI + "BPMNPlane"):
            d = self._lire_plan(racine, plan, procs, colabs, grilles)
            if d:
                diagrammes.append(d)
        return diagrammes

    # -------------------------------------------------------------- identités
    @staticmethod
    def _prefixe(plan, proc):
        """Le préfixe d'identifiant du niveau, tel que l'export l'a posé.

        Rend `None` sur un fichier qui ne suit pas notre convention : la suite
        retombe alors sur la forme historique, qui devine.
        """
        for ident, suffixe in ((proc.get("id"), "_process"),
                               (plan.get("id"), "_plane")):
            if ident and ident.endswith(suffixe) and len(ident) > len(suffixe):
                return ident[:-len(suffixe)]
        return None

    @staticmethod
    def _code(ident, prefixe=None):
        """Retire le préfixe de niveau que l'export ajoute (`d3_t1` → `t1`).

        Avec `prefixe`, le retrait est exact. Sans lui — fichier d'un autre
        éditeur, qui n'a aucune raison de suivre notre convention — on retombe
        sur la forme historique, qui suppose `d` suivi d'un nombre.
        """
        if not ident:
            return ident
        if prefixe and ident.startswith(prefixe + "_"):
            return ident[len(prefixe) + 1:]
        return re.sub(r"^d\d+_", "", ident)

    @classmethod
    def _code_membre(cls, ident, prefixe, famille):
        """Le code d'un couloir ou d'un participant externe.

        L'export les nomme `{niveau}_lane_{code}` et `{niveau}_pool_{code}` :
        sans retirer aussi ce second morceau, un couloir `f` revenait sous le
        code `lane_f`, et toute comparaison avec la carte stockée le lisait
        comme un couloir retiré plus un couloir ajouté.
        """
        # `id` est facultatif dans le schéma BPMN : un participant ou un couloir
        # sans identifiant se nomme, il ne fait pas tomber la lecture.
        code = cls._code(ident, prefixe) or ""
        tete = famille + "_"
        return code[len(tete):] if code.startswith(tete) and len(code) > len(tete) else code

    # ------------------------------------------------------------------ bornes
    @staticmethod
    def _bornes(forme):
        """Les bornes d'une forme, ou un refus qui nomme la forme fautive.

        Un `.bpmn` n'arrive pas forcément de chez nous, et un éditeur tiers
        n'a pas à être malveillant pour écrire salement. Une forme sans
        `dc:Bounds`, un attribut absent ou une valeur non numérique tombaient
        en trace de 500 — précisément ce que le décorateur de refus lisible
        existe pour éviter.

        ⚠️ Le cas qui compte vraiment est le dernier. `float("nan")` et
        `float("inf")` se lisent SANS lever quoi que ce soit, traversent toute
        l'arithmétique de la lecture, et s'écrivaient tels quels dans la
        colonne d'un nœud. La carte devenait alors définitivement intraçable :
        chaque tracé, chaque export et chaque PDF ultérieur rejouait la même
        opération. Un plantage se voit ; celui-là ne se voyait pas.
        """
        quoi = forme.get("bpmnElement") or forme.get("id") or "?"
        b = forme.find(DC + "Bounds")
        if b is None:
            raise UserError(_(
                "La forme « %s » n'a pas de <dc:Bounds> : ce fichier porte sa "
                "sémantique, pas ses coordonnées.") % quoi)
        valeurs = []
        for attr in ("x", "y", "width", "height"):
            brut = b.get(attr)
            if brut is None:
                raise UserError(_(
                    "Les bornes de la forme « %(quoi)s » n'ont pas d'attribut "
                    "%(attr)s.", quoi=quoi, attr=attr))
            try:
                valeur = float(brut)
            except (TypeError, ValueError):
                raise UserError(_(
                    "La borne %(attr)s de la forme « %(quoi)s » n'est pas un "
                    "nombre : %(brut)s.", quoi=quoi, attr=attr, brut=brut))
            if not math.isfinite(valeur):
                raise UserError(_(
                    "La borne %(attr)s de la forme « %(quoi)s » vaut "
                    "%(brut)s. Une valeur pareille ne lève aucune erreur en se "
                    "propageant : elle contaminerait la carte en silence.",
                    quoi=quoi, attr=attr, brut=brut))
            valeurs.append(valeur)
        return tuple(valeurs)

    def _lire_plan(self, racine, plan, procs, colabs, grilles):
        formes = {el.get("bpmnElement"): el for el in plan
                  if el.tag == DI + "BPMNShape"}

        colab = colabs.get(plan.get("bpmnElement"))
        if colab is None:
            return None
        participants = colab.findall(B + "participant")
        principal = next((p for p in participants if p.get("processRef")), None)
        if principal is None:
            return None
        proc = procs.get(principal.get("processRef"))
        if proc is None or principal.get("id") not in formes:
            return None

        prefixe = self._prefixe(plan, proc)
        x_pool, y_pool = self._bornes(formes[principal.get("id")])[:2]
        nom_diagramme = self._nom_diagramme(racine, plan)
        libelle, titre = self._scinder(nom_diagramme)
        d = {"title": titre, "nom_diagramme": nom_diagramme,
             "pool": principal.get("name") or "",
             "lanes": [], "ext": [], "nodes": [], "flows": [], "msgs": []}
        if libelle:
            d["level"] = libelle
        if prefixe:
            # ce qui permettra de reconnaître ce niveau s'il existe déjà
            d["code"] = prefixe
            d["bpmn_id"] = prefixe

        # `code_de` traduit un identifiant du fichier en code du modèle. Le
        # construire une fois évite d'avoir à redeviner, à chaque référence,
        # de quelle famille elle relève.
        code_de = {}

        # Ce que la lecture laisse de côté, et pourquoi. Chaque `continue` qui
        # suit en alimente la liste, au lieu de perdre l'élément sans un mot.
        # `vus` retient ce qui a été lu OU nommé : la passe finale nomme toute
        # forme dessinée qui n'y est pas.
        ecartes = []
        vus = {principal.get("id")}
        tous = {el.get("id"): el for el in racine.iter() if el.get("id")}

        # pools externes : au-dessus ou en dessous du pool principal
        ids_pools = set()
        for p in participants:
            if p is principal:
                continue
            vus.add(p.get("id"))
            if p.get("id") not in formes:
                ecartes.append({
                    "famille": "pool", "motif": "forme",
                    "id": self._code_membre(p.get("id"), prefixe, "pool"),
                    "nom": p.get("name") or ""})
                continue
            ex, ey, ew, eh = self._bornes(formes[p.get("id")])
            code = self._code_membre(p.get("id"), prefixe, "pool")
            code_de[p.get("id")] = code
            ids_pools.add(p.get("id"))
            d["ext"].append({"id": code, "name": p.get("name") or "",
                             "pos": "top" if ey < y_pool else "bottom"})
            d["ext_header"] = eh

        # couloirs, dans l'ordre vertical
        bandes = []
        for lane in proc.iter(B + "lane"):
            vus.add(lane.get("id"))
            if lane.get("id") not in formes:
                ecartes.append({
                    "famille": "couloir", "motif": "forme",
                    "id": self._code_membre(lane.get("id"), prefixe, "lane"),
                    "nom": lane.get("name") or ""})
                continue
            lx, ly, lw, lh = self._bornes(formes[lane.get("id")])
            bandes.append((ly, lh, lane))
        bandes.sort(key=lambda b: b[0])
        for _y, _h, lane in bandes:
            code = self._code_membre(lane.get("id"), prefixe, "lane")
            code_de[lane.get("id")] = code
            d["lanes"].append({"id": code, "name": lane.get("name") or ""})
        couloir_de = {}
        for _y, _h, lane in bandes:
            for ref in lane.findall(B + "flowNodeRef"):
                couloir_de[(ref.text or "").strip()] = code_de[lane.get("id")]
        # Un couloir écarté faute de forme garde ses membres : le fichier les y
        # déclare encore, et la carte connaît ce couloir. Sans ça, ses nœuds
        # retombaient dans le premier couloir, et la fusion proposait de les
        # sortir du leur — à « Appliquer » par défaut.
        sans_forme = [lane for lane in proc.iter(B + "lane")
                      if lane.get("id") not in formes]
        for lane in sans_forme:
            code = self._code_membre(lane.get("id"), prefixe, "lane")
            for ref in lane.findall(B + "flowNodeRef"):
                couloir_de.setdefault((ref.text or "").strip(), code)

        # --- nœuds : bornes d'abord, grille ensuite, coordonnées enfin -------
        bruts = []
        for el in proc:
            balise = el.tag.replace(B, "")
            if balise in ("laneSet", "sequenceFlow", "association"):
                continue
            ident = el.get("id")
            vus.add(ident)
            evdef = next((c.tag.replace(B, "") for c in el
                          if c.tag.replace(B, "").endswith("EventDefinition")), None)
            genre = VERS_GENRE.get((balise, evdef)) or AUTRES_GENRES.get(balise)
            if ident not in formes:
                # Sans forme, aucune position à reprendre. Ce n'est une perte
                # que pour ce qui se dessine : la documentation ou les
                # extensions d'un processus n'en ont jamais.
                if genre or self._se_dessine(balise):
                    ecartes.append({
                        "famille": "noeud", "motif": "forme",
                        "id": self._code(ident, prefixe), "genre": genre,
                        "balise": balise, "nom": self._libelle(el, balise)})
                continue
            if genre is None:
                # Dessiné dans le fichier, donc vu par l'usager dans son
                # éditeur : le taire ferait croire qu'il a été repris.
                ecartes.append({
                    "famille": "noeud", "motif": "type",
                    "id": self._code(ident, prefixe), "genre": None,
                    "balise": balise, "evdef": evdef,
                    "nom": self._libelle(el, balise)})
                continue
            code = self._code(ident, prefixe)
            code_de[ident] = code
            bruts.append((el, ident, code, genre, balise, self._bornes(formes[ident])))

        impose = grilles.get(prefixe) if prefixe else None
        if impose:
            col_w, row_h, lane_pad = impose
        else:
            col_w, row_h, lane_pad = self._grille(
                [b[5] for b in bruts], bandes)
        d.update(col_w=col_w, row_h=row_h, lane_pad=lane_pad)

        # (code, bord haut, bord bas) de chaque couloir, dans l'ordre vertical
        pistes = [(code_de[lane.get("id")], y, y + h) for y, h, lane in bandes]
        for el, ident, code, genre, balise, (x, y, w, h) in bruts:
            cx, cy = x + w / 2, y + h / 2
            noeud = {"id": code, "kind": genre, "name": self._libelle(el, balise),
                     "col": (cx - x_pool - geo.POOL_HDR_W) / col_w, "row": 0.0}
            couloir, haut = self._couloir(ident, cy, couloir_de, pistes, y_pool)
            if couloir:
                noeud["lane"] = couloir
            elif sans_forme:
                # Ni déclaré dans un couloir ni dans une bande, alors qu'un
                # couloir a perdu sa forme : une annotation, typiquement. Le
                # fichier ne dit pas où elle est, et le deviner ferait proposer
                # un déplacement que personne n'a fait.
                noeud["couloir_inconnu"] = True
            noeud["row"] = (cy - haut - lane_pad) / row_h
            # Une surcharge de taille ne se conserve que si la forme observée
            # s'écarte de la taille naturelle. Comparer la forme observée à
            # elle-même — en injectant `w` et `h` dans le calcul du naturel —
            # rendait toujours l'égalité, donc n'enregistrait JAMAIS de
            # surcharge : une annotation élargie à la main revenait à la
            # largeur par défaut, en silence.
            naturel = geo.node_box(noeud)
            if abs(naturel[0] - w) > 0.5:
                noeud["w"] = w
            if abs(naturel[1] - h) > 0.5:
                noeud["h"] = h
            d["nodes"].append(noeud)
        self._canoniser(d)

        # Un lien ne tient que si ses deux extrémités ont été lues. Garder un
        # lien vers un nœud écarté faisait planter l'import (aucun nœud à
        # relier) et proposait à la fusion un ajout voué au refus.
        lus = {b[1] for b in bruts}

        def nom_de(ident):
            el = tous.get(ident)
            return self._libelle(el, el.tag.replace(B, "")) if el is not None else ""

        def code_noeud(ident):
            return code_de.get(ident) or self._code(ident, prefixe) or ""

        for el in list(proc.iter(B + "sequenceFlow")) + list(proc.iter(B + "association")):
            src, tgt = el.get("sourceRef"), el.get("targetRef")
            if src not in lus or tgt not in lus:
                ecartes.append({
                    "famille": "flux", "motif": "lien",
                    "src": code_noeud(src), "tgt": code_noeud(tgt),
                    "de": nom_de(src) or code_noeud(src),
                    "vers": nom_de(tgt) or code_noeud(tgt),
                    "nom": el.get("name") or ""})
                continue
            d["flows"].append({
                "src": code_de[src],
                "tgt": code_de[tgt],
                "label": el.get("name") or "",
                **({"r": "assoc"} if el.tag == B + "association" else {}),
            })
        # Le sens se lit sur les participants déclarés, lus ou non : un
        # participant écarté faute de forme ne doit pas inverser les rôles,
        # sinon le message écarté ne se reconnaît plus dans la carte.
        externes = {p.get("id") for p in participants if p is not principal}
        for el in colab.findall(B + "messageFlow"):
            src, tgt = el.get("sourceRef"), el.get("targetRef")
            entrant = src in externes
            noeud, pool = (tgt, src) if entrant else (src, tgt)
            if noeud not in lus or pool not in ids_pools:
                ecartes.append({
                    "famille": "message", "motif": "lien",
                    "node": code_noeud(noeud),
                    "pool": code_de.get(pool)
                    or self._code_membre(pool, prefixe, "pool") or "",
                    "nom": el.get("name") or ""})
                continue
            d["msgs"].append({
                "node": code_de[noeud],
                "pool": code_de[pool],
                "dir": "in" if entrant else "out",
                "label": el.get("name") or "",
            })
        # Dessiné, mais hors de ce que la lecture parcourt : l'intérieur d'un
        # sous-processus déplié, le processus d'un autre participant, une
        # annotation posée au niveau de la collaboration. La forme est là, donc
        # l'usager le voit dans son éditeur ; le taire ferait croire qu'il est
        # revenu dans la carte.
        for ident in formes:
            if ident is None or ident in vus:
                continue
            el = tous.get(ident)
            balise = el.tag.replace(B, "") if el is not None else "?"
            evdef = next((c.tag.replace(B, "") for c in el
                          if c.tag.replace(B, "").endswith("EventDefinition")),
                         None) if el is not None else None
            ecartes.append({
                "famille": "noeud", "motif": "hors",
                "id": self._code(ident, prefixe) or "",
                "genre": VERS_GENRE.get((balise, evdef)) or AUTRES_GENRES.get(balise),
                "balise": balise,
                "nom": self._libelle(el, balise) if el is not None else ""})
        # Posé seulement quand il y a quelque chose à dire : la forme
        # d'échange d'un fichier complet reste exactement ce qu'elle était.
        if ecartes:
            d["ecartes"] = ecartes
        return d

    @staticmethod
    def _se_dessine(balise):
        return balise in SE_DESSINE or balise.endswith(SE_DESSINE_SUFFIXES)

    @staticmethod
    def _couloir(ident, cy, couloir_de, pistes, y_defaut):
        """Le couloir d'un nœud, et le bord haut dont sa rangée se compte.

        Un couloir BPMN déclare ses membres par `flowNodeRef`, et cette liste
        ne porte QUE des nœuds de flux : une annotation ou une réserve de
        données n'y figure pas, alors qu'elle appartient bel et bien à un
        couloir dans notre modèle. S'en tenir aux `flowNodeRef` faisait donc
        remonter chaque annotation dans le pool, et sa rangée se comptait
        depuis le haut de la page au lieu du haut de sa bande. La bande qui
        contient le centre de la forme tranche le cas, exactement comme
        l'éditeur le fait pour un nœud lâché à la souris.
        """
        code = couloir_de.get(ident)
        if code:
            for c, haut, _bas in pistes:
                if c == code:
                    return c, haut
            # Couloir déclaré mais sans forme : le nœud y reste, et sa rangée se
            # compte depuis le haut du pool. `_canoniser` ramène chaque couloir
            # à sa propre rangée la plus haute, donc cet écart constant tombe.
            return code, y_defaut
        for c, haut, bas in pistes:
            if haut <= cy <= bas:
                return c, haut
        return None, y_defaut

    @staticmethod
    def _canoniser(d):
        """Ramène les coordonnées lues à leur forme canonique.

        `geometrie` cale le bord gauche du pool sur le nœud le plus à gauche,
        et chaque couloir sur sa propre rangée la plus haute : l'inversion rend
        donc des valeurs décalées d'une constante par page et d'une constante
        par couloir. Ces constantes ne portent aucune information — un niveau
        décalé en bloc se dessine à l'identique — et se retrouvent dans les
        données elles-mêmes, sans recopier de `geometrie` un facteur qui
        pourrait changer de son côté sans prévenir.
        """
        if not d["nodes"]:
            return
        gauche = min(n["col"] for n in d["nodes"])
        hauts = {}
        for n in d["nodes"]:
            cle = n.get("lane")
            hauts[cle] = min(hauts.get(cle, n["row"]), n["row"])
        for n in d["nodes"]:
            n["col"] = round(n["col"] - gauche, 3)
            n["row"] = round(n["row"] - hauts[n.get("lane")], 3)

    @staticmethod
    def _grille(bornes, bandes):
        """Retrouve le pas de la grille depuis les écarts observés.

        Dernier recours : n'est utilisé que sur un fichier dont aucun niveau
        n'est déjà connu. Le pas déduit vaut ce que vaut la régularité de la
        mise en page, et une carte réarrangée à la main peut le fausser.
        """
        xs = sorted({round(x + w / 2, 1) for x, _y, w, _h in bornes})
        ecarts = [round(b - a, 1) for a, b in zip(xs, xs[1:]) if b - a > 40]
        col_w = min(ecarts) if ecarts else 168.0
        lane_pad = min(50.0, bandes[0][1] / 2) if bandes else 50.0
        return col_w, 100.0, lane_pad

    @staticmethod
    def _nom_diagramme(racine, plan):
        """Le nom du diagramme, tel quel.

        L'export y écrit « Niveau 2 — Traiter le dossier » quand le niveau
        porte un libellé, et le titre seul sinon. Ce nom brut est ce qui se
        compare sans ambiguïté d'un côté et de l'autre ; le découper, lui,
        relève de la devinette et n'a lieu qu'au moment de créer une page.
        """
        for diag in racine.iter(DI + "BPMNDiagram"):
            if diag.find(DI + "BPMNPlane") is plan:
                return diag.get("name") or ""
        return ""

    @staticmethod
    def _scinder(nom):
        """Sépare « Niveau 2 — Traiter le dossier » en libellé et titre.

        Découper sur le DERNIER séparateur décapitait tout titre qui en
        contient un : « Service à la clientèle — vue d'ensemble » revenait sous le
        seul titre « vue d'ensemble ». Le libellé de niveau est en tête, et
        seulement s'il ressemble à un libellé de niveau — sinon le nom entier
        est le titre, ponctuation comprise.
        """
        morceaux = (nom or "").split(" — ", 1)
        if len(morceaux) == 2 and re.match(
                r"^(niveau|level|page|n[ivo]*\.?)\s*\d+$",
                morceaux[0].strip(), re.IGNORECASE):
            return morceaux[0].strip(), morceaux[1].strip()
        return None, (nom or "").strip() or "Niveau importé"

    @staticmethod
    def _libelle(el, balise):
        if balise == "textAnnotation":
            t = el.find(B + "text")
            return (t.text or "") if t is not None else ""
        return el.get("name") or ""

    def _dire_ecartes(self, diagrammes, consequence):
        """Ce que la lecture a laissé de côté, nommé un par un.

        Rend un fragment vide quand rien n'a été écarté. Les libellés viennent
        du fichier : chacun passe par `Markup %`, qui l'échappe.
        """
        genres = dict(KINDS)
        motifs = {
            "forme": _("aucune forme dans la partie graphique (DI), donc "
                       "aucune position à reprendre"),
            "hors": _("dessiné hors du processus que le module lit (dans un "
                      "sous-processus déplié, le processus d'un autre "
                      "participant ou la collaboration)"),
        }
        liens = {
            "flux": _("une de ses extrémités n'est pas une étape lue"),
            "message": _("il ne relie pas une étape lue à un participant "
                         "externe lu"),
        }
        items = []
        for d in diagrammes:
            for e in d.get("ecartes") or []:
                famille = e["famille"]
                if famille == "noeud":
                    # le genre quand le module le connaît ; sinon le motif dit
                    # déjà de quel type BPMN il s'agit
                    quoi = (genres[e["genre"]].lower() if e.get("genre")
                            else _("élément"))
                elif famille == "couloir":
                    quoi = _("couloir")
                elif famille == "pool":
                    quoi = _("participant")
                elif famille == "message":
                    quoi = _("message")
                else:
                    quoi = None
                if quoi is None:
                    objet = _("lien de « %(de)s » vers « %(vers)s »",
                              de=e.get("de") or "?", vers=e.get("vers") or "?")
                elif e.get("nom"):
                    objet = "%s « %s »" % (quoi, e["nom"])
                else:
                    objet = "%s %s" % (quoi, e.get("id") or "")
                if e["motif"] == "type":
                    # la définition d'événement fait le type : un `endEvent`
                    # simple se trace, un `endEvent` de terminaison non
                    balise = e.get("balise") or "?"
                    if e.get("evdef"):
                        balise = "%s + %s" % (balise, e["evdef"])
                    motif = _("type BPMN « %s » que le module ne trace pas") \
                        % balise
                elif e["motif"] == "lien":
                    motif = liens.get(famille, e["motif"])
                else:
                    motif = motifs.get(e["motif"], e["motif"])
                items.append(Markup("<li>%s — %s : %s</li>") % (
                    d.get("title") or "?", objet.strip(), motif))
        if not items:
            return Markup("")
        return Markup("<p><b>%s</b> %s</p><ul>%s</ul>") % (
            _("%d élément(s) du fichier n'ont pas été lus.") % len(items),
            consequence, Markup("").join(items))


class BfProcessImportWizard(models.TransientModel):
    _name = "bf.process.import.wizard"
    _inherit = ["bf.process.lecture"]
    _description = "Importer un BPMN 2.0"

    name = fields.Char(string="Nom de la cartographie", required=True)
    version = fields.Char(string="Version", default="1.0", required=True)
    fichier = fields.Binary(string="Fichier .bpmn", required=True)
    nom_fichier = fields.Char(string="Nom du fichier")
    partner_id = fields.Many2one("res.partner", string="Client")
    project_id = fields.Many2one("project.project", string="Projet")
    rapport = fields.Text(string="Ce qui a été lu", readonly=True)

    @refus_lisible
    def action_importer(self):
        self.ensure_one()
        diagrammes = self._lire_fichier(base64.b64decode(self.fichier or b""))
        processus = self.env["bf.process"].create({
            "name": self.name,
            "version": self.version,
            "pool_name": diagrammes[0]["pool"],
            "partner_id": self.partner_id.id,
            "project_id": self.project_id.id,
            "source": _("Importé du fichier BPMN 2.0 « %s ».") % (
                self.nom_fichier or "sans nom"),
        })
        processus._charger_niveaux(diagrammes)
        # `nom_fichier` est fourni par l'appelant, donc échappé comme le reste :
        # la sanitisation de `mail.message` est un filet, pas une raison de
        # composer du HTML par concaténation.
        corps = Markup(_(
            "Import de <b>%s</b> : %s niveau(x), %s nœud(s).")) % (
            self.nom_fichier or "?", len(diagrammes), processus.node_count)
        corps += self._dire_ecartes(diagrammes, _(
            "Ils ne sont pas dans la carte : posez-les ici à la main, ou "
            "corrigez-les dans l'éditeur d'origine avant de réimporter."))
        processus.message_post(body=corps)
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.process",
            "res_id": processus.id,
            "view_mode": "form",
            "target": "current",
        }
