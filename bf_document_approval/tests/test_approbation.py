# -*- coding: utf-8 -*-
"""Le tour de table : plusieurs avis, un verrou, une diffusion."""
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestApprobation(TransactionCase):

    def setUp(self):
        super().setUp()
        self.projet = self.env["project.project"].create({"name": "Essai doc"})
        self.matrice = self.env["project.knowledge.matrix"].create({
            "name": "Matrice d'essai", "project_id": self.projet.id})
        self.section = self.env["project.knowledge.section"].create({
            "name": "Politiques", "code": "S01"})
        self.type_doc = self.env["project.document.type"].create({
            "name": "Politique", "code": "POL"})
        self.doc = self.env["project.document"].create({
            "name": "Politique de sécurité des postes", "code": "POL-001",
            "matrix_id": self.matrice.id,
            "type_id": self.type_doc.id, "project_id": self.projet.id})
        self.version = self.env["project.document.version"].create({
            "document_id": self.doc.id, "version_number": "1.0"})
        self.tremblay = self.env["res.users"].create({
            "name": "M. Tremblay", "login": "tremblay@essai.test"})
        self.couture = self.env["res.users"].create({
            "name": "Mme Couture", "login": "couture@essai.test"})

    def _approbateur(self, utilisateur, requis=True):
        return self.env["project.document.approver"].create({
            "version_id": self.version.id, "user_id": utilisateur.id,
            "requis": requis})

    # ----------------------------------------------------------- le verrou
    def test_sans_approbateur_rien_ne_change(self):
        """Une organisation sans tour de table n'a pas à en inventer un."""
        self.version.action_release()
        self.assertEqual(self.version.state, "released")

    def test_un_avis_manquant_bloque_la_publication(self):
        """⚠️ C'est `action_release` qu'il faut fermer, pas seulement
        `action_approve` : le module hôte approuve d'office à la publication."""
        self._approbateur(self.tremblay)
        with self.assertRaises(UserError):
            self.version.action_release()
        with self.assertRaises(UserError):
            self.version.action_approve()
        self.assertEqual(self.version.state, "draft")

    def test_le_dernier_avis_debloque(self):
        a = self._approbateur(self.tremblay)
        b = self._approbateur(self.couture)
        a.action_approuver()
        self.version.invalidate_recordset()
        self.assertEqual(self.version.approbation_attendue_count, 1)
        with self.assertRaises(UserError):
            self.version.action_release()
        b.action_approuver()
        self.version.invalidate_recordset()
        self.assertTrue(self.version.approbation_complete)
        self.version.action_release()
        self.assertEqual(self.version.state, "released")

    def test_un_avis_non_requis_ne_bloque_pas(self):
        self._approbateur(self.tremblay).action_approuver()
        self._approbateur(self.couture, requis=False)
        self.version.invalidate_recordset()
        self.version.action_release()
        self.assertEqual(self.version.state, "released")

    def test_un_refus_bloque_et_se_dit(self):
        a = self._approbateur(self.tremblay)
        a.write({"commentaire": "La séquence de vérification est fausse."})
        a.action_refuser()
        self.version.invalidate_recordset()
        self.assertTrue(self.version.approbation_refusee)
        with self.assertRaises(UserError) as e:
            self.version.action_release()
        self.assertIn("vérification", str(e.exception))

    def test_un_refus_sans_motif_est_refuse(self):
        with self.assertRaises(UserError):
            self._approbateur(self.tremblay).action_refuser()

    def test_une_personne_ne_se_prononce_qu_une_fois(self):
        self._approbateur(self.tremblay)
        with self.assertRaises(Exception):
            self._approbateur(self.tremblay)

    # -------------------------------------------------------- la diffusion
    def _element_avec_informe(self, partenaire):
        return self.env["project.knowledge.item"].create({
            "name": "Décision d'essai", "matrix_id": self.matrice.id,
            "section_id": self.section.id, "decision_id": "D01",
            "project_id": self.projet.id,
            "stakeholder_informed_ids": [(4, partenaire.id)]})

    def test_la_diffusion_suit_le_raci_de_la_matrice(self):
        contremaitre = self.env["res.partner"].create({"name": "Contremaître"})
        self._element_avec_informe(contremaitre)
        self._approbateur(self.tremblay).action_approuver()
        self.version.invalidate_recordset()
        self.version.action_release()
        self.version.action_diffuser_aux_informes()
        distributions = self.env["project.document.distribution"].search(
            [("version_id", "=", self.version.id)])
        self.assertEqual(distributions.mapped("partner_id"), contremaitre)

    def test_on_ne_diffuse_pas_deux_fois_a_la_meme_personne(self):
        contremaitre = self.env["res.partner"].create({"name": "Contremaître"})
        self._element_avec_informe(contremaitre)
        self._approbateur(self.tremblay).action_approuver()
        self.version.invalidate_recordset()
        self.version.action_release()
        self.version.action_diffuser_aux_informes()
        with self.assertRaises(UserError):
            self.version.action_diffuser_aux_informes()

    def test_on_ne_diffuse_pas_une_version_non_approuvee(self):
        contremaitre = self.env["res.partner"].create({"name": "Contremaître"})
        self._element_avec_informe(contremaitre)
        with self.assertRaises(UserError):
            self.version.action_diffuser_aux_informes()
