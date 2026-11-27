"""La barre de titre des applis installées suit la barre de navigation (18.0.3.33.0).

Chrome et Edge colorent la barre de titre d'une PWA avec la balise
``<meta name="theme-color">`` de la page, qui passe devant le ``theme_color`` du
manifeste. Odoo y écrit son mauve en dur dans ``web.webclient_bootstrap`` :
un manifeste à la marque ne suffisait donc pas, toutes les applis installées
restaient mauves.
"""
import json
import re

from odoo.tests import HttpCase, new_test_user, tagged

MOT_DE_PASSE = 'essai-barre-titre-xyz'


@tagged('post_install', '-at_install')
class TestBarreDeTitre(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.societe = cls.env.company
        cls.autre = cls.env['res.company'].create({'name': 'Essai barre de titre'})
        cls.usager = new_test_user(
            cls.env, login='essai.barre', password=MOT_DE_PASSE, groups='base.group_user',
            company_id=cls.societe.id, company_ids=[(6, 0, (cls.societe | cls.autre).ids)])

    def _couleur_de_la_page(self, cids=None):
        self.authenticate('essai.barre', MOT_DE_PASSE)
        if cids:
            self.opener.cookies.set('cids', str(cids))
        page = self.url_open('/odoo').text
        self.assertIn('o_web_client', page)
        balises = re.findall(r'<meta name="theme-color" content="([^"]*)"', page)
        self.assertEqual(len(balises), 1, balises)
        return balises[0]

    def test_la_page_prend_la_couleur_de_la_barre(self):
        self.societe.report_brand_dark = '#123456'
        self.assertEqual(self._couleur_de_la_page(), '#123456')

    def test_sans_couleur_foncee_la_page_prend_le_repli_de_la_barre(self):
        # Le même littéral que `.o_main_navbar` dans branding.scss.
        self.societe.report_brand_dark = False
        self.assertEqual(self._couleur_de_la_page(), '#2E3132')

    def test_la_page_suit_le_selecteur_de_societe(self):
        self.societe.report_brand_dark = '#123456'
        self.autre.report_brand_dark = '#654321'
        self.assertEqual(self._couleur_de_la_page(cids=self.autre.id), '#654321')

    def test_le_manifeste_prend_la_meme_couleur(self):
        principale = self.env.ref('base.main_company')
        principale.report_brand_dark = '#123456'
        for chemin in ('/web/manifest.webmanifest',
                       '/web/manifest.scoped_app_manifest?app_id=project&path=/odoo/project&app_name=Projet'):
            with self.subTest(chemin):
                manifeste = json.loads(self.url_open(chemin).text)
                self.assertEqual(manifeste['theme_color'], '#123456')
                self.assertEqual(manifeste['background_color'], '#123456')
