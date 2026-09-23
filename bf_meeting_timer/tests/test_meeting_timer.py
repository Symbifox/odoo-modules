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
            # Deux minutes sur le deuxième : au-delà du seuil de retour arrière,
            # sinon revenir au premier annulerait le « Sujet suivant ».
            horloge.move_to('2026-09-21 18:06:00')
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
                         "les 24 minutes de pause n'appartiennent à personne")
        self.assertEqual(deuxieme.timer_seconds, 2 * 60)
        self.assertEqual(agenda.timer_elapsed_seconds, 9 * 60)

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
            # Deux minutes : au-delà du seuil, c'est un vrai second passage.
            horloge.move_to('2026-09-21 18:07:00')
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
        corps = self._recaps(agenda).body
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
        corps = self._recaps(agenda).body
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
    # Le retour arrière
    # ------------------------------------------------------------------
    def test_revenir_aussitot_annule_le_sujet_suivant(self):
        """Le motif relevé en rencontre réelle : « Sujet suivant », une demi-minute,
        retour au sujet d'avant. Ce n'est pas un passage, c'est un geste annulé :
        la projection doit être exactement celle d'une rencontre sans l'aller-retour."""
        agenda = self._agenda()
        temoin = self._agenda(name="Témoin sans aller-retour")
        premier, deuxieme, _t = self._sujets(agenda)
        with freeze_time('2026-09-21 18:00:00') as horloge:
            agenda.action_timer_start()
            temoin.action_timer_start()
            horloge.move_to('2026-09-21 18:30:00')      # 30 min sur un sujet de 20
            agenda.action_timer_split()
            horloge.move_to('2026-09-21 18:30:29')
            agenda.action_timer_goto(premier.id)
            horloge.move_to('2026-09-21 18:31:00')
            etat = agenda.timer_payload()
            etat_temoin = temoin.timer_payload()

        self.assertEqual(deuxieme.timer_state, 'pending', "le deuxième reste à venir")
        self.assertEqual(deuxieme.timer_visits, 0)
        self.assertEqual(deuxieme.timer_seconds, 0)
        self.assertFalse(deuxieme.timer_first_at)
        self.assertEqual(premier.timer_state, 'current')
        self.assertEqual(premier.timer_visits, 1, "le même passage continue")
        self.assertEqual(premier.timer_seconds, 30 * 60 + 29,
                         "les 29 secondes vont au sujet dont on parlait encore")
        for cle in ('delta_seconds', 'remaining_planned_seconds', 'projected_end',
                    'elapsed_seconds'):
            self.assertEqual(etat[cle], etat_temoin[cle], cle)
        self.assertEqual(etat['delta_seconds'], 11 * 60, "le retard se voit")

    def test_revenir_apres_le_seuil_compte_un_passage(self):
        agenda = self._agenda()
        premier, deuxieme, _t = self._sujets(agenda)
        with freeze_time('2026-09-21 18:00:00') as horloge:
            agenda.action_timer_start()
            horloge.move_to('2026-09-21 18:10:00')
            agenda.action_timer_split()
            horloge.move_to('2026-09-21 18:11:30')      # 90 s pile : plus un geste
            agenda.action_timer_goto(premier.id)
        self.assertEqual(deuxieme.timer_state, 'done')
        self.assertEqual(deuxieme.timer_visits, 1)
        self.assertEqual(deuxieme.timer_seconds, 90)
        self.assertEqual(premier.timer_visits, 2)
        self.assertEqual(premier.timer_seconds, 10 * 60)

    def test_revenir_juste_sous_le_seuil_annule_encore(self):
        agenda = self._agenda()
        premier, deuxieme, _t = self._sujets(agenda)
        with freeze_time('2026-09-21 18:00:00') as horloge:
            agenda.action_timer_start()
            horloge.move_to('2026-09-21 18:10:00')
            agenda.action_timer_split()
            horloge.move_to('2026-09-21 18:11:29')
            agenda.action_timer_goto(premier.id)
        self.assertEqual(deuxieme.timer_state, 'pending')
        self.assertEqual(premier.timer_visits, 1)

    def test_ouvrir_un_autre_sujet_n_annule_rien(self):
        """Seul le sujet ouvert JUSTE AVANT se reprend par retour arrière."""
        agenda = self._agenda()
        premier, deuxieme, troisieme = self._sujets(agenda)
        with freeze_time('2026-09-21 18:00:00') as horloge:
            agenda.action_timer_start()
            horloge.move_to('2026-09-21 18:10:00')
            agenda.action_timer_split()                 # vers le deuxième
            horloge.move_to('2026-09-21 18:10:20')
            agenda.action_timer_goto(troisieme.id)      # on saute au troisième
        self.assertEqual(deuxieme.timer_state, 'done')
        self.assertEqual(deuxieme.timer_visits, 1)
        self.assertEqual(troisieme.timer_visits, 1)
        self.assertEqual(premier.timer_seconds, 10 * 60)

    def test_un_sujet_deja_repris_ne_s_annule_pas(self):
        """Un sujet à son deuxième passage a déjà été couvert une fois."""
        agenda = self._agenda()
        premier, deuxieme, _t = self._sujets(agenda)
        with freeze_time('2026-09-21 18:00:00') as horloge:
            agenda.action_timer_start()
            horloge.move_to('2026-09-21 18:00:30')
            agenda.action_timer_split()                 # 30 s sur le premier
            horloge.move_to('2026-09-21 18:10:00')
            agenda.action_timer_goto(premier.id)        # 9:30 sur le deuxième
            horloge.move_to('2026-09-21 18:10:10')      # 2e passage, 40 s en tout
            agenda.action_timer_goto(deuxieme.id)
        self.assertEqual(premier.timer_visits, 2)
        self.assertEqual(premier.timer_state, 'done')
        self.assertEqual(premier.timer_seconds, 40)
        self.assertEqual(deuxieme.timer_visits, 2)

    def test_le_retour_arriere_marche_en_pause(self):
        agenda = self._agenda()
        premier, deuxieme, _t = self._sujets(agenda)
        with freeze_time('2026-09-21 18:00:00') as horloge:
            agenda.action_timer_start()
            horloge.move_to('2026-09-21 18:10:00')
            agenda.action_timer_split()
            horloge.move_to('2026-09-21 18:10:20')
            agenda.action_timer_pause()
            horloge.move_to('2026-09-21 18:20:00')
            agenda.action_timer_goto(premier.id)
            self.assertFalse(agenda.timer_segment_since,
                             "aucune tranche ne s'ouvre pendant une pause")
            horloge.move_to('2026-09-21 18:25:00')
            agenda.action_timer_resume()
            horloge.move_to('2026-09-21 18:26:00')
            agenda.action_timer_pause()
        self.assertEqual(deuxieme.timer_state, 'pending')
        self.assertEqual(premier.timer_visits, 1)
        self.assertEqual(premier.timer_seconds, 10 * 60 + 20 + 60,
                         "ni la pause ni l'attente ne comptent")
        self.assertEqual(agenda.timer_elapsed_seconds, 10 * 60 + 20 + 60)

    def test_le_retour_arriere_defait_aussi_un_passer(self):
        agenda = self._agenda()
        premier, deuxieme, _t = self._sujets(agenda)
        with freeze_time('2026-09-21 18:00:00') as horloge:
            agenda.action_timer_start()
            horloge.move_to('2026-09-21 18:00:10')
            agenda.action_timer_skip()                  # le premier est sauté
            horloge.move_to('2026-09-21 18:00:20')
            agenda.action_timer_goto(premier.id)
        self.assertEqual(premier.timer_state, 'current',
                         "un sujet sauté par erreur se reprend")
        self.assertEqual(deuxieme.timer_state, 'pending')
        self.assertEqual(premier.timer_seconds, 20)

    def test_on_n_annule_qu_un_geste(self):
        """Le retour arrière ne se chaîne pas : il n'y a pas de pile de gestes."""
        agenda = self._agenda()
        premier, deuxieme, _t = self._sujets(agenda)
        with freeze_time('2026-09-21 18:00:00') as horloge:
            agenda.action_timer_start()
            horloge.move_to('2026-09-21 18:00:20')
            agenda.action_timer_split()
            horloge.move_to('2026-09-21 18:00:40')
            agenda.action_timer_goto(premier.id)        # annulé
            self.assertFalse(agenda.timer_previous_topic_id)
            horloge.move_to('2026-09-21 18:00:50')
            agenda.action_timer_split()                 # le deuxième, de nouveau
        self.assertEqual(deuxieme.timer_visits, 1)
        self.assertEqual(deuxieme.timer_state, 'current')
        self.assertEqual(agenda.timer_previous_topic_id, premier)

    def test_trois_allers_retours_rejoues(self):
        """Trois allers-retours relevés en rencontre réelle, au rythme mesuré.
        Avant la correction, l'écart affichait −0:04 alors que le sujet en
        cours avait seize minutes de retard."""
        agenda = self._agenda(duration_planned=29, topic_ids=[
            Command.create({'sequence': 10, 'name': 'Premier sujet', 'duration_planned': 12}),
            Command.create({'sequence': 20, 'name': 'Deuxième sujet', 'duration_planned': 12}),
            Command.create({'sequence': 30, 'name': 'Troisième sujet', 'duration_planned': 5}),
        ])
        un, deux, trois = self._sujets(agenda)
        with freeze_time('2026-09-22 18:02:14') as horloge:
            agenda.action_timer_start()
            horloge.move_to('2026-09-22 18:18:01')
            agenda.action_timer_split()
            horloge.move_to('2026-09-22 18:18:30')
            agenda.action_timer_goto(un.id)
            horloge.move_to('2026-09-22 18:28:57')
            agenda.action_timer_split()
            horloge.move_to('2026-09-22 18:29:25')
            agenda.action_timer_goto(un.id)
            horloge.move_to('2026-09-22 18:31:10')
            etat = agenda.timer_payload()
        self.assertEqual(deux.timer_state, 'pending')
        self.assertEqual(trois.timer_state, 'pending')
        self.assertEqual(un.timer_visits, 1)
        self.assertEqual(etat['delta_seconds'], 28 * 60 + 56 - 12 * 60)
        self.assertEqual(etat['remaining_planned_seconds'], 17 * 60)
        self.assertEqual(etat['projected_end'], '2026-09-22 18:48:10')
        self.assertEqual(etat['planned_end'], '2026-09-22 18:31:14')

    # ------------------------------------------------------------------
    # Terminer, c'est terminer la rencontre
    # ------------------------------------------------------------------
    def _recaps(self, agenda):
        return agenda.message_ids.filtered(lambda m: 'min courues' in (m.body or ''))

    def test_terminer_au_chronometre_termine_l_ordre_du_jour(self):
        for depart, pause in (('draft', False), ('confirmed', False), ('confirmed', True)):
            with self.subTest(depart=depart, pause=pause):
                agenda = self._agenda()
                if depart == 'confirmed':
                    agenda.action_confirm()
                with freeze_time('2026-09-21 18:00:00') as horloge:
                    agenda.action_timer_start()
                    horloge.move_to('2026-09-21 18:10:00')
                    if pause:
                        agenda.action_timer_pause()
                    etat = agenda.action_timer_stop()
                self.assertEqual(agenda.state, 'done')
                self.assertEqual(agenda.timer_state, 'done')
                self.assertEqual(etat['agenda_state'], 'done',
                                 "le panneau reçoit l'état à jour")
                self.assertEqual(len(self._recaps(agenda)), 1, "un seul récapitulatif")

    def test_terminer_en_tete_arrete_le_chronometre(self):
        for pause in (False, True):
            with self.subTest(pause=pause):
                agenda = self._agenda()
                agenda.action_confirm()
                premier = self._sujets(agenda)[0]
                with freeze_time('2026-09-21 18:00:00') as horloge:
                    agenda.action_timer_start()
                    horloge.move_to('2026-09-21 18:10:00')
                    if pause:
                        agenda.action_timer_pause()
                        horloge.move_to('2026-09-21 18:15:00')
                    agenda.action_done()
                self.assertEqual(agenda.state, 'done')
                self.assertEqual(agenda.timer_state, 'done')
                self.assertEqual(premier.timer_seconds, 10 * 60)
                self.assertEqual(len(self._recaps(agenda)), 1)

    def test_creer_le_compte_rendu_arrete_le_chronometre(self):
        """Sans ça, le compte rendu perdait son tableau de temps par sujet : il
        ne l'imprime que d'un chronomètre terminé."""
        agenda = self._agenda()
        agenda.action_confirm()
        with freeze_time('2026-09-21 18:00:00') as horloge:
            agenda.action_timer_start()
            horloge.move_to('2026-09-21 18:10:00')
            agenda.action_create_meeting_record()
        self.assertEqual(agenda.state, 'done')
        self.assertEqual(agenda.timer_state, 'done')
        self.assertTrue(agenda.meeting_record_id)

    def test_un_ordre_du_jour_qui_refuse_de_finir_n_empeche_pas_l_arret(self):
        agenda = self._agenda()
        agenda.action_confirm()
        chemin = 'odoo.addons.bf_meeting.models.meeting_agenda.MeetingAgenda.action_done'
        with freeze_time('2026-09-21 18:00:00') as horloge:
            agenda.action_timer_start()
            horloge.move_to('2026-09-21 18:10:00')
            with patch(chemin, side_effect=UserError("refus simulé")):
                etat = agenda.action_timer_stop()
        self.assertEqual(etat['state'], 'done', "le chronomètre est arrêté")
        self.assertEqual(agenda.timer_state, 'done')
        self.assertEqual(agenda.state, 'confirmed')

    def test_un_chronometre_qui_refuse_n_empeche_pas_de_terminer(self):
        agenda = self._agenda()
        agenda.action_confirm()
        chemin = 'odoo.addons.bf_meeting_timer.models.meeting_agenda.MeetingAgenda._timer_finish'
        with freeze_time('2026-09-21 18:00:00') as horloge:
            agenda.action_timer_start()
            horloge.move_to('2026-09-21 18:10:00')
            with patch(chemin, side_effect=UserError("refus simulé")):
                agenda.action_done()
        self.assertEqual(agenda.state, 'done', "la rencontre se termine quand même")

    def test_revenir_a_confirme_ne_relance_pas_le_chronometre(self):
        """On peut toujours ramener l'ordre du jour à une étape
        précédente. La barre d'étapes le permet ; la course, elle, est finie."""
        agenda = self._agenda()
        with freeze_time('2026-09-21 18:00:00') as horloge:
            agenda.action_start_meeting()
            horloge.move_to('2026-09-21 18:10:00')
            agenda.action_timer_stop()
            agenda.state = 'confirmed'
            horloge.move_to('2026-09-21 18:20:00')
            agenda.action_start_meeting()
        self.assertEqual(agenda.timer_state, 'done')
        self.assertEqual(agenda.timer_elapsed_seconds, 10 * 60)

    # ------------------------------------------------------------------
    # « + Varia » et les notes par sujet
    # ------------------------------------------------------------------
    def test_varia_cree_le_sujet_et_l_ouvre(self):
        agenda = self._agenda()
        premier = self._sujets(agenda)[0]
        with freeze_time('2026-09-21 18:00:00') as horloge:
            agenda.action_timer_start()
            horloge.move_to('2026-09-21 18:05:00')
            etat_avant = agenda.timer_payload()
            etat = agenda.action_timer_varia()
        varia = agenda.topic_ids.filtered(lambda t: t.name == 'Varia')
        self.assertEqual(len(varia), 1)
        self.assertEqual(varia.sequence, 40, "à la fin de l'ordre du jour")
        self.assertEqual(varia.duration_planned, 0)
        self.assertEqual(varia.moderation_state, 'accepted', "il entre dans la course")
        self.assertEqual(agenda.timer_current_topic_id, varia)
        self.assertEqual(agenda.timer_previous_topic_id, premier)
        self.assertEqual(premier.timer_seconds, 5 * 60)
        self.assertEqual(etat['current_topic_id'], varia.id)
        # Ouvrir Varia termine le sujet quitté, comme « Revenir » : l'alloué
        # qui lui restait (15 min) sort de la projection. Varia, à 0 minute
        # allouée, n'y ajoute rien.
        self.assertEqual(premier.timer_state, 'done')
        self.assertEqual(etat['remaining_planned_seconds'],
                         etat_avant['remaining_planned_seconds'] - 15 * 60)

    def test_varia_ne_se_cree_qu_une_fois(self):
        agenda = self._agenda()
        premier = self._sujets(agenda)[0]
        with freeze_time('2026-09-21 18:00:00') as horloge:
            agenda.action_timer_start()
            agenda.action_timer_varia()
            horloge.move_to('2026-09-21 18:05:00')
            agenda.action_timer_goto(premier.id)
            horloge.move_to('2026-09-21 18:10:00')
            agenda.action_timer_varia()
            agenda.action_timer_varia()         # déjà ouvert : rien ne bouge
        varia = agenda.topic_ids.filtered(lambda t: t.name == 'Varia')
        self.assertEqual(len(varia), 1)
        self.assertEqual(varia.timer_visits, 2)
        self.assertEqual(agenda.timer_current_topic_id, varia)

    def test_varia_se_reconnait_sans_la_casse(self):
        agenda = self._agenda(topic_ids=[
            Command.create({'sequence': 10, 'name': 'Premier', 'duration_planned': 10}),
            Command.create({'sequence': 20, 'name': ' VARIA ', 'duration_planned': 5}),
        ])
        agenda.action_timer_start()
        agenda.action_timer_varia()
        self.assertEqual(len(agenda.topic_ids), 2, "le Varia prévu est repris")
        self.assertEqual(agenda.timer_current_topic_id.name, ' VARIA ')

    def test_un_varia_en_moderation_n_est_pas_repris(self):
        agenda = self._agenda(topic_ids=[
            Command.create({'sequence': 10, 'name': 'Premier', 'duration_planned': 10}),
            Command.create({'sequence': 20, 'name': 'Varia', 'duration_planned': 5,
                            'source': 'contributed', 'moderation_state': 'pending'}),
        ])
        agenda.action_timer_start()
        agenda.action_timer_varia()
        ouvert = agenda.timer_current_topic_id
        self.assertEqual(ouvert.moderation_state, 'accepted')
        self.assertEqual(len(agenda.topic_ids), 3)

    def test_varia_refuse_hors_rencontre(self):
        """🔴 Pas d'`assertRaises` : il ouvre un point de reprise et annule ce
        qui s'est écrit avant l'erreur, donc un Varia créé puis refusé
        disparaîtrait et l'essai serait vert même sans la garde."""
        agenda = self._agenda()
        refus = None
        try:
            agenda.action_timer_varia()
        except UserError as erreur:
            refus = erreur
        self.assertTrue(refus, "le geste est refusé")
        self.assertFalse(agenda.topic_ids.filtered(lambda t: t.name == 'Varia'),
                         "et rien n'a été créé avant le refus")

    def test_varia_en_pause_ne_repart_pas(self):
        agenda = self._agenda()
        with freeze_time('2026-09-21 18:00:00') as horloge:
            agenda.action_timer_start()
            horloge.move_to('2026-09-21 18:05:00')
            agenda.action_timer_pause()
            agenda.action_timer_varia()
        self.assertEqual(agenda.timer_state, 'paused')
        self.assertFalse(agenda.timer_segment_since)

    def test_un_detour_par_varia_s_annule(self):
        """Varia s'ouvre comme « Revenir » : un retour aussitôt annule le détour."""
        agenda = self._agenda()
        premier = self._sujets(agenda)[0]
        with freeze_time('2026-09-21 18:00:00') as horloge:
            agenda.action_timer_start()
            horloge.move_to('2026-09-21 18:05:00')
            agenda.action_timer_varia()
            horloge.move_to('2026-09-21 18:05:20')
            agenda.action_timer_goto(premier.id)
        varia = agenda.topic_ids.filtered(lambda t: t.name == 'Varia')
        self.assertEqual(varia.timer_state, 'pending')
        self.assertEqual(premier.timer_seconds, 5 * 60 + 20)

    def test_varia_au_fil_continu_pose_un_titre(self):
        """Au fil continu, le saut vers les notes cherche un titre : Varia en a un."""
        agenda = self._agenda()
        agenda.company_id.meeting_notes_layout = 'flow'
        agenda.action_start_meeting()
        agenda.action_timer_varia()
        self.assertIn('<h3>Varia</h3>', str(agenda.live_notes_html))

    def test_varia_par_sujet_ne_touche_pas_aux_notes_generales(self):
        agenda = self._agenda()
        agenda.company_id.meeting_notes_layout = 'split'
        agenda.action_start_meeting()
        avant = str(agenda.live_notes_html)
        agenda.action_timer_varia()
        self.assertEqual(str(agenda.live_notes_html), avant)

    def test_par_sujet_les_notes_generales_restent_vides(self):
        """Sinon le résumé du compte rendu reprend la liste des sujets."""
        agenda = self._agenda()
        agenda.company_id.meeting_notes_layout = 'split'
        agenda.action_start_meeting()
        self.assertFalse(agenda.live_notes_html)

    def test_au_fil_continu_les_notes_generales_sont_preremplies(self):
        agenda = self._agenda()
        agenda.company_id.meeting_notes_layout = 'flow'
        agenda.action_start_meeting()
        self.assertIn('<h3>Premier</h3>', str(agenda.live_notes_html))

    def test_des_notes_generales_deja_tapees_ne_sont_pas_effacees(self):
        agenda = self._agenda(live_notes_html='<p>Tapé avant</p>')
        agenda.company_id.meeting_notes_layout = 'split'
        agenda.action_start_meeting()
        self.assertIn('Tapé avant', str(agenda.live_notes_html))

    def test_la_mise_en_page_par_defaut_est_par_sujet(self):
        """Une colonne de sélection ajoutée reste NULLE sur les sociétés existantes."""
        agenda = self._agenda()
        agenda.company_id.meeting_notes_layout = False
        self.assertEqual(agenda.timer_payload()['notes_layout'], 'split')
        agenda.company_id.meeting_notes_layout = 'flow'
        self.assertEqual(agenda.timer_payload()['notes_layout'], 'flow')
        self.assertEqual(agenda.timer_notes_layout, 'flow')

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

    def test_la_fiche_charge_le_detail_a_cote_des_notes(self):
        """Le volet de droite lit le détail sur la fiche, pas par une lecture à part.

        Il ne peut lire que ce que la fiche a chargé : le détail de chaque
        sujet doit être dans la liste (cachée) de l'onglet des notes, et les
        objectifs, le contexte et la préparation sur la fiche elle-même. Qu'une
        de ces cases manque, et le volet affiche « rien de plus » sur un sujet
        qui a un détail, sans erreur nulle part.
        """
        from lxml import etree

        vue = self.env['meeting.agenda'].get_views([(False, 'form')])['views']['form']
        arch = etree.fromstring(vue['arch'])
        page = arch.xpath("//page[@name='live_notes']")
        self.assertEqual(len(page), 1)
        self.assertTrue(page[0].xpath(".//widget[@name='bf_meeting_timer_notes']"))
        liste = page[0].xpath("./field[@name='topic_ids']/list")
        self.assertEqual(len(liste), 1)
        self.assertTrue(liste[0].xpath("./field[@name='description']"),
                        "le détail du sujet doit être chargé avec sa ligne de notes")
        self.assertTrue(liste[0].xpath("./field[@name='live_notes_html']"))
        for champ in ('objectives', 'context_html', 'preparation_html'):
            self.assertTrue(arch.xpath(f"//field[@name='{champ}']"),
                            f"{champ} doit être sur la fiche pour les notes générales")
