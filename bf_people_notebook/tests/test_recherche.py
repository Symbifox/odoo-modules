"""Retrouver quelqu'un par ce dont on se souvient : un trait, un lieu, un intérêt, une
phrase d'une note. Toujours sous les droits de qui cherche."""
from odoo.tests import tagged

from .common import SECRET, FoyerCase


@tagged("post_install", "-at_install")
class TestRecherche(FoyerCase):

    def cherche(self, user, texte):
        return self.as_(user).search([("everything", "ilike", texte)])

    def test_on_retrouve_par_n_importe_quel_detail_de_la_fiche(self):
        for souvenir in ("Ondi", "casque", "Café du quai", "Rimouski", "Gaspésie",
                         "Festival", "kayak", "marathon", "vélo"):
            self.assertEqual(self.cherche(self.alex, souvenir), self.fiche, souvenir)

    def test_on_retrouve_par_le_texte_d_une_note_liee(self):
        self.env["bf.note"].with_user(self.alex).create({
            "body": "<p>Il connaît un luthier à Matane</p>",
            "link_ids": [(0, 0, {"res_model": "bf.people.person", "res_id": self.fiche.id})],
        })
        self.assertEqual(self.cherche(self.alex, "luthier"), self.fiche)

    def test_la_note_privee_d_alex_ne_guide_pas_sam(self):
        self.env["bf.note"].with_user(self.alex).create({
            "body": "<p>Il connaît un luthier à Matane</p>",
            "link_ids": [(0, 0, {"res_model": "bf.people.person", "res_id": self.fiche.id})],
        })
        self.partager_avec_sam()
        self.assertFalse(self.cherche(self.sam, "luthier"))
        self.assertEqual(self.cherche(self.sam, "casque"), self.fiche)

    def test_une_autre_personne_ne_trouve_rien(self):
        for souvenir in (SECRET, "casque", "Festival", "Gaspésie"):
            self.assertFalse(self.cherche(self.lee, souvenir), souvenir)
            self.assertFalse(self.cherche(self.carole, souvenir), souvenir)

    def test_les_filtres_du_quotidien(self):
        Person = self.as_(self.alex)
        self.assertIn(self.fiche, Person.search([("is_mine", "=", True)]))
        self.assertIn(self.fiche, Person.search([("partner_id", "=", False)]))
        self.assertIn(self.fiche, Person.search([("name_uncertain", "=", True)]))
        self.partager_avec_sam()
        self.assertEqual(self.as_(self.sam).search([("is_mine", "=", False)]), self.fiche)
        self.assertFalse(self.as_(self.sam).search([("is_mine", "=", True)]))

    def test_regrouper_par_occasion_ne_compte_que_ses_fiches(self):
        groupes = self.as_(self.alex).read_group([], ["occasion_id"], ["occasion_id"])
        self.assertEqual(groupes[0]["occasion_id_count"], 1)
        self.assertFalse(self.as_(self.lee).read_group([], ["occasion_id"], ["occasion_id"]))
        self.assertEqual(self.gaspesie.with_user(self.alex).person_count, 1)
        self.assertEqual(self.jazz.with_user(self.alex).person_count, 1)
