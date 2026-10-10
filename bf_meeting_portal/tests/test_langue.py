"""Le portail se lit dans la langue du visiteur.

Les libellés de présence vivaient dans un dictionnaire de module, écrits une
fois pour toutes : la source passée en anglais, un client francophone les
aurait lus en anglais. Ils sont traduits à l'appel, par l'environnement de la
requête.
"""

from types import SimpleNamespace

from odoo.tests import TransactionCase, tagged

from ..controllers import portal as portal_module
from ..controllers.portal import PortalMeeting


@tagged('post_install', '-at_install')
class TestLanguePortail(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['res.lang']._activate_lang('fr_CA')
        cls.env['ir.module.module']._load_module_terms(
            ['bf_meeting_portal'], ['fr_CA'], overwrite=True)
        cls.rencontre = cls.env['meeting.record'].create({
            'name': 'Rencontre portail', 'room_name': 'Portail',
            'date': '2026-09-15 17:00:00',
        })
        cls.client = cls.env['res.partner'].create({'name': 'Client Portail'})
        cls.env['meeting.attendance'].create({
            'meeting_id': cls.rencontre.id, 'partner_id': cls.client.id,
            'status': 'excused',
        })

    def _visiteur(self, lang):
        env = self.env(context=dict(self.env.context, lang=lang))
        self.patch(portal_module, 'request', SimpleNamespace(env=env))

    def test_presence_dans_la_langue_du_visiteur(self):
        self._visiteur('fr_CA')
        statuts = [a['status'] for a in PortalMeeting()._attendance_ctx(self.rencontre)]
        self.assertEqual(statuts, ['Excusé'])
        self._visiteur('en_US')
        statuts = [a['status'] for a in PortalMeeting()._attendance_ctx(self.rencontre)]
        self.assertEqual(statuts, ['Excused'])

    def test_statut_inconnu_reste_vide(self):
        self._visiteur('fr_CA')
        self.assertEqual(portal_module._attendance_label('present'), 'Présent')
        self.assertEqual(portal_module._attendance_label('absent'), 'Absent')
        self.assertEqual(portal_module._attendance_label('autre'), '')
