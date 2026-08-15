# -*- coding: utf-8 -*-
"""Chargement d'une carte dans les enregistrements, et sortie des fichiers.

Le dict manipulé ici est celui que le générateur attend depuis toujours : c'est
la forme d'échange, pas un format de plus. Charger, c'est le poser en
enregistrements ; exporter, c'est le reconstituer et le donner au générateur.
"""
import base64

from odoo import _, api, models
from odoo.exceptions import UserError

from ..generateur import bpmn as gen_bpmn
from ..generateur import mxgraph as gen_mx
from ..generateur import geometrie as geo
from .erreurs import refus_lisible

GROUPE = "bf_process.group_bf_process_manager"




class BfProcessChargement(models.Model):
    _inherit = "bf.process"

    # ------------------------------------------------------------------ lire
    def to_dicts(self):
        """Rend les niveaux dans l'ordre, sous la forme attendue par le rendu."""
        self.ensure_one()
        return [d.to_dict() for d in self.diagram_ids]

    def _prefixes(self):
        self.ensure_one()
        return [d.bpmn_id for d in self.diagram_ids]

    @refus_lisible
    def exporter_bpmn(self):
        """XML BPMN 2.0 des niveaux, avec la partie DI."""
        self.ensure_one()
        return gen_bpmn.to_bpmn(self.to_dicts(), prefixes=self._prefixes())

    @refus_lisible
    def exporter_mxgraph(self):
        """XML mxGraph : un onglet par niveau, pour diagrams.net."""
        self.ensure_one()
        return gen_mx.to_mxgraph(self.to_dicts(), prefixes=self._prefixes())

    @refus_lisible
    def exporter_pdf(self, diagrammes=None):
        """PDF encodé en base64 — la forme qui traverse XML-RPC.

        Les deux exports XML rendent du texte, donc une chaîne suffit ; un PDF
        est binaire, et rendre des octets bruts fait échouer tout appelant
        XML-RPC sur un `UnicodeDecodeError` avant même qu'il voie le fichier.
        Les appelants internes prennent `_pdf_octets`, qui rend les octets.
        """
        self.ensure_one()
        return base64.b64encode(self._pdf_octets(diagrammes)).decode("ascii")

    def _pdf_octets(self, diagrammes=None):
        """PDF vectoriel : une page par niveau, à l'échelle 1:1.

        `reportlab` n'est importé qu'ici : les deux exports XML doivent rester
        possibles même dans un environnement qui ne saurait pas tracer.
        """
        self.ensure_one()
        from ..generateur import pdf as gen_pdf
        pied = (self.source or "").strip().splitlines()
        return gen_pdf.to_pdf(
            diagrammes if diagrammes is not None else self.to_dicts(),
            titre=f"{self.name} · v{self.version}",
            sous_titre=self.partner_id.display_name or "",
            pied=pied[0] if pied else "")

    @refus_lisible
    def action_telecharger_bpmn(self):
        return self._telecharger(self.exporter_bpmn(), "bpmn", "application/xml")

    @refus_lisible
    def action_telecharger_mxgraph(self):
        return self._telecharger(self.exporter_mxgraph(), "drawio", "application/xml")

    @refus_lisible
    def action_telecharger_pdf(self):
        return self._telecharger(self._pdf_octets(), "pdf", "application/pdf")

    def _telecharger(self, contenu, extension, mimetype, suffixe=""):
        self.ensure_one()
        nom = (f"{(self.code or self.name).strip()}-v{self.version}"
               f"{suffixe}.{extension}")
        if isinstance(contenu, str):
            contenu = contenu.encode("utf-8")
        piece = self.env["ir.attachment"].create({
            "name": nom,
            "datas": base64.b64encode(contenu),
            "res_model": self._name,
            "res_id": self.id,
            "mimetype": mimetype,
        })
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{piece.id}?download=true",
            "target": "self",
        }

    @refus_lisible
    def rendu(self, niveau_id=False):
        """Rendu d'un niveau du processus — le premier par défaut.

        Surface RPC volontaire : le même composant sert sur la fiche du
        processus et sur celle du niveau.
        """
        self.ensure_one()
        niveau = self.env["bf.process.diagram"].browse(niveau_id) \
            if niveau_id else self.diagram_ids[:1]
        if not niveau or niveau.process_id != self:
            niveau = self.diagram_ids[:1]
        if not niveau:
            raise UserError(_("Cette cartographie n'a encore aucun niveau."))
        return niveau.rendu()

    # --------------------------------------------------------------- écrire
    @api.model
    def charger_dicts(self, vals_processus, diagrammes):
        """Crée un processus et ses niveaux depuis la forme d'échange.

        Surface RPC volontaire — c'est le chemin d'entrée d'une carte produite
        hors serveur. Réservée aux gestionnaires de cartographie.
        """
        if not self.env.user.has_group(GROUPE):
            raise UserError(_("Charger une carte demande le droit de gestion des"
                              " cartographies."))
        processus = self.create(vals_processus)
        processus._charger_niveaux(diagrammes)
        return processus.id

    def _charger_niveaux(self, diagrammes):
        """Pose les niveaux, puis raccorde chaque page à son sous-processus."""
        self.ensure_one()
        Diagram = self.env["bf.process.diagram"]
        par_titre = {}
        for i, d in enumerate(diagrammes, 1):
            code = d.get("code") or f"d{i}"
            niveau = Diagram.create({
                "process_id": self.id,
                "sequence": i * 10,
                "title": d["title"],
                "level": d.get("level"),
                "code": code,
                "bpmn_id": d.get("bpmn_id") or code,
                "pool_name": d["pool"] if d["pool"] != self.pool_name else False,
                "flat": bool(d.get("flat")),
                "col_w": d.get("col_w", 168.0),
                "row_h": d.get("row_h", 100.0),
                "lane_pad": d.get("lane_pad", 50.0),
                # 0.0 = à mesurer. Poser 62 par défaut donnait un bandeau
                # plausible et faux dès qu'un participant avait un nom long.
                "ext_header": d.get("ext_header") or 0.0,
            })
            self._charger_niveau(niveau, d)
            par_titre[d["title"].strip().lower()] = niveau

        # « les portes du parent portent les états de fin de l'enfant » : le
        # raccord se fait par le nom du sous-processus, comme au contrôle
        # qualité du générateur. Chaque appelant pointe vers la page — un
        # sous-processus commun en a plusieurs, et n'en garder qu'un en perdait.
        for noeud in self.mapped("diagram_ids.node_ids").filtered(
                lambda n: n.kind == "sub"):
            enfant = par_titre.get((noeud.name or "").strip().lower())
            if enfant and enfant != noeud.diagram_id:
                noeud.child_diagram_id = enfant.id

    def _charger_niveau(self, niveau, d):
        """Couloirs, pools, nœuds, flux et messages d'une page."""
        env = self.env
        couloirs, pools, noeuds = {}, {}, {}

        for i, ln in enumerate(d.get("lanes") or [], 1):
            couloirs[ln["id"]] = env["bf.process.lane"].create({
                "diagram_id": niveau.id, "sequence": i * 10,
                "name": ln["name"], "code": ln["id"]})
        for i, p in enumerate(d.get("ext") or [], 1):
            pools[p["id"]] = env["bf.process.pool"].create({
                "diagram_id": niveau.id, "sequence": i * 10,
                "name": p["name"], "code": p["id"], "position": p.get("pos", "top")})

        # Une annotation sans hauteur n'est plus refusée : `mesure` la refait,
        # et une carte saisie à la main dans Odoo n'a aucune raison d'en porter
        # une. La hauteur qui arrive, quand elle arrive, reste conservée telle
        # quelle — c'est ce qui garde les cartes déjà chargées au bit près.
        for i, n in enumerate(d["nodes"], 1):
            noeuds[n["id"]] = env["bf.process.node"].create({
                "diagram_id": niveau.id, "sequence": i * 10,
                "lane_id": couloirs[n["lane"]].id if n.get("lane") in couloirs else False,
                "code": n["id"],
                "bpmn_id": f'{niveau.bpmn_id}_{n["id"]}',
                "name": n.get("name") or False,
                "kind": n["kind"],
                "tone": n.get("tone") or False,
                "col": n["col"], "row": n["row"],
                "width": n.get("w", 0.0), "height": n.get("h", 0.0),
            })

        for i, f in enumerate(d.get("flows") or [], 1):
            lp = f.get("lp") or (0.0, 0.0)
            env["bf.process.flow"].create({
                "diagram_id": niveau.id, "sequence": i * 10,
                "bpmn_id": f"{niveau.bpmn_id}_flow{i}",
                "source_id": noeuds[f["src"]].id, "target_id": noeuds[f["tgt"]].id,
                "label": f.get("label") or False,
                "routing": f.get("r", "auto"),
                "detour": f.get("d", 0.0),
                "anchor_source": f.get("a") or False,
                "anchor_target": f.get("b") or False,
                "label_dx": lp[0], "label_dy": lp[1],
                "label_width": f.get("lw", 0.0),
            })
        for i, m in enumerate(d.get("msgs") or [], 1):
            env["bf.process.message"].create({
                "diagram_id": niveau.id, "sequence": i * 10,
                "bpmn_id": f"{niveau.bpmn_id}_msg{i}",
                "node_id": noeuds[m["node"]].id, "pool_id": pools[m["pool"]].id,
                "direction": m.get("dir", "out"), "label": m["label"],
                "dx": m.get("dx", 0.0), "label_dx": m.get("lx", 0.0),
                "label_dy": m.get("ly", 0.0), "label_width": m.get("lw", 0.0),
            })


class BfProcessDiagramRendu(models.Model):
    """Données de tracé pour le visualiseur OWL.

    Le composant client ne recalcule rien : il reçoit exactement les
    coordonnées qui partent dans le `.bpmn` et dans le PDF. Trois moteurs de
    rendu, une seule géométrie.
    """

    _inherit = "bf.process.diagram"

    @refus_lisible
    def action_telecharger_pdf(self):
        """Ce niveau seul, en PDF — une page, taillée sur la carte."""
        self.ensure_one()
        contenu = self.process_id._pdf_octets([self.to_dict()])
        return self.process_id._telecharger(
            contenu, "pdf", "application/pdf", suffixe=f"-{self.code}")

    @refus_lisible
    def rendu(self):
        """Surface RPC volontaire : c'est ce que le composant OWL appelle."""
        self.ensure_one()
        freres = self.process_id.diagram_ids
        d = self.to_dict()
        pos, lane_geo, pool, ext, noeuds, dx, dy = geo.plan(d)
        lanes = d.get("lanes") or []
        mono = not lanes

        def boite(nid):
            cx, cy = pos[nid]
            w, h = geo.node_box(noeuds[nid])
            return {"x": cx - w / 2 + dx, "y": cy - h / 2 + dy, "w": w, "h": h}

        externes = [dict(nom=e["name"], x=e["x0"] + dx, y=e["y0"] + dy,
                         w=e["x1"] - e["x0"], h=e["y1"] - e["y0"])
                    for e in ext.values()]
        couloirs = [] if mono else [
            dict(nom=ln["name"], code=ln["id"], x=pool["x_lane"] + dx,
                 y=pool["y0"] + lane_geo[ln["id"]]["y0"] + dy,
                 w=pool["x1"] - pool["x_lane"], h=lane_geo[ln["id"]]["h"])
            for ln in lanes]

        # de quoi rendre le tracé navigable : chaque forme sait quel
        # enregistrement elle représente, et quelle page elle ouvre
        par_code = {n.code: n for n in self.node_ids}
        noeuds_rendus = []
        for n in d["nodes"]:
            rec = par_code.get(n["id"])
            noeuds_rendus.append(dict(
                id=n["id"], genre=n["kind"], nom=n.get("name") or "",
                ton=n.get("tone") or "",
                rid=rec.id if rec else False,
                enfant=rec.child_diagram_id.id if rec and rec.child_diagram_id else False,
                fil=len(rec.message_ids.filtered(
                    lambda m: m.message_type == "comment")) if rec else 0,
                validation=rec.validation if rec else "",
                **boite(n["id"])))

        flux = []
        for f in d.get("flows", []):
            pts = geo.points_flux(f, noeuds, pos)
            lp = f.get("lp") or (0, 0)
            flux.append(dict(
                points=[[x + dx, y + dy] for x, y in pts],
                etiquette=f.get("label") or "",
                largeur_etiquette=f.get("lw") or 116,
                decalage=[lp[0], lp[1]],
                # de quoi couper un lien depuis le tracé, sans passer par la
                # liste des flux
                src=f["src"], tgt=f["tgt"],
                association=f.get("r") == "assoc"))
        messages = []
        for m in d.get("msgs", []):
            pts = geo.points_message(m, noeuds, pos, ext)
            messages.append(dict(
                points=[[x + dx, y + dy] for x, y in pts],
                etiquette=m.get("label") or "",
                largeur_etiquette=m.get("lw") or 124,
                decalage=[m.get("lx", 7), m.get("ly", 0)]))

        bords = [pool["x1"] + dx] + [e["x"] + e["w"] for e in externes]
        bas = [pool["y1"] + dy] + [e["y"] + e["h"] for e in externes]
        return {
            "titre": " — ".join(filter(None, (self.level, self.title))),
            "niveau_id": self.id,
            # l'éditeur lit le gel AVANT d'offrir une poignée : laisser
            # déplacer une forme pour rendre une erreur au relâchement serait
            # une promesse qu'on ne tient pas
            "modifiable": self._modifiable(),
            "gele": self.process_id.state == "valide",
            "pas": self.pas_grille or 0.05,
            "col_w": self.col_w or 168.0,
            "row_h": self.row_h or 100.0,
            "palette": self._palette(),
            # de quoi sauter d'un niveau à l'autre sans quitter le tracé
            "niveaux": [{"id": f.id,
                         "titre": " — ".join(filter(None, (f.level, f.title)))}
                        for f in freres],
            "largeur": max(bords) + geo.MARGE,
            "hauteur": max(bas) + geo.MARGE,
            "entete_pool": geo.POOL_HDR_W,
            "entete_couloir": geo.LANE_HDR,
            "pool": dict(nom=d["pool"], x=pool["x0"] + dx, y=pool["y0"] + dy,
                         w=pool["x1"] - pool["x0"], h=pool["y1"] - pool["y0"]),
            "couloirs": couloirs,
            "externes": externes,
            "noeuds": noeuds_rendus,
            "flux": flux,
            "messages": messages,
        }
