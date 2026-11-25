"""Le module se lit dans les deux langues, et les citations officielles NON.

🔴 Les autres essais du module ne prouvent que la SOURCE : on pourrait livrer un
`fr_CA.po` vide ou abîmé et ils passeraient tous. Ceux-ci lisent le français
rendu par la base.

🔴 Le dernier essai fixe une DÉCISION, pas une mécanique : un extrait de loi
cité dans un registre de conformité doit rester citable tel quel le jour de
l'inspection. Il échouera si quelqu'un décide un jour de traduire ces onze
extraits sans le vouloir — et c'est son but.
"""

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_training_qc")
class TestDeuxLangues(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env["res.lang"]._activate_lang("en_US")
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_training_qc"], ["fr_CA"], overwrite=True)

    def test_un_libelle_de_champ_se_lit_dans_les_deux_langues(self):
        champ = self.env["ir.model.fields"].search(
            [("model", "=", "bf.training.statement"), ("name", "=", "payroll")], limit=1)
        self.assertTrue(champ, "le champ témoin a disparu : l'essai ne mesure rien")
        self.assertEqual(champ.with_context(lang="en_US").field_description, "Payroll")
        self.assertEqual(champ.with_context(lang="fr_CA").field_description,
                         "Masse salariale")

    def test_une_base_d_admissibilite_se_lit_dans_les_deux_langues(self):
        activite = self.env["bf.training.activity"]
        for langue, attendu in (
                ("en_US", "Training at a recognized educational institution"),
                ("fr_CA", "Formation auprès d'un établissement d'enseignement reconnu")):
            vue = activite.with_context(lang=langue)
            libelles = dict(vue._fields["qc_basis"]._description_selection(vue.env))
            self.assertEqual(libelles["etablissement"], attendu)

    def test_aucune_citation_officielle_n_est_traduite_au_catalogue(self):
        """🔴 DÉCISION, pas oubli. LégisQuébec publie des versions anglaises
        officielles ; les reprendre serait un travail de recherche à décider.
        Fabriquer une traduction maison d'un extrait de loi produirait une pièce
        fausse dans un registre qui sert de preuve le jour de l'inspection.

        ⚠️ L'essai lit le CATALOGUE LIVRÉ, pas la base. Les références vivent
        dans un `<data noupdate="1">` : sur une base où le module est déjà
        installé, ni le catalogue ni le fichier de données ne peuvent plus rien
        changer, et un essai qui lirait la base passerait quoi qu'on écrive.
        Le seul endroit où la décision se défend est le fichier livré.
        """
        import polib
        from odoo.tools import file_open
        with file_open("bf_training_qc/i18n/fr_CA.po") as f:
            catalogue = polib.pofile(f.read())
        references = self.env["bf.training.legal.reference"].search([])
        self.assertTrue(references, "aucune référence : l'essai ne mesure rien")
        citations = {r.with_context(lang="en_US").name for r in references}
        citations |= {r.with_context(lang="en_US").citation for r in references if r.citation}
        citations.discard(False)
        vus = 0
        for entree in catalogue:
            if entree.msgid in citations:
                vus += 1
                self.assertEqual(
                    entree.msgstr, entree.msgid,
                    "une citation officielle a reçu une traduction : %r" % entree.msgid[:60])
        self.assertGreaterEqual(
            vus, 10, "le catalogue ne porte pas les citations : l'essai ne mesure rien")
