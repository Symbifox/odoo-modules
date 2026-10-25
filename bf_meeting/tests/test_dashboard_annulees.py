"""Une rencontre annulée sort du tableau de bord et du digest.

Constat : un OdJ annulé ressortait chaque matin dans « CR à compléter » du
digest « Votre journée ». La source 1 de la vue
`meeting_dashboard_line` (rencontres ancrées sur un événement) n'écartait ni
l'OdJ annulé ni l'événement que le calendrier dit annulé ; seule la source 2
(OdJ sans événement) écartait l'OdJ annulé.

Chaque essai d'absence a son témoin vivant, construit de la même façon : une
vue qui ne rendrait plus rien passerait sinon pour un correctif.
"""

from datetime import timedelta

from odoo import Command, fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'bf_meeting_annulees')
class TestDashboardAnnulees(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True,
                                       skip_auto_refine=True))
        cls.client = cls.env['res.partner'].create({
            'name': 'Cliente Annulée', 'email': 'cliente.annulee@example.com'})
        cls.project = cls.env['project.project'].create({
            'name': 'Projet Annulées', 'partner_id': cls.client.id})
        cls.organiser = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Organisatrice Annulées',
            'login': 'annulees_organiser',
            'email': 'organisatrice.annulees@example.com',
            'groups_id': [Command.set([
                cls.env.ref('base.group_user').id,
                cls.env.ref('bf_meeting.group_meeting_user').id,
            ])],
        })
        cls.Line = cls.env['meeting.dashboard.line']
        # La vue en base est celle de la dernière pose : on la refait depuis le
        # code sur le disque (annulé avec la transaction de l'essai).
        cls.Line._bf_create_view(cls.Line._bf_has_event_status())

    def _event(self, days, name):
        start = (fields.Datetime.now() + timedelta(days=days)).replace(microsecond=0)
        return self.env['calendar.event'].create({
            'name': name,
            'start': start,
            'stop': start + timedelta(hours=1),
            'user_id': self.organiser.id,
            'partner_ids': [Command.set([self.organiser.partner_id.id, self.client.id])],
        })

    def _agenda(self, event, state='draft'):
        agenda = self.env['meeting.agenda'].create({
            'project_id': self.project.id,
            'date': event.start,
            'duration_planned': 60,
            'calendar_event_id': event.id,
            'organizer_id': self.organiser.id,
            'participant_ids': [Command.set([self.client.id])],
        })
        if state != 'draft':
            agenda.state = state
        return agenda

    def _line(self, event):
        self.env.flush_all()
        return self.Line.search([('event_id', '=', event.id)])

    def _buckets(self):
        self.env.flush_all()
        bucket = self.env['meeting.dashboard']._get_digest_buckets(
            user_ids=[self.organiser.id]).get(self.organiser.id, {'odj': [], 'cr': []})
        return ({r['event_id'] for r in bucket['odj']},
                {r['event_id'] for r in bucket['cr']})

    def test_odj_annule_passe_sort_du_cr_a_completer(self):
        temoin = self._event(-2, 'Passée vivante')
        self._agenda(temoin)
        annulee = self._event(-2, 'Passée annulée')
        self._agenda(annulee, state='cancelled')
        self.assertTrue(self._line(temoin))
        self.assertFalse(self._line(annulee))
        _odj, cr = self._buckets()
        self.assertIn(temoin.id, cr)
        self.assertNotIn(annulee.id, cr)

    def test_odj_annule_a_venir_sort_de_l_odj_a_preparer(self):
        temoin = self._event(2, 'À venir vivante')
        self._agenda(temoin)
        annulee = self._event(2, 'À venir annulée')
        self._agenda(annulee, state='cancelled')
        odj, _cr = self._buckets()
        self.assertIn(temoin.id, odj)
        self.assertNotIn(annulee.id, odj)

    def test_un_odj_vivant_passe_devant_un_odj_annule(self):
        """Un OdJ annulé puis refait : la rencontre reste, avec l'OdJ vivant."""
        event = self._event(2, 'Refaite')
        ancien = self._agenda(event, state='cancelled')
        nouveau = self._agenda(event)
        self.assertLess(ancien.id, nouveau.id)
        line = self._line(event)
        self.assertEqual(line.agenda_id, nouveau)

    def test_evenement_annule_sort_meme_sans_odj(self):
        temoin = self._event(2, 'Sans OdJ vivante')
        annulee = self._event(2, 'Sans OdJ annulée')
        if 'bf_event_status' not in annulee._fields:
            self.skipTest("bf_calendar_invite absent : l'annulation archive l'événement")
        annulee.bf_event_status = 'cancelled'
        self.assertTrue(self._line(temoin))
        self.assertFalse(self._line(annulee))
        odj, _cr = self._buckets()
        self.assertIn(temoin.id, odj)
        self.assertNotIn(annulee.id, odj)

    def test_evenement_annule_l_emporte_sur_un_odj_vivant(self):
        event = self._event(-2, 'Annulée au calendrier')
        if 'bf_event_status' not in event._fields:
            self.skipTest("bf_calendar_invite absent")
        self._agenda(event)
        self.assertTrue(self._line(event))
        event.bf_event_status = 'cancelled'
        self.assertFalse(self._line(event))

    def _record(self, event):
        return self.env['meeting.record'].create({
            'project_id': self.project.id,
            'date': event.start,
            'calendar_event_id': event.id,
        })

    def test_odj_annule_mais_cr_redige_reste_a_completer(self):
        """Absence notée : l'OdJ est annulé, puis le CR est rédigé quand même."""
        event = self._event(-2, 'Tenue malgré tout')
        self._agenda(event, state='cancelled')
        self.assertFalse(self._line(event))
        self._record(event)
        self.assertTrue(self._line(event))
        _odj, cr = self._buckets()
        self.assertIn(event.id, cr)

    def test_evenement_annule_mais_cr_redige_reste_a_completer(self):
        event = self._event(-2, 'Annulée puis tenue')
        if 'bf_event_status' not in event._fields:
            self.skipTest("bf_calendar_invite absent")
        event.bf_event_status = 'cancelled'
        self.assertFalse(self._line(event))
        self._record(event)
        _odj, cr = self._buckets()
        self.assertIn(event.id, cr)

    def test_sans_la_colonne_l_odj_annule_sort_quand_meme(self):
        """Variante posée sans bf_calendar_invite : la vue ne cite pas la colonne."""
        temoin = self._event(-2, 'Sans colonne vivante')
        self._agenda(temoin)
        annulee = self._event(-2, 'Sans colonne annulée')
        self._agenda(annulee, state='cancelled')
        self.env.flush_all()
        self.Line._bf_create_view(False)
        self.assertTrue(self._line(temoin))
        self.assertFalse(self._line(annulee))

    def test_un_echec_du_registre_ne_bloque_pas_le_chargement(self):
        """Vue emportée et non refaite : le registre se charge, l'erreur se voit."""
        self.env.flush_all()
        self.env.cr.execute("DROP VIEW meeting_dashboard_line")

        def echec(self, has_event_status):
            raise RuntimeError("verrou non obtenu")
        self.patch(type(self.Line), '_bf_create_view', echec)
        with self.assertLogs('odoo.addons.bf_meeting.models.meeting_dashboard', 'ERROR') as journal:
            self.Line._register_hook()
        self.assertIn('ABSENTE', journal.output[0])

    def test_le_registre_refait_une_vue_disparue(self):
        """`DROP COLUMN … CASCADE` à la désinstallation de bf_calendar_invite."""
        event = self._event(-2, 'Vue refaite')
        self._agenda(event)
        self.env.flush_all()
        self.env.cr.execute("DROP VIEW meeting_dashboard_line")
        self.Line._register_hook()
        self.assertTrue(self._line(event))

    def test_le_registre_refait_une_vue_qui_ignore_la_colonne(self):
        """bf_calendar_invite posé après bf_meeting : la vue doit le rattraper."""
        if not self.Line._bf_has_event_status():
            self.skipTest("bf_calendar_invite absent")
        event = self._event(2, 'Rattrapée')
        event.bf_event_status = 'cancelled'
        self.env.flush_all()
        self.Line._bf_create_view(False)
        self.assertTrue(self._line(event))
        self.Line._register_hook()
        self.assertFalse(self._line(event))

    # La règle de la vue change de version à chaque CREATE OR REPLACE.
    _regle_sql = """
        SELECT r.xmin FROM pg_rewrite r
        JOIN pg_class c ON c.oid = r.ev_class
        WHERE c.relname = 'meeting_dashboard_line'
    """

    def test_le_registre_ne_touche_pas_une_vue_a_jour(self):
        self.env.flush_all()
        self.env.cr.execute("SELECT pg_get_viewdef('meeting_dashboard_line'::regclass)")
        avant = self.env.cr.fetchone()[0]
        self.env.cr.execute(self._regle_sql)
        xmin = self.env.cr.fetchone()[0]
        self.Line._register_hook()
        self.env.cr.execute(self._regle_sql)
        self.assertEqual(self.env.cr.fetchone()[0], xmin)
        self.env.cr.execute("SELECT pg_get_viewdef('meeting_dashboard_line'::regclass)")
        self.assertEqual(self.env.cr.fetchone()[0], avant)
