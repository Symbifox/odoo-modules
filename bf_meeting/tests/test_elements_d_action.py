"""Les éléments d'action d'un compte rendu : la même liste sur les quatre surfaces.

Le PDF, le courriel et la copie d'échange listaient `task_ids` tel quel. Une
tâche annulée à la relecture du verbatim y passait pour une action à faire, et
la tâche qui porte le compte rendu (même nom que la rencontre, elle reçoit le
temps de la séance) aussi. Le portail les écartait déjà : le lecteur lisait deux
listes différentes du même compte rendu.

Trois promesses :
* **une seule règle** : annulée et tâche du compte rendu écartées, faite gardée ;
* **la même liste partout** : PDF (liste et compteur), courriel, copie d'échange ;
* **les traductions du courriel suivent** : il se traduit en bloc, la migration
  réécrit chaque traduction, une seule fois, sans toucher un gabarit retouché.
"""

import importlib.util
import re
from pathlib import Path

from odoo.tests import TransactionCase, tagged

PNG_1PX = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg=="
)

OUVERTE = 'ACTION-ENCORE-OUVERTE'
FAITE = 'ACTION-DEJA-LIVREE'
ANNULEE = 'ACTION-ANNULEE-AU-VERBATIM'
RENCONTRE = 'Statutaire du 19 octobre'


def _load_migration():
    path = (Path(__file__).resolve().parent.parent
            / "migrations" / "18.0.3.69.0" / "post-migrate.py")
    spec = importlib.util.spec_from_file_location("bf_meeting_mig_3_69_0", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@tagged('post_install', '-at_install')
class TestElementsDAction(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Le bandeau du PDF appelle image_data_uri() sans garde.
        cls.env.company.meeting_logo = PNG_1PX
        cls.env['res.lang']._activate_lang('fr_CA')
        cls.env['ir.module.module']._load_module_terms(['bf_meeting'], ['fr_CA'], overwrite=True)
        cls.env = cls.env(context=dict(cls.env.context, lang='fr_CA'))
        project = cls.env['project.project'].create({'name': "Projet des éléments d'action"})
        cls.rencontre = cls.env['meeting.record'].create({
            'name': RENCONTRE, 'room_name': 'Statutaire',
            'date': '2026-10-19 21:30:00', 'project_id': project.id, 'lang': 'fr_CA',
        })
        Task = cls.env['project.task']
        lie = {'project_id': project.id, 'meeting_id': cls.rencontre.id}
        cls.ouverte = Task.create(dict(lie, name=OUVERTE))
        cls.faite = Task.create(dict(lie, name=FAITE))
        cls.faite.state = '1_done'
        cls.annulee = Task.create(dict(lie, name=ANNULEE))
        cls.annulee.state = '1_canceled'
        # La tâche du compte rendu : même nom que la rencontre (aux espaces près).
        cls.mere = Task.create(dict(lie, name=f'  {RENCONTRE} '))
        cls.mere.state = '1_done'

    # -- la règle ----------------------------------------------------------

    def test_la_regle(self):
        self.assertEqual(self.rencontre._action_tasks_for_report(), self.ouverte | self.faite)
        self.assertEqual(len(self.rencontre.task_ids), 4, "rien n'est détaché du compte rendu")

    def test_une_tache_rouverte_revient(self):
        self.annulee.state = '01_in_progress'
        self.assertIn(self.annulee, self.rencontre._action_tasks_for_report())

    def test_la_tache_du_compte_rendu_renommee_reste_ecartee(self):
        """La rencontre est souvent renommée après la création de la tâche qui la
        porte : celle-ci se reconnaît alors à son étape, pas à son nom."""
        etape = self.env['project.task.type'].create({
            'name': 'Comptes rendus de rencontres', 'project_ids': [(4, self.ouverte.project_id.id)]})
        renommee = self.env['project.task'].create({
            'name': 'Rencontre Alice & Bruno — 2026-10-01', 'stage_id': etape.id,
            'project_id': self.ouverte.project_id.id, 'meeting_id': self.rencontre.id})
        renommee.state = '1_done'
        self.assertNotIn(renommee, self.rencontre._action_tasks_for_report())
        self.assertNotIn('Rencontre Alice &amp; Bruno', self._pdf())

    # -- le PDF ------------------------------------------------------------

    def _pdf(self):
        html = self.env['ir.actions.report']._render_qweb_html(
            'bf_meeting.action_report_meeting_record', [self.rencontre.id])[0]
        return html.decode() if isinstance(html, bytes) else html

    def test_le_pdf_liste_les_seules_actions(self):
        html = self._pdf()
        self.assertIn(f'<td>{OUVERTE}</td>', html)
        self.assertIn(f'<td>{FAITE}</td>', html)
        self.assertNotIn(ANNULEE, html)
        self.assertNotRegex(html, r'<td>\s*' + re.escape(RENCONTRE) + r'\s*</td>',
                            "la tâche du compte rendu n'est pas une action")

    def test_le_compteur_du_pdf_suit_la_liste(self):
        html = self._pdf()
        compteur = re.search(
            r'mr-kpi-number">\s*(\d+)\s*</div>\s*<div class="mr-kpi-label">[^<]*action',
            html, re.I)
        self.assertTrue(compteur, "compteur des éléments d'action introuvable")
        self.assertEqual(compteur.group(1), '2')

    def test_le_pdf_sans_action_n_a_pas_de_section(self):
        """Tout annulé : ni titre de section ni tableau vide, et un compteur à 0."""
        (self.ouverte | self.faite).state = '1_canceled'
        html = self._pdf()
        self.assertNotIn(OUVERTE, html)
        self.assertNotRegex(html, r'mr-section-title">\s*(Action items|Éléments d.action)\s*<')
        compteur = re.search(
            r'mr-kpi-number">\s*(\d+)\s*</div>\s*<div class="mr-kpi-label">[^<]*action',
            html, re.I)
        self.assertEqual(compteur.group(1), '0')

    # -- le courriel -------------------------------------------------------

    def _courriel(self, lang):
        template = self.env.ref('bf_meeting.meeting_report_mail_template')
        return template.with_context(lang=lang)._render_field(
            'body_html', [self.rencontre.id])[self.rencontre.id]

    def test_le_courriel_liste_les_seules_actions(self):
        for lang in ('fr_CA', 'en_US'):
            with self.subTest(lang=lang):
                body = self._courriel(lang)
                self.assertIn(OUVERTE, body)
                self.assertIn(FAITE, body)
                self.assertNotIn(ANNULEE, body)
                self.assertNotRegex(body, r'<li[^>]*>\s*' + re.escape(RENCONTRE),
                                    "la tâche du compte rendu n'est pas une action")

    def test_la_traduction_livree_suit_le_gabarit(self):
        """fr_CA.po porte le corps entier du courriel (traduction en bloc) : son
        texte source doit être le gabarit livré, et sa traduction lire la liste
        filtrée. Une entrée périmée est ignorée par Odoo sans un mot."""
        from odoo.tools.translate import PoFileReader
        po = Path(__file__).resolve().parent.parent / 'i18n' / 'fr_CA.po'
        template = self.env.ref('bf_meeting.meeting_report_mail_template')
        self.env.flush_all()
        self.env.cr.execute("SELECT body_html ->> 'en_US' FROM mail_template WHERE id = %s",
                            [template.id])
        source = self.env.cr.fetchone()[0]
        with po.open('rb') as fh:
            entrees = [e for e in PoFileReader(fh)
                       if e.get('name') == 'mail.template,body_html'
                       and e.get('imd_name') == 'meeting_report_mail_template']
        self.assertEqual(len(entrees), 1)
        self.assertEqual(entrees[0]['src'], source, "texte source du .po = gabarit livré")
        self.assertIn('_action_tasks_for_report', entrees[0]['value'])
        self.assertNotIn('object.task_ids', entrees[0]['value'])

    # -- la copie d'échange ------------------------------------------------

    def test_la_copie_d_echange(self):
        payload = self.env['meeting.exchange'].build_payload(self.rencontre)
        noms = [a['name'] for a in payload['meeting']['action_items']]
        self.assertCountEqual(noms, [OUVERTE, FAITE])

    # -- la migration des traductions du courriel --------------------------

    def _body_values(self, template):
        self.env.flush_all()
        self.env.cr.execute("SELECT body_html FROM mail_template WHERE id = %s", [template.id])
        return self.env.cr.fetchone()[0]

    def _ancien(self, source):
        mig = _load_migration()
        ancien = source.replace(mig.NEW_IF, mig.OLD_IF).replace(mig.NEW_FOREACH, mig.OLD_FOREACH)
        self.assertNotIn('_action_tasks_for_report', ancien, "précondition : l'ancien gabarit")
        return ancien

    def test_migration_reecrit_chaque_traduction_une_fois(self):
        mig = _load_migration()
        template = self.env.ref('bf_meeting.meeting_report_mail_template')
        source = self._body_values(template)['en_US']
        ancien = self._ancien(source)
        self.env.cr.execute(
            "UPDATE mail_template SET body_html = body_html"
            " || jsonb_build_object('fr_CA', %s::text, 'en_CA', %s::text) WHERE id = %s",
            [ancien, ancien.replace('Action items', 'Action items (CA)'), template.id])
        template.invalidate_recordset(['body_html'])

        mig.migrate(self.env.cr, '18.0.3.68.1')
        premiere = self._body_values(template)
        self.assertEqual(premiere['fr_CA'], source, "au caractère près")
        self.assertIn('Action items (CA)', premiere['en_CA'], "la traduction garde son texte")
        self.assertNotIn('object.task_ids', premiere['en_CA'])
        self.assertEqual(premiere['en_US'], source, "la source n'est pas touchée")

        mig.migrate(self.env.cr, '18.0.3.68.1')
        self.assertEqual(self._body_values(template), premiere, "rejouée : rien ne change")

        template.invalidate_recordset(['body_html'])
        self.assertNotIn(ANNULEE, self._courriel('fr_CA'))

    def test_migration_laisse_un_gabarit_retouche(self):
        mig = _load_migration()
        template = self.env.ref('bf_meeting.meeting_report_mail_template')
        ancien = self._ancien(self._body_values(template)['en_US'])
        # Une seconde liste ajoutée à la main : le bloc n'est plus unique.
        retouche = ancien + '\n<t t-if="object.task_ids"><p>Retouche locale</p></t>'
        self.env.cr.execute(
            "UPDATE mail_template SET body_html = body_html"
            " || jsonb_build_object('fr_CA', %s::text) WHERE id = %s",
            [retouche, template.id])
        template.invalidate_recordset(['body_html'])
        with self.assertLogs(mig.__name__, level='WARNING'):
            mig.migrate(self.env.cr, '18.0.3.68.1')
        self.assertEqual(self._body_values(template)['fr_CA'], retouche,
                         "un gabarit qui ne correspond pas exactement reste tel quel")

    def test_migration_ne_fabrique_pas_de_traduction(self):
        mig = _load_migration()
        template = self.env.ref('bf_meeting.meeting_report_mail_template')
        avant = set(self._body_values(template))
        mig.migrate(self.env.cr, '18.0.3.68.1')
        self.assertEqual(set(self._body_values(template)), avant)
