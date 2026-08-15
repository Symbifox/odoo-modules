# -*- coding: utf-8 -*-
"""Importer un fichier BPMN 2.0 dans une cartographie.

C'est le sens du retour, et il n'est pas symétrique de l'aller. Un `.bpmn`
porte des coordonnées absolues ; le modèle, lui, raisonne en grille (colonne =
avancement, rangée = décalage dans le couloir). L'import reconstitue donc la
grille depuis la mise en page, ce qui suppose une mise en page régulière.

**La garantie tenue est celle-ci** : réimporter un fichier que ce module a
produit reconstitue exactement les mêmes enregistrements. Un fichier venu d'un
autre éditeur passe aussi, mais sa grille est déduite au mieux — le résultat
est à relire avant d'être validé.
"""
import base64
import re
import xml.etree.ElementTree as ET

from odoo import _, fields, models
from odoo.exceptions import UserError

from .erreurs import refus_lisible

from ..generateur import geometrie as geo
from ..generateur.bpmn import ELEMENT

B = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"
DI = "{http://www.omg.org/spec/BPMN/20100524/DI}"
DC = "{http://www.omg.org/spec/DD/20100524/DC}"

# l'inverse de la table d'export : balise BPMN + définition d'événement -> genre
VERS_GENRE = {}
for genre, (balise, evdef) in ELEMENT.items():
    VERS_GENRE[(balise, evdef)] = genre


class BfProcessImportWizard(models.TransientModel):
    _name = "bf.process.import.wizard"
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
        xml = base64.b64decode(self.fichier or b"")
        try:
            racine = ET.fromstring(xml)
        except ET.ParseError as e:
            raise UserError(_("Ce fichier n'est pas du XML lisible : %s") % e)
        if not racine.tag.endswith("definitions"):
            raise UserError(_("La racine attendue est <bpmn:definitions>."))

        diagrammes = self._lire(racine)
        if not diagrammes:
            raise UserError(_(
                "Aucun diagramme exploitable : il faut la partie DI (les "
                "coordonnées), pas seulement la sémantique."))
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
        processus.message_post(body=_(
            "Import de <b>%s</b> : %d niveau(x), %d nœud(s).") % (
            self.nom_fichier or "?", len(diagrammes), processus.node_count))
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.process",
            "res_id": processus.id,
            "view_mode": "form",
            "target": "current",
        }

    # ------------------------------------------------------------------ lecture
    def _lire(self, racine):
        procs = {p.get("id"): p for p in racine.iter(B + "process")}
        diagrammes = []
        for plan in racine.iter(DI + "BPMNPlane"):
            d = self._lire_plan(racine, plan, procs)
            if d:
                diagrammes.append(d)
        return diagrammes

    @staticmethod
    def _bornes(forme):
        b = forme.find(DC + "Bounds")
        return (float(b.get("x")), float(b.get("y")),
                float(b.get("width")), float(b.get("height")))

    def _lire_plan(self, racine, plan, procs):
        formes, aretes = {}, {}
        for el in plan:
            if el.tag == DI + "BPMNShape":
                formes[el.get("bpmnElement")] = el
            elif el.tag == DI + "BPMNEdge":
                aretes[el.get("bpmnElement")] = el

        colab = racine.find(f".//{B}collaboration[@id='{plan.get('bpmnElement')}']")
        if colab is None:
            return None
        participants = colab.findall(B + "participant")
        principal = next((p for p in participants if p.get("processRef")), None)
        if principal is None:
            return None
        proc = procs.get(principal.get("processRef"))
        if proc is None:
            return None

        px, py, pw, ph = self._bornes(formes[principal.get("id")])
        d = {"title": self._titre(racine, plan), "pool": principal.get("name") or "",
             "lanes": [], "ext": [], "nodes": [], "flows": [], "msgs": []}

        # pools externes : au-dessus ou en dessous du pool principal
        for p in participants:
            if p is principal:
                continue
            ex, ey, ew, eh = self._bornes(formes[p.get("id")])
            d["ext"].append({"id": self._code(p.get("id")), "name": p.get("name") or "",
                             "pos": "top" if ey < py else "bottom"})
            d["ext_header"] = eh

        # couloirs, dans l'ordre vertical
        bandes = []
        for lane in proc.iter(B + "lane"):
            if lane.get("id") not in formes:
                continue
            lx, ly, lw, lh = self._bornes(formes[lane.get("id")])
            bandes.append((ly, lh, lane))
        bandes.sort()
        for _y, _h, lane in bandes:
            d["lanes"].append({"id": self._code(lane.get("id")),
                               "name": lane.get("name") or ""})
        couloir_de = {}
        for _y, _h, lane in bandes:
            for ref in lane.findall(B + "flowNodeRef"):
                couloir_de[(ref.text or "").strip()] = self._code(lane.get("id"))

        # la grille se déduit de la mise en page
        col_w, row_h, lane_pad = self._grille(proc, formes, bandes, pw)
        d.update(col_w=col_w, row_h=row_h, lane_pad=lane_pad)
        base_y = bandes[0][0] if bandes else py
        y_pool = py

        for el in proc:
            balise = el.tag.replace(B, "")
            if balise in ("laneSet", "sequenceFlow", "association"):
                continue
            ident = el.get("id")
            if ident not in formes:
                continue
            evdef = next((c.tag.replace(B, "") for c in el
                          if c.tag.replace(B, "").endswith("EventDefinition")), None)
            genre = VERS_GENRE.get((balise, evdef))
            if genre is None:
                genre = {"textAnnotation": "note",
                         "dataStoreReference": "store"}.get(balise)
            if genre is None:
                continue
            x, y, w, h = self._bornes(formes[ident])
            cx, cy = x + w / 2, y + h / 2
            noeud = {"id": self._code(ident), "kind": genre,
                     "name": self._libelle(el, balise),
                     "col": round((cx - px - geo.POOL_HDR_W) / col_w, 3),
                     "row": 0.0}
            couloir = couloir_de.get(ident)
            if couloir:
                noeud["lane"] = couloir
                bande = next(b for b in bandes
                             if self._code(b[2].get("id")) == couloir)
                noeud["row"] = round((cy - bande[0] - lane_pad) / row_h, 3)
            else:
                noeud["row"] = round((cy - y_pool - lane_pad) / row_h, 3)
            attendu = geo.node_box({**noeud, "h": h, "w": w})
            if abs(attendu[0] - w) > 0.5:
                noeud["w"] = w
            if genre == "note" or abs(attendu[1] - h) > 0.5:
                noeud["h"] = h
            d["nodes"].append(noeud)

        for el in list(proc.iter(B + "sequenceFlow")) + list(proc.iter(B + "association")):
            d["flows"].append({
                "src": self._code(el.get("sourceRef")),
                "tgt": self._code(el.get("targetRef")),
                "label": el.get("name") or "",
                **({"r": "assoc"} if el.tag == B + "association" else {}),
            })
        codes_pools = {e["id"] for e in d["ext"]}
        for el in colab.findall(B + "messageFlow"):
            src, tgt = self._code(el.get("sourceRef")), self._code(el.get("targetRef"))
            entrant = src in codes_pools
            d["msgs"].append({
                "node": tgt if entrant else src,
                "pool": src if entrant else tgt,
                "dir": "in" if entrant else "out",
                "label": el.get("name") or "",
            })
        return d

    @staticmethod
    def _grille(proc, formes, bandes, largeur_pool):
        """Retrouve le pas de la grille depuis les écarts observés."""
        xs = sorted({round(BfProcessImportWizard._bornes(f)[0]
                           + BfProcessImportWizard._bornes(f)[2] / 2, 1)
                     for i, f in formes.items() if i.startswith(("d", "n"))})
        ecarts = [round(b - a, 1) for a, b in zip(xs, xs[1:]) if b - a > 40]
        col_w = min(ecarts) if ecarts else 168.0
        lane_pad = 50.0
        row_h = 100.0
        if bandes:
            lane_pad = min(50.0, bandes[0][1] / 2)
        return col_w, row_h, lane_pad

    @staticmethod
    def _titre(racine, plan):
        for diag in racine.iter(DI + "BPMNDiagram"):
            if diag.find(DI + "BPMNPlane") is plan:
                nom = diag.get("name") or ""
                return nom.split(" — ")[-1] or nom or "Niveau importé"
        return "Niveau importé"

    @staticmethod
    def _libelle(el, balise):
        if balise == "textAnnotation":
            t = el.find(B + "text")
            return (t.text or "") if t is not None else ""
        return el.get("name") or ""

    @staticmethod
    def _code(ident):
        """Retire le préfixe de niveau que l'export ajoute (`d3_t1` → `t1`)."""
        if not ident:
            return ident
        return re.sub(r"^d\d+_", "", ident)
