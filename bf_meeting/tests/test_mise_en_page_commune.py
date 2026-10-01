"""Les deux courriels passent par la mise en page commune.

L'ordre du jour et le compte rendu portaient leur propre coquille (fond, carte,
en-tête, pied), et le composeur l'enveloppait encore dans
`mail.mail_notification_light` : deux habillages l'un dans l'autre. Ils ne
gardent plus que le contenu ; `bf_onboarding_base.bf_mail_layout` (remplacée par
celle de bluefox_branding quand il est installé) les habille, une fois, par
tous les chemins d'envoi : envoi direct, composeur, envoi après la revue Gen.

La migration 18.0.3.63.0 fait de même pour les traductions posées en base, que
la mise à jour du module ne touche pas.
"""

import importlib.util
import re
from pathlib import Path

from odoo import Command
from odoo.tests import TransactionCase, tagged

MISE_EN_PAGE = 'bf_onboarding_base.bf_mail_layout'
# Propre à la carte de bf_mail_layout (copie et original) : une seule carte.
CARTE = 'box-shadow:0 4px 24px'
# Pied de `mail.mail_notification_light`, que le composeur ajoutait.
MISE_EN_PAGE_LEGERE = 'utm_medium=email'
# Carte de l'ancienne coquille (sans ombre : CARTE ne la voit pas).
ANCIENNE_CARTE = 'border:1px solid #e5e7eb; border-collapse:collapse'
DONNEES = Path(__file__).resolve().parent / 'data'


def _charger(nom):
    path = (Path(__file__).resolve().parent.parent
            / 'migrations' / '18.0.3.63.0' / f'{nom}-migrate.py')
    spec = importlib.util.spec_from_file_location(f'bf_meeting_mig_3_63_0_{nom}', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ancienne_coquille(titre, contenu, pied):
    """Une valeur construite comme avant 18.0.3.63.0, réduite à ses repères."""
    return f'''
<t t-set="company" t-value="object.company_id"/>
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color:#F8FAFC;">
    <tbody><tr><td align="center" style="padding:24px;">
        <table role="presentation" width="600"><tbody>
            <!-- Header -->
            <tr><td><table><tbody><tr>
                <td align="left"><img t-att-src="'/brand/logo/%d/meeting' % object.company_id.id" alt="Logo"/></td>
                <td align="right" style="color:#E6EDF3; font-size:22px; font-weight:800;">
                    {titre}
                </td>
            </tr></tbody></table></td></tr>
            <!-- Content -->
            <tr>
                <td style="padding:24px;">
                    {contenu}
                </td>
            </tr>
            <!-- Separator -->
            <tr><td>&amp;nbsp;</td></tr>
            <!-- Footer -->
            <tr><td><p>{pied}</p></td></tr>
        </tbody></table>
    </td></tr></tbody>
</table>
'''


# Le contenu ferme lui aussi des `</td></tr>` : le découpage ne doit pas s'y arrêter.
CONTENU_EN = ('<h2><t t-out="object.name"/></h2>\n'
              '<table><tr t-attf-style="background:{{ brand_dark }}; color:#FFFFFF;">'
              '<td>CELLULE-DU-CONTENU</td></tr></table>\n'
              '<h3 t-attf-style="padding-left:10px; border-left:4px solid {{ brand_primary }};">Topics</h3>\n'
              '<p>Hello,</p>')


@tagged('post_install', '-at_install')
class TestMiseEnPageCommune(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.destinataire = cls.env['res.partner'].create({
            'name': 'Destinataire du banc', 'email': 'dest@essai.test'})
        cls.projet = cls.env['project.project'].create({'name': 'Projet du banc'})
        # Un sujet et le lien de contribution : l'en-tête du tableau et le bouton
        # sortaient en blanc sur blanc par le composeur.
        cls.odj = cls.env['meeting.agenda'].with_context(skip_auto_refine=True).create({
            'name': 'OdJ-MISE-EN-PAGE', 'date': '2026-10-01 14:00:00',
            'project_id': cls.projet.id, 'allow_contributions': True,
            'participant_ids': [Command.set(cls.destinataire.ids)],
            'recipient_ids': [Command.set(cls.destinataire.ids)],
            'topic_ids': [Command.create({'sequence': 10, 'name': 'SUJET-DU-BANC',
                                          'duration_planned': 15})]})
        cls.cr_ = cls.env['meeting.record'].create({
            'name': 'CR-MISE-EN-PAGE', 'date': '2026-10-01 14:00:00',
            'duration_minutes': 30, 'project_id': cls.projet.id,
            'report_recipient_ids': [Command.set(cls.destinataire.ids)]})

    def _courriels(self, record):
        return self.env['mail.mail'].search([
            ('model', '=', record._name), ('res_id', '=', record.id)])

    def _un_seul_habillage(self, corps, surtitre, nom):
        self.assertEqual(corps.count(CARTE), 1, "une carte, pas deux")
        self.assertNotIn(ANCIENNE_CARTE, corps)
        self.assertNotIn(MISE_EN_PAGE_LEGERE, corps)
        self.assertNotIn('/meeting"', corps, "plus de logo propre au module")
        self.assertNotIn('module Rencontres', corps)
        self.assertIn(f'/brand/logo/{self.odj.company_id.id}', corps)
        self.assertIn(surtitre, corps)
        self.assertIn(nom, corps)

    def test_les_gabarits_pointent_la_mise_en_page_commune(self):
        for xmlid in ('bf_meeting.meeting_agenda_mail_template',
                      'bf_meeting.meeting_report_mail_template'):
            with self.subTest(gabarit=xmlid):
                gabarit = self.env.ref(xmlid)
                self.assertEqual(gabarit.email_layout_xmlid, MISE_EN_PAGE)
                # Toutes les langues posées en base, pas seulement celle de l'essai.
                self.env.flush_all()
                self.env.cr.execute("SELECT body_html FROM mail_template WHERE id = %s",
                                    [gabarit.id])
                for lang, corps in self.env.cr.fetchone()[0].items():
                    with self.subTest(lang=lang):
                        self.assertNotIn('#F8FAFC', corps)
                        self.assertNotIn('/brand/logo/', corps)
                        self.assertNotIn('<!-- Content -->', corps)
                        # Le nettoyeur du composeur jette ces deux abrégés.
                        self.assertFalse(re.search(r'(?<![\w-])(background|border-left):', corps))

    def test_envoi_direct_du_compte_rendu(self):
        self.cr_.action_send_report_direct()
        courriel = self._courriels(self.cr_)
        self.assertEqual(len(courriel), 1)
        self._un_seul_habillage(courriel.body_html, 'Compte rendu', 'CR-MISE-EN-PAGE')

    def test_envoi_direct_de_l_ordre_du_jour(self):
        # `action_send_agenda` envoie tout de suite : sans effacement, le courriel
        # parti reste lisible.
        self.env.ref('bf_meeting.meeting_agenda_mail_template').auto_delete = False
        self.odj.action_send_agenda()
        courriel = self._courriels(self.odj)
        self.assertEqual(len(courriel), 1)
        self._un_seul_habillage(courriel.body_html, 'Ordre du jour', 'OdJ-MISE-EN-PAGE')

    def test_le_composeur_de_l_ordre_du_jour(self):
        action = self.odj.action_send_agenda_wizard()
        # En file, pas envoyé : un courriel parti s'efface avant qu'on le lise.
        composeur = self.env['mail.compose.message'].with_context(
            action['context'], mail_notify_force_send=False).create({})
        self.assertEqual(composeur.email_layout_xmlid, MISE_EN_PAGE)
        composeur.partner_ids = [Command.set(self.destinataire.ids)]
        composeur.action_send_mail()
        courriel = self._courriels(self.odj)
        self.assertTrue(courriel)
        for corps in courriel.mapped('body_html'):
            self._un_seul_habillage(corps, 'Ordre du jour', 'OdJ-MISE-EN-PAGE')
            self.assertIn('SUJET-DU-BANC', corps)
            # Passés par le nettoyeur : le fond de l'en-tête et celui du bouton tiennent.
            self.assertRegex(corps, r'<tr style="background-color:[^;"]+; color:#FFFFFF')
            self.assertRegex(corps, r'href="[^"]*/meeting/agenda/[^"]+" style="[^"]*background-color:')

    def test_le_composeur_du_compte_rendu(self):
        action = self.cr_.action_send_report()
        # En file, pas envoyé : un courriel parti s'efface avant qu'on le lise.
        composeur = self.env['mail.compose.message'].with_context(
            action['context'], mail_notify_force_send=False).create({})
        self.assertEqual(composeur.email_layout_xmlid, MISE_EN_PAGE)
        composeur.partner_ids = [Command.set(self.destinataire.ids)]
        composeur.action_send_mail()
        courriel = self._courriels(self.cr_)
        self.assertTrue(courriel)
        for corps in courriel.mapped('body_html'):
            self._un_seul_habillage(corps, 'Compte rendu', 'CR-MISE-EN-PAGE')


@tagged('post_install', '-at_install')
class TestMigrationCoquille(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pre = _charger('pre')
        cls.post = _charger('post')

    def _valeurs(self, gabarit):
        self.env.flush_all()
        self.env.cr.execute("SELECT body_html FROM mail_template WHERE id = %s", [gabarit.id])
        return self.env.cr.fetchone()[0]

    def test_decoupage(self):
        ancienne = _ancienne_coquille('Meeting minutes', CONTENU_EN,
                                      'Minutes generated from the Meetings module.')
        nouvelle = self.post.retirer_coquille(ancienne)
        self.assertIn('>Meeting minutes</p>', nouvelle)
        self.assertIn('CELLULE-DU-CONTENU', nouvelle)
        self.assertIn('<p>Hello,</p>', nouvelle)
        self.assertIn('<t t-set="company"', nouvelle)
        self.assertIn('background-color:{{ brand_dark }}; color:#FFFFFF;', nouvelle)
        self.assertIn('border-left-width:4px; border-left-style:solid; '
                      'border-left-color:{{ brand_primary }};', nouvelle)
        for parti in ('#F8FAFC', 'Minutes generated', '/brand/logo/', '<!-- '):
            self.assertNotIn(parti, nouvelle)
        self.assertIsNone(self.post.retirer_coquille(nouvelle), "rejouée : rien")

    def test_les_copies_redeviennent_la_source_et_les_traductions_perdent_la_coquille(self):
        gabarit = self.env.ref('bf_meeting.meeting_report_mail_template')
        source = self._valeurs(gabarit)['en_US']
        ancienne_fr = _ancienne_coquille('Compte rendu', '<p>Bonjour,</p>', 'Pied.')
        ancienne_en = _ancienne_coquille('Meeting minutes', CONTENU_EN, 'Footer.')
        # Avant la montée, comme sur une base réelle : fr_CA copie de la source, en_CA traduite.
        self.env.cr.execute(
            "UPDATE mail_template SET body_html = jsonb_build_object("
            "'en_US', %s::text, 'fr_CA', %s::text, 'en_CA', %s::text) WHERE id = %s",
            [ancienne_fr, ancienne_fr, ancienne_en, gabarit.id])
        gabarit.invalidate_recordset(['body_html'])
        self.pre.relever_copies(self.env.cr)
        # La montée réécrit la source et laisse le reste.
        self.env.cr.execute(
            "UPDATE mail_template SET body_html = jsonb_set(body_html, '{en_US}', to_jsonb(%s::text))"
            " WHERE id = %s", [source, gabarit.id])

        self.post.convertir_traductions(self.env)
        apres = self._valeurs(gabarit)
        self.assertEqual(apres['en_US'], source)
        self.assertEqual(apres['fr_CA'], source, "la copie redevient la source, au caractère près")
        self.assertEqual(apres['en_CA'], self.post.retirer_coquille(ancienne_en))

        self.post.convertir_traductions(self.env)
        self.assertEqual(self._valeurs(gabarit), apres, "rejouée : rien ne bouge")

    def test_une_traduction_sans_reperes_reste_telle_quelle(self):
        gabarit = self.env.ref('bf_meeting.meeting_agenda_mail_template')
        abimee = _ancienne_coquille('Agenda', '<p>Hi,</p>', 'Footer.').replace(
            '<!-- Separator -->', '')
        self.env.cr.execute(
            "UPDATE mail_template SET body_html = body_html || jsonb_build_object('en_CA', %s::text)"
            " WHERE id = %s", [abimee, gabarit.id])
        gabarit.invalidate_recordset(['body_html'])
        with self.assertLogs('bf_meeting_mig_3_63_0_post', level='WARNING'):
            self.post.convertir_traductions(self.env)
        self.assertEqual(self._valeurs(gabarit)['en_CA'], abimee)

    def test_sans_socle_la_montee_crie_sans_bloquer(self):
        """Lever ici laisserait le module « à mettre à jour » et la base ne
        démarrerait plus : on journalise une erreur et on continue."""
        self.assertTrue(self.pre.verifier_mise_en_page(self.env.cr))
        self.env.cr.execute(
            "DELETE FROM ir_model_data WHERE module = 'bf_onboarding_base'"
            " AND name = 'bf_mail_layout'")
        with self.assertLogs('bf_meeting_mig_3_63_0_pre', level='ERROR'):
            self.assertFalse(self.pre.verifier_mise_en_page(self.env.cr))

    def test_les_vraies_valeurs_de_la_3_62_2(self):
        """Pas une coquille fabriquée par l'essai : les valeurs posées en base
        sur une installation réelle avant la montée (source de l'OdJ, traduction anglaise du CR)."""
        odj = (DONNEES / 'courriel_odj_3_62_2_en_US.html').read_text(encoding='utf-8')
        cr_en = (DONNEES / 'courriel_cr_3_62_2_en_CA.html').read_text(encoding='utf-8')
        for nom, ancienne, surtitre, garde in (
                ('odj', odj, '>Ordre du jour</p>', 'resend_changes_block_html'),
                ('cr_en', cr_en, '>Meeting minutes</p>', '_discussed_tasks_for_report')):
            with self.subTest(valeur=nom):
                nouvelle = self.post.retirer_coquille(ancienne)
                self.assertTrue(nouvelle)
                self.assertIn(surtitre, nouvelle)
                self.assertIn(garde, nouvelle)
                for parti in ('#F8FAFC', '/brand/logo/', 'width="600"', 'Rencontres.',
                              'Meetings module'):
                    self.assertNotIn(parti, nouvelle)
                self.assertFalse(re.search(r'(?<![\w-])(background|border-left):', nouvelle))
        self.assertIn('Hello,', self.post.retirer_coquille(cr_en))
        # La source neuve EST le découpage de l'ancienne (aux blancs près).
        source = self._valeurs(self.env.ref('bf_meeting.meeting_agenda_mail_template'))['en_US']
        self.assertEqual(''.join(self.post.retirer_coquille(odj).split()), ''.join(source.split()))

    def test_les_styles_qui_ne_sont_pas_une_couleur_restent(self):
        for style in ('background: url(/x.png)', 'border-left:1px dashed #000',
                      'border-left:none', "background-image:url('/x.png')"):
            with self.subTest(style=style):
                corps = f'<p style="{style};">x</p>'
                self.assertEqual(self.post.styles_admis(corps), corps)
        self.assertEqual(self.post.styles_admis("<p style='background:#FFF;'>x</p>"),
                         "<p style='background-color:#FFF;'>x</p>")
