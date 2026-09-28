"""Accès aux entrées RPC du tableau de bord des rencontres.

`meeting.dashboard` est un AbstractModel dont les méthodes publiques
`@api.model` s'appellent par RPC sans contrôle des droits du modèle ;
`get_dashboard_data()` lit par SQL brut. Le scoping société/projet y est
recopié à la main, mais c'est le contrôle de groupe en entrée qui empêche un
usager portail ou un interne hors `group_meeting_user` d'y lire les rencontres.
"""

from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestMeetingDashboardAccess(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env['res.users'].with_context(no_reset_password=True)
        cls.portal_user = Users.create({
            'name': 'Portail Rencontres',
            'login': 'meeting_portal',
            'groups_id': [Command.set([cls.env.ref('base.group_portal').id])],
        })
        cls.internal_user = Users.create({
            'name': 'Interne hors groupe',
            'login': 'meeting_internal',
            'groups_id': [Command.set([cls.env.ref('base.group_user').id])],
        })
        cls.meeting_user = Users.create({
            'name': 'Membre Rencontres',
            'login': 'meeting_user',
            'groups_id': [Command.set([cls.env.ref('bf_meeting.group_meeting_user').id])],
        })
        start = fields.Datetime.now() + timedelta(days=2)
        cls.event = cls.env['calendar.event'].create({
            'name': 'Rencontre confidentielle essai',
            'start': start,
            'stop': start + timedelta(hours=1),
            'user_id': cls.meeting_user.id,
            'partner_ids': [Command.set([
                cls.meeting_user.partner_id.id,
                cls.env['res.partner'].create({'name': 'Invité essai'}).id,
            ])],
        })

    def _dashboard(self, user):
        return self.env['meeting.dashboard'].with_user(user)

    def _assert_refused(self, user):
        dash = self._dashboard(user)
        with self.assertRaises(AccessError):
            dash.get_dashboard_data()
        with self.assertRaises(AccessError):
            dash.open_filtered_list('upcoming')
        with self.assertRaises(AccessError):
            dash.open_record('calendar.event', self.event.id)
        with self.assertRaises(AccessError):
            dash.dismiss_event(self.event.id)
        with self.assertRaises(AccessError):
            dash.dismiss_events([self.event.id])
        with self.assertRaises(AccessError):
            dash.toggle_step_skip(self.event.id, 1)
        self.assertFalse(self.event.bf_skip_dashboard)

    def test_portal_user_refused(self):
        self._assert_refused(self.portal_user)

    def test_internal_user_without_group_refused(self):
        self._assert_refused(self.internal_user)

    def test_meeting_user_gets_data(self):
        data = self._dashboard(self.meeting_user).get_dashboard_data()
        self.assertIn('kpis', data)
        self.assertGreaterEqual(data['kpis']['total_open'], 1)
        names = [c['name'] for c in data['cards_left'] + data['cards_right']]
        self.assertIn(self.event.name, names)
        action = self._dashboard(self.meeting_user).open_filtered_list('upcoming')
        self.assertEqual(action['res_model'], 'meeting.dashboard.line')
