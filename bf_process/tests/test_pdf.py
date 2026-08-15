# -*- coding: utf-8 -*-
"""Le PDF sort du serveur, sans navigateur ni moteur typographique.

Ce que ces tests peuvent prouver ici : que le PDF est produit, qu'il a une
page par niveau, qu'il est taillé sur la carte et qu'il embarque Lexend. Ce
qu'ils ne peuvent pas prouver, faute de PyMuPDF dans l'image : que le tracé est
au point près celui du moteur de référence. C'est un contrôle hors serveur,
dans le harnais de QA, qui superpose les deux — tracés, mots et pixels.
"""
import re

from odoo.tests import TransactionCase, tagged

from ..generateur import mesure
from .test_aller_retour import CARTE


@tagged("post_install", "-at_install")
class TestPdf(TransactionCase):

    def setUp(self):
        super().setUp()
        self.processus = self.env["bf.process"].create({
            "name": "Essai PDF", "code": "pdf", "pool_name": "Blue Fox",
            "source": "Entrevue du 14 août 2026."})
        self.processus._charger_niveaux(CARTE)
        self.niveau = self.processus.diagram_ids

    def _media_box(self, contenu):
        """Les bornes de page telles que le PDF les déclare."""
        trouve = re.search(rb"/MediaBox\s*\[([^\]]+)\]", contenu)
        self.assertTrue(trouve, "le PDF ne déclare aucune taille de page")
        return [float(v) for v in trouve.group(1).split()]

    def test_pdf_produit_et_taille_sur_la_carte(self):
        contenu = self.processus._pdf_octets()
        self.assertTrue(contenu.startswith(b"%PDF-"))
        self.assertIn(b"%%EOF", contenu)
        self.assertIn(b"/Count 1", contenu)
        # la page fait la taille de la carte, pas un format standard : c'est
        # ce qui permet de garder l'échelle 1:1 avec les deux exports XML
        rendu = self.niveau.rendu()
        x0, y0, x1, y1 = self._media_box(contenu)
        self.assertAlmostEqual(x1 - x0, rendu["largeur"], places=2)
        self.assertAlmostEqual(y1 - y0, rendu["hauteur"] + 34.0 + 26.0, places=2)

    def test_une_page_par_niveau(self):
        carte = [dict(CARTE[0]), dict(CARTE[0], title="Deuxième niveau")]
        p = self.env["bf.process"].create({"name": "Deux pages",
                                           "pool_name": "Blue Fox"})
        p._charger_niveaux(carte)
        self.assertIn(b"/Count 2", p._pdf_octets())

    def test_export_public_est_base64_donc_franchit_xmlrpc(self):
        """Rendre des octets bruts casse tout appelant XML-RPC.

        `exporter_pdf` est publique, donc appelable par XML-RPC, qui sérialise
        en chaîne : des octets bruts y lèvent un `UnicodeDecodeError` avant que
        l'appelant voie le fichier. La forme publique est donc du base64.
        """
        import base64
        publie = self.processus.exporter_pdf()
        self.assertIsInstance(publie, str)
        publie.encode("ascii")          # ce que fait XML-RPC, et qui échouait
        # on ne compare pas à une seconde génération : reportlab pose un
        # identifiant de document neuf à chaque appel, donc deux PDF du même
        # contenu diffèrent par quelques octets.
        octets = base64.b64decode(publie)
        self.assertTrue(octets.startswith(b"%PDF-"))
        self.assertIn(b"%%EOF", octets)

    def test_lexend_est_embarque(self):
        """Sans la police, les largeurs figées ne veulent plus rien dire."""
        contenu = self.processus._pdf_octets()
        self.assertIn(b"Lexend", contenu)
        self.assertIn(b"/FontFile2", contenu)

    def test_telechargement_cree_la_piece_jointe(self):
        action = self.processus.action_telecharger_pdf()
        self.assertEqual(action["type"], "ir.actions.act_url")
        piece = self.env["ir.attachment"].search(
            [("res_model", "=", "bf.process"), ("res_id", "=", self.processus.id)],
            limit=1)
        self.assertTrue(piece.name.endswith(".pdf"))
        self.assertEqual(piece.mimetype, "application/pdf")

    def test_un_niveau_seul(self):
        action = self.niveau.action_telecharger_pdf()
        self.assertEqual(action["type"], "ir.actions.act_url")
        piece = self.env["ir.attachment"].search(
            [("res_model", "=", "bf.process")], order="id desc", limit=1)
        self.assertIn(self.niveau.code, piece.name)

    def test_bandeau_optionnel(self):
        """Sans bandeau, la page rétrécit d'exactement sa hauteur."""
        from ..generateur import pdf as gen_pdf
        d = self.niveau.to_dict()
        avec = gen_pdf.to_pdf([d])
        sans = gen_pdf.to_pdf([d], entete=False)
        self.assertTrue(sans.startswith(b"%PDF-"))
        self.assertNotEqual(avec, sans)

    def test_caractere_hors_table_refuse_plutot_que_deviner(self):
        note = self.niveau.node_ids.filtered(lambda n: n.kind == "note")
        note.write({"name": "Un renard \U0001F98A ici", "height": 0.0})
        with self.assertRaises(mesure.MesureImpossible):
            self.processus._pdf_octets()
