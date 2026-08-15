# -*- coding: utf-8 -*-
"""Versions, gel, validation, comparaison et import."""
import base64

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from .test_aller_retour import CARTE


@tagged("post_install", "-at_install")
class TestQuickWins(TransactionCase):

    def setUp(self):
        super().setUp()
        self.processus = self.env["bf.process"].create({
            "name": "Essai versions", "code": "essai", "pool_name": "Blue Fox"})
        self.processus._charger_niveaux(CARTE)
        self.noeuds = {n.code: n for n in self.processus.diagram_ids.node_ids}

    # ------------------------------------------------------------ validation
    def test_registre_de_validation(self):
        t1 = self.noeuds["t1"]
        self.assertEqual(t1.validation, "a_valider")
        t1.valide_proprietaire = True
        self.assertEqual(t1.validation, "partielle")
        t1.valide_executant = True
        self.assertEqual(t1.validation, "validee")
        t1.conteste = True
        self.assertEqual(t1.validation, "conteste")

    def test_taux_de_validation(self):
        activites = self.processus.diagram_ids.node_ids.filtered(
            lambda n: n.kind in ("task", "send", "receive", "user", "sub"))
        self.assertEqual(self.processus.activite_count, len(activites))
        self.assertEqual(self.processus.taux_validation, 0.0)
        activites.write({"valide_proprietaire": True, "valide_executant": True})
        self.processus.invalidate_recordset()
        self.assertEqual(self.processus.taux_validation, 100.0)

    # ------------------------------------------------------------------- gel
    def test_version_validee_est_gelee(self):
        self.processus.action_valider()
        self.assertEqual(self.processus.state, "valide")
        with self.assertRaises(UserError):
            self.noeuds["t1"].name = "Autre chose"
        with self.assertRaises(UserError):
            self.processus.diagram_ids[0].title = "Autre titre"
        with self.assertRaises(UserError):
            self.noeuds["t1"].unlink()
        # le contexte de la carte reste renseignable
        self.processus.source = "Relecture du 14 août"

    def test_rouvrir_degele(self):
        self.processus.action_valider()
        self.processus.action_rouvrir()
        self.assertEqual(self.processus.state, "brouillon")
        self.noeuds["t1"].name = "Qualifier autrement"
        self.assertEqual(self.noeuds["t1"].name, "Qualifier autrement")

    # -------------------------------------------------------------- versions
    def test_nouvelle_version(self):
        suite = self.env["bf.process"].browse(
            self.processus.action_nouvelle_version()["res_id"])
        self.assertEqual(suite.version, "1.1")
        self.assertEqual(suite.version_precedente_id, self.processus)
        self.assertEqual(suite.state, "brouillon")
        self.assertEqual(len(suite.diagram_ids), len(self.processus.diagram_ids))
        self.assertEqual(suite.node_count, self.processus.node_count)
        # les codes se conservent : c'est ce qui rend le diff possible
        self.assertEqual(sorted(suite.diagram_ids.node_ids.mapped("code")),
                         sorted(self.processus.diagram_ids.node_ids.mapped("code")))
        # et le tracé sort identique
        self.assertEqual(suite.exporter_bpmn().replace(f"v{suite.version}", ""),
                         self.processus.exporter_bpmn().replace(
                             f"v{self.processus.version}", ""))

    def test_nouvelle_version_saute_un_numero_deja_pris(self):
        """Un essai laissé derrière ne doit pas coller sur un numéro vivant."""
        # simule un résidu : une « v1.1 » du même nom existe déjà
        self.env["bf.process"].create({
            "name": self.processus.name, "version": "1.1", "pool_name": "Blue Fox"})
        suite = self.env["bf.process"].browse(
            self.processus.action_nouvelle_version()["res_id"])
        self.assertEqual(suite.version, "1.2")

    def test_nouvelle_version_repart_la_validation_a_zero(self):
        self.processus.diagram_ids.node_ids.write({"valide_proprietaire": True})
        suite = self.env["bf.process"].browse(
            self.processus.action_nouvelle_version()["res_id"])
        self.assertFalse(any(suite.diagram_ids.node_ids.mapped("valide_proprietaire")))

    # ----------------------------------------------------------- comparaison
    def test_comparaison_voit_le_renommage(self):
        suite = self.env["bf.process"].browse(
            self.processus.action_nouvelle_version()["res_id"])
        cible = {n.code: n for n in suite.diagram_ids.node_ids}
        cible["t1"].name = "Qualifier et documenter la demande"
        cible["e2"].unlink()
        wiz = self.env["bf.process.compare.wizard"].create({
            "source_id": suite.id, "cible_id": self.processus.id})
        wiz.action_comparer()
        self.assertIn("renommage", wiz.rapport_html)
        self.assertIn("Qualifier et documenter la demande", wiz.rapport_html)
        self.assertIn("retrait", wiz.rapport_html)
        self.assertGreaterEqual(wiz.ecart_count, 2)

    def test_comparaison_echappe_les_libelles(self):
        """Un nom de nœud hostile ne doit pas s'exécuter dans le rapport.

        Les libellés viennent de la saisie, d'un `.bpmn` tiers ou du tracé :
        concaténés tels quels dans du HTML rendu sans filtre, ils s'exécutent
        dans la session de quiconque ouvre la comparaison.
        """
        charge = '<img src=x onerror=alert(1)>'
        suite = self.env["bf.process"].browse(
            self.processus.action_nouvelle_version()["res_id"])
        cible = {n.code: n for n in suite.diagram_ids.node_ids}
        cible["t1"].name = charge
        wiz = self.env["bf.process.compare.wizard"].create({
            "source_id": suite.id, "cible_id": self.processus.id})
        wiz.action_comparer()
        rapport = str(wiz.rapport_html or "")
        # ce qui compte n'est pas l'absence du mot « onerror » — il survit en
        # texte inerte à l'intérieur de l'entité échappée — mais l'absence de
        # la BALISE : sans « < » brut, rien ne s'exécute.
        self.assertNotIn("<img", rapport)
        self.assertNotIn("<script", rapport)
        # et le texte reste lisible, simplement neutralisé
        self.assertIn("&lt;img", rapport)

    def test_comparaison_sans_ecart(self):
        suite = self.env["bf.process"].browse(
            self.processus.action_nouvelle_version()["res_id"])
        wiz = self.env["bf.process.compare.wizard"].create({
            "source_id": suite.id, "cible_id": self.processus.id})
        wiz.action_comparer()
        self.assertEqual(wiz.ecart_count, 0)
        self.assertIn("même chose", wiz.rapport_html)

    # --------------------------------------------------------------- import
    def test_import_de_notre_propre_export(self):
        """La garantie tenue : notre export réimporté redonne la même carte."""
        xml = self.processus.exporter_bpmn()
        wiz = self.env["bf.process.import.wizard"].create({
            "name": "Essai réimporté", "version": "1.0",
            "nom_fichier": "essai.bpmn",
            "fichier": base64.b64encode(xml.encode("utf-8"))})
        relu = self.env["bf.process"].browse(wiz.action_importer()["res_id"])
        self.assertEqual(len(relu.diagram_ids), len(self.processus.diagram_ids))
        self.assertEqual(relu.node_count, self.processus.node_count)
        avant = self.processus.diagram_ids[0]
        apres = relu.diagram_ids[0]
        self.assertEqual(sorted(apres.node_ids.mapped("code")),
                         sorted(avant.node_ids.mapped("code")))
        self.assertEqual(sorted(apres.node_ids.mapped("kind")),
                         sorted(avant.node_ids.mapped("kind")))
        self.assertEqual(sorted(apres.node_ids.mapped("name")),
                         sorted(avant.node_ids.mapped("name")))
        self.assertEqual(len(apres.flow_ids), len(avant.flow_ids))
        self.assertEqual(len(apres.message_ids), len(avant.message_ids))
        self.assertEqual(sorted(apres.lane_ids.mapped("name")),
                         sorted(avant.lane_ids.mapped("name")))

    def test_import_refuse_un_fichier_qui_n_en_est_pas_un(self):
        wiz = self.env["bf.process.import.wizard"].create({
            "name": "Rien", "version": "1.0",
            "fichier": base64.b64encode(b"ceci n'est pas du XML")})
        with self.assertRaises(UserError):
            wiz.action_importer()

    # --------------------------------------------------------------- tracé
    def test_rendu_depuis_le_processus(self):
        r = self.processus.rendu()
        self.assertEqual(r["niveau_id"], self.processus.diagram_ids[0].id)
        self.assertEqual(len(r["niveaux"]), len(self.processus.diagram_ids))
        self.assertTrue(all("validation" in n for n in r["noeuds"]))
