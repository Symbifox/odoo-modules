"""Forme du cadre par société (18.0.3.27.0).

Ce qui doit tenir avant tout : une société qui garde les valeurs par défaut
reçoit exactement ce qu'elle recevait avant (rien d'émis dans la page ni dans
le PDF, les mêmes octets dans le courriel). Ensuite, chaque surface suit le
réglage par le chemin qu'elle lit vraiment : variables CSS dans l'interface,
valeurs littérales dans les courriels et les PDF.
"""
import base64

from markupsafe import Markup

from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged
from odoo.tools import is_html_empty

LIGHT = 'mail.mail_notification_light'
MISE_EN_PAGE = 'bluefox_branding.bf_mail_layout'

# Les fragments que la 18.0.3.26.0 écrivait en dur, à l'octet près.
CARTE_AVANT = 'border-radius:16px;box-shadow:0 4px 24px rgba(0,0,0,0.08);'
EN_TETE_AVANT = 'border-radius:16px 16px 0 0;'
FILETS_AVANT = ('border-radius:2px 0 0 2px;', 'border-radius:0 2px 2px 0;')


@tagged('post_install', '-at_install')
class TestFormeDuCadre(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, mail_notify_force_send=False))
        cls.company = cls.env.company

    def _light(self, company=None):
        company = company or self.company
        return self.env['mail.render.mixin']._render_encapsulate(LIGHT, Markup('<p>CORPS</p>'), add_context={
            'message': self.env['mail.message'].sudo().new(dict(body=Markup('<p>CORPS</p>'), record_name='X')),
            'model_description': 'Account',
            'company': company,
        })

    def _mise_en_page(self, company=None):
        return self.env['ir.qweb']._render(
            MISE_EN_PAGE, {'message': self.env['mail.message'].new({'body': '<p>Avis</p>'}),
                           'company': company or self.company, 'is_html_empty': is_html_empty},
            minimal_qcontext=True, raise_if_not_found=False)

    def _feuille_pdf(self):
        return base64.b64decode(self.env['res.company']._get_asset_style_b64()).decode()

    # -- Le défaut ne change rien ------------------------------------------------

    def test_valeurs_par_defaut(self):
        societe = self.env['res.company'].create({'name': 'Société neuve'})
        self.assertEqual(
            (societe.brand_frame_shape, societe.brand_frame_shadow, societe.brand_frame_border),
            ('soft', 'subtle', 'thin'))
        self.assertEqual(societe._brand_frame_css(), '')
        self.assertEqual(societe._brand_frame_pdf_scss(), '')

    def test_courriel_par_defaut_identique_a_avant(self):
        self.company.write({'brand_frame_shape': 'soft', 'brand_frame_shadow': 'subtle'})
        for nom, html in (('light', self._light()), ('bf_mail_layout', self._mise_en_page())):
            with self.subTest(nom):
                self.assertIn(CARTE_AVANT, html)
                self.assertIn(EN_TETE_AVANT, html)
                for filet in FILETS_AVANT:
                    self.assertIn(filet, html)

    def test_pdf_par_defaut_sans_regle(self):
        self.company.write({'brand_frame_shape': 'soft'})
        self.assertNotIn('.o_company_%d_layout {\n    #informations' % self.company.id, self._feuille_pdf())

    # -- Courriels -------------------------------------------------------------

    def test_courriel_carre_sans_ombre(self):
        self.company.write({'brand_frame_shape': 'square', 'brand_frame_shadow': 'none'})
        for nom, html in (('light', self._light()), ('bf_mail_layout', self._mise_en_page())):
            with self.subTest(nom):
                self.assertIn('border-radius:0;box-shadow:none;', html)
                self.assertIn('border-radius:0 0 0 0;', html)
                self.assertNotIn(CARTE_AVANT, html)
                self.assertNotIn(EN_TETE_AVANT, html)
                for filet in FILETS_AVANT:
                    self.assertNotIn(filet, html)

    def test_courriel_arrondi_ombre_marquee(self):
        self.company.write({'brand_frame_shape': 'rounded', 'brand_frame_shadow': 'strong'})
        html = self._light()
        self.assertIn('border-radius:24px;box-shadow:0 8px 32px rgba(0,0,0,0.16);', html)
        self.assertIn('border-radius:24px 24px 0 0;', html)

    def test_courriel_suit_la_societe_du_rendu(self):
        """Deux sociétés, deux formes : chaque courriel porte la sienne."""
        autre = self.env['res.company'].create({'name': 'Autre société', 'brand_frame_shape': 'square'})
        self.company.write({'brand_frame_shape': 'rounded'})
        self.assertIn('border-radius:24px;', self._light(self.company))
        self.assertIn('border-radius:0;', self._light(autre))

    def test_envoi_reel_par_gabarit(self):
        """Le chemin d'un vrai envoi : mail.template sous bf_mail_layout, mail.mail en file."""
        self.company.write({'brand_frame_shape': 'square'})
        partenaire = self.env['res.partner'].create({'name': 'Dest', 'email': 'dest@cadre.test'})
        gabarit = self.env['mail.template'].create({
            'name': 'Essai cadre', 'model_id': self.env.ref('base.model_res_partner').id,
            'subject': 'Essai', 'body_html': '<p>CORPS</p>', 'partner_to': '{{ object.id }}',
            'email_layout_xmlid': MISE_EN_PAGE})
        gabarit.send_mail(partenaire.id, force_send=False)
        courriel = self.env['mail.mail'].search([('recipient_ids', 'in', partenaire.ids)], order='id desc', limit=1)
        self.assertIn('border-radius:0;box-shadow:', courriel.body_html)

    def test_sans_societe_le_courriel_garde_l_ancienne_forme(self):
        html = self.env['ir.qweb']._render(
            'bluefox_branding.bf_mail_card', {'company': False, 'message': self.env['mail.message'].new({})},
            minimal_qcontext=True, raise_if_not_found=False)
        self.assertIn(CARTE_AVANT, html)

    # -- Interface ---------------------------------------------------------------

    def test_css_interface_par_reglage(self):
        self.company.write({'brand_frame_shape': 'square', 'brand_frame_shadow': 'none', 'brand_frame_border': 'bold'})
        css = self.company._brand_frame_css()
        self.assertIn('--border-radius: 0 !important;', css)
        self.assertIn('--box-shadow: none !important;', css)
        self.assertIn('--border-width: 2px !important;', css)
        self.assertIn('.modal { --modal-border-radius', css)
        # Rien qui puisse fermer la balise <style> ou en sortir.
        for interdit in ('<', '>', '"', "'"):
            self.assertNotIn(interdit, css)

    def test_css_interface_reglages_independants(self):
        self.company.write({'brand_frame_shape': 'soft', 'brand_frame_shadow': 'subtle', 'brand_frame_border': 'bold'})
        css = self.company._brand_frame_css()
        self.assertTrue(css.startswith(':root { --border-width: 2px !important; }'), css)
        self.assertNotIn('--border-radius', css)
        self.assertNotIn('--box-shadow', css)

    # -- PDF -------------------------------------------------------------------

    def test_pdf_valeurs_litterales(self):
        self.company.write({'brand_frame_shape': 'square'})
        feuille = self._feuille_pdf()
        bloc = '.o_company_%d_layout {' % self.company.id
        self.assertIn(bloc, feuille)
        regle = feuille[feuille.index(bloc + '\n    #informations'):]
        self.assertIn('border-radius: 0 !important;', regle)
        self.assertNotIn('var(', regle.split('}\n}')[0])
        # Le sélecteur parent n'est pas échappé : la feuille doit compiler.
        self.assertNotIn('&amp;', feuille)

    def test_pdf_feuille_recalculee_a_l_ecriture(self):
        piece = self.env.ref('web.asset_styles_company_report', raise_if_not_found=False)
        if not piece:
            self.skipTest('web.asset_styles_company_report absent')
        self.company.write({'brand_frame_shape': 'rounded'})
        self.assertIn('border-radius: 0.625rem !important;', base64.b64decode(piece.datas).decode())
        self.company.write({'brand_frame_shape': 'soft'})
        self.assertNotIn('border-radius: 0.625rem !important;', base64.b64decode(piece.datas).decode())

    def test_pdf_feuille_compile(self):
        """La feuille des rapports, avec nos blocs, passe le compilateur SCSS.

        Une erreur de compilation n'échoue pas : Odoo sert alors une feuille qui
        n'affiche que le message d'erreur, et tous les PDF de toutes les
        sociétés perdent leur mise en forme.
        """
        self.company.write({'brand_frame_shape': 'rounded'})
        self.env['res.company'].create({'name': 'Carrée', 'brand_frame_shape': 'square'})
        bundle = self.env['ir.qweb']._get_asset_bundle('web.report_assets_common', js=False, debug_assets=True)
        bundle.css()
        self.assertFalse(bundle.css_errors, bundle.css_errors)


@tagged('post_install', '-at_install')
class TestFormeDuCadreHttp(HttpCase):
    """La forme vit dans le client web seulement (relecture du 2026-10-22 :
    posée dans web.layout, elle repeignait le site public et le portail)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.usager = new_test_user(cls.env, login='essai.cadre', password='essai-cadre-motif-xyz',
                                   groups='base.group_user', company_id=cls.env.company.id)

    def _client_web(self):
        self.authenticate('essai.cadre', 'essai-cadre-motif-xyz')
        return self.url_open('/odoo').text

    def test_client_web_par_defaut_sans_style(self):
        self.env.company.write({'brand_frame_shape': 'soft', 'brand_frame_shadow': 'subtle', 'brand_frame_border': 'thin'})
        page = self._client_web()
        self.assertIn('o_web_client', page)
        self.assertNotIn('bf_brand_frame', page)

    def test_client_web_porte_la_forme(self):
        self.env.company.write({'brand_frame_shape': 'square'})
        page = self._client_web()
        self.assertIn('id="bf_brand_frame"', page)
        self.assertIn('--border-radius: 0 !important;', page)
        # Après les feuilles du client : la balise arrive après le bundle.
        self.assertGreater(page.index('bf_brand_frame'), page.index('web.assets_web'))

    def test_pages_publiques_et_portail_epargnes(self):
        self.env.company.write({'brand_frame_shape': 'square', 'brand_frame_border': 'bold'})
        chemins = ['/web/login']
        if self.env['ir.module.module'].search_count([('name', '=', 'website'), ('state', '=', 'installed')]):
            chemins.append('/')
        for chemin in chemins:
            with self.subTest(chemin):
                self.assertNotIn('bf_brand_frame', self.url_open(chemin).text)
        self.authenticate('essai.cadre', 'essai-cadre-motif-xyz')
        self.assertNotIn('bf_brand_frame', self.url_open('/my').text)
