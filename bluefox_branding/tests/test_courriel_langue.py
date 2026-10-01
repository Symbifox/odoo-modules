"""Le slogan et le pied des courriels suivent la langue du destinataire.

`brand_email_tagline` et `brand_email_footer_html` étaient des champs simples :
un courriel anglais portait le slogan et le pied français de la société. Ils
deviennent traduisibles, et la mise en page lit la société dans la langue du
rendu par les deux chemins d'Odoo : `mail.template.send_mail` (langue dans le
contexte) et les avis (`message_post`, composeur : langue en option du rendu).
"""

from odoo.tests import TransactionCase, tagged
from odoo.tools import is_html_empty

SLOGAN_FR = 'SLOGAN-FRANCAIS'
SLOGAN_EN = 'ENGLISH-TAGLINE'
PIED_FR = '<p>PIED-FRANCAIS</p>'
PIED_EN = '<p>ENGLISH-FOOTER</p>'
MISE_EN_PAGE = 'bluefox_branding.bf_mail_layout'


@tagged('post_install', '-at_install')
class TestCourrielLangue(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        actives = dict(cls.env['res.lang'].get_installed())
        cls.fr = 'fr_CA' if 'fr_CA' in actives else None
        cls.en = 'en_CA' if 'en_CA' in actives else None
        cls.env = cls.env(context=dict(cls.env.context, mail_notify_force_send=False))
        cls.societe = cls.env.company
        cls.societe.with_context(lang='en_US').write({
            'brand_email_tagline': SLOGAN_FR, 'brand_email_footer_html': PIED_FR})
        cls.gabarit = cls.env['mail.template'].create({
            'name': 'Essai de langue', 'model_id': cls.env.ref('base.model_res_partner').id,
            'subject': 'Essai', 'body_html': '<p>CORPS</p>', 'lang': '{{ object.lang }}',
            'email_layout_xmlid': MISE_EN_PAGE, 'partner_to': '{{ object.id }}'})

    def _traduire(self):
        if not (self.fr and self.en):
            self.skipTest("fr_CA et en_CA doivent être actives")
        self.societe.update_field_translations('brand_email_tagline', {self.en: SLOGAN_EN})
        self.societe.update_field_translations('brand_email_footer_html', {self.en: PIED_EN})

    def _destinataire(self, lang):
        return self.env['res.partner'].create({
            'name': 'Destinataire %s' % lang, 'email': '%s@essai.test' % lang.lower(), 'lang': lang})

    def _courriel(self, partenaire):
        return self.env['mail.mail'].search([('recipient_ids', 'in', partenaire.ids)],
                                            order='id desc', limit=1)

    def test_les_deux_champs_sont_traduisibles(self):
        Societe = self.env['res.company']
        self.assertTrue(Societe._fields['brand_email_tagline'].translate)
        self.assertTrue(Societe._fields['brand_email_footer_html'].translate)

    def test_envoi_direct_dans_la_langue_du_destinataire(self):
        self._traduire()
        # La personne qui envoie écrit en français : la société lui arrive en français.
        envoyeur = self.gabarit.with_context(lang=self.fr)
        for lang, slogan, pied, autre in ((self.en, SLOGAN_EN, 'ENGLISH-FOOTER', SLOGAN_FR),
                                          (self.fr, SLOGAN_FR, 'PIED-FRANCAIS', SLOGAN_EN)):
            with self.subTest(lang=lang):
                dest = self._destinataire(lang)
                envoyeur.send_mail(dest.id, force_send=False)
                corps = self._courriel(dest).body_html
                self.assertIn(slogan, corps)
                self.assertIn(pied, corps)
                self.assertNotIn(autre, corps)

    def test_avis_dans_la_langue_du_destinataire(self):
        """Le rendu tel que le fait `_notify_by_email_render_layout` : langue en
        option, société lue par l'appelant (ici en français). Appelé directement :
        sur une base réelle, des modules filtrent les avis des fiches contact."""
        self._traduire()
        societe = self.societe.with_context(lang=self.fr)
        message = self.env['mail.message'].new({'body': '<p>Avis</p>'})
        for lang, slogan, autre in ((self.en, SLOGAN_EN, SLOGAN_FR), (self.fr, SLOGAN_FR, SLOGAN_EN)):
            with self.subTest(lang=lang):
                corps = self.env['ir.qweb']._render(
                    MISE_EN_PAGE, {'message': message, 'company': societe, 'lang': lang,
                                   'is_html_empty': is_html_empty},
                    minimal_qcontext=True, raise_if_not_found=False, lang=lang)
                self.assertIn(slogan, corps)
                self.assertNotIn(autre, corps)

    def test_sans_traduction_la_source_reste(self):
        if not self.en:
            self.skipTest("en_CA doit être active")
        dest = self._destinataire(self.en)
        self.gabarit.send_mail(dest.id, force_send=False)
        self.assertIn(SLOGAN_FR, self._courriel(dest).body_html)

    def test_le_rendu_sans_valeurs_ne_casse_pas(self):
        vue = self.env['ir.ui.view'].create({
            'name': 'essai', 'type': 'qweb', 'arch': '<t t-name="essai"><p>RENDU</p></t>'})
        self.assertIn('RENDU', self.env['ir.qweb']._render(vue.id, None))
