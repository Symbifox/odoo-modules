"""Les tâches existantes qu'une rencontre a discutées.

Le meeting-processor verse désormais une action dans une tâche déjà ouverte au
lieu d'en créer une neuve. Trois promesses, qu'aucune lecture du code ne rend
évidentes :

* **la tâche garde sa rencontre d'origine** : `task_ids` est l'inverse d'un
  champ unique, et l'ancien rattachement l'écrasait (une tâche de mars arrachée
  à son compte rendu par un appel de septembre) ;
* **le client lit la même liste partout** : PDF, courriel et copie d'échange
  passent par un seul filtre ;
* **une traduction anglaise existante reçoit le titre neuf en anglais**, et une
  langue sans traduction n'en reçoit pas une fabriquée.
"""

import importlib.util
import re
from pathlib import Path

from odoo import Command
from odoo.tests import TransactionCase, tagged

PNG_1PX = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg=="
)

HEADING = "Tâches existantes discutées"


def _load_migration():
    path = (Path(__file__).resolve().parent.parent
            / "migrations" / "18.0.3.59.0" / "post-migrate.py")
    spec = importlib.util.spec_from_file_location("bf_meeting_mig_3_59_0", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@tagged('post_install', '-at_install')
class TestDiscussedTasks(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Le bandeau du PDF appelle image_data_uri() sans garde.
        cls.env.company.meeting_logo = PNG_1PX
        cls.langue = cls.env.user.lang or 'en_US'
        cls.project = cls.env['project.project'].create({'name': 'Projet Discuté'})
        cls.origine = cls.env['meeting.record'].create({
            'name': 'Statutaire de mars', 'room_name': 'Statutaire',
            'date': '2026-03-16 17:00:00', 'project_id': cls.project.id,
            'lang': cls.langue,
        })
        cls.rencontre = cls.env['meeting.record'].create({
            'name': 'Appel de septembre', 'room_name': 'Appel',
            'date': '2026-09-03 17:00:00', 'project_id': cls.project.id,
            'lang': cls.langue,
        })
        cls.ancienne = cls.env['project.task'].create({
            'name': 'LIVRABLE-OUVERT-DEPUIS-MARS', 'project_id': cls.project.id,
            'meeting_id': cls.origine.id,
        })
        cls.annulee = cls.env['project.task'].create({
            'name': 'TACHE-ANNULEE-DEPUIS', 'project_id': cls.project.id,
        })
        cls.annulee.state = '1_canceled'
        cls.neuve = cls.env['project.task'].create({
            'name': 'ACTION-NEE-DE-LA-RENCONTRE', 'project_id': cls.project.id,
            'meeting_id': cls.rencontre.id,
        })
        cls.rencontre.discussed_task_ids = [
            Command.link(cls.ancienne.id), Command.link(cls.annulee.id),
            Command.link(cls.neuve.id),
        ]

    def test_la_tache_garde_sa_rencontre_d_origine(self):
        self.assertEqual(self.ancienne.meeting_id, self.origine)
        self.assertIn(self.ancienne, self.origine.task_ids)
        self.assertNotIn(self.ancienne, self.rencontre.task_ids)
        self.assertIn(self.ancienne, self.rencontre.discussed_task_ids)

    def test_filtre_du_compte_rendu(self):
        montrees = self.rencontre._discussed_tasks_for_report()
        self.assertEqual(montrees, self.ancienne,
                         "ni l'annulée, ni celle déjà listée parmi les éléments d'action")

    def test_le_pdf_montre_la_section(self):
        html = self.env['ir.actions.report']._render_qweb_html(
            'bf_meeting.action_report_meeting_record', [self.rencontre.id])[0]
        html = html.decode() if isinstance(html, bytes) else html
        self.assertIn('LIVRABLE-OUVERT-DEPUIS-MARS', html)
        self.assertNotIn('TACHE-ANNULEE-DEPUIS', html)
        # L'action née de la rencontre reste listée, une seule fois.
        self.assertEqual(html.count('ACTION-NEE-DE-LA-RENCONTRE'), 1)

    def test_le_pdf_sans_tache_discutee_n_a_pas_de_section(self):
        html = self.env['ir.actions.report']._render_qweb_html(
            'bf_meeting.action_report_meeting_record', [self.origine.id])[0]
        html = html.decode() if isinstance(html, bytes) else html
        self.assertNotIn('existantes discut', html)

    def test_le_courriel_montre_la_section(self):
        template = self.env.ref('bf_meeting.meeting_report_mail_template')
        body = template._render_field('body_html', [self.rencontre.id])[self.rencontre.id]
        self.assertIn(HEADING, body)
        self.assertIn('LIVRABLE-OUVERT-DEPUIS-MARS', body)
        self.assertNotIn('TACHE-ANNULEE-DEPUIS', body)
        self.assertEqual(body.count('ACTION-NEE-DE-LA-RENCONTRE'), 1)
        vide = template._render_field('body_html', [self.origine.id])[self.origine.id]
        self.assertNotIn(HEADING, vide)

    def test_la_copie_d_echange_porte_ce_que_le_pdf_montre(self):
        payload = self.env['meeting.exchange'].build_payload(self.rencontre)
        noms = [a['name'] for a in payload['meeting']['action_items']]
        self.assertIn('LIVRABLE-OUVERT-DEPUIS-MARS', noms)
        self.assertIn('ACTION-NEE-DE-LA-RENCONTRE', noms)
        self.assertNotIn('TACHE-ANNULEE-DEPUIS', noms)
        self.assertEqual(len(noms), len(set(noms)))

    def _langue_anglaise(self):
        if 'en_CA' not in dict(self.env['res.lang'].get_installed()):
            self.skipTest("en_CA n'est pas installée sur cette base")

    def _body_values(self, template):
        self.env.flush_all()
        self.env.cr.execute("SELECT body_html FROM mail_template WHERE id = %s", [template.id])
        return self.env.cr.fetchone()[0]

    def test_migration_ajoute_la_section_au_courriel_anglais(self):
        """Le courriel se traduit EN BLOC : la valeur en_CA posée avant 3.59.0 n'a
        pas la section, et la mise à jour du module ne la lui donnera pas."""
        self._langue_anglaise()
        migration = _load_migration()
        template = self.env.ref('bf_meeting.meeting_report_mail_template')
        source = self._body_values(template)['en_US']
        # Les valeurs d'avant, comme chez BF : fr_CA copie de l'ancienne source,
        # en_CA vraie traduction, ni l'une ni l'autre n'a la section.
        ancienne_fr = re.sub(r'\s*<t t-set="discussed_tasks".*?</ul>\s*</t>', '', source,
                             count=1, flags=re.S)
        self.assertNotIn('_discussed_tasks_for_report', ancienne_fr)
        ancienne_en = ancienne_fr.replace("Éléments d'action", 'Action items').replace(
            '(échéance', '(due')
        self.env.cr.execute(
            "UPDATE mail_template SET body_html = body_html"
            " || jsonb_build_object('en_CA', %s::text, 'fr_CA', %s::text) WHERE id = %s",
            [ancienne_en, ancienne_fr, template.id])
        template.invalidate_recordset(['body_html'])

        migration.add_section_to_mail_translations(self.env)
        premiere = self._body_values(template)
        self.assertIn('Existing tasks discussed', premiere['en_CA'],
                      "dès la première passe, pas grâce à la seconde")
        self.assertNotIn(HEADING, premiere['en_CA'])
        migration.add_section_to_mail_translations(self.env)   # rejouée : rien de plus

        valeurs = self._body_values(template)
        self.assertEqual(valeurs, premiere, "la seconde passe ne change rien")
        self.assertEqual(valeurs['en_CA'].count('_discussed_tasks_for_report'), 1)
        self.assertIn('Existing tasks discussed', valeurs['en_CA'])
        self.assertNotIn(HEADING, valeurs['en_CA'])
        self.assertEqual(valeurs['en_US'], source, "la source française est intacte")
        self.assertEqual(valeurs['fr_CA'], source,
                         "la copie française redevient la source, au caractère près")

        rendu_fr = template.with_context(lang='fr_CA')._render_field(
            'body_html', [self.rencontre.id])[self.rencontre.id]
        self.assertIn(HEADING, rendu_fr)
        self.assertIn('LIVRABLE-OUVERT-DEPUIS-MARS', rendu_fr)

        rendu = template.with_context(lang='en_CA')._render_field(
            'body_html', [self.rencontre.id])[self.rencontre.id]
        self.assertIn('Existing tasks discussed', rendu)
        self.assertIn('LIVRABLE-OUVERT-DEPUIS-MARS', rendu)
        self.assertIn('Action items', rendu)

    def test_migration_ne_fabrique_pas_de_traduction(self):
        migration = _load_migration()
        template = self.env.ref('bf_meeting.meeting_report_mail_template')
        self.env.cr.execute(
            "UPDATE mail_template SET body_html = body_html - 'en_CA' WHERE id = %s",
            [template.id])
        vue = self.env.ref('bf_meeting.report_meeting_record')
        self.env.cr.execute(
            "UPDATE ir_ui_view SET arch_db = arch_db - 'en_CA' WHERE id = %s", [vue.id])
        self.env.invalidate_all()
        migration.translate_report_heading(self.env)
        migration.add_section_to_mail_translations(self.env)
        self.env.flush_all()
        self.assertNotIn('en_CA', self._body_values(template))
        self.env.cr.execute("SELECT arch_db FROM ir_ui_view WHERE id = %s", [vue.id])
        self.assertNotIn('en_CA', self.env.cr.fetchone()[0])

    def test_migration_traduit_le_titre_du_pdf_anglais(self):
        """Le PDF se traduit TERME PAR TERME : le titre neuf arrive en français."""
        self._langue_anglaise()
        vue = self.env.ref('bf_meeting.report_meeting_record')
        # Partir de l'état d'une montée fraîche, quelle que soit la base : le titre
        # neuf SANS traduction (False = retour au terme source), l'ancien traduit.
        vue.update_field_translations('arch_db', {'en_CA': {
            "Éléments d'action": 'Action items', HEADING: False}})
        self.env.flush_all()
        self.env.cr.execute("SELECT arch_db ->> 'en_CA' FROM ir_ui_view WHERE id = %s", [vue.id])
        self.assertIn(HEADING, self.env.cr.fetchone()[0], "précondition : titre encore en français")
        _load_migration().translate_report_heading(self.env)
        self.env.flush_all()
        self.env.cr.execute("SELECT arch_db ->> 'en_CA' FROM ir_ui_view WHERE id = %s", [vue.id])
        arch = self.env.cr.fetchone()[0]
        self.assertIn('Existing tasks discussed', arch)
        self.assertIn('Action items', arch)
        self.assertNotIn(HEADING, arch)
