"""La porte /q/, jouée en HTTP."""
from odoo.tests import HttpCase, tagged

from .commun import monter


@tagged("post_install", "-at_install")
class TestPorte(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        monter(cls)

    def _get(self, chemin):
        return self.url_open(chemin, allow_redirects=False)

    def test_code_inconnu_404(self):
        self.assertEqual(self._get("/q/ZZZZZZZZ").status_code, 404)

    def test_vierge_anonyme_page_neutre(self):
        tag = self.etiquettes[0]
        reponse = self._get("/q/%s" % tag.code)
        self.assertEqual(reponse.status_code, 200)
        self.assertIn("pas encore", reponse.text)
        self.assertIn("/web/login?redirect=", reponse.text)

    def test_vierge_gestion_mene_a_la_fiche(self):
        self.authenticate("qr_gestion", "qr_gestion_mdp")
        tag = self.etiquettes[0]
        reponse = self._get("/q/%s" % tag.code)
        self.assertIn(reponse.status_code, (302, 303))
        self.assertIn("/odoo/bf.nfc.tag/%s" % tag.id, reponse.headers["Location"])

    def test_vierge_concierge_mene_a_la_fiche(self):
        self.authenticate("qr_concierge", "qr_concierge_mdp")
        reponse = self._get("/q/%s" % self.etiquettes[0].code)
        self.assertIn(reponse.status_code, (302, 303))

    def test_vierge_interne_page_neutre(self):
        self.authenticate("qr_interne", "qr_interne_mdp")
        reponse = self._get("/q/%s" % self.etiquettes[0].code)
        self.assertEqual(reponse.status_code, 200)
        self.assertIn("ne peut pas associer", reponse.text)

    def test_adresse_publique_suivie_et_journalisee(self):
        tag = self.etiquettes[1]
        tag.with_user(self.gestion)._associer(self.geste_url, url="https://symbifox.com/guide",
                                              public=True)
        reponse = self._get("/q/%s" % tag.code)
        self.assertIn(reponse.status_code, (302, 303))
        self.assertEqual(reponse.headers["Location"], "https://symbifox.com/guide")
        journal = tag.tap_ids
        self.assertEqual(journal.mapped("door"), ["qr_public"])
        self.assertEqual(tag.tap_count, 1)

    def test_adresse_non_publique_passe_par_la_connexion(self):
        tag = self.etiquettes[2]
        tag.with_user(self.gestion)._associer(self.geste_url, url="https://symbifox.com", public=False)
        reponse = self._get("/q/%s" % tag.code)
        self.assertIn("/web/login", reponse.headers.get("Location", ""))

    def test_fiche_interne_passe_par_la_connexion(self):
        tag = self.etiquettes[3]
        tag.with_user(self.gestion)._associer(self.geste_open, cible=self.partenaire, public=True)
        reponse = self._get("/q/%s" % tag.code)
        self.assertIn("/web/login", reponse.headers.get("Location", ""))
        self.assertFalse(tag.tap_ids)

    def test_geste_qui_ecrit_jamais_par_la_porte_publique(self):
        tag = self.etiquettes[4]
        tag.with_user(self.gestion)._associer(self.geste_note, cible=self.partenaire)
        # Même forcé en base, le drapeau public ne fait pas agir un geste qui écrit.
        tag.sudo().qr_public = True
        reponse = self._get("/q/%s" % tag.code)
        self.assertIn("/web/login", reponse.headers.get("Location", ""))
        self.assertFalse(self.partenaire.message_ids.filtered(lambda m: "Pastille" in (m.body or "")))

    def test_geste_adresse_declare_ecrivant_reste_ferme(self):
        # Un geste du catalogue retouché par la gestion (« Ce geste écrit » coché) : la
        # porte publique ne le sert plus, même si l'étiquette était publique.
        geste = self.env["bf.nfc.gesture"].create({
            "name": "Adresse tracée", "code": "essai_url_ecrit", "kind": "url", "writes": True})
        tag = self.etiquettes[7]
        tag.with_user(self.gestion)._associer(self.geste_url, url="https://symbifox.com", public=True)
        tag.sudo().gesture_id = geste
        reponse = self._get("/q/%s" % tag.code)
        self.assertIn("/web/login", reponse.headers.get("Location", ""))

    def test_connecte_passe_par_la_porte_du_socle(self):
        tag = self.etiquettes[5]
        tag.with_user(self.gestion)._associer(self.geste_open, cible=self.partenaire)
        self.authenticate("qr_interne", "qr_interne_mdp")
        reponse = self._get("/q/%s" % tag.code.lower())
        self.assertTrue(reponse.headers["Location"].endswith("/nfc/%s" % tag.code))

    def test_etiquette_retiree_404(self):
        tag = self.etiquettes[6]
        tag.active = False
        self.assertEqual(self._get("/q/%s" % tag.code).status_code, 404)

    def test_planche_d_un_autre_404(self):
        assistant = self.env["bf.qr.imprimer"].with_user(self.gestion).create(
            {"tag_ids": [(6, 0, self.etiquettes[:2].ids)]})
        self.authenticate("qr_concierge", "qr_concierge_mdp")
        self.assertEqual(self._get("/bf_qr/planche/%s" % assistant.id).status_code, 404)
        self.authenticate("qr_gestion", "qr_gestion_mdp")
        reponse = self._get("/bf_qr/planche/%s" % assistant.id)
        self.assertEqual(reponse.status_code, 200)
        self.assertTrue(reponse.content.startswith(b"%PDF"))

    def test_tableur_prerempli(self):
        self.authenticate("qr_interne", "qr_interne_mdp")
        self.assertEqual(self._get("/bf_qr/modele/%s" % self.lot.id).status_code, 403)
        self.authenticate("qr_concierge", "qr_concierge_mdp")
        reponse = self._get("/bf_qr/modele/%s" % self.lot.id)
        self.assertEqual(reponse.status_code, 200)
        import io
        import openpyxl
        feuille = openpyxl.load_workbook(io.BytesIO(reponse.content)).active
        self.assertEqual(feuille["A1"].value, "etiquette")
        self.assertEqual(feuille["A2"].value, "TST-0001")
        self.assertEqual(feuille.max_row, 11)
