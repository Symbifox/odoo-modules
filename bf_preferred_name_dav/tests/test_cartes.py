# -*- coding: utf-8 -*-
"""Les pronoms dans les cartes servies en CardDAV, en sortie seulement."""

from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_dav_server.dav import vcard as V

from ..models.dav_carddav import property_name


@tagged("post_install", "-at_install", "bf_preferred_name")
class TestCartes(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.personne = cls.env["res.partner"].create({
            "name": "Sam Exemple", "email": "sam@exemple.example",
            "bf_pronouns": "iel, they/them", "bf_grammatical_gender": "neuter"})
        cls.rien = cls.env["res.partner"].create({"name": "Lou Exemple"})
        cls.societe = cls.env["res.partner"].create({
            "name": "Société Exemple", "is_company": True, "bf_pronouns": "iel"})
        cls.Dav = cls.env["bf.dav.service"]

    def _carte(self, partner):
        return self.Dav._book_body(None, {"key": "p%d" % partner.id})

    def test_la_carte_porte_les_pronoms(self):
        carte = V.unfold(self._carte(self.personne))
        self.assertIn("PRONOUNS:iel\\, they/them", carte)
        self.assertIn("GRAMGENDER:neuter", carte)
        self.assertTrue(carte.rstrip().endswith("END:VCARD"))
        V.parse_card(self._carte(self.personne))  # reste une carte valide

    def test_rien_pour_qui_n_a_rien_donne_ni_une_societe(self):
        for partner in (self.rien, self.societe):
            carte = self._carte(partner)
            self.assertNotIn("PRONOUNS", carte)
            self.assertNotIn("GRAMGENDER", carte)

    def test_ce_que_renvoie_un_telephone_n_est_pas_garde(self):
        renvoyee = self._carte(self.personne).replace(
            "END:VCARD", "item1.PRONOUNS;LANGUAGE=fr:elle\r\nNOTE:à garder\r\nEND:VCARD")
        carte = V.parse_card(renvoyee)
        extra = self.Dav._card_extra(carte)
        self.assertFalse([ln for ln in extra if property_name(ln) in ("PRONOUNS", "GRAMGENDER")])
        self.assertTrue([ln for ln in extra if property_name(ln) == "NOTE"], "le reste est gardé")

    def test_nom_de_propriete(self):
        self.assertEqual(property_name("item1.PRONOUNS;LANGUAGE=fr:iel"), "PRONOUNS")
        self.assertEqual(property_name("gramgender:feminine"), "GRAMGENDER")

    def _carnet(self):
        return self.env["bf.dav.addressbook"].search([("kind", "=", "company")], limit=1)

    def test_les_requetes_groupees_portent_les_pronoms(self):
        # Multiget, query et sync-collection.
        from types import SimpleNamespace
        noeud = SimpleNamespace(book=SimpleNamespace(kind="company"))
        membres = [{"key": "p%d" % self.personne.id}, {"key": "p%d" % self.rien.id}]
        self.Dav._book_bodies(noeud, membres)
        self.assertIn("PRONOUNS:iel", V.unfold(membres[0]["body"]))
        self.assertNotIn("PRONOUNS", membres[1]["body"])
        self.assertEqual(membres[0]["body"], self._carte(self.personne), "même corps que le GET")

    def test_l_etag_change_a_la_pose_sans_rien_ecrire(self):
        # Sans écriture, l'ETag de bf_dav_server seul
        # ne bouge pas, et un téléphone garde la carte sans pronoms.
        from ..models.dav_carddav import BfDavServiceCarddav
        carnet = self._carnet()
        if not carnet:
            self.skipTest("pas de carnet d'entreprise")
        self.personne.write({"bf_dav_href": "sam-essai.vcf"})
        self.rien.write({"bf_dav_href": "lou-essai.vcf"})
        noeud = type("Noeud", (), {"book": carnet})()
        cles = ["p%d" % self.personne.id, "p%d" % self.rien.id]
        pont = {m["key"]: m["etag"] for m in self.Dav._book_members(noeud, keys=cles)}
        seul = {m["key"]: m["etag"] for m in super(BfDavServiceCarddav, self.Dav)._book_members(noeud, keys=cles)}
        if len(seul) < 2:
            self.skipTest("les fiches ne sont pas dans le carnet de la société")
        self.assertNotEqual(pont[cles[0]], seul[cles[0]], "la carte aux pronoms change d'ETag")
        self.assertEqual(pont[cles[0]], self.Dav._etag("card-pronouns", seul[cles[0]]))
        self.assertEqual(pont[cles[1]], seul[cles[1]], "les autres cartes ne bougent pas")

    def test_changer_les_pronoms_est_journalise(self):
        self.personne.write({"bf_dav_href": "sam-essaib.vcf"})
        Change = self.env["bf.dav.change"].sudo()
        cle = [("resource_key", "=", "p%d" % self.personne.id)]
        avant = Change.search_count(cle)
        self.personne.write({"bf_pronouns": "elle"})
        self.assertGreater(Change.search_count(cle), avant)
        avant = Change.search_count(cle)
        self.personne.write({"bf_grammatical_gender": "feminine"})
        self.assertGreater(Change.search_count(cle), avant)
        # Témoin : un champ que ni le module ni le pont ne suivent.
        avant = Change.search_count(cle)
        self.personne.write({"ref": "témoin-essai"})
        self.assertEqual(Change.search_count(cle), avant)

    def test_un_renvoi_d_avant_la_pose_n_est_pas_servi_en_double(self):
        # Des pronoms gardés avec les données de l'usager.
        cle = "p%d" % self.personne.id
        self.env["bf.dav.user.data"]._store(self.env.user, cle, {"": ["PRONOUNS:elle", "NOTE:x"]}, {})
        carte = V.unfold(self._carte(self.personne))
        self.assertEqual(carte.count("PRONOUNS"), 1)
        self.assertIn("PRONOUNS:iel", carte)
        self.assertIn("NOTE:x", carte)
