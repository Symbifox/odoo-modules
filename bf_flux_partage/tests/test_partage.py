# -*- coding: utf-8 -*-
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests.common import HttpCase, tagged

from odoo.addons.bf_flux.models.flux_source import FluxSource, analyser


@tagged("post_install", "-at_install")
class TestPartage(HttpCase):

    def setUp(self):
        super().setUp()
        Src = self.env["bf.flux.source"]
        self.src = Src.create({"name": "Veille défense", "url": "https://flux.example.com/d"})
        self.liste = self.env["bf.flux.liste"].create({
            "name": "Veille du projet Arctique", "description": "Pour l'équipe et le client",
            "source_ids": [(6, 0, self.src.ids)],
            "termes": "défense\ncontrat",
        })
        E = self.env["bf.flux.element"]
        self.e1 = E.create({
            "titre": "Contrat de défense <script>alert(1)</script>", "cle": "p-1",
            "lien": "https://nouvelles.example.com/1", "emetteur": "Télésat",
            "resume": "Un contrat de défense pour l'Arctique.",
            "texte": "TEXTE COMPLET À NE JAMAIS PUBLIER", "texte_etat": "ok",
            "image_url": "https://img.example.com/1.jpg",
            "date_publication": fields.Datetime.now(), "source_ids": [(6, 0, self.src.ids)]})
        self.e2 = E.create({
            "titre": "Écarté par le jugement", "cle": "p-2", "lien": "https://nouvelles.example.com/2",
            "resume": "contrat", "source_ids": [(6, 0, self.src.ids)]})
        R = self.env["bf.flux.retenue"]
        R.create({"liste_id": self.liste.id, "element_id": self.e1.id,
                  "motifs": "MOTIF-INTERNE", "raison": "RAISON-INTERNE", "note": 88})
        R.create({"liste_id": self.liste.id, "element_id": self.e2.id, "etat": "ecarte"})
        self.part = self.env["bf.flux.partage"].create({"name": "Client Arctique", "liste_id": self.liste.id})

    def _get(self, chemin):
        return self.url_open(chemin, allow_redirects=False)

    def test_jeton_et_echeance_par_defaut(self):
        self.assertGreaterEqual(len(self.part.jeton), 40)
        self.assertEqual(self.part.date_echeance, fields.Date.today() + timedelta(days=90))
        autre = self.env["bf.flux.partage"].create({"name": "B", "liste_id": self.liste.id})
        self.assertNotEqual(autre.jeton, self.part.jeton)

    def test_page_publique_sans_connexion(self):
        rep = self._get(f"/flux/partage/{self.part.jeton}")
        self.assertEqual(rep.status_code, 200)
        page = rep.text
        self.assertTrue(page.startswith("<!DOCTYPE html>"))
        self.assertNotIn("&lt;!DOCTYPE", page)
        self.assertIn("Veille du projet Arctique", page)
        self.assertIn("https://nouvelles.example.com/1", page)
        self.assertIn("Un contrat de défense pour l", page)
        # Échappé, jamais exécuté.
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)
        # Ce qui ne sort jamais.
        for interne in ("TEXTE COMPLET", "MOTIF-INTERNE", "RAISON-INTERNE", "Écarté par le jugement", "/flux/lire/",
                        "Pour l'équipe et le client", "Pour l&#39;équipe et le client"):
            self.assertNotIn(interne, page)
        self.assertEqual(rep.headers["X-Robots-Tag"], "noindex, nofollow")
        self.assertEqual(rep.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(rep.headers["Cache-Control"], "no-store")
        self.part.invalidate_recordset()
        self.assertEqual(self.part.acces_count, 1)
        self.assertTrue(self.part.dernier_acces)

    def test_rss_se_relit(self):
        rep = self._get(f"/flux/partage/{self.part.jeton}/rss")
        self.assertEqual(rep.status_code, 200)
        self.assertIn("application/rss+xml", rep.headers["Content-Type"])
        # Notre propre lecteur de flux le relit : c'est un RSS valide.
        (rec,) = analyser(rep.content)
        self.assertEqual(rec["titre"], "Contrat de défense <script>alert(1)</script>")
        self.assertEqual(rec["lien"], "https://nouvelles.example.com/1")
        self.assertEqual(rec["cle"], "p-1")
        self.assertEqual(rec.get("image"), "https://img.example.com/1.jpg")
        self.assertNotIn(b"TEXTE COMPLET", rep.content)
        self.assertNotIn(b"MOTIF-INTERNE", rep.content)
        self.assertNotIn("Pour l'équipe et le client".encode(), rep.content)

    def test_introuvable_meme_reponse(self):
        for chemin in ("/flux/partage/inconnu-inconnu-inconnu-inconnu",
                       "/flux/partage/court", "/flux/partage/inconnu-inconnu-inconnu-inconnu/rss"):
            self.assertEqual(self._get(chemin).status_code, 404, chemin)
        jeton = self.part.jeton
        self.part.date_echeance = fields.Date.today() - timedelta(days=1)
        self.assertEqual(self._get(f"/flux/partage/{jeton}").status_code, 404)
        self.part.date_echeance = False  # sans échéance
        self.assertEqual(self._get(f"/flux/partage/{jeton}").status_code, 200)
        self.part.action_revoquer()
        self.assertEqual(self.part.etat, "revoque")
        self.assertEqual(self._get(f"/flux/partage/{jeton}").status_code, 404)
        self.assertEqual(self._get(f"/flux/partage/{jeton}/rss").status_code, 404)

    def test_regenerer_coupe_l_ancien_lien(self):
        ancien = self.part.jeton
        self.part.action_regenerer()
        self.assertNotEqual(self.part.jeton, ancien)
        self.assertEqual(self._get(f"/flux/partage/{ancien}").status_code, 404)
        self.assertEqual(self._get(f"/flux/partage/{self.part.jeton}").status_code, 200)

    def test_liste_archivee_ne_repond_plus(self):
        self.liste.active = False
        self.assertEqual(self._get(f"/flux/partage/{self.part.jeton}").status_code, 404)

    def test_regenerer_reserve_a_la_gestion(self):
        from odoo.exceptions import AccessError
        u = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Interne", "login": "flux_interne_regen",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])]})
        jeton = self.part.jeton
        with self.assertRaises(AccessError):
            self.env["bf.flux.partage"].with_user(u).browse(self.part.id).action_regenerer()
        self.assertEqual(self.part.jeton, jeton)

    def test_sources_d_autres_listes_ne_sortent_pas(self):
        privee = self.env["bf.flux.source"].create({
            "name": "Alerte privée", "url": "https://alertes.example.com/feeds/JETON-PRIVE"})
        self.e1.source_ids = [(4, privee.id)]
        page = self._get(f"/flux/partage/{self.part.jeton}").text
        rss = self._get(f"/flux/partage/{self.part.jeton}/rss").content
        self.assertIn("Veille défense", page)
        self.assertNotIn("Alerte privée", page)
        self.assertNotIn(b"Alerte priv", rss)
        self.assertNotIn(b"JETON-PRIVE", rss)
        self.assertNotIn(b"flux.example.com/d", rss, "aucune adresse de flux")

    def test_liens_d_une_liste_reserves_a_la_gestion(self):
        u = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Membre", "login": "flux_membre_partage",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])]})
        from odoo.exceptions import AccessError
        with self.assertRaises(AccessError):
            self.env["bf.flux.partage"].with_user(u).search([])
