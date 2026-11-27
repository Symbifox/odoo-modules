"""Les liens d'une étape (18.0.2.9.0).

Ce qui compte : un lien se déclare sur l'étape comme une pièce, sort en bouton à
une étape faite ou en cours et nulle part ailleurs, n'accepte qu'une adresse
https://, n'est jamais une pièce (ni à la page, ni à la route de dépôt), et son
libellé est échappé comme le reste de la page.
"""
import re

from odoo.exceptions import ValidationError
from odoo.tests import HttpCase, tagged

from .test_portail import _Mandat

RENDEZ_VOUS = "https://rdv.exemple.invalid/prendre-rendez-vous"


@tagged("post_install", "-at_install")
class TestLiensDuPortail(HttpCase, _Mandat):

    def setUp(self):
        super().setUp()
        self.projet, self.colonnes = self._mandat(self.env)
        self.Element = self.env["bf.progression.document"]
        # L'étape 2 (colonnes[1]) est l'étape en cours du mandat d'essai.
        self.lien = self.Element.create({
            "kind": "link", "name": "Prendre rendez-vous", "url": RENDEZ_VOUS,
            "instructions": "Choisissez une plage de 30 minutes.",
            "stage_id": self.colonnes[1].id})
        self.projet.action_progression_portal_publish()
        self.adresse = "/progression/%s/%s" % (self.projet.id, self.projet.progression_portal_token)

    def _page(self):
        reponse = self.url_open(self.adresse)
        self.assertEqual(reponse.status_code, 200)
        return reponse.text

    def test_le_lien_sort_en_bouton_a_son_etape(self):
        page = self._page()
        bouton = re.search(r'<a[^>]*href="%s"[^>]*>' % re.escape(RENDEZ_VOUS), page)
        self.assertTrue(bouton, "aucun bouton vers l'adresse du lien")
        self.assertIn('target="_blank"', bouton.group(0))
        self.assertIn("noopener", bouton.group(0))
        self.assertIn("Prendre rendez-vous", page)
        self.assertIn("Choisissez une plage de 30 minutes.", page)
        # Dans le bloc de SON étape.
        blocs = page.split("o_progression_portal_step o_progression_portal_step_")
        porteurs = [b for b in blocs[1:] if RENDEZ_VOUS in b]
        self.assertEqual(len(porteurs), 1)
        self.assertIn("Pièces du client", porteurs[0])

    def test_un_lien_n_est_pas_une_piece(self):
        """Ni badge « À envoyer », ni formulaire de dépôt pour un lien."""
        page = self._page()
        self.assertNotIn("doc-%s" % self.lien.id, page)
        self.assertNotIn("/deposit/%s" % self.lien.id, page)
        self.assertEqual(self.projet._progression_portal_documents(
            {"state": "current", "stage_ids": [self.colonnes[1].id]}), [])

    def test_un_lien_d_une_etape_a_venir_ou_interne_ne_sort_pas(self):
        self.Element.create({"kind": "link", "name": "Signer en ligne",
                             "url": "https://signature.exemple.invalid/acte",
                             "stage_id": self.colonnes[3].id})
        self.Element.create({"kind": "link", "name": "Lien interne",
                             "url": "https://interne.exemple.invalid/",
                             "stage_id": self.colonnes[2].id})
        page = self._page()
        self.assertNotIn("signature.exemple.invalid", page)
        self.assertNotIn("interne.exemple.invalid", page)

    def test_un_lien_archive_ne_sort_pas(self):
        self.lien.active = False
        self.assertNotIn(RENDEZ_VOUS, self._page())

    def test_seule_une_adresse_https_est_admise(self):
        for adresse in ("http://rdv.exemple.invalid/", "javascript:alert(1)",
                        "JaVaScRiPt:alert(1)", "data:text/html,<b>x</b>", "//rdv.exemple.invalid",
                        "https://", "https://rdv.exemple.invalid/ espace",
                        "https://rdv.exemple.invalid/\nligne", "", False,
                        "https://rdv.exemple.invalid/\u00a0a", "https://rdv.exemple.invalid/\u200b",
                        "https://rdv.exemple.invalid/\u202egnp.exe", "https://rdv.exemple.invalid/\x85",
                        "https://" + "a" * 2050):
            with self.subTest(adresse=adresse), self.assertRaises(ValidationError):
                with self.env.cr.savepoint():
                    self.Element.create({"kind": "link", "name": "Mauvais lien",
                                         "url": adresse, "stage_id": self.colonnes[1].id})
        # Majuscules au schéma : admis.
        self.Element.create({"kind": "link", "name": "Bon lien", "url": "HTTPS://rdv.exemple.invalid/",
                             "stage_id": self.colonnes[1].id})

    def test_une_piece_n_a_pas_besoin_d_adresse(self):
        piece = self.Element.create({"name": "Bail signé", "stage_id": self.colonnes[1].id})
        self.assertEqual(piece.kind, "deposit")
        self.assertIn("doc-%s" % piece.id, self._page())

    def test_changer_une_piece_en_lien_exige_l_adresse(self):
        piece = self.Element.create({"name": "Bail signé", "stage_id": self.colonnes[1].id})
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            piece.kind = "link"

    def test_rien_ne_se_depose_sur_un_lien(self):
        """La route de dépôt (et le pont S3, qui passe par la même garde)
        refuse un lien comme une pièce inconnue."""
        page = self._page()
        csrf = re.search(r'csrf_token[^"]*"([^"]+)"', page).group(1)
        reponse = self.url_open(
            "/progression/%s/%s/deposit/%s" % (self.projet.id, self.projet.progression_portal_token, self.lien.id),
            data={"csrf_token": csrf}, files={"file": ("essai.pdf", b"%PDF-1.4 essai")},
            allow_redirects=False)
        self.assertEqual(reponse.status_code, 404)
        self.assertFalse(self.projet.progression_deposit_ids)

    def test_le_libelle_du_lien_est_echappe(self):
        self.lien.write({"name": '<img src=x onerror="alert(1)">Rendez-vous',
                         "instructions": "<script>alert(2)</script>"})
        page = self._page()
        self.assertNotIn('<img src=x onerror="alert(1)">', page)
        self.assertNotIn("<script>alert(2)</script>", page)
        self.assertIn("&lt;img", page)

    def test_une_adresse_devenue_invalide_ne_sort_pas(self):
        """Défense en profondeur : une adresse écrite en SQL, hors contrainte,
        ne sort pas en bouton."""
        self.env.cr.execute("UPDATE bf_progression_document SET url = 'javascript:alert(1)' WHERE id = %s",
                            (self.lien.id,))
        self.lien.invalidate_recordset(["url"])
        page = self._page()
        self.assertNotIn("javascript:alert(1)", page)
        self.assertNotIn("Prendre rendez-vous", page)

    def test_une_piece_deja_deposee_ne_devient_pas_un_lien(self):
        """Sans cette garde, la trace de dépôt pointait sur un lien, et le
        client perdait son « Reçu »."""
        piece = self.Element.create({"name": "Certificat XYZ", "stage_id": self.colonnes[1].id})
        self.env["bf.progression.deposit"].sudo()._verser(
            self.projet, piece, "certificat.pdf", b"%PDF-1.4 essai")
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            piece.write({"kind": "link", "url": RENDEZ_VOUS})
        self.assertEqual(piece.kind, "deposit")
        # Sans dépôt, le changement reste permis.
        vierge = self.Element.create({"name": "Pièce jamais reçue", "stage_id": self.colonnes[1].id})
        vierge.write({"kind": "link", "url": RENDEZ_VOUS})
        self.assertEqual(vierge.kind, "link")
