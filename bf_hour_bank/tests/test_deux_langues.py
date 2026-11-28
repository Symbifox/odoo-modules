"""Le module se lit dans les deux langues.

🔴 Les autres essais du module ne prouvent que la SOURCE : on pourrait livrer un
`fr_CA.po` vide ou abîmé et ils passeraient tous. Ceux-ci lisent le français
rendu par la base, et ils échouent si le catalogue livré ne porte pas les
libellés qu'ils nomment. Les trois témoins passent par trois chemins de
chargement différents : `ir.model.fields` (libellé et aide) et
`ir.model.fields.selection`.

Les témoins ont été choisis dans le catalogue LIVRÉ et résolus en base :
aucun nom de champ n'est écrit de mémoire ici.
"""

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestDeuxLangues(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # ⚠️ Jamais de `lang` posé sur `cls.env` : ça change le contexte de TOUTE
        # la création de données de la classe, pas seulement la lecture d'un
        # libellé. Chaque essai lit avec `with_context`.
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env["res.lang"]._activate_lang("en_US")
        cls.env["ir.module.module"]._load_module_terms(
            ['bf_hour_bank'], ["fr_CA"], overwrite=True)

    def test_un_libelle_de_champ_se_lit_dans_les_deux_langues(self):
        champ = self.env["ir.model.fields"].search(
            [("model", "=", 'hour.bank.threshold.event'), ("name", "=", 'mode_snapshot')], limit=1)
        self.assertTrue(champ, "le champ témoin a disparu : l'essai ne mesure rien")
        self.assertEqual(champ.with_context(lang="en_US").field_description, 'Mode at the time of the trigger')
        self.assertEqual(champ.with_context(lang="fr_CA").field_description, 'Mode au moment du déclenchement')

    def test_une_valeur_de_liste_se_lit_dans_les_deux_langues(self):
        modele = self.env['hour.bank.client']
        for langue, attendu in (("en_US", 'Postpaid: bill the hours consumed'), ("fr_CA", 'Postpayé : facturer les heures consommées')):
            vue = modele.with_context(lang=langue)
            libelles = dict(vue._fields['invoice_mode']._description_selection(vue.env))
            self.assertEqual(libelles['postpaid'], attendu)

    def test_le_texte_d_aide_ne_reste_pas_anglais_pour_un_lecteur_francais(self):
        champ = self.env["ir.model.fields"].search(
            [("model", "=", 'hour.bank.client'), ("name", "=", 'invoice_split_by_project')], limit=1)
        self.assertTrue(champ, "le champ témoin a disparu : l'essai ne mesure rien")
        aide_fr = champ.with_context(lang="fr_CA").help or ""
        aide_en = champ.with_context(lang="en_US").help or ""
        self.assertIn('Par défaut, une ligne par projet. Sinon, une seule ligne pou', aide_fr)
        self.assertIn('By default, one line per project. Otherwise a single line fo', aide_en)
        # Le témoin qui compte : la source anglaise ne doit PAS être ce que lit un
        # francophone. Sans cette ligne, un catalogue vide passerait.
        self.assertNotIn('By default, one line per project. Otherwise a single line fo', aide_fr)

