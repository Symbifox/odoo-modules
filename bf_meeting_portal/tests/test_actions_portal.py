"""La page portail d'un compte rendu montre les mêmes éléments d'action que le
PDF et le courriel : la règle vient de bf_meeting (`_action_tasks_for_report`),
ni tâche annulée, ni la tâche qui porte le compte rendu."""

from types import SimpleNamespace

from odoo.tests import TransactionCase, tagged

from ..controllers import portal as portal_module
from ..controllers.portal import PortalMeeting


@tagged('post_install', '-at_install')
class TestActionsPortal(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        project = cls.env['project.project'].create({'name': 'Projet Portail Actions'})
        cls.rencontre = cls.env['meeting.record'].create({
            'name': 'Statutaire du 19 octobre', 'room_name': 'Statutaire',
            'date': '2026-10-19 21:30:00', 'project_id': project.id,
        })
        Task = cls.env['project.task']
        lie = {'project_id': project.id, 'meeting_id': cls.rencontre.id}
        cls.ouverte = Task.create(dict(lie, name='ACTION-OUVERTE'))
        cls.faite = Task.create(dict(lie, name='ACTION-FAITE'))
        cls.faite.state = '1_done'
        cls.annulee = Task.create(dict(lie, name='ACTION-ANNULEE'))
        cls.annulee.state = '1_canceled'
        cls.mere = Task.create(dict(lie, name=cls.rencontre.name))
        # La tâche du compte rendu, renommée depuis : reconnue à son étape.
        etape = cls.env['project.task.type'].create({
            'name': 'Comptes rendus de rencontres', 'project_ids': [(4, project.id)]})
        cls.mere_renommee = Task.create(dict(lie, name='Rencontre — 2026-10-19', stage_id=etape.id))

    def setUp(self):
        super().setUp()
        self.patch(portal_module, 'request', SimpleNamespace(env=self.env))

    def test_la_page_et_le_pdf_lisent_la_meme_liste(self):
        ctx = PortalMeeting()._record_ctx(self.rencontre)
        self.assertCountEqual([a['name'] for a in ctx['actions']],
                              ['ACTION-OUVERTE', 'ACTION-FAITE'])
        self.assertEqual([a['name'] for a in ctx['actions']],
                         self.rencontre._action_tasks_for_report().mapped('name'))
        self.assertEqual([a['done'] for a in ctx['actions'] if a['name'] == 'ACTION-FAITE'], [True])
