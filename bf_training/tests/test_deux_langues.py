"""Le module se lit dans les deux langues, et le gabarit de relance aussi.

🔴 Tous les autres essais du module ne prouvent que la SOURCE : on pourrait
livrer un `fr_CA.po` vide ou abîmé et ils passeraient tous au vert. Ceux-ci
lisent le français rendu par la base, et ils échouent si le catalogue livré ne
porte pas les libellés qu'ils nomment.

⚠️ Le catalogue est rechargé dans chaque essai (`_load_module_terms`,
`overwrite=True`) : l'essai ne suppose pas qu'une montée l'a déjà posé, et il
mesure le FICHIER livré, pas un état laissé par un geste précédent.
"""

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_training")
class TestDeuxLangues(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env["res.lang"]._activate_lang("en_US")
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_training"], ["fr_CA"], overwrite=True)

    def test_un_libelle_de_champ_se_lit_dans_les_deux_langues(self):
        champ = self.env["ir.model.fields"].search(
            [("model", "=", "bf.training.requirement"), ("name", "=", "legal_basis")],
            limit=1)
        self.assertTrue(champ, "le champ témoin a disparu : l'essai ne mesure rien")
        self.assertEqual(champ.with_context(lang="en_US").field_description,
                         "Regulatory basis")
        self.assertEqual(champ.with_context(lang="fr_CA").field_description,
                         "Base réglementaire")

    def test_une_valeur_de_liste_se_lit_dans_les_deux_langues(self):
        """Trois chemins de chargement differents, pas un seul répété."""
        activite = self.env["bf.training.activity"]
        for langue, attendu in (("en_US", "On-the-job training"),
                                ("fr_CA", "Entraînement à la tâche")):
            vue = activite.with_context(lang=langue)
            libelles = dict(vue._fields["mode"]._description_selection(vue.env))
            self.assertEqual(libelles["on_the_job"], attendu)

    def test_le_texte_d_aide_ne_reste_pas_anglais_pour_un_lecteur_francais(self):
        champ = self.env["ir.model.fields"].search(
            [("model", "=", "bf.training.activity"), ("name", "=", "validity_months")],
            limit=1)
        aide_fr = champ.with_context(lang="fr_CA").help or ""
        aide_en = champ.with_context(lang="en_US").help or ""
        self.assertIn("ne périme pas", aide_fr)
        self.assertNotIn("never expires", aide_fr)
        self.assertIn("never expires", aide_en)

    def test_le_gabarit_de_relance_porte_les_deux_corps(self):
        """🔴 Le corps vit dans un `<data noupdate="1">` : la source anglaise
        n'atteint la base QUE par la migration `18.0.1.4.3`. Sans elle, un
        anglophone recevait le courriel en français, catalogue ou pas — et rien
        dans le reste des essais ne l'aurait dit.

        ⚠️ La virgule décimale est le point mesuré : le corps français écrit
        « 1,5 heures », l'anglais « 1.5 hours ». Les deux corps ne sont donc pas
        une traduction mot à mot, et c'est voulu.

        ⚠️ Cet essai-ci mesure la MIGRATION, pas le catalogue. Abîmer le corps
        français du `fr_CA.po` ne le fait pas tomber, et c'est normal :
        `_load_module_terms(overwrite=True)` laisse intacte la clé d'un
        enregistrement `noupdate`, donc le français que la base sert ici est
        celui que la migration y a posé. L'entrée du catalogue reste
        nécessaire pour une base NEUVE, où le gabarit est créé en anglais et le
        catalogue pose son français au premier chargement.
        """
        gabarit = self.env.ref("bf_training.mail_template_training_reminder")
        corps_en = gabarit.with_context(lang="en_US").body_html or ""
        corps_fr = gabarit.with_context(lang="fr_CA").body_html or ""
        self.assertIn("The training", corps_en)
        self.assertIn("Open the training", corps_en)
        self.assertNotIn("La formation", corps_en)
        self.assertIn("La formation", corps_fr)
        self.assertIn("Ouvrir la formation", corps_fr)
        self.assertNotIn("The training", corps_fr)
        # La virgule decimale appartient au seul corps francais.
        self.assertIn(".replace('.', ',')", corps_fr)
        self.assertNotIn(".replace('.', ',')", corps_en)

    def test_les_noms_du_cron_et_des_categories_sont_bascules(self):
        """Les autres valeurs que `noupdate` figeait : trois travaux planifiés
        et six catégories semées. Mesurées sur la BASE, pas sur le fichier."""
        cron = self.env.ref("bf_training.cron_training_refresh_expiry")
        porteur = cron.ir_actions_server_id
        self.assertEqual(porteur.with_context(lang="en_US").name,
                         "Training register: refresh the validity dates")
        self.assertEqual(porteur.with_context(lang="fr_CA").name,
                         "Registre de formation : rafraîchir les validités")
        categorie = self.env.ref("bf_training.category_sst")
        self.assertEqual(categorie.with_context(lang="en_US").name,
                         "Occupational health and safety")
        self.assertEqual(categorie.with_context(lang="fr_CA").name,
                         "Santé et sécurité du travail")
