"""Le module se lit dans les deux langues, les six gabarits compris.

🔴 Les autres essais du module ne prouvent que la SOURCE : on pourrait livrer un
`fr_CA.po` vide ou abîmé et ils passeraient tous. Ceux-ci lisent le français
rendu par la base.
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
            ["bf_celebrations"], ["fr_CA"], overwrite=True)

    def test_un_libelle_de_champ_se_lit_dans_les_deux_langues(self):
        champ = self.env["ir.model.fields"].search(
            [("model", "=", "bf.celebration.board"), ("name", "=", "delivery_date")],
            limit=1)
        self.assertTrue(champ, "le champ témoin a disparu : l'essai ne mesure rien")
        self.assertEqual(champ.with_context(lang="en_US").field_description,
                         "Delivery planned")
        self.assertEqual(champ.with_context(lang="fr_CA").field_description,
                         "Livraison prévue")

    def test_une_occasion_se_lit_dans_les_deux_langues(self):
        occasion = self.env["bf.celebration.occasion"]
        for langue, attendus in (
                ("en_US", {"birthday": "Birthday", "retirement": "Retirement"}),
                ("fr_CA", {"birthday": "Anniversaire", "retirement": "Retraite"})):
            vue = occasion.with_context(lang=langue)
            libelles = dict(vue._fields["occasion_type"]._description_selection(vue.env))
            for cle, attendu in attendus.items():
                self.assertEqual(libelles[cle], attendu)

    def test_les_six_gabarits_portent_les_deux_corps(self):
        """🔴 Les six corps vivent dans un `<data noupdate="1">` : leur source
        anglaise n'atteint la base que par la migration `18.0.2.2.1`. Celui de
        la LIVRAISON portait en plus une rédaction antérieure, que la garde
        « non retouché ? » refusait : sans la forme déclarée dans la migration,
        la personne fêtée recevait son courriel en français.
        """
        attendus = {
            "mail_template_invitation": ("Four possible answers", "Quatre réponses possibles"),
            # ⚠️ Côté français, le témoin est une phrase présente dans les DEUX
            # rédactions. La base portait une rédaction ANTÉRIEURE de ce corps,
            # figée par `noupdate` depuis l'installation ; la migration l'a
            # recopiée en `fr_CA` et a mis l'anglais COURANT en `en_US`. Un
            # francophone lit donc ce qu'il lisait déjà, un anglophone lit enfin
            # l'anglais. Rafraîchir la rédaction française est une décision de
            # produit, pas de traduction : à trancher à part.
            "mail_template_livraison": ("Your colleagues have written",
                                        "Vos collègues vous ont écrit"),
            "mail_template_merci": ("has received their card", "a reçu sa carte"),
            "mail_template_invitation_signataire": ("A group card is open",
                                                    "Une carte collective est ouverte"),
            "mail_template_rappel_organisateur": ("Nothing is open yet",
                                                  "Rien n'est encore ouvert"),
            "mail_template_tableau_mince": ("goes out soon", "part bientôt"),
        }
        for xmlid, (en, fr) in attendus.items():
            gabarit = self.env.ref("bf_celebrations.%s" % xmlid)
            corps_en = gabarit.with_context(lang="en_US").body_html or ""
            corps_fr = gabarit.with_context(lang="fr_CA").body_html or ""
            self.assertIn(en, corps_en, xmlid)
            self.assertNotIn(fr, corps_en, xmlid)
            self.assertIn(fr, corps_fr, xmlid)
            self.assertNotIn(en, corps_fr, xmlid)

    def test_les_noms_des_travaux_planifies_sont_bascules(self):
        cron = self.env.ref("bf_celebrations.cron_livrer")
        porteur = cron.ir_actions_server_id
        self.assertEqual(porteur.with_context(lang="en_US").name,
                         "Celebrations: deliver the boards that are due")
        self.assertEqual(porteur.with_context(lang="fr_CA").name,
                         "Célébrations : livrer les tableaux dus")
