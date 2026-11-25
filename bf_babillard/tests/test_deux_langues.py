"""Le module se lit dans les deux langues, gabarits et réactions comprises.

🔴 Les autres essais du module ne prouvent que la SOURCE. Celui du catalogue
(`TestCatalogueFrancais`) le relit comme un FICHIER, ce qui ne dit rien de ce
que la base sert. Ceux-ci lisent le français rendu.
"""

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestDeuxLangues(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env["res.lang"]._activate_lang("en_US")
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_babillard"], ["fr_CA"], overwrite=True)

    def test_un_libelle_de_champ_se_lit_dans_les_deux_langues(self):
        champ = self.env["ir.model.fields"].search(
            [("model", "=", "bf.babillard.post"), ("name", "=", "lecture_requise")],
            limit=1)
        self.assertTrue(champ, "le champ témoin a disparu : l'essai ne mesure rien")
        self.assertEqual(champ.with_context(lang="en_US").field_description,
                         "Mandatory read")
        self.assertEqual(champ.with_context(lang="fr_CA").field_description,
                         "Lecture obligatoire")

    def test_un_type_de_publication_se_lit_dans_les_deux_langues(self):
        post = self.env["bf.babillard.post"]
        for langue, attendus in (("en_US", {"sondage": "Poll", "nouvelle": "News"}),
                                 ("fr_CA", {"sondage": "Sondage", "nouvelle": "Nouvelle"})):
            vue = post.with_context(lang=langue)
            libelles = dict(vue._fields["type_publication"]._description_selection(vue.env))
            for cle, attendu in attendus.items():
                self.assertEqual(libelles[cle], attendu)

    def test_les_reactions_livrees_portent_les_deux_langues(self):
        """🔴 Les douze réactions sont des DONNÉES sous `noupdate` : leur source
        anglaise n'atteint la base que par la migration `18.0.1.8.1`. Sans elle,
        un anglophone lisait « Coup de cœur » et « Ça m'a fait rire ».
        """
        reaction = self.env.ref("bf_babillard.reaction_coup_de_coeur")
        self.assertEqual(reaction.with_context(lang="en_US").name, "Love it")
        self.assertEqual(reaction.with_context(lang="fr_CA").name, "Coup de cœur")

    def test_le_gabarit_de_l_annonce_porte_les_deux_corps(self):
        """🔴 Le corps vit aussi dans un `<data noupdate="1">`."""
        gabarit = self.env.ref("bf_babillard.mail_template_annonce_a_lire")
        corps_en = gabarit.with_context(lang="en_US").body_html or ""
        corps_fr = gabarit.with_context(lang="fr_CA").body_html or ""
        self.assertIn("Read and confirm", corps_en)
        self.assertNotIn("Lire et confirmer", corps_en)
        self.assertIn("Lire et confirmer", corps_fr)
        self.assertNotIn("Read and confirm", corps_fr)
