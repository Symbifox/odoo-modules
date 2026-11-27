"""L'assistant « Mise en page des documents » (base.document.layout).

Odoo rend web.styles_company_report avec ``company_ids: self`` pour l'aperçu de
l'assistant : la boucle du gabarit parcourt alors l'ASSISTANT, pas une société.
Le cadre du PDF (18.0.3.32.0) y appelait ``company._brand_frame_pdf_scss()`` :
l'écran plantait (« Oups ! ») sur toute instance, quel que soit le réglage.
"""
import base64

from odoo.tests import Form, TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestAssistantMiseEnPage(TransactionCase):

    def test_ouvrir_l_assistant(self):
        # Le chemin de l'usager : l'assistant s'ouvre, son aperçu se calcule au onchange.
        for forme in ('soft', 'square', 'rounded'):
            with self.subTest(forme):
                self.env.company.brand_frame_shape = forme
                assistant = Form(self.env['base.document.layout'])
                self.assertTrue(assistant.preview)

    def test_feuille_de_l_apercu(self):
        self.env.company.brand_frame_shape = 'rounded'
        assistant = self.env['base.document.layout'].create({})
        self.assertIn('_layout', assistant._get_asset_style())
        # L'aperçu ne porte pas l'arrondi du cadre : la règle vise .o_company_<id>_layout,
        # et l'id ici est celui de l'assistant, pas d'une société.
        self.assertEqual(assistant._brand_frame_pdf_scss(), '')

    def test_le_pdf_garde_le_cadre(self):
        # Le correctif ne touche que l'assistant : la feuille des PDF suit toujours la société.
        self.env.company.brand_frame_shape = 'rounded'
        feuille = base64.b64decode(self.env['res.company']._get_asset_style_b64()).decode()
        self.assertIn('.o_company_%d_layout' % self.env.company.id, feuille)
        self.assertIn('border-radius: 0.625rem !important', feuille)
