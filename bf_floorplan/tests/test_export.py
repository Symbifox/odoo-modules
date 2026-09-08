# -*- coding: utf-8 -*-
import base64
from xml.etree import ElementTree as ET

from odoo.tests import tagged

from .commun import CasPlan


@tagged("post_install", "-at_install")
class TestExport(CasPlan):

    def _racine(self, xml):
        return ET.fromstring(xml)

    def test_drawio_bien_forme_avec_ses_calques(self):
        racine = self._racine(self.plan.exporter_mxgraph())
        self.assertEqual(racine.tag, "mxfile")
        diagramme = racine.find("diagram")
        self.assertEqual(diagramme.get("name"), "Banc (Atelier, étage 1)")
        cellules = racine.findall(".//mxCell")
        par_id = {c.get("id"): c for c in cellules}
        for calque in ("1", "zones", "elements", "liens"):
            self.assertEqual(par_id[calque].get("parent"), "0")
        self.assertNotIn("fond", par_id)

    def test_drawio_formes_et_aretes(self):
        racine = self._racine(self.plan.exporter_mxgraph())
        cellules = racine.findall(".//mxCell")
        zones = [c for c in cellules if c.get("parent") == "zones"]
        elements = [c for c in cellules if c.get("parent") == "elements"]
        aretes = [c for c in cellules if c.get("parent") == "liens"]
        self.assertEqual(len(zones), 2)
        self.assertEqual(len(elements), 3)
        self.assertEqual(len(aretes), 1)
        poste = next(c for c in elements if c.get("value") == "P-01")
        self.assertIn("shape=mxgraph.floorplan.workstation;", poste.get("style"))
        geo = poste.find("mxGeometry")
        self.assertEqual((geo.get("x"), geo.get("y"), geo.get("width"), geo.get("height")),
                         ("100", "100", "160", "80"))
        arete = aretes[0]
        self.assertEqual(arete.get("source"), f"element-{self.commutateur.id}")
        self.assertEqual(arete.get("target"), f"element-{self.borne.id}")
        self.assertEqual(arete.get("value"), "port 3")
        aire = next(c for c in zones if c.get("value").startswith("Aire ouverte"))
        self.assertEqual(aire.get("value"), "Aire ouverte (1/4)")
        self.assertIn("fillColor=#FFF8E1", aire.get("style"))

    def test_drawio_rotation_et_grille(self):
        self.poste.rotation = "90"
        racine = self._racine(self.plan.exporter_mxgraph())
        poste = next(c for c in racine.findall(".//mxCell") if c.get("value") == "P-01")
        self.assertIn("rotation=90;", poste.get("style"))
        modele = racine.find(".//mxGraphModel")
        self.assertEqual(modele.get("gridSize"), "25")
        self.assertEqual(modele.get("pageWidth"), "1000")

    def test_drawio_incorpore_le_fond(self):
        self.plan.fond = self.fond_b64()
        racine = self._racine(self.plan.exporter_mxgraph())
        fond = next(c for c in racine.findall(".//mxCell") if c.get("id") == "fond")
        self.assertEqual(fond.get("parent"), "1")
        self.assertIn("image=data:image/png,", fond.get("style"))
        b64 = fond.get("style").split("image=data:image/png,")[1].rstrip(";")
        self.assertEqual(base64.b64decode(b64)[:8], b"\x89PNG\r\n\x1a\n")

    def test_le_lecteur_exporte_aussi(self):
        """Qui peut lire le plan peut l'emporter : la pièce se joint sans
        exiger le droit d'écrire sur le plan."""
        action = self.plan.with_user(self.lecteur).action_telecharger_mxgraph()
        self.assertEqual(action["type"], "ir.actions.act_url")
        piece = self.env["ir.attachment"].search([
            ("res_model", "=", "bf.floorplan"), ("res_id", "=", self.plan.id),
            ("name", "like", "%.drawio")])
        self.assertEqual(len(piece), 1)
        self.assertEqual(piece.create_uid, self.lecteur)

    def test_telecharger_laisse_une_piece_jointe(self):
        action = self.plan.action_telecharger_mxgraph()
        self.assertEqual(action["type"], "ir.actions.act_url")
        piece = self.env["ir.attachment"].search([
            ("res_model", "=", "bf.floorplan"), ("res_id", "=", self.plan.id),
            ("name", "like", "%.drawio")])
        self.assertEqual(len(piece), 1)
        self.assertIn(f"/web/content/{piece.id}", action["url"])
        self.assertIn(b"<mxfile", piece.raw)
