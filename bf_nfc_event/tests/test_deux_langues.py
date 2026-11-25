"""Le module se lit dans les deux langues.

🔴 Les autres essais du module ne prouvent que la SOURCE : on pourrait livrer un
`fr_CA.po` vide ou abîmé et ils passeraient tous. Ceux-ci lisent le français
rendu par la base, et ils échouent si le catalogue livré ne porte pas les
libellés qu'ils nomment. Les trois témoins passent par trois chemins de
chargement différents : `ir.model.fields` (libellé et aide) et
`ir.model.fields.selection`.

Les témoins ont été choisis dans le catalogue LIVRÉ et résolus en base
(`scripts/i18n_source_anglaise/temoins.py`) : aucun nom de champ n'est écrit
de mémoire ici.
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
            ['bf_nfc_event'], ["fr_CA"], overwrite=True)

    def test_un_libelle_de_champ_se_lit_dans_les_deux_langues(self):
        champ = self.env["ir.model.fields"].search(
            [("model", "=", 'bf.nfc.gesture'), ("name", "=", 'kind')], limit=1)
        self.assertTrue(champ, "le champ témoin a disparu : l'essai ne mesure rien")
        self.assertEqual(champ.with_context(lang="en_US").field_description, 'Kind')
        self.assertEqual(champ.with_context(lang="fr_CA").field_description, 'Nature')

    def test_une_valeur_de_liste_se_lit_dans_les_deux_langues(self):
        modele = self.env['bf.nfc.gesture']
        for langue, attendu in (("en_US", 'Record my attendance at the session'), ("fr_CA", 'Noter ma présence à la séance')):
            vue = modele.with_context(lang=langue)
            libelles = dict(vue._fields['kind']._description_selection(vue.env))
            self.assertEqual(libelles['event_presence'], attendu)

    # Pas d'essai d'aide : ce module n'a aucune aide de champ
    # traduisible, et un essai sans témoin ne mesurerait rien.

