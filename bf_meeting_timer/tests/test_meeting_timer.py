"""Le chronomètre de rencontre.

Ces essais gardent les promesses qui ne se lisent pas dans le code :

* **un segment de rencontre est une SOMME d'intervalles** : un sujet repris plus
  tard cumule ses passages, ce qu'aucun chronomètre de course ne sait faire ;
* **le temps couru va au sujet qui était ouvert**, jamais au suivant, et une
  pause n'appartient à personne ;
* **cloner un ordre du jour depuis la série ne rapporte pas le temps d'une autre
  rencontre** (tous les champs sont `copy=False`) ;
* **une méthode publique est appelable par XML-RPC** : chacune vérifie le droit
  d'écriture sur l'enregistrement, pas seulement l'appartenance à un groupe ;
* **un nom de sujet est du texte saisi** : le récapitulatif au chatter l'échappe.

Le temps est piloté par `freeze_time` : un essai qui dort vraiment est un essai
qui rougit un jour de machine chargée.
"""

from unittest.mock import patch

from freezegun import freeze_time

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestMeetingTimer(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.project = cls.env['project.project'].create({'name': 'Projet Chrono'})
        cls.gestionnaire = cls.env['res.users'].create({
            'name': 'Gestionnaire Chrono',
            'login': 'chrono_gestionnaire',
            'email': 'gestionnaire@essai.test',
            'groups_id': [Command.link(cls.env.ref('bf_meeting.group_meeting_manager').id),
                          Command.link(cls.env.ref('base.group_user').id)],
        })
        cls.passant = cls.env['res.users'].create({
            'name': 'Passant Chrono',
            'login': 'chrono_passant',
            'email': 'passant@essai.test',
            'groups_id': [Command.link(cls.env.ref('base.group_user').id)],
        })

    def _agenda(self, **overrides):
        vals = {
            'name': "Ordre du jour d'essai",
            'project_id': self.project.id,
            'date': '2026-09-21 18:00:00',
            'duration_planned': 60,
            'topic_ids': [
                Command.create({'sequence': 10, 'name': 'Premier', 'duration_planned': 20}),
                Command.create({'sequence': 20, 'name': 'Deuxième', 'duration_planned': 25}),
                Command.create({'sequence': 30, 'name': 'Troisième', 'duration_planned': 15}),
            ],
        }
        vals.update(overrides)
        return self.env['meeting.agenda'].with_context(
            skip_auto_refine=True).create(vals)

    def _sujets(self, agenda):
        return agenda.topic_ids.sorted(lambda t: (t.sequence, t.id))

    # ------------------------------------------------------------------
    # La mécanique
    # ------------------------------------------------------------------
    def test_depart_ouvre_le_premier_sujet(self):
        agenda = self._agenda()          # rencontre prévue à 18 h 00
        with freeze_time('2026-09-21 18:07:42'):
            agenda.action_timer_start()  # tout le monde est arrivé en retard
        premier = self._sujets(agenda)[0]
        self.assertEqual(agenda.timer_state, 'running')
        self.assertEqual(agenda.timer_current_topic_id, premier)
        self.assertEqual(premier.timer_state, 'current')
        self.assertEqual(premier.timer_visits, 1)
        # L'origine du temps est le geste, pas la date de la rencontre : c'est
        # la seule façon d'avoir une heure de fin projetée qui veuille dire
        # quelque chose.
        self.assertEqual(str(agenda.timer_started_at), '2026-09-21 18:07:42')
        self.assertNotEqual(agenda.timer_started_at, agenda.date)
        self.assertEqual(str(premier.timer_first_at), '2026-09-21 18:07:42')

    def test_le_temps_va_au_sujet_ouvert(self):
        agenda = self._agenda()
        premier, deuxieme, _t = self._sujets(agenda)
        with freeze_time('2026-09-21 18:00:00') as horloge:
            agenda.action_timer_start()
            horloge.move_to('2026-09-21 18:07:30')
            agenda.action_timer_split()
        self.assertEqual(premier.timer_seconds, 450)
        self.assertEqual(premier.timer_state, 'done')
        self.assertEqual(deuxieme.timer_seconds, 0, "le suivant part de zéro")
        self.assertEqual(deuxieme.timer_state, 'current')
        self.assertEqual(agenda.timer_elapsed_seconds, 450)

    def test_dernier_split_termine_la_course(self):
        agenda = self._agenda()
        with freeze_time('2026-09-21 18:00:00') as horloge:
            agenda.action_timer_start()
            for minute in (5, 10, 15):
                horloge.move_to('2026-09-21 18:%02d:00' % minute)
                agenda.action_timer_split()
        self.assertEqual(agenda.timer_state, 'done')
        self.assertFalse(agenda.timer_current_topic_id)
        self.assertEqual(str(agenda.timer_ended_at), '2026-09-21 18:15:00')
        self.assertTrue(all(t.timer_state == 'done' for t in agenda.topic_ids))

    def test_un_sujet_repris_cumule_ses_passages(self):
        """La différence de fond avec un chronomètre de course."""
        agenda = self._agenda()
        premier, deuxieme, _t = self._sujets(agenda)
        with freeze_time('2026-09-21 18:00:00') as horloge:
            agenda.action_timer_start()
            horloge.move_to('2026-09-21 18:04:00')
            agenda.action_timer_split()            # 4 min sur le premier
            horloge.move_to('2026-09-21 18:06:00')
            agenda.action_timer_goto(premier.id)   # on y revient
            horloge.move_to('2026-09-21 18:09:00')
            agenda.action_timer_split()
        self.assertEqual(premier.timer_seconds, 4 * 60 + 3 * 60)
        self.assertEqual(premier.timer_visits, 2)
        self.assertEqual(deuxieme.timer_seconds, 2 * 60,
                         "les deux minutes du deuxième lui restent")

    def test_passer_un_sujet_le_marque_sans_effacer_le_temps(self):
        agenda = self._agenda()
        premier, deuxieme, _t = self._sujets(agenda)
        with freeze_time('2026-09-21 18:00:00') as horloge:
            agenda.action_timer_start()
            horloge.move_to('2026-09-21 18:00:20')
            agenda.action_timer_skip()
        self.assertEqual(premier.timer_state, 'skipped')
        self.assertEqual(premier.timer_seconds, 20,
                         "le temps couru est réel, sauter ne l'efface pas")
        self.assertEqual(agenda.timer_current_topic_id, deuxieme)

    def test_la_pause_n_appartient_a_personne(self):
        agenda = self._agenda()
        premier = self._sujets(agenda)[0]
        with freeze_time('2026-09-21 18:00:00') as horloge:
            agenda.action_timer_start()
            horloge.move_to('2026-09-21 18:03:00')
            agenda.action_timer_pause()
            horloge.move_to('2026-09-21 18:20:00')      # 17 minutes de pause
            agenda.action_timer_resume()
            horloge.move_to('2026-09-21 18:22:00')
            agenda.action_timer_stop()
        self.assertEqual(premier.timer_seconds, 5 * 60)
        self.assertEqual(agenda.timer_elapsed_seconds, 5 * 60)
        self.assertEqual(agenda.timer_state, 'done')

    def test_sujet_ajoute_pendant_la_rencontre(self):
        """Le sujet imprévu est justement celui qu'on veut voir."""
        agenda = self._agenda()
        with freeze_time('2026-09-21 18:00:00') as horloge:
            agenda.action_timer_start()
            horloge.move_to('2026-09-21 18:05:00')
            imprevu = self.env['meeting.agenda.topic'].create({
                'agenda_id': agenda.id, 'sequence': 15, 'name': 'Imprévu',
                'duration_planned': 0,
            })
            agenda.action_timer_split()
        self.assertEqual(agenda.timer_current_topic_id, imprevu,
                         "un sujet né en cours de route prend sa place dans l'ordre")

    def test_sujet_en_moderation_hors_course(self):
        agenda = self._agenda()
        propose = self.env['meeting.agenda.topic'].create({
            'agenda_id': agenda.id, 'sequence': 5, 'name': 'Proposé',
            'duration_planned': 10, 'source': 'contributed',
            'moderation_state': 'pending',
        })
        with freeze_time('2026-09-21 18:00:00'):
            agenda.action_timer_start()
        self.assertNotEqual(agenda.timer_current_topic_id, propose)
        self.assertEqual(agenda.timer_current_topic_id.name, 'Premier')
        with self.assertRaises(UserError):
            agenda.action_timer_goto(propose.id)

    def test_revenir_sur_un_sujet_marche_aussi_en_pause(self):
        """On met la rencontre en pause, puis on décide de reprendre un sujet.

        Rien ne court pendant la pause : le sujet rouvert compte un passage de
        plus, mais pas une seconde de plus tant qu'on n'a pas repris.
        """
        agenda = self._agenda()
        premier, deuxieme, _t = self._sujets(agenda)
        with freeze_time('2026-09-21 18:00:00') as horloge:
            agenda.action_timer_start()
            horloge.move_to('2026-09-21 18:04:00')
            agenda.action_timer_split()          # 4 min sur le premier
            agenda.action_timer_pause()
            horloge.move_to('2026-09-21 18:30:00')
            agenda.action_timer_goto(premier.id)  # retour pendant la pause
            # 🔴 L'invariant : `timer_segment_since` veut dire « une tranche
            # court ». Pendant une pause, il doit rester vide, sinon un lecteur
            # futur croira qu'il tourne. Aucun consommateur d'aujourd'hui ne s'y
            # fie sans vérifier l'état, donc SEUL cet essai tient la promesse.
            self.assertFalse(agenda.timer_segment_since,
                             "aucune tranche ne s'ouvre pendant une pause")
            self.assertEqual(agenda.timer_current_topic_id, premier)
            horloge.move_to('2026-09-21 18:32:00')
            agenda.action_timer_resume()
            horloge.move_to('2026-09-21 18:35:00')
            agenda.action_timer_stop()
        self.assertEqual(premier.timer_visits, 2)
        self.assertEqual(premier.timer_seconds, 4 * 60 + 3 * 60,
                         "les 28 minutes de pause n'appartiennent à personne")
        self.assertEqual(deuxieme.timer_seconds, 0)
        self.assertEqual(agenda.timer_elapsed_seconds, 7 * 60)

    def test_un_sujet_d_un_autre_ordre_du_jour_est_refuse(self):
        agenda = self._agenda()
        autre = self._agenda(name='Autre OdJ')
        with freeze_time('2026-09-21 18:00:00'):
            agenda.action_timer_start()
            with self.assertRaises(UserError):
                agenda.action_timer_goto(self._sujets(autre)[0].id)

    def test_effacer_remet_tout_a_zero(self):
        agenda = self._agenda()
        with freeze_time('2026-09-21 18:00:00') as horloge:
            agenda.action_timer_start()
            horloge.move_to('2026-09-21 18:09:00')
            agenda.action_timer_stop()
            agenda.action_timer_reset()
        self.assertEqual(agenda.timer_state, 'idle')
        self.assertEqual(agenda.timer_elapsed_seconds, 0)
        self.assertFalse(agenda.timer_started_at)
        self.assertTrue(all(t.timer_seconds == 0 and t.timer_visits == 0
                            and t.timer_state == 'pending' for t in agenda.topic_ids))

    # ------------------------------------------------------------------
    # Les gardes d'état
    # ------------------------------------------------------------------
    def test_les_gestes_refusent_hors_etat(self):
        agenda = self._agenda()
        with self.assertRaises(UserError):
            agenda.action_timer_split()
        with self.assertRaises(UserError):
            agenda.action_timer_pause()
        with self.assertRaises(UserError):
            agenda.action_timer_resume()
        with self.assertRaises(UserError):
            agenda.action_timer_stop()
        with freeze_time('2026-09-21 18:00:00'):
            agenda.action_timer_start()
            with self.assertRaises(UserError):
                agenda.action_timer_start()
            agenda.action_timer_pause()
            with self.assertRaises(UserError):
                agenda.action_timer_split()

    def test_une_rencontre_annulee_ne_part_pas(self):
        agenda = self._agenda()
        agenda.state = 'cancelled'
        with self.assertRaises(UserError):
            agenda.action_timer_start()

    # ------------------------------------------------------------------
    # Ce que le panneau lit
    # ------------------------------------------------------------------
    def test_ecart_et_fin_projetee(self):
        agenda = self._agenda()
        with freeze_time('2026-09-21 18:00:00') as horloge:
            agenda.action_timer_start()
            horloge.move_to('2026-09-21 18:30:00')     # 30 min sur un sujet de 20
            etat = agenda.timer_payload()
        self.assertEqual(etat['elapsed_seconds'], 30 * 60)
        self.assertEqual(etat['delta_seconds'], 10 * 60,
                         "dix minutes de retard sur le seul sujet couvert")
        # Fin projetée : maintenant + rien pour le sujet ouvert (déjà dépassé)
        # + 25 et 15 minutes allouées aux deux qui restent.
        self.assertEqual(etat['projected_end'], '2026-09-21 19:10:00')
        self.assertEqual(etat['planned_end'], '2026-09-21 19:00:00')
        self.assertEqual(etat['remaining_planned_seconds'], 40 * 60)
        self.assertEqual(etat['server_now'], '2026-09-21 18:30:00')

    def test_la_fin_projetee_compte_ce_qu_il_reste_au_sujet_ouvert(self):
        """Le sujet ouvert n'a pas encore mangé son alloué : il compte quand même."""
        agenda = self._agenda()
        with freeze_time('2026-09-21 18:00:00') as horloge:
            agenda.action_timer_start()
            horloge.move_to('2026-09-21 18:05:00')     # 5 min sur un sujet de 20
            etat = agenda.timer_payload()
        self.assertEqual(etat['delta_seconds'], -15 * 60, "quinze minutes d'avance")
        # 15 min restent au sujet ouvert, plus 25 et 15 aux deux suivants.
        self.assertEqual(etat['remaining_planned_seconds'], 55 * 60)
        self.assertEqual(etat['projected_end'], '2026-09-21 19:00:00')

    def test_le_reste_alloue_ne_devient_pas_negatif(self):
        agenda = self._agenda()
        with freeze_time('2026-09-21 18:00:00') as horloge:
            agenda.action_timer_start()
            horloge.move_to('2026-09-21 19:30:00')
            etat = agenda.timer_payload()
        self.assertEqual(etat['remaining_planned_seconds'], 40 * 60,
                         "un sujet déjà dépassé ne rend pas du temps aux autres")

    def test_le_payload_suit_les_passages(self):
        agenda = self._agenda()
        premier = self._sujets(agenda)[0]
        with freeze_time('2026-09-21 18:00:00') as horloge:
            agenda.action_timer_start()
            horloge.move_to('2026-09-21 18:05:00')
            agenda.action_timer_split()
            horloge.move_to('2026-09-21 18:06:00')
            etat = agenda.action_timer_goto(premier.id)
        ligne = next(l for l in etat['topics'] if l['id'] == premier.id)
        self.assertEqual(ligne['visits'], 2)
        self.assertTrue(ligne['is_current'])
        self.assertEqual(ligne['state'], 'current')

    # ------------------------------------------------------------------
    # Ce qui reste derrière
    # ------------------------------------------------------------------
    def test_le_recap_echappe_le_nom_du_sujet(self):
        agenda = self._agenda(topic_ids=[
            Command.create({'sequence': 10, 'name': '<b>Gras</b> & co',
                            'duration_planned': 10}),
        ])
        with freeze_time('2026-09-21 18:00:00') as horloge:
            agenda.action_timer_start()
            horloge.move_to('2026-09-21 18:12:00')
            agenda.action_timer_stop()
        corps = agenda.message_ids[0].body
        self.assertIn('&lt;b&gt;Gras&lt;/b&gt;', corps)
        self.assertNotIn('<b>Gras</b>', corps)

    def test_la_trace_qui_echoue_n_emporte_pas_la_mesure(self):
        """Vu en jouant le parcours au navigateur : `message_post` lève pour un
        usager sans adresse courriel, et « Terminer » refusait de terminer. La
        rencontre restait en cours, et le seul geste restant effaçait tout."""
        agenda = self._agenda()
        sans_courriel = self.env['res.users'].create({
            'name': 'Sans Courriel',
            'login': 'chrono_sans_courriel',
            'groups_id': [Command.link(self.env.ref('bf_meeting.group_meeting_manager').id),
                          Command.link(self.env.ref('base.group_user').id)],
        })
        sans_courriel.partner_id.email = False
        premier = self._sujets(agenda)[0]

        with freeze_time('2026-09-21 18:00:00') as horloge:
            agenda.with_user(sans_courriel).action_timer_start()
            horloge.move_to('2026-09-21 18:08:00')
            etat = agenda.with_user(sans_courriel).action_timer_stop()

        self.assertEqual(etat['state'], 'done', "la course doit pouvoir se terminer")
        agenda.invalidate_recordset()
        self.assertEqual(agenda.timer_state, 'done')
        self.assertEqual(premier.timer_seconds, 8 * 60, "la mesure survit à la trace")

    def test_le_recap_dit_ce_qu_on_n_a_pas_abordé(self):
        """Un ordre du jour de six sujets dont on n'en couvre que deux ne doit
        pas se lire comme un ordre du jour tenu au complet."""
        agenda = self._agenda()
        with freeze_time('2026-09-21 18:00:00') as horloge:
            agenda.action_timer_start()
            horloge.move_to('2026-09-21 18:22:00')
            agenda.action_timer_stop()          # un seul sujet abordé sur trois
        lignes = agenda._timer_recap_lines()
        self.assertEqual(len(lignes), 3, "les trois sujets sont au récapitulatif")
        self.assertTrue(lignes[0]['covered'])
        self.assertFalse(lignes[1]['covered'])
        self.assertFalse(lignes[2]['covered'])
        corps = agenda.message_ids[0].body
        self.assertIn('non abordé', corps)

    def test_le_clone_ne_rapporte_pas_le_temps(self):
        """⚠️ `copy()` sur l'ordre du jour ne rapporte AUCUN sujet : l'essai qui
        s'y fiait passait sur une liste vide. Le chemin qui
        compte est la duplication d'un SUJET, que la liste de l'onglet Sujets
        offre en un clic."""
        agenda = self._agenda()
        premier = self._sujets(agenda)[0]
        with freeze_time('2026-09-21 18:00:00') as horloge:
            agenda.action_timer_start()
            horloge.move_to('2026-09-21 18:11:00')
            agenda.action_timer_stop()
        self.assertEqual(premier.timer_seconds, 11 * 60)

        jumeau = premier.copy()
        self.assertEqual(jumeau.timer_seconds, 0,
                         "un sujet dupliqué ne rapporte pas le temps de l'original")
        self.assertEqual(jumeau.timer_visits, 0)
        self.assertEqual(jumeau.timer_state, 'pending')
        self.assertFalse(jumeau.timer_first_at)

        clone = agenda.copy()
        self.assertEqual(clone.timer_state, 'idle')
        self.assertEqual(clone.timer_elapsed_seconds, 0)
        self.assertFalse(clone.timer_started_at)

    def test_les_minutes_calculees(self):
        agenda = self._agenda()
        premier = self._sujets(agenda)[0]
        with freeze_time('2026-09-21 18:00:00') as horloge:
            agenda.action_timer_start()
            horloge.move_to('2026-09-21 18:27:00')
            agenda.action_timer_split()
        self.assertEqual(premier.timer_minutes, 27.0)
        self.assertEqual(premier.timer_delta_minutes, 7.0)

    # ------------------------------------------------------------------
    # La place du panneau, et le départ couplé à la rencontre
    # ------------------------------------------------------------------
    def test_la_place_par_defaut_tient_sur_une_colonne_nulle(self):
        """Une société qui existait avant le module a la colonne à NULL : la vue
        doit quand même poser le panneau au-dessus des onglets."""
        agenda = self._agenda()
        agenda.company_id.meeting_timer_placement = False
        agenda.invalidate_recordset()
        self.assertFalse(agenda.timer_panel_placement)
        # La vue lit « ce n'est pas 'tab' », donc nul vaut au-dessus des onglets.
        self.assertNotEqual(agenda.timer_panel_placement, 'tab')

    def test_la_place_suit_la_societe(self):
        agenda = self._agenda()
        agenda.company_id.meeting_timer_placement = 'tab'
        agenda.invalidate_recordset()
        self.assertEqual(agenda.timer_panel_placement, 'tab')
        agenda.company_id.meeting_timer_placement = 'above'
        agenda.invalidate_recordset()
        self.assertEqual(agenda.timer_panel_placement, 'above')

    def test_demarrer_la_rencontre_lance_le_chronometre(self):
        agenda = self._agenda()
        with freeze_time('2026-09-21 18:03:00'):
            agenda.action_start_meeting()
        self.assertEqual(agenda.timer_state, 'running')
        self.assertEqual(agenda.timer_current_topic_id, self._sujets(agenda)[0])
        self.assertEqual(str(agenda.timer_started_at), '2026-09-21 18:03:00')

    def test_demarrer_deux_fois_ne_remet_pas_le_chronometre_a_zero(self):
        agenda = self._agenda()
        with freeze_time('2026-09-21 18:00:00') as horloge:
            agenda.action_start_meeting()
            horloge.move_to('2026-09-21 18:06:00')
            agenda.action_start_meeting()      # deuxième clic, en pleine rencontre
            etat = agenda.timer_payload()
        self.assertEqual(str(agenda.timer_started_at), '2026-09-21 18:00:00')
        self.assertEqual(etat['elapsed_seconds'], 6 * 60)

    def test_un_second_clic_ne_remplit_pas_le_journal(self):
        """La garde d'état n'est pas redondante avec celle de `action_timer_start`.

        Sans elle, le second clic lève « le chronomètre est déjà parti », le filet
        l'avale et écrit un avertissement : rien ne casse, mais chaque rencontre
        salit le journal d'une alerte qui ne veut rien dire. Le seul essai qui
        voit la différence est celui qui exige le SILENCE.
        """
        agenda = self._agenda()
        with freeze_time('2026-09-21 18:00:00') as horloge:
            agenda.action_start_meeting()
            horloge.move_to('2026-09-21 18:06:00')
            with self.assertNoLogs('odoo.addons.bf_meeting_timer.models.meeting_agenda',
                                   level='WARNING'):
                agenda.action_start_meeting()
        self.assertEqual(agenda.timer_state, 'running')

    def test_un_chronometre_qui_refuse_ne_bloque_pas_la_rencontre(self):
        """Le chronomètre est un ajout à ce bouton, pas sa raison d'être."""
        agenda = self._agenda()
        chemin = 'odoo.addons.bf_meeting_timer.models.meeting_agenda.MeetingAgenda.action_timer_start'
        with patch(chemin, side_effect=UserError("refus simulé")):
            agenda.action_start_meeting()
        self.assertEqual(agenda.state, 'confirmed', "la rencontre démarre quand même")
        self.assertEqual(agenda.timer_state, 'idle')

    # ------------------------------------------------------------------
    # Les droits
    # ------------------------------------------------------------------
    def test_une_methode_publique_verifie_le_droit_d_ecriture(self):
        """Sans `check_access`, le panneau serait un point d'entrée XML-RPC ouvert."""
        agenda = self._agenda()
        with freeze_time('2026-09-21 18:00:00'):
            with self.assertRaises(AccessError):
                agenda.with_user(self.passant).action_timer_start()

    def test_un_refus_sur_les_sujets_ne_laisse_pas_un_chrono_a_moitie_parti(self):
        """Le droit s'évalue AVANT la première écriture, sinon l'ordre du jour
        part et le sujet reste derrière.

        🔴 `assertRaises` d'Odoo ouvre un POINT DE REPRISE et le ramène quand
        l'exception attendue arrive (`BaseCase._assertRaises`). Il efface donc
        exactement ce que cet essai veut voir : l'écriture à moitié faite. Le
        refus se rattrape ici à la main, sans point de reprise.
        """
        agenda = self._agenda()
        self.env['ir.model.access'].search([
            ('model_id.model', '=', 'meeting.agenda.topic'),
        ]).write({'perm_write': False})

        leve = False
        with freeze_time('2026-09-21 18:00:00'):
            try:
                agenda.with_user(self.gestionnaire).action_timer_start()
            except AccessError:
                leve = True
        self.assertTrue(leve, "le refus doit venir, d'où qu'il vienne")

        agenda.invalidate_recordset()
        self.assertEqual(agenda.timer_state, 'idle',
                         "rien ne doit avoir été écrit sur l'ordre du jour")
        self.assertFalse(agenda.timer_started_at)
        self.assertFalse(agenda.topic_ids.filtered(lambda t: t.timer_visits))

    def test_le_gestionnaire_peut_chronometrer(self):
        agenda = self._agenda()
        with freeze_time('2026-09-21 18:00:00') as horloge:
            agenda.with_user(self.gestionnaire).action_timer_start()
            horloge.move_to('2026-09-21 18:03:00')
            etat = agenda.with_user(self.gestionnaire).action_timer_split()
        self.assertEqual(etat['state'], 'running')
