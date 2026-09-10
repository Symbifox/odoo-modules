"""Ce qui a changé dans l'ordre du jour depuis son dernier envoi.

Ces tests gardent quatre promesses qui ne se lisent pas dans le code :

* **le repère se pose au DÉPART, pas à l'ouverture du composeur** : le module
  distingue déjà les deux (`sent_date` contre `email_sent_date`) et le repère
  doit suivre le second ;
* **ce que le destinataire n'a jamais vu ne peut pas produire d'écart** : les
  notes prises en direct, en particulier, qu'un seul clic sur « Démarrer la
  rencontre » écrit sur TOUS les sujets ;
* **la liste des éléments d'action se vide toute seule** quand la date passe,
  et un écart naïf annoncerait alors leur retrait en bloc ;
* **le repère ne recopie aucun corps de texte** : il répond « ça a changé »,
  jamais « ça disait ceci ».
"""

import json
from unittest.mock import patch

from odoo import Command, fields
from odoo.tests import TransactionCase, tagged

from ..models.agenda_diff import SNAPSHOT_VERSION, build_snapshot, diff_snapshots


@tagged('post_install', '-at_install')
class TestAgendaChangesSinceSent(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.alice = cls.env['res.partner'].create({
            'name': 'Alice Exemple', 'email': 'alice@essai.test'})
        cls.bruno = cls.env['res.partner'].create({
            'name': 'Bruno Exemple', 'email': 'bruno@essai.test'})
        cls.project = cls.env['project.project'].create({'name': 'Projet Repère'})

    def _agenda(self, **overrides):
        vals = {
            'name': 'OdJ du banc',
            'project_id': self.project.id,
            'duration_planned': 60,
            'location': 'Visioconférence',
            'participant_ids': [Command.set([self.alice.id, self.bruno.id])],
            'objectives': "Trancher la question du repère.",
            'context_html': '<p>État des lieux.</p>',
            'topic_ids': [
                Command.create({'sequence': 10, 'name': 'Premier sujet',
                                'duration_planned': 20,
                                'description': '<p>Contexte du premier.</p>'}),
                Command.create({'sequence': 20, 'name': 'Deuxième sujet',
                                'duration_planned': 40,
                                'description': '<p>Contexte du deuxième.</p>'}),
            ],
        }
        vals.update(overrides)
        vals.setdefault('date', self._in_days(3))
        return self.env['meeting.agenda'].with_context(
            skip_auto_refine=True).create(vals)

    @staticmethod
    def _in_days(days):
        from datetime import timedelta
        return fields.Datetime.to_string(fields.Datetime.now() + timedelta(days=days))

    @staticmethod
    def _sent(agenda):
        """Un départ réel, sans passer par un serveur de courriel."""
        agenda.action_mark_sent_manually()
        return agenda

    # -- le repère se pose au bon moment --------------------------------

    def test_composeur_ouvert_puis_referme_ne_pose_aucun_repere(self):
        agenda = self._agenda()
        agenda.action_send_agenda_wizard()
        self.assertEqual(agenda.send_state, 'prepared')
        self.assertFalse(agenda.sent_snapshot_json)
        self.assertEqual(agenda.changes_since_sent_count, 0)
        self.assertFalse(agenda.has_changes_since_sent)

    def test_le_repere_se_pose_au_depart_reel(self):
        agenda = self._sent(self._agenda())
        self.assertTrue(agenda.sent_snapshot_json)
        self.assertTrue(agenda.sent_snapshot_date)
        self.assertEqual(agenda.changes_since_sent_count, 0,
                         "un ordre du jour qui vient de partir n'a rien changé")
        self.assertFalse(agenda.sent_baseline_missing)

    def test_le_composeur_qui_envoie_vraiment_pose_le_repere(self):
        agenda = self._agenda()
        agenda.message_post(
            body='<p>Voici l\'ordre du jour.</p>',
            message_type='comment',
            partner_ids=[self.alice.id],
        )
        self.assertTrue(agenda.email_sent_date)
        self.assertTrue(agenda.sent_snapshot_json)

    def test_une_note_interne_ne_pose_aucun_repere(self):
        agenda = self._agenda()
        agenda.message_post(body='<p>Note pour moi.</p>',
                            message_type='comment',
                            subtype_xmlid='mail.mt_note')
        self.assertFalse(agenda.sent_snapshot_json)

    def test_envoi_anterieur_au_suivi_se_declare_sans_repere(self):
        """Un OdJ parti avant cette fonction n'a pas de référence : le dire."""
        agenda = self._agenda()
        agenda.write({'email_sent_date': fields.Datetime.now(),
                      'sent_manually': True})
        self.assertEqual(agenda.send_state, 'manual')
        self.assertTrue(agenda.sent_baseline_missing)
        self.assertEqual(agenda.changes_since_sent_count, 0)

    # -- les sujets -----------------------------------------------------

    def test_sujet_ajoute_apres_l_envoi(self):
        agenda = self._sent(self._agenda())
        neuf = self.env['meeting.agenda.topic'].create({
            'agenda_id': agenda.id, 'sequence': 30, 'name': 'Sujet de dernière minute'})
        self.env.invalidate_all()
        self.assertEqual(agenda.changes_since_sent_count, 1)
        self.assertIn('Sujet de dernière minute', agenda.changes_since_sent_html)
        self.assertEqual(neuf.change_since_sent, 'added')
        self.assertFalse(agenda.topic_ids[0].change_since_sent)

    def test_sujet_retire_apres_l_envoi(self):
        agenda = self._sent(self._agenda())
        agenda.topic_ids[0].unlink()
        self.env.invalidate_all()
        self.assertEqual(agenda.changes_since_sent_count, 1)
        self.assertIn('retiré', agenda.changes_since_sent_html)

    def test_sujet_renomme(self):
        agenda = self._sent(self._agenda())
        topic = agenda.topic_ids[0]
        topic.name = 'Premier sujet, revu'
        self.env.invalidate_all()
        self.assertEqual(agenda.changes_since_sent_count, 1)
        self.assertIn('renommé', agenda.changes_since_sent_html)
        self.assertEqual(topic.change_since_sent, 'modified')

    def test_sujet_dont_le_contexte_change(self):
        agenda = self._sent(self._agenda())
        topic = agenda.topic_ids[0]
        topic.description = '<p>Contexte réécrit de fond en comble.</p>'
        self.env.invalidate_all()
        self.assertEqual(agenda.changes_since_sent_count, 1)
        self.assertIn('contexte', agenda.changes_since_sent_html)
        self.assertEqual(topic.change_since_sent, 'modified')

    def test_reordonner_les_sujets_compte_pour_un_seul_changement(self):
        agenda = self._sent(self._agenda())
        premier, second = agenda.topic_ids[0], agenda.topic_ids[1]
        premier.sequence, second.sequence = 20, 10
        self.env.invalidate_all()
        self.assertEqual(agenda.changes_since_sent_count, 1)
        self.assertIn('ordre', agenda.changes_since_sent_html)

    def test_une_insertion_ne_declare_pas_un_reordonnancement(self):
        """Insérer en tête décale tout le reste ; ce n'est pas un désordre."""
        agenda = self._sent(self._agenda())
        self.env['meeting.agenda.topic'].create({
            'agenda_id': agenda.id, 'sequence': 5, 'name': 'Sujet inséré en tête'})
        self.env.invalidate_all()
        diff = agenda._changes_since_sent()
        self.assertFalse(diff['topics_reordered'])
        self.assertEqual(agenda.changes_since_sent_count, 1)

    # -- ce qui NE DOIT PAS produire d'écart ----------------------------

    def test_les_notes_en_direct_n_entrent_pas_dans_le_repere(self):
        agenda = self._sent(self._agenda())
        agenda.live_notes_html = '<p>Notes générales prises en séance.</p>'
        agenda.topic_ids[0].live_notes_html = '<p>Notes sur le premier sujet.</p>'
        self.env.invalidate_all()
        self.assertEqual(agenda.changes_since_sent_count, 0)
        self.assertFalse(agenda.topic_ids[0].change_since_sent)

    def test_demarrer_la_rencontre_ne_produit_aucun_changement(self):
        """Le geste qui allumait un drapeau naïf 66 fois sur 75.

        « Démarrer la rencontre » confirme l'ordre du jour et pré-remplit la
        boîte de notes de CHAQUE sujet : la date d'écriture de toutes les
        lignes bouge d'un seul clic, sans qu'un mot de ce que les destinataires
        ont reçu ait changé.
        """
        agenda = self._sent(self._agenda())
        agenda.action_start_meeting()
        self.env.invalidate_all()
        self.assertEqual(agenda.state, 'confirmed')
        self.assertTrue(agenda.topic_ids[0].live_notes_html)
        self.assertEqual(agenda.changes_since_sent_count, 0)

    def test_confirmer_l_ordre_du_jour_n_est_pas_un_changement(self):
        agenda = self._sent(self._agenda())
        agenda.action_confirm()
        self.env.invalidate_all()
        self.assertEqual(agenda.changes_since_sent_count, 0)

    def test_sujet_propose_en_moderation_n_est_pas_un_changement(self):
        agenda = self._sent(self._agenda())
        propose = self.env['meeting.agenda.topic'].create({
            'agenda_id': agenda.id, 'sequence': 30,
            'name': 'Proposé par un destinataire',
            'source': 'contributed', 'moderation_state': 'pending',
        })
        self.env.invalidate_all()
        self.assertEqual(agenda.changes_since_sent_count, 0,
                         "une proposition n'est ni dans le courriel ni dans le PDF")
        self.assertFalse(propose.change_since_sent)
        propose.action_accept_contribution()
        self.env.invalidate_all()
        self.assertEqual(agenda.changes_since_sent_count, 1)
        self.assertEqual(propose.change_since_sent, 'added')

    # -- l'en-tête ------------------------------------------------------

    def test_date_deplacee_apres_l_envoi(self):
        agenda = self._sent(self._agenda())
        agenda.date = self._in_days(5)
        self.env.invalidate_all()
        diff = agenda._changes_since_sent()
        self.assertEqual([e['key'] for e in diff['head']], ['date'])
        self.assertTrue(diff['head'][0]['from'])
        self.assertTrue(diff['head'][0]['to'])
        self.assertNotEqual(diff['head'][0]['from'], diff['head'][0]['to'])

    def test_participant_ajoute_apres_l_envoi(self):
        agenda = self._sent(self._agenda())
        carla = self.env['res.partner'].create({'name': 'Carla Exemple'})
        agenda.participant_ids = [Command.link(carla.id)]
        self.env.invalidate_all()
        diff = agenda._changes_since_sent()
        self.assertEqual(diff['head'][0]['added'], ['Carla Exemple'])
        self.assertEqual(diff['head'][0]['removed'], [])

    def test_objectifs_reecrits_se_disent_sans_se_montrer(self):
        agenda = self._sent(self._agenda())
        agenda.objectives = "Nouvelle formulation entièrement différente."
        self.env.invalidate_all()
        diff = agenda._changes_since_sent()
        self.assertEqual([e['key'] for e in diff['head']], ['objectives'])
        self.assertNotIn('from', diff['head'][0])
        self.assertNotIn('Nouvelle formulation', agenda.changes_since_sent_html)

    # -- les éléments d'action ------------------------------------------

    def test_element_d_action_ajoute_apres_l_envoi(self):
        agenda = self._sent(self._agenda())
        self.env['project.task'].create({
            'name': 'Action surgie après coup',
            'project_id': self.project.id,
            'bf_meeting_agenda_id': agenda.id,
        })
        self.env.invalidate_all()
        self.assertEqual(agenda.changes_since_sent_count, 1)
        self.assertIn('Action surgie après coup', agenda.changes_since_sent_html)

    def test_odj_dont_la_date_est_passee_ne_declare_pas_les_taches_retirees(self):
        """Le piège : `agenda_task_ids` se vide TOUT SEUL quand la date passe.

        Un écart qui comparerait cette liste vide au repère annoncerait le
        retrait de tous les éléments d'action à la minute où la rencontre
        commence, c'est-à-dire au moment précis où on ouvre l'écran.
        """
        agenda = self._agenda()
        self.env['project.task'].create({
            'name': 'Action portée par l\'ordre du jour',
            'project_id': self.project.id,
            'bf_meeting_agenda_id': agenda.id,
        })
        self.env.invalidate_all()
        self.assertEqual(len(agenda.agenda_task_ids), 1)
        self._sent(agenda)
        self.assertEqual(agenda.changes_since_sent_count, 0)

        agenda.date = self._in_days(-1)
        self.env.invalidate_all()
        self.assertFalse(agenda.agenda_task_ids, "la liste se vide d'elle-même")
        diff = agenda._changes_since_sent()
        self.assertEqual(diff['tasks_removed'], [],
                         "aucun élément d'action ne doit être déclaré retiré")
        # Seule la date bouge, et c'est vrai.
        self.assertEqual([e['key'] for e in diff['head']], ['date'])

    def test_le_predicat_des_taches_est_le_meme_des_deux_cotes(self):
        """S'ils divergent, l'écart ment, et rien ne le dirait.

        Le prédicat doit valoir exactement « cet ordre du jour résout des
        tâches », observé sur des ordres du jour qui EN PORTENT une.
        """
        cas = {
            'à venir': self._agenda(),
            'date passée': self._agenda(date=self._in_days(-2)),
            'annulé': self._agenda(),
            'terminé': self._agenda(),
        }
        cas['annulé'].action_cancel()
        cas['terminé'].write({'state': 'done'})
        for etiquette, agenda in cas.items():
            self.env['project.task'].create({
                'name': f'Action {etiquette}',
                'project_id': self.project.id,
                'bf_meeting_agenda_id': agenda.id,
            })
        self.env.invalidate_all()
        for etiquette, agenda in cas.items():
            self.assertEqual(
                bool(agenda.agenda_task_ids), agenda._carries_agenda_tasks(),
                f"prédicat et résolution divergent sur « {etiquette} »")

    # -- cycle de vie du repère -----------------------------------------

    def test_un_renvoi_remet_le_repere_a_neuf(self):
        agenda = self._sent(self._agenda())
        self.env['meeting.agenda.topic'].create({
            'agenda_id': agenda.id, 'sequence': 30, 'name': 'Ajouté avant le renvoi'})
        self.env.invalidate_all()
        self.assertEqual(agenda.changes_since_sent_count, 1)
        premier_repere = agenda.sent_snapshot_date
        agenda.message_post(body='<p>Version à jour.</p>', message_type='comment',
                            partner_ids=[self.alice.id])
        self.env.invalidate_all()
        self.assertEqual(agenda.changes_since_sent_count, 0,
                         "les destinataires tiennent la dernière copie reçue")
        self.assertGreaterEqual(agenda.sent_snapshot_date, premier_repere)

    def test_retirer_l_envoi_manuel_efface_le_repere(self):
        agenda = self._sent(self._agenda())
        self.assertTrue(agenda.sent_snapshot_json)
        agenda.action_unmark_sent_manually()
        self.assertEqual(agenda.send_state, 'not_sent')
        self.assertFalse(agenda.sent_snapshot_json)
        self.assertFalse(agenda.sent_snapshot_date)
        self.assertFalse(agenda.sent_baseline_missing)

    def test_la_copie_n_herite_pas_du_repere(self):
        agenda = self._sent(self._agenda())
        copie = agenda.with_context(skip_auto_refine=True).copy()
        self.assertFalse(copie.sent_snapshot_json)
        self.assertFalse(copie.sent_snapshot_date)
        self.assertEqual(copie.send_state, 'not_sent')

    def test_repere_illisible_ne_casse_pas_la_fiche(self):
        agenda = self._sent(self._agenda())
        agenda.sudo().sent_snapshot_json = 'ceci n\'est pas du JSON'
        self.env.invalidate_all()
        self.assertEqual(agenda.changes_since_sent_count, 0)
        self.assertFalse(agenda.changes_since_sent_html)

    def test_repere_d_une_version_anterieure_est_ignore(self):
        agenda = self._sent(self._agenda())
        vieux = json.loads(agenda.sudo().sent_snapshot_json)
        vieux['v'] = SNAPSHOT_VERSION - 1
        agenda.sudo().sent_snapshot_json = json.dumps(vieux)
        self.env.invalidate_all()
        self.assertIsNone(agenda._changes_since_sent())
        self.assertEqual(agenda.changes_since_sent_count, 0)

    # -- ce que le repère ne porte pas ----------------------------------

    def test_le_repere_ne_porte_aucun_corps_de_texte(self):
        """Une empreinte ne peut pas ressusciter une phrase retirée à la main."""
        agenda = self._agenda(
            objectives='OBJECTIF-EN-CLAIR',
            context_html='<p>CONTEXTE-EN-CLAIR</p>',
            preparation_html='<p>PREPARATION-EN-CLAIR</p>',
        )
        agenda.topic_ids[0].description = '<p>DESCRIPTION-EN-CLAIR</p>'
        agenda.live_notes_html = '<p>NOTES-EN-DIRECT</p>'
        self._sent(agenda)
        repere = agenda.sudo().sent_snapshot_json
        for interdit in ('OBJECTIF-EN-CLAIR', 'CONTEXTE-EN-CLAIR',
                         'PREPARATION-EN-CLAIR', 'DESCRIPTION-EN-CLAIR',
                         'NOTES-EN-DIRECT'):
            self.assertNotIn(interdit, repere)
        self.assertIn('Premier sujet', repere,
                      "le NOM d'un sujet voyage : sans lui on ne peut pas "
                      "dire lequel a été retiré")

    def test_le_repere_lit_la_meme_source_que_le_pdf(self):
        agenda = self._agenda()
        data = agenda._get_report_data()
        repere = build_snapshot(agenda)
        self.assertEqual([t['id'] for t in repere['topics']],
                         [t['id'] for t in data['topics']])

    # -- le filtre ------------------------------------------------------

    def test_filtre_modifie_depuis_l_envoi(self):
        intact = self._sent(self._agenda())
        touche = self._sent(self._agenda())
        self.env['meeting.agenda.topic'].create({
            'agenda_id': touche.id, 'sequence': 30, 'name': 'Ajout tardif'})
        self.env.invalidate_all()
        trouves = self.env['meeting.agenda'].search(
            [('has_changes_since_sent', '=', True)])
        self.assertIn(touche, trouves)
        self.assertNotIn(intact, trouves)

    # -- le moteur, hors ORM --------------------------------------------

    def test_diff_sur_des_reperes_vides_ne_leve_pas(self):
        for before, after in ((None, None), ({}, {}), ('x', 'y'), ({'v': 1}, {'v': 1})):
            self.assertEqual(diff_snapshots(before, after)['count'], 0)

    # -- l'encadré du courriel de renvoi --------------------------------

    def test_le_bloc_de_renvoi_se_tait_quand_la_case_est_decochee(self):
        agenda = self._sent(self._agenda())
        self.assertFalse(agenda.resend_include_changes,
                         "décoché par défaut, société comme fiche")
        self.env['meeting.agenda.topic'].create({
            'agenda_id': agenda.id, 'sequence': 30, 'name': 'Ajout tardif'})
        self.env.invalidate_all()
        self.assertEqual(agenda.changes_since_sent_count, 1)
        self.assertEqual(agenda.resend_changes_block_html(), '')

    def test_le_bloc_de_renvoi_se_tait_au_PREMIER_envoi(self):
        """Sans repère, il n'y a rien à comparer, et rien à dire."""
        agenda = self._agenda(resend_include_changes=True)
        self.assertEqual(agenda.resend_changes_block_html(), '')

    def test_le_bloc_de_renvoi_se_tait_quand_rien_n_a_bouge(self):
        agenda = self._sent(self._agenda(resend_include_changes=True))
        self.assertEqual(agenda.resend_changes_block_html(), '')

    def test_le_bloc_de_renvoi_nomme_les_sujets_sans_montrer_les_corps(self):
        agenda = self._sent(self._agenda(resend_include_changes=True))
        self.env['meeting.agenda.topic'].create({
            'agenda_id': agenda.id, 'sequence': 30, 'name': 'Sujet ajouté au renvoi'})
        agenda.topic_ids[0].description = '<p>TEXTE-CONFIDENTIEL-REECRIT</p>'
        agenda.objectives = 'OBJECTIF-REECRIT'
        self.env.invalidate_all()
        bloc = agenda.resend_changes_block_html()
        self.assertIn('Sujet ajouté au renvoi', bloc)
        self.assertIn('contexte', bloc)
        self.assertNotIn('TEXTE-CONFIDENTIEL-REECRIT', bloc)
        self.assertNotIn('OBJECTIF-REECRIT', bloc)

    def test_la_preference_de_societe_s_applique_a_la_creation(self):
        """Et par `create`, pas par un défaut qui lirait res_company trop tôt."""
        self.env.company.meeting_resend_changes_default = True
        try:
            self.assertTrue(self._agenda().resend_include_changes)
            self.assertFalse(
                self._agenda(resend_include_changes=False).resend_include_changes,
                "une valeur explicite prime sur la préférence")
        finally:
            self.env.company.meeting_resend_changes_default = False
        self.assertFalse(self._agenda().resend_include_changes)

    def _corps_du_courriel(self, agenda):
        template = self.env.ref('bf_meeting.meeting_agenda_mail_template')
        return template._render_field('body_html', agenda.ids)[agenda.id]

    def test_le_courriel_de_renvoi_porte_vraiment_le_bloc(self):
        """Le rendu a lieu AVANT que le nouveau repère soit posé.

        Sinon le courriel de renvoi comparerait l'ordre du jour à lui-même et
        n'annoncerait jamais rien.
        """
        agenda = self._sent(self._agenda(resend_include_changes=True))
        self.env['meeting.agenda.topic'].create({
            'agenda_id': agenda.id, 'sequence': 30, 'name': 'Ajouté entre deux envois'})
        self.env.invalidate_all()
        corps = self._corps_du_courriel(agenda)
        self.assertIn('Ce qui a changé depuis le dernier envoi', corps)
        self.assertIn('Ajouté entre deux envois', corps)

    def test_le_courriel_ne_porte_pas_le_bloc_quand_la_case_est_decochee(self):
        agenda = self._sent(self._agenda())
        self.env['meeting.agenda.topic'].create({
            'agenda_id': agenda.id, 'sequence': 30, 'name': 'Ajout tardif discret'})
        self.env.invalidate_all()
        corps = self._corps_du_courriel(agenda)
        self.assertNotIn('Ce qui a changé depuis le dernier envoi', corps)

    def test_le_premier_courriel_ne_porte_jamais_le_bloc(self):
        agenda = self._agenda(resend_include_changes=True)
        self.assertNotIn('Ce qui a changé depuis le dernier envoi',
                         self._corps_du_courriel(agenda))

    def test_la_case_de_renvoi_ne_se_recopie_pas(self):
        agenda = self._sent(self._agenda(resend_include_changes=True))
        copie = agenda.with_context(skip_auto_refine=True).copy()
        self.assertFalse(copie.resend_include_changes)

    def test_action_send_agenda_rend_le_bloc_AVANT_de_reposer_le_repere(self):
        """L'ordre des deux gestes est tout ce qui rend l'encadré utile.

        Si le repère était reposé avant le rendu, le courriel comparerait
        l'ordre du jour à lui-même et n'annoncerait jamais rien. Rien dans le
        code ne dit cet ordre à voix haute : ce test le tient.
        """
        agenda = self._sent(self._agenda(resend_include_changes=True))
        agenda.recipient_ids = [Command.set([self.alice.id])]
        self.env['meeting.agenda.topic'].create({
            'agenda_id': agenda.id, 'sequence': 30, 'name': 'Ajouté avant le renvoi'})
        self.env.invalidate_all()

        vu = {}
        Template = type(self.env['mail.template'])

        def espion(self_tmpl, res_id, **kw):
            vu['bloc'] = self.env['meeting.agenda'].browse(
                res_id).resend_changes_block_html()
            return 0

        with patch.object(Template, 'send_mail', espion):
            agenda.action_send_agenda()

        self.assertIn('Ajouté avant le renvoi', vu.get('bloc', ''),
                      "au moment du rendu, le repère doit être l'ANCIEN")
        self.env.invalidate_all()
        self.assertEqual(agenda.changes_since_sent_count, 0,
                         "et une fois parti, le repère est reposé")
