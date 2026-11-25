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
            ['bf_persona'], ["fr_CA"], overwrite=True)

    def test_un_libelle_de_champ_se_lit_dans_les_deux_langues(self):
        champ = self.env["ir.model.fields"].search(
            [("model", "=", 'contact.persona'), ("name", "=", 'avg_payment_delay_days')], limit=1)
        self.assertTrue(champ, "le champ témoin a disparu : l'essai ne mesure rien")
        self.assertEqual(champ.with_context(lang="en_US").field_description, 'Average payment delay (days)')
        self.assertEqual(champ.with_context(lang="fr_CA").field_description, 'Délai moyen de paiement (jours)')

    def test_une_valeur_de_liste_se_lit_dans_les_deux_langues(self):
        modele = self.env['contact.persona']
        for langue, attendu in (("en_US", 'Average'), ("fr_CA", 'Moyen')):
            vue = modele.with_context(lang=langue)
            libelles = dict(vue._fields['payer_quality']._description_selection(vue.env))
            self.assertEqual(libelles['average'], attendu)

    def test_le_texte_d_aide_ne_reste_pas_anglais_pour_un_lecteur_francais(self):
        champ = self.env["ir.model.fields"].search(
            [("model", "=", 'contact.persona'), ("name", "=", 'avg_payment_delay_days')], limit=1)
        self.assertTrue(champ, "le champ témoin a disparu : l'essai ne mesure rien")
        aide_fr = champ.with_context(lang="fr_CA").help or ""
        aide_en = champ.with_context(lang="en_US").help or ""
        self.assertIn('Délai moyen entre la date de facture', aide_fr)
        self.assertIn('Average delay between the invoice date', aide_en)
        self.assertNotIn('Average delay between the invoice date', aide_fr)
