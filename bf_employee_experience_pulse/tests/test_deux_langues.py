"""Le module se lit dans les deux langues.

🔴 Les autres essais du module ne prouvent que la SOURCE : on pourrait livrer un
`fr_CA.po` vide ou abîmé et ils passeraient tous. Ceux-ci lisent le français
rendu par la base, et ils échouent si le catalogue livré ne porte pas les
libellés qu'ils nomment. Les trois témoins passent par trois chemins de
chargement différents : `ir.model.fields` (libellé et aide) et
`ir.model.fields.selection`.
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
            ['bf_employee_experience_pulse'], ["fr_CA"], overwrite=True)

    def test_un_libelle_de_champ_se_lit_dans_les_deux_langues(self):
        champ = self.env["ir.model.fields"].search(
            [("model", "=", 'bf.ex.pulse.score'), ("name", "=", 'is_displayable')], limit=1)
        self.assertTrue(champ, "le champ témoin a disparu : l'essai ne mesure rien")
        self.assertEqual(champ.with_context(lang="en_US").field_description, 'Above the threshold')
        self.assertEqual(champ.with_context(lang="fr_CA").field_description, 'Au-dessus du seuil')

    def test_une_valeur_de_liste_se_lit_dans_les_deux_langues(self):
        modele = self.env['bf.ex.pulse.campaign']
        for langue, attendu in (("en_US", 'By department'), ("fr_CA", 'Par département')):
            vue = modele.with_context(lang=langue)
            libelles = dict(vue._fields['segment_mode']._description_selection(vue.env))
            self.assertEqual(libelles['department'], attendu)

    def test_le_texte_d_aide_ne_reste_pas_anglais_pour_un_lecteur_francais(self):
        champ = self.env["ir.model.fields"].search(
            [("model", "=", 'bf.ex.pulse.metric'), ("name", "=", 'code')], limit=1)
        self.assertTrue(champ, "le champ témoin a disparu : l'essai ne mesure rien")
        aide_fr = champ.with_context(lang="fr_CA").help or ""
        aide_en = champ.with_context(lang="en_US").help or ""
        self.assertIn('Identifiant stable, employé par les agrégats', aide_fr)
        self.assertIn('A stable identifier, used by the aggregates', aide_en)
        self.assertNotIn('A stable identifier, used by the aggregates', aide_fr)
