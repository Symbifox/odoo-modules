"""Planifier une rencontre à partir d'un ordre du jour.

Quatre gestes, et ce que chacun doit garder :

* le champ « Ordre du jour » d'une rencontre ne propose que les OdJ « à
  planifier » : brouillon ou confirmé, sans rencontre vivante ;
* choisir un OdJ dans une rencontre NEUVE la remplit, là où elle est vide ;
  une rencontre déjà enregistrée n'est pas touchée (elle vient de Nextcloud
  ou d'un rendez-vous, et ses invités sont déjà prévenus) ;
* lier, de quelque côté que ce soit, recale la date et la durée de l'OdJ sur
  la rencontre, et la date que porte son titre ;
* « Reporter » détache l'OdJ de sa rencontre sans rien lui retirer.
"""

from datetime import datetime
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import Form, TransactionCase, tagged


@tagged('post_install', '-at_install', 'bf_meeting_planifier')
class TestPlanifierDepuisOdj(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True,
                                       tz='America/Toronto',
                                       skip_auto_refine=True))
        cls.client = cls.env['res.partner'].create({
            'name': 'Cliente Planif', 'email': 'cliente.planif@example.com'})
        cls.collegue = cls.env['res.partner'].create({
            'name': 'Collègue Planif', 'email': 'collegue.planif@example.com'})
        cls.project = cls.env['project.project'].create({
            'name': 'Projet Planif', 'partner_id': cls.client.id})
        # Une usagère ordinaire des Rencontres : le superutilisateur ne voit pas
        # la vue comme elle (champ réservé à un groupe), et son partenaire,
        # OdooBot, n'est pas invité par défaut comme le serait le sien.
        cls.organiser = cls.env['res.users'].create({
            'name': 'Organisatrice Planif',
            'login': 'bf_planif_organiser',
            'email': 'organisatrice.planif@example.com',
            'groups_id': [Command.set([
                cls.env.ref('base.group_user').id,
                cls.env.ref('bf_meeting.group_meeting_user').id,
            ])],
        })
        # La règle des OdJ suit l'accès au projet : il faut le suivre.
        cls.project.message_subscribe(partner_ids=cls.organiser.partner_id.ids)

    def _agenda(self, **values):
        vals = {
            'project_id': self.project.id,
            'date': '2026-09-28 19:00:00',
            'duration_planned': 45,
            'participant_ids': [Command.set([self.client.id, self.collegue.id])],
        }
        vals.update(values)
        return self.env['meeting.agenda'].create(vals)

    def _event(self, **values):
        vals = {
            'name': 'Rencontre existante',
            'start': '2026-09-29 19:00:00',
            'stop': '2026-09-29 20:30:00',
            'partner_ids': [Command.set([self.env.user.partner_id.id])],
        }
        vals.update(values)
        return self.env['calendar.event'].with_context(
            no_mail_to_attendees=True).create(vals)

    # --- la liste « à planifier » ------------------------------------------

    def test_a_planifier_selon_l_etat_et_la_rencontre(self):
        libre = self._agenda()
        confirme = self._agenda(state='confirmed')
        lie = self._agenda(calendar_event_id=self._event().id)
        termine = self._agenda(state='done')
        annule = self._agenda(state='cancelled')
        archive = self._event(name='Rencontre archivée')
        orphelin = self._agenda(calendar_event_id=archive.id)
        archive.active = False

        self.assertTrue(libre.bf_to_schedule)
        self.assertTrue(confirme.bf_to_schedule)
        self.assertFalse(lie.bf_to_schedule, "une rencontre vivante le retient")
        self.assertFalse(termine.bf_to_schedule)
        self.assertFalse(annule.bf_to_schedule)
        orphelin.invalidate_recordset(['bf_to_schedule'])
        self.assertTrue(orphelin.bf_to_schedule,
                        "une rencontre archivée n'aura pas lieu")

        tous = libre | confirme | lie | termine | annule | orphelin
        Agenda = self.env['meeting.agenda']
        oui = Agenda.search([('id', 'in', tous.ids), ('bf_to_schedule', '=', True)])
        non = Agenda.search([('id', 'in', tous.ids), ('bf_to_schedule', '=', False)])
        self.assertEqual(oui, libre | confirme | orphelin)
        self.assertEqual(non, lie | termine | annule,
                         "la négation doit porter sur tout le domaine")

    def test_la_liste_ne_montre_que_les_projets_suivis(self):
        """Le domaine s'ajoute à la règle des OdJ, il ne la contourne pas."""
        agenda = self._agenda(name='OdJ planif suivi')
        etrangere = self.env['res.users'].create({
            'name': 'Hors projet', 'login': 'bf_planif_hors_projet',
            'groups_id': [Command.set([
                self.env.ref('base.group_user').id,
                self.env.ref('bf_meeting.group_meeting_user').id,
            ])],
        })
        domaine = [('bf_to_schedule', '=', True)]
        Agenda = self.env['meeting.agenda']
        vus = Agenda.with_user(self.organiser).name_search('OdJ planif suivi', domaine)
        self.assertIn(agenda.id, [i for i, _n in vus])
        vus = Agenda.with_user(etrangere).name_search('OdJ planif suivi', domaine)
        self.assertNotIn(agenda.id, [i for i, _n in vus])

    def test_une_rencontre_annulee_rend_l_odj_a_planifier(self):
        event = self._event()
        if 'bf_event_status' not in event._fields:
            self.skipTest("bf_calendar_invite absent : l'annulation supprime")
        agenda = self._agenda(calendar_event_id=event.id)
        event.bf_event_status = 'cancelled'
        agenda.invalidate_recordset(['bf_to_schedule'])
        self.assertTrue(agenda.bf_to_schedule)
        self.assertIn(agenda, self.env['meeting.agenda'].search(
            [('bf_to_schedule', '=', True)]))

    def test_le_champ_de_la_rencontre_porte_le_filtre(self):
        """Le domaine vit dans la vue : les deux formulaires doivent le porter."""
        for xmlid in ('calendar.view_calendar_event_form',
                      'calendar.view_calendar_event_form_quick_create'):
            view = self.env.ref(xmlid)
            arch = self.env['calendar.event'].with_user(self.organiser).get_view(view.id)['arch']
            self.assertIn('meeting_agenda_id', arch, xmlid)
            self.assertIn('bf_to_schedule', arch, xmlid)

    def test_sans_rencontres_la_creation_rapide_n_a_pas_le_champ(self):
        calendrier = self.env['res.users'].create({
            'name': 'Calendrier seul', 'login': 'bf_planif_calendrier',
            'groups_id': [Command.set([self.env.ref('base.group_user').id])],
        })
        view = self.env.ref('calendar.view_calendar_event_form_quick_create')
        arch = self.env['calendar.event'].with_user(calendrier).get_view(view.id)['arch']
        self.assertNotIn('meeting_agenda_id', arch)

    def test_une_rencontre_s_ouvre_sans_les_rencontres(self):
        """🔴 18.0.3.65.2 : le formulaire de rencontre ne lit rien des Rencontres
        pour qui n'en a pas le groupe. Le client web lit tous les champs d'une
        vue, invisibles compris : un compte sans ce groupe recevait un refus
        d'accès sur `meeting.agenda` en ouvrant n'importe quelle rencontre."""
        interne = self.env['res.users'].create({
            'name': 'Interne sans Rencontres', 'login': 'bf_planif_sans_rencontres',
            'email': 'interne.sans.rencontres@example.com',
            'groups_id': [Command.set([self.env.ref('base.group_user').id])],
        })
        vue = self.env.ref('calendar.view_calendar_event_form').id
        arch = self.env['calendar.event'].with_user(interne).get_view(vue)['arch']
        for nom in ('meeting_agenda_id', 'meeting_record_id', 'meeting_agenda_count',
                    'meeting_record_count', 'bf_needs_agenda'):
            self.assertNotIn('name="%s"' % nom, arch, nom)
        arch = self.env['calendar.event'].with_user(self.organiser).get_view(vue)['arch']
        self.assertIn('name="meeting_agenda_id"', arch)

    # --- le recalage de l'OdJ -----------------------------------------------

    def test_lier_depuis_la_rencontre_recale_date_et_duree(self):
        agenda = self._agenda()
        event = self._event()
        event.meeting_agenda_id = agenda
        self.assertEqual(agenda.calendar_event_id, event)
        self.assertEqual(agenda.date, event.start)
        self.assertEqual(agenda.duration_planned, 90)

    def test_lier_depuis_l_odj_recale_date_et_duree(self):
        agenda = self._agenda()
        event = self._event()
        agenda.calendar_event_id = event
        self.assertEqual(agenda.date, event.start)
        self.assertEqual(agenda.duration_planned, 90)

    def test_creer_l_odj_sur_une_rencontre_prend_sa_date(self):
        event = self._event()
        agenda = self._agenda(calendar_event_id=event.id)
        self.assertEqual(agenda.date, event.start)

    def test_une_journee_entiere_ne_deplace_rien(self):
        agenda = self._agenda()
        event = self._event(allday=True, start='2026-09-29 00:00:00',
                            stop='2026-09-29 00:00:00')
        agenda.calendar_event_id = event
        self.assertEqual(agenda.date, datetime(2026, 9, 28, 19, 0))
        self.assertEqual(agenda.duration_planned, 45)

    def test_le_titre_suit_sa_date(self):
        agenda = self._agenda(name='Projet Planif — OdJ — 2026-09-28')
        agenda.calendar_event_id = self._event()
        self.assertEqual(agenda.name, 'Projet Planif — OdJ — 2026-09-29')

    def test_un_titre_sans_date_reste_tel_quel(self):
        agenda = self._agenda(name='Statutaire Ana & Léo')
        agenda.calendar_event_id = self._event()
        self.assertEqual(agenda.name, 'Statutaire Ana & Léo')

    def test_un_miroir_de_federation_ne_se_recale_pas(self):
        """Le module de fédération refuse qu'on écrive la date d'un miroir : le
        recaler rendait impossible de le lier à une rencontre."""
        agenda = self._agenda()
        with patch.object(type(agenda), '_bf_is_mirror', lambda self: True):
            agenda.calendar_event_id = self._event()
        self.assertEqual(agenda.date, datetime(2026, 9, 28, 19, 0))
        self.assertTrue(agenda.calendar_event_id)

    # --- ce que l'OdJ donne à la rencontre ----------------------------------

    def test_titre_de_rencontre(self):
        cas = {
            'Cliente Alpha - Rencontre statutaire — OdJ — 2026-10-06':
                'Cliente Alpha - Rencontre statutaire',
            'OdJ - Statutaire Bêta & Gamma - 2026-10-05':
                'Statutaire Bêta & Gamma',
            'Agenda - Exploratory Meeting - 2026-09-10':
                'Exploratory Meeting',
            'Delta — OdJ Touchpoint de suivi': 'Delta — Touchpoint de suivi',
            'Soudure-Bess Design': 'Soudure-Bess Design',
        }
        for nom, attendu in cas.items():
            self.assertEqual(self._agenda(name=nom)._bf_event_title(), attendu, nom)

    def test_titre_vide_retombe_sur_la_serie_puis_le_projet(self):
        self.assertEqual(
            self._agenda(name='OdJ — 2026-09-10', series_name='Statutaire')._bf_event_title(),
            'Statutaire')
        self.assertEqual(self._agenda(name='OdJ — 2026-09-10')._bf_event_title(),
                         'Projet Planif')

    def test_description_objectifs_et_sujets_acceptes_seulement(self):
        agenda = self._agenda(objectives="Arrêter le périmètre\nFixer <la> date",
                              context_html='<p>Note interne du contexte</p>',
                              preparation_html='<p>Note de préparation</p>')
        Topic = self.env['meeting.agenda.topic']
        Topic.create({'agenda_id': agenda.id, 'name': 'Budget & échéancier',
                      'duration_planned': 15, 'sequence': 1})
        Topic.create({'agenda_id': agenda.id, 'name': 'Proposé en attente',
                      'source': 'contributed', 'moderation_state': 'pending',
                      'sequence': 2})
        html = str(agenda._bf_event_description())
        self.assertIn('Objectifs', html)
        self.assertIn('Fixer &lt;la&gt; date', html, "le texte doit être échappé")
        self.assertIn('Budget &amp; échéancier (15 min)', html)
        self.assertNotIn('Proposé en attente', html)
        self.assertNotIn('Note interne', html)
        self.assertNotIn('préparation', html)

    def test_description_en_anglais(self):
        agenda = self._agenda(objectives='Scope', lang='en_US')
        if (agenda._bf_langue_envoi() or '')[:2] != 'en':
            self.skipTest("en_US non installé sur cette base")
        self.assertIn('Objectives', str(agenda._bf_event_description()))

    # --- le remplissage d'une rencontre neuve -------------------------------

    def test_une_rencontre_neuve_se_remplit(self):
        agenda = self._agenda(name='Statutaire Cliente — OdJ — 2026-09-28',
                              objectives='Arrêter le périmètre',
                              meeting_type='video',
                              location='https://meet.example.com/abc')
        with Form(self.env['calendar.event'].with_user(self.organiser).with_context(
                no_mail_to_attendees=True)) as form:
            form.start = datetime(2026, 10, 14, 18, 0)
            form.meeting_agenda_id = agenda
            self.assertEqual(form.name, 'Statutaire Cliente')
            self.assertEqual(form.duration, 0.75)
            self.assertEqual(form.videocall_location, 'https://meet.example.com/abc')
            self.assertIn('Arrêter le périmètre', str(form.description))
        event = form.record
        self.assertIn(self.client, event.partner_ids)
        self.assertIn(self.collegue, event.partner_ids)
        self.assertIn(self.organiser.partner_id, event.partner_ids,
                      "l'organisatrice reste invitée")
        self.assertEqual(agenda.calendar_event_id, event)
        self.assertEqual(agenda.date, event.start)
        self.assertEqual(agenda.name, 'Statutaire Cliente — OdJ — 2026-10-14')

    def test_une_rencontre_neuve_garde_ce_qu_on_y_a_ecrit(self):
        agenda = self._agenda(location='Salle 3', meeting_type='in_person',
                              objectives='Arrêter le périmètre')
        with Form(self.env['calendar.event'].with_user(self.organiser).with_context(
                no_mail_to_attendees=True)) as form:
            form.name = 'Mon titre'
            form.start = datetime(2026, 10, 14, 18, 0)
            form.location = 'Au bureau'
            form.description = '<p>Ma description</p>'
            form.meeting_agenda_id = agenda
            self.assertEqual(form.name, 'Mon titre')
            self.assertEqual(form.location, 'Au bureau')
            self.assertIn('Ma description', str(form.description))

    def test_une_rencontre_enregistree_n_est_pas_touchee(self):
        """Ajouter des invités à une rencontre de Nextcloud leur enverrait une
        seconde invitation : c'est l'OdJ qui suit, pas l'inverse."""
        agenda = self._agenda(objectives='Arrêter le périmètre')
        event = self._event()
        with Form(event.with_user(self.organiser).with_context(no_mail_to_attendees=True)) as form:
            form.meeting_agenda_id = agenda
            self.assertEqual(form.name, 'Rencontre existante')
            self.assertEqual(form.duration, 1.5)
        self.assertNotIn(self.client, event.partner_ids)
        self.assertEqual(agenda.calendar_event_id, event)
        self.assertEqual(agenda.date, event.start)

    def test_plus_d_options_garde_la_duree_choisie(self):
        """Le formulaire complet rejoue l'onchange : la durée de la création
        rapide arrive en `default_duration` et doit tenir."""
        agenda = self._agenda()
        Event = self.env['calendar.event'].with_user(self.organiser).with_context(
            # Ce que transmet le cœur (`getDefaultValuesFromRecord`) : début,
            # fin ET durée de la création rapide, plus l'OdJ par notre patch.
            no_mail_to_attendees=True, default_duration=2.0,
            default_start='2026-10-14 18:00:00', default_stop='2026-10-14 20:00:00',
            default_meeting_agenda_id=agenda.id)
        with Form(Event) as form:
            self.assertEqual(form.duration, 2.0)
            self.assertEqual(form.meeting_agenda_id, agenda)
        self.assertEqual(agenda.calendar_event_id, form.record)
        self.assertEqual(agenda.duration_planned, 120, "l'OdJ suit la rencontre")

    def test_le_clic_sur_la_grille_prend_la_duree_de_l_odj(self):
        """🔴 La grille passe toujours sa durée en `default_duration`
        (`date_delay`) : une garde sur ce contexte coupait la durée de l'OdJ
        dans la création rapide."""
        agenda = self._agenda()
        Event = self.env['calendar.event'].with_user(self.organiser).with_context(
            no_mail_to_attendees=True, default_duration=1.0,
            default_start='2026-10-14 18:00:00', default_stop='2026-10-14 19:00:00')
        with Form(Event) as form:
            form.meeting_agenda_id = agenda
            self.assertEqual(form.duration, 0.75)

    def test_un_odj_deja_lie_n_est_pas_repris_par_defaut(self):
        """« Nouveau » depuis le formulaire complet garde le contexte de
        « Plus d'options » : l'OdJ déjà lié ne doit pas suivre."""
        libre = self._agenda()
        lie = self._agenda(calendar_event_id=self._event().id)
        Event = self.env['calendar.event'].with_user(self.organiser)
        self.assertEqual(Event.with_context(default_meeting_agenda_id=libre.id)
                         .default_get(['meeting_agenda_id']).get('meeting_agenda_id'),
                         libre.id)
        self.assertNotIn('meeting_agenda_id', Event.with_context(
            default_meeting_agenda_id=lie.id).default_get(['meeting_agenda_id']))

    def test_le_lieu_part_avec_la_creation_rapide(self):
        """Sans le champ dans la vue, le lieu posé par l'onchange ne repartait
        pas à l'enregistrement direct."""
        view = self.env.ref('calendar.view_calendar_event_form_quick_create')
        arch = self.env['calendar.event'].with_user(self.organiser).get_view(view.id)['arch']
        self.assertIn('name="location"', arch)

    # --- Reporter -----------------------------------------------------------

    def test_reporter_detache_sans_rien_retirer(self):
        event = self._event()
        agenda = self._agenda(calendar_event_id=event.id, state='confirmed',
                              allow_contributions=True)
        self.env['meeting.agenda.topic'].create({'agenda_id': agenda.id,
                                                 'name': 'Sujet gardé'})
        task = self.env['project.task'].create({
            'name': 'Tâche épinglée', 'project_id': self.project.id,
            'bf_meeting_agenda_id': agenda.id})
        agenda.action_postpone()
        self.assertFalse(agenda.calendar_event_id)
        self.assertEqual(agenda.state, 'draft')
        self.assertTrue(agenda.bf_to_schedule)
        self.assertEqual(task.bf_meeting_agenda_id, agenda,
                         "Reporter, contrairement à Annuler, garde les tâches")
        self.assertEqual(agenda.topic_ids.mapped('name'), ['Sujet gardé'])
        self.assertTrue(event.exists() and event.active,
                        "la rencontre reste un geste du calendrier")
        self.assertIn('Rencontre reportée', agenda.message_ids[:1].body)

    def test_reporter_puis_lier_une_nouvelle_rencontre(self):
        agenda = self._agenda(calendar_event_id=self._event().id)
        agenda.action_postpone()
        nouvelle = self._event(name='Reprise', start='2026-10-20 18:00:00',
                               stop='2026-10-20 19:00:00')
        nouvelle.meeting_agenda_id = agenda
        self.assertEqual(agenda.calendar_event_id, nouvelle)
        self.assertEqual(agenda.date, nouvelle.start)
        self.assertFalse(agenda.bf_to_schedule)

    def test_un_odj_termine_ne_se_reporte_pas(self):
        agenda = self._agenda(state='done')
        with self.assertRaises(UserError):
            agenda.action_postpone()

    # --- le filtre « Besoin d'un OdJ », nié ---------------------------------

    def test_besoin_d_un_odj_nie_correctement(self):
        """🔴 `['!'] + domaine` ne niait que le premier terme (la dispense)."""
        futur = fields.Datetime.add(fields.Datetime.now(), days=10)
        avec_odj = self._event(start=futur, stop=fields.Datetime.add(futur, hours=1))
        self._agenda(calendar_event_id=avec_odj.id)
        sans_odj = self._event(start=futur, stop=fields.Datetime.add(futur, hours=1))
        Event = self.env['calendar.event']
        non = Event.search([('id', 'in', (avec_odj | sans_odj).ids),
                            ('bf_needs_agenda', '=', False)])
        oui = Event.search([('id', 'in', (avec_odj | sans_odj).ids),
                            ('bf_needs_agenda', '=', True)])
        self.assertEqual(oui, sans_odj)
        self.assertEqual(non, avec_odj)
