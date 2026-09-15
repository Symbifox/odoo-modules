"""La page portail d'un compte rendu montre les tâches existantes discutées
, avec le même filtre que le PDF et les mêmes exclusions que les
éléments d'action : ni tâche annulée, ni la tâche qui porte le compte rendu."""

from types import SimpleNamespace

from odoo import Command
from odoo.tests import TransactionCase, tagged

from ..controllers import portal as portal_module
from ..controllers.portal import PortalMeeting


@tagged('post_install', '-at_install')
class TestDiscussedPortal(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        project = cls.env['project.project'].create({'name': 'Projet Portail'})
        cls.rencontre = cls.env['meeting.record'].create({
            'name': 'Statutaire du 15 septembre', 'room_name': 'Statutaire',
            'date': '2026-09-15 17:00:00', 'project_id': project.id,
        })
        Task = cls.env['project.task']
        cls.ouverte = Task.create({'name': 'LIVRABLE-OUVERT', 'project_id': project.id})
        cls.annulee = Task.create({'name': 'LIVRABLE-ANNULE', 'project_id': project.id})
        cls.annulee.state = '1_canceled'
        # La tâche du compte rendu porte le nom de la rencontre : jamais une action.
        cls.mere = Task.create({'name': cls.rencontre.name, 'project_id': project.id})
        cls.rencontre.discussed_task_ids = [
            Command.link(t.id) for t in (cls.ouverte, cls.annulee, cls.mere)]

    def setUp(self):
        super().setUp()
        # Le contexte des présences lit `request.env` ; hors requête HTTP, on
        # lui prête l'environnement du test.
        self.patch(portal_module, 'request', SimpleNamespace(env=self.env))

    def test_la_page_recoit_les_taches_discutees(self):
        ctx = PortalMeeting()._record_ctx(self.rencontre)
        self.assertEqual([a['name'] for a in ctx['discussed']], ['LIVRABLE-OUVERT'])
        self.assertEqual(ctx['actions'], [])

    def test_le_gabarit_affiche_la_section(self):
        arch = self.env.ref('bf_meeting_portal.portal_meeting_record').arch
        self.assertIn("doc.get('discussed')", arch)
        self.assertIn('Tâches existantes discutées', arch)
