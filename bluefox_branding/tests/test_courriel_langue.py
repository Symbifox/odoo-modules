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

    def test_les_liens_legaux_suivent_la_langue(self):
        """Les trois textes du pied étaient littéraux et français.

        « Confidentialité » et « Conditions » étaient écrits en dur dans la vue :
        un courriel anglais les portait en français, et un locataire qui n'a que
        en_US d'actif les lisait en français à chaque envoi.
        """
        if not (self.fr and self.en):
            self.skipTest("fr_CA et en_CA doivent être actives")
        self.societe.write({'brand_privacy_url': 'https://essai.test/vie-privee',
                            'brand_terms_url': 'https://essai.test/conditions'})
        envoyeur = self.gabarit.with_context(lang=self.fr)
        for lang, attendus, absents in (
                (self.en, ('>Privacy</a>', '>Terms</a>'),
                          ('>Confidentialité</a>', '>Conditions</a>')),
                (self.fr, ('>Confidentialité</a>', '>Conditions</a>'),
                          ('>Privacy</a>', '>Terms</a>'))):
            with self.subTest(lang=lang):
                dest = self._destinataire(lang)
                envoyeur.send_mail(dest.id, force_send=False)
                corps = self._courriel(dest).body_html
                for attendu in attendus:
                    self.assertIn(attendu, corps)
                for absent in absents:
                    self.assertNotIn(absent, corps)

    def test_le_lien_d_arret_de_suivi_est_retirable_et_traduit(self):
        """« Se désabonner » était littéral, et le lien n'était pas retirable.

        Sans `<span id="mail_unfollow">`, `mail.mail._personalize_outgoing_body`
        ne peut pas effacer le lien pour un destinataire qui n'a rien à ne plus
        suivre : le courriel part avec un /mail/unfollow sans jeton.
        """
        if not (self.fr and self.en):
            self.skipTest("fr_CA et en_CA doivent être actives")
        message = self.env['mail.message'].new({'body': '<p>Avis</p>'})
        for lang, attendu in ((self.en, '>Unfollow</a>'), (self.fr, '>Ne plus suivre</a>')):
            with self.subTest(lang=lang):
                corps = self.env['ir.qweb']._render(
                    MISE_EN_PAGE,
                    {'message': message, 'company': self.societe, 'lang': lang,
                     'is_html_empty': is_html_empty,
                     'email_notification_force_footer': True},
                    minimal_qcontext=True, raise_if_not_found=False, lang=lang)
                self.assertIn('<span id="mail_unfollow">', corps)
                self.assertIn(attendu, corps)
                self.assertNotIn('désabonner', corps)
                # Et le retrait lui-même, par le chemin d'Odoo : sans le span,
                # `_UNFOLLOW_REGEX` ne trouve rien et le lien part sans jeton.
                etranger = self.env['res.partner'].create({
                    'name': 'Non abonné', 'email': 'nonabonne@essai.test'})
                courriel = self.env['mail.mail'].create({
                    'subject': 'Essai unfollow', 'body_html': corps,
                    'recipient_ids': [(4, etranger.id)],
                    'model': 'res.partner', 'res_id': etranger.id})
                sortant = courriel._personalize_outgoing_body(corps, etranger, None)
                self.assertNotIn('/mail/unfollow', sortant)
                self.assertNotIn(attendu, sortant)

    def test_l_avis_de_retard_a_un_corps_anglais(self):
        """Le gabarit 141 n'existait qu'en français, écrit dans toutes les langues."""
        from odoo.addons.bluefox_branding.hooks import _late_invoice_body
        fr, en = _late_invoice_body('fr_CA'), _late_invoice_body('en_CA')
        self.assertIn('Bonjour ', fr)
        self.assertNotIn('Hello ', fr)
        self.assertIn('Hello ', en)
        self.assertNotIn('Bonjour ', en)
        # Aucun jeton de substitution ne doit survivre au rendu.
        for corps in (fr, en):
            self.assertNotIn('@@', corps)
            # 🔴 Le corps est dé-coquillé depuis la 18.0.3.29.0 : il rend DANS
            # la mise en page partagée. Y remettre un <body> ou un pied de
            # marque donnerait une carte dans la carte et deux pieds, donc deux
            # lignes de liens légaux.
            self.assertNotIn('<body', corps)
            self.assertNotIn('brand_privacy_url', corps)
            self.assertNotIn('brand_email_footer_html', corps)

    def test_les_corps_publies_sont_reconnus(self):
        """Une version qui change une phrase doit ajouter son empreinte.

        Sans cela, la version SUIVANTE trouve un corps qu'elle ne reconnaît pas,
        garde le gabarit et journalise « réécrit à la main » sur le travail du
        module lui-même. Le refus serait silencieux et le journal mentirait.
        """
        import hashlib

        from odoo.addons.bluefox_branding.hooks import (
            _late_invoice_body, _LATE_INVOICE_SHA_CONNUS,
        )
        for lang in ('fr_CA', 'en_CA'):
            with self.subTest(lang=lang):
                empreinte = hashlib.sha256(_late_invoice_body(lang).encode()).hexdigest()
                self.assertIn(empreinte, _LATE_INVOICE_SHA_CONNUS,
                              "ajouter cette empreinte à _LATE_INVOICE_SHA_CONNUS")

    def test_l_avis_de_retard_porte_sa_mise_en_page_et_sa_langue(self):
        """Sans `lang`, le corps suit la langue de la session, pas celle du client.

        Mesuré sur une base réelle : l'avis de retard n'avait pas de `lang`, alors
        que ses quatre voisins sur account.move portent tous
        `{{ object.partner_id.lang }}`. Le corps anglais neuf serait resté
        inatteignable.
        """
        from odoo.addons.bluefox_branding.hooks import (
            _apply_late_invoice, _get_active_langs, _late_invoice_body,
            _LATE_INVOICE_LANG, _LATE_INVOICE_LAYOUT,
        )
        gabarit = self.env['mail.template'].create({
            'name': 'Avis de retard sur facture (essai)',
            'model_id': self.env.ref('account.model_account_move').id,
            'subject': 'Essai',
            'body_html': '<p>brand_primary</p>',
        })
        langs = _get_active_langs(self.env)
        # Borné au gabarit de l'essai : sans ça, l'appel attrape aussi le vrai
        # 141 de la base où l'essai tourne.
        ecrits = _apply_late_invoice(self.env, langs, gabarits=gabarit)
        self.assertNotIn(gabarit, ecrits,
                         "un corps qui porte notre marque sans être l'un des nôtres se garde")
        gabarit.body_html = _late_invoice_body('fr_CA')
        ecrits = _apply_late_invoice(self.env, langs, gabarits=gabarit)
        self.assertIn(gabarit, ecrits)
        self.assertEqual(gabarit.email_layout_xmlid, _LATE_INVOICE_LAYOUT)
        self.assertEqual(gabarit.lang, _LATE_INVOICE_LANG)

    def test_le_rendu_sans_valeurs_ne_casse_pas(self):
        vue = self.env['ir.ui.view'].create({
            'name': 'essai', 'type': 'qweb', 'arch': '<t t-name="essai"><p>RENDU</p></t>'})
        self.assertIn('RENDU', self.env['ir.qweb']._render(vue.id, None))
