"""La section « Temps par sujet » du compte rendu suit la langue du document.

Le PDF du compte rendu se rend dans la langue d'envoi du document (bf_meeting,
`t-lang` de l'enveloppe). Cette section, ajoutée par ce module au corps du
rapport, était écrite en dur en français : un compte rendu anglais la portait en
français au milieu de l'anglais. Sa source est maintenant anglaise, et le
français vit dans `i18n/fr_CA.po`.

Les essais épinglent fr_CA et en_CA, qu'ils activent : selon la base, en_US est
absente ou seule.
"""

from freezegun import freeze_time

from odoo import Command
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestLangueDuRapport(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ('fr_CA', 'en_CA'):
            cls.env['res.lang']._activate_lang(code)
        cls.env['ir.module.module']._load_module_terms(
            ['bf_meeting', 'bf_meeting_timer'], ['fr_CA'], overwrite=True)
        cls.projet = cls.env['project.project'].create({'name': 'Projet Chrono Langue'})

    def _compte_rendu_chronometre(self, lang):
        odj = self.env['meeting.agenda'].with_context(skip_auto_refine=True).create({
            'name': 'OdJ chronométré', 'project_id': self.projet.id,
            'date': '2026-09-21 18:00:00', 'duration_planned': 30, 'lang': lang,
            'topic_ids': [
                Command.create({'sequence': 10, 'name': 'Sujet A', 'duration_planned': 10}),
                Command.create({'sequence': 20, 'name': 'Sujet B', 'duration_planned': 20}),
            ],
        })
        with freeze_time('2026-09-21 18:00:00') as horloge:
            odj.action_timer_start()
            horloge.move_to('2026-09-21 18:12:00')
            odj.action_timer_split()
            horloge.move_to('2026-09-21 18:20:00')
            odj.action_timer_split()
        self.assertEqual(odj.timer_state, 'done')
        cr = self.env['meeting.record'].create({
            'name': 'CR chronométré', 'date': '2026-09-21 18:00:00', 'duration_minutes': 20,
            'project_id': self.projet.id, 'lang': lang,
        })
        # Le lien se pose côté ordre du jour ; `agenda_id` du compte rendu en découle.
        odj.meeting_record_id = cr
        self.assertEqual(cr.agenda_id, odj)
        return cr

    def _rendu(self, cr, lang_imprimeur):
        Report = self.env['ir.actions.report'].with_context(lang=lang_imprimeur)
        return Report._render_qweb_html(
            'bf_meeting.action_report_meeting_record', cr.ids)[0].decode()

    def test_compte_rendu_anglais_imprime_par_un_francophone(self):
        html = self._rendu(self._compte_rendu_chronometre('en_CA'), 'fr_CA')
        self.assertIn('Time per agenda topic', html)
        self.assertIn('Planned time', html)
        self.assertNotIn('Temps par sujet', html)
        self.assertNotIn('Prévu', html)

    def test_compte_rendu_francais_imprime_par_un_anglophone(self):
        html = self._rendu(self._compte_rendu_chronometre('fr_CA'), 'en_CA')
        self.assertIn("Temps par sujet de l'ordre du jour", html.replace('&#39;', "'"))
        self.assertIn('Écart', html)
        self.assertNotIn('Time per agenda topic', html)
