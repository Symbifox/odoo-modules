"""La passe Gen automatique des comptes rendus et l'avis à l'organisateur.

Le Meeting Processor finit un brouillon, puis demande à Odoo la passe Gen.
Trois semaines durant, la passe qu'il lançait lui-même se disait « terminée
en 2 s » sans avoir rien lu : un échec qui s'écrivait comme un succès, et
personne n'était avisé de rien.

Ces tests gardent sept promesses :

* **une seule passe automatique, sur un brouillon neuf d'une société qui l'a
  demandée**, lancée par un gestionnaire ; sinon rien ne part et l'appel rend
  False sans lever ;
* **une passe qui ne peut pas partir, ou que le pont ne rend jamais, est dite
  à l'organisateur** : pont injoignable, locataire absent, passe perdue ;
* **un utilisateur simple ne fabrique pas d'avis par RPC** : le drapeau ne
  s'écrit qu'en sudo, ni par `write`, ni par `create`, ni par `copy`, et l'état
  du raffinage ne s'écrit que par un gestionnaire ;
* **l'avis se lit sur le compte rendu, pas sur le signal du pont** : « prêt »
  seulement s'il est passé à « Révisé » ; resté brouillon ou en erreur,
  l'activité le dit et donne la cause ;
* **l'organisateur est avisé même quand le pont écrit sous son compte** : Odoo
  n'avise pas ce qu'on s'assigne soi-même, et le pont écrit souvent sous le
  compte de l'organisateur ;
* **une passe lancée au bouton n'avise personne**, et l'activité se ferme
  quand le compte rendu part ;
* **le locataire annoncé au pont est celui de la base**, jamais « bf » par
  défaut : c'est ce défaut qui écrivait l'état des passes d'une base sur les
  comptes rendus d'une autre.
"""

from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

PONT = 'odoo.addons.bf_ai_bridge.models.bf_ai_bridge.BfAiBridge'


@tagged('post_install', '-at_install')
class TestRaffinageAutomatique(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.env['ir.config_parameter'].sudo().set_param('bf_ai_bridge.tenant', 'bf')
        # Le compte du processeur : gestionnaire des rencontres, comme en production.
        cls.robot = new_test_user(
            cls.env, login='essai-robot-raffinage', name='Processeur du banc',
            email='robot@essai.test',
            groups='base.group_user,bf_meeting.group_meeting_manager')
        cls.simple = new_test_user(
            cls.env, login='essai-simple-raffinage', name='Utilisatrice du banc',
            email='simple@essai.test',
            groups='base.group_user,bf_meeting.group_meeting_user')
        cls.organisatrice = new_test_user(
            cls.env, login='essai-organisatrice-raffinage', name='Organisatrice du banc',
            email='organisatrice@essai.test', notification_type='email',
            tz='Pacific/Auckland',
            groups='base.group_user,bf_meeting.group_meeting_manager')
        cls.type_revision = cls.env.ref('bf_meeting.mail_act_meeting_review')

    def setUp(self):
        super().setUp()
        # Le cron balaie toute la base : une passe automatique d'une autre
        # fiche (données de la base d'essai) fausserait ses comptes.
        self.env['meeting.record'].sudo().search(
            [('refine_auto', '=', True)]).write({'refine_auto': False})
        self.env['ir.config_parameter'].sudo().set_param('bf_claude_chat.enabled', 'True')
        self.company.meeting_auto_refine = True
        self.rencontre = self.env['meeting.record'].create({
            'name': 'Appel du banc',
            'date': '2026-10-10 15:00:00',
            'duration_minutes': 12,
            'organizer_id': self.organisatrice.id,
            'company_id': self.company.id,
        })
        patcher = patch(PONT + '.available', return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Gen n'est pas une dépendance du module : les essais ne doivent pas
        # exiger qu'il soit installé. Les essais de la garde la relâchent.
        self._gen = patch.object(self.registry['meeting.record'], '_bf_gen_actif',
                                 return_value=True)
        self._gen.start()
        self.addCleanup(self._gen.stop)

    # ── Outils ───────────────────────────────────────────────────────────

    def _lancer(self, user=None):
        return self.rencontre.with_user(user or self.robot).action_auto_refine()

    def _activites(self, rec=None):
        rec = rec or self.rencontre
        return self.env['mail.activity'].search([
            ('res_model', '=', 'meeting.record'), ('res_id', '=', rec.id),
            ('activity_type_id', '=', self.type_revision.id)])

    def _avis(self, user):
        # Ce que la personne reçoit, pas ce qu'Odoo écrit : le message
        # d'avis existe même quand Odoo retire l'auteur de ses destinataires.
        # On compte donc les notifications par courriel qui lui sont adressées,
        # après avoir vidé la file où `mail_post_defer` (s'il est installé) les met 30 s.
        self.env['mail.message.schedule'].sudo().search([
            ('mail_message_id.model', '=', 'meeting.record'),
            ('mail_message_id.res_id', '=', self.rencontre.id),
        ])._send_notifications()
        return self.env['mail.notification'].search_count([
            ('mail_message_id.model', '=', 'meeting.record'),
            ('mail_message_id.res_id', '=', self.rencontre.id),
            ('mail_message_id.message_type', '=', 'user_notification'),
            ('res_partner_id', '=', user.partner_id.id),
            ('notification_type', '=', 'email')])

    def _passe_auto_lancee(self):
        self.assertTrue(self._lancer())
        self.assertTrue(self.rencontre.refine_auto)

    # ── Qui déclenche, et quand ──────────────────────────────────────────

    def test_brouillon_neuf_lance_une_passe(self):
        # Le fil vers le pont part après la validation : il est rangé dans les
        # rappels `postcommit`, que la transaction de l'essai ne lance jamais.
        avant = len(self.env.cr.postcommit._funcs)
        self.assertTrue(self._lancer())
        self.assertEqual(len(self.env.cr.postcommit._funcs), avant + 1)
        self.assertEqual(self.rencontre.refine_state, 'queued')
        self.assertTrue(self.rencontre.refine_auto)
        self.assertTrue(self.rencontre.refine_in_progress)

    def test_reglage_decoche_ne_lance_rien(self):
        self.company.meeting_auto_refine = False
        self.assertFalse(self._lancer())
        self.assertEqual(self.rencontre.refine_state, 'none')
        self.assertFalse(self.rencontre.refine_auto)

    def test_une_seule_passe_par_compte_rendu(self):
        self._passe_auto_lancee()
        self.rencontre.set_refine_state('error', 'Plafond du pont.')
        self.assertFalse(self._lancer(), "jamais de relance en boucle")

    def test_gen_eteint_ne_lance_pas(self):
        # Gen installé mais éteint dans ses réglages : la case de la société
        # ne suffit pas. Seulement là où Gen est installé.
        if 'claude.chat.session' not in self.env:
            self.skipTest("Gen (bf_claude_chat) n'est pas installé")
        self._gen.stop()
        self.env['ir.config_parameter'].sudo().set_param('bf_claude_chat.enabled', 'False')
        avant = len(self.env.cr.postcommit._funcs)
        self.assertFalse(self._lancer())
        self.assertEqual(len(self.env.cr.postcommit._funcs), avant)
        self.assertEqual(self.rencontre.refine_state, 'none')
        self.assertFalse(self._activites())

    def test_gen_absent_ne_lance_pas(self):
        self._gen.stop()
        with patch.object(self.registry['meeting.record'], '_bf_gen_actif',
                          return_value=False):
            self.assertFalse(self._lancer())
        self.assertEqual(self.rencontre.refine_state, 'none')

    def test_odj_automatique_ne_part_pas_sans_gen(self):
        # Même garde pour le pré-remplissage automatique des ordres du jour.
        self.env['ir.config_parameter'].sudo().set_param('bf_meeting.agenda_auto_refine', '1')
        self._gen.stop()
        projet = self.env['project.project'].create({'name': 'Mandat du banc, OdJ'})
        with patch.object(self.registry['meeting.record'], '_bf_gen_actif', return_value=False), \
                patch.object(self.registry['meeting.agenda'], '_launch_refine_agenda') as lancer:
            self.env['meeting.agenda'].create({'name': 'OdJ sans Gen', 'project_id': projet.id})
            lancer.assert_not_called()

    def test_compte_rendu_deja_revise_ne_relance_pas(self):
        self.rencontre.report_state = 'reviewed'
        self.assertFalse(self._lancer())

    def test_utilisateur_non_gestionnaire_ne_lance_pas(self):
        self.assertFalse(self._lancer(self.simple))
        self.assertEqual(self.rencontre.refine_state, 'none')

    def test_pont_injoignable_avise_l_echec(self):
        avant = len(self.env.cr.postcommit._funcs)
        with patch(PONT + '.available', return_value=False):
            self.assertTrue(self._lancer(), "Odoo prend le compte rendu en charge")
        self.assertEqual(len(self.env.cr.postcommit._funcs), avant, "aucun fil vers le pont")
        self.assertEqual(self.rencontre.refine_state, 'error')
        act = self._activites()
        self.assertEqual(act.summary, 'Raffinage Gen en échec : réviser à la main')
        self.assertIn('injoignable', act.note)
        self.assertEqual(self._avis(self.organisatrice), 1)

    def test_plusieurs_comptes_rendus_d_un_coup_refuses(self):
        autre = self.rencontre.copy()
        self.assertFalse((self.rencontre | autre).with_user(self.robot).action_auto_refine())

    # ── L'avis se lit sur le compte rendu ────────────────────────────────

    def test_passe_aboutie_avise_l_organisateur(self):
        self._passe_auto_lancee()
        # Ce que fait le skill en dernier, puis le pont en sortant.
        self.rencontre.report_state = 'reviewed'
        self.rencontre.set_refine_state('done', 'Passe terminée (modèle claude-opus-5).')
        act = self._activites()
        self.assertEqual(len(act), 1)
        self.assertEqual(act.user_id, self.organisatrice)
        self.assertEqual(act.summary, 'Réviser le compte rendu')
        self.assertEqual(act.date_deadline, fields.Date.context_today(
            self.rencontre.with_context(tz='Pacific/Auckland')))
        self.assertEqual(self._avis(self.organisatrice), 1)
        self.assertFalse(self.rencontre.refine_auto)

    def test_avise_meme_quand_le_pont_ecrit_sous_le_compte_de_l_organisateur(self):
        self._passe_auto_lancee()
        self.rencontre.report_state = 'reviewed'
        self.rencontre.with_user(self.organisatrice).set_refine_state('done', 'Passe terminée.')
        self.assertEqual(len(self._activites()), 1)
        self.assertEqual(self._avis(self.organisatrice), 1,
                         "Odoo n'avise pas ce qu'on s'assigne : l'avis doit partir quand même")

    def test_done_sans_revise_n_est_pas_pret(self):
        self._passe_auto_lancee()
        self.rencontre.set_refine_state('done', 'Passe automatique terminée en 2 s.')
        act = self._activites()
        self.assertEqual(len(act), 1)
        self.assertIn("ne l'a pas passé à « Révisé »", act.summary)
        self.assertEqual(self._avis(self.organisatrice), 1)

    def test_erreur_avise_avec_la_cause(self):
        self._passe_auto_lancee()
        self.rencontre.set_refine_state('error', 'Passe interrompue après 1800 s (plafond du pont).')
        act = self._activites()
        self.assertEqual(act.summary, 'Raffinage Gen en échec : réviser à la main')
        self.assertIn('1800 s', act.note)

    def test_erreur_apres_revise_dit_fin_non_confirmee(self):
        # Le skill a fini (CR « Révisé »), puis le pont est mort : le cron
        # déclare la passe perdue, mais le travail est fait.
        self._passe_auto_lancee()
        self.rencontre.report_state = 'reviewed'
        self.rencontre.set_refine_state('error', 'Aucun signal du pont depuis 35 min.')
        act = self._activites()
        self.assertEqual(act.summary, 'Réviser le compte rendu : fin de passe non confirmée')

    def test_refus_du_pont_avise(self):
        self._passe_auto_lancee()
        self.rencontre._bf_refine_bridge_reply('error', "Tenant 'autre' not supported")
        act = self._activites()
        self.assertEqual(act.summary, 'Raffinage Gen en échec : réviser à la main')
        self.assertIn('autre', act.note)

    def test_accuse_de_reception_du_pont_n_avise_pas(self):
        self._passe_auto_lancee()
        self.rencontre._bf_refine_bridge_reply('ok', 'Raffinement lancé en arrière-plan.')
        self.assertEqual(self.rencontre.refine_state, 'queued')
        self.assertFalse(self._activites())
        self.assertTrue(self.rencontre.refine_auto)

    def test_panne_de_l_avis_garde_l_etat_et_laisse_une_note(self):
        # Sans savepoint, la panne avortait la transaction : la note de repli
        # échouait à son tour et l'état écrit par le pont était perdu.
        # Ce que le savepoint garantit en plus : ce qui a été fait dans l'avis
        # avant la panne (ici, retirer l'activité précédente) est annulé avec
        # lui, au lieu de laisser le compte rendu à moitié modifié.
        ancienne = self.rencontre.activity_schedule(
            'bf_meeting.mail_act_meeting_review', user_id=self.organisatrice.id,
            summary='Ancienne activité')
        self._passe_auto_lancee()
        self.rencontre.report_state = 'reviewed'
        with patch.object(self.registry['meeting.record'], 'activity_schedule',
                          side_effect=RuntimeError('panne simulée')):
            self.rencontre.set_refine_state('done', 'Passe terminée.')
        self.assertEqual(self.rencontre.refine_state, 'done')
        self.assertTrue(ancienne.exists(), "le retrait fait avant la panne est annulé")
        self.assertIn("n'a pas pu être avisé", self.rencontre.message_ids[0].body)

    def test_un_seul_avis_par_passe(self):
        self._passe_auto_lancee()
        self.rencontre.report_state = 'reviewed'
        self.rencontre.set_refine_state('done', 'Passe terminée.')
        # Un second signal terminal (relecture du pont, course) n'avise plus.
        self.rencontre.set_refine_state('done', 'Passe terminée.')
        self.assertEqual(len(self._activites()), 1)
        self.assertEqual(self._avis(self.organisatrice), 1)

    def test_sans_organisateur_une_note_au_fil(self):
        self.rencontre.organizer_id = False
        self._passe_auto_lancee()
        self.rencontre.report_state = 'reviewed'
        avant = len(self.rencontre.message_ids)
        self.rencontre.set_refine_state('done', 'Passe terminée.')
        self.assertFalse(self._activites())
        self.assertGreater(len(self.rencontre.message_ids), avant)
        self.assertIn('Réviser le compte rendu', self.rencontre.message_ids[0].body)

    def test_compte_rendu_deja_envoye_n_avise_pas(self):
        self._passe_auto_lancee()
        self.rencontre.report_state = 'sent'
        self.rencontre.set_refine_state('done', 'Passe terminée.')
        self.assertFalse(self._activites())

    # ── Bouton, envoi, péremption ────────────────────────────────────────

    def test_passe_lancee_au_bouton_n_avise_personne(self):
        self.rencontre.with_user(self.organisatrice).action_refine_meeting()
        self.assertEqual(self.rencontre.refine_state, 'queued')
        self.assertFalse(self.rencontre.refine_auto)
        self.rencontre.report_state = 'reviewed'
        self.rencontre.set_refine_state('done', 'Passe terminée.')
        self.assertFalse(self._activites())

    def test_bouton_apres_une_passe_auto_reprend_la_main(self):
        self._passe_auto_lancee()
        self.rencontre.set_refine_state('error', 'Plafond du pont.')
        self.assertEqual(len(self._activites()), 1)
        self.rencontre.with_user(self.organisatrice).action_refine_meeting('Consigne du banc.')
        self.assertFalse(self.rencontre.refine_auto)
        self.assertFalse(self._activites(), "relancer à la main, c'est prendre la suite")
        fait = self.env['mail.message'].search_count([
            ('model', '=', 'meeting.record'), ('res_id', '=', self.rencontre.id),
            ('mail_activity_type_id', '=', self.type_revision.id)])
        self.assertEqual(fait, 1, "fermée, pas supprimée")

    def test_assistant_refuse_pendant_une_passe(self):
        self._passe_auto_lancee()
        with self.assertRaises(UserError):
            self.rencontre.with_user(self.organisatrice).action_open_refine_wizard()

    def test_bouton_refuse_pendant_une_passe(self):
        # Un formulaire ouvert avant le lancement automatique montre encore le
        # bouton : la seconde passe doublerait tâche-mère et feuille de temps.
        self._passe_auto_lancee()
        with self.assertRaises(UserError):
            self.rencontre.with_user(self.organisatrice).action_refine_meeting('Consigne.')
        self.assertTrue(self.rencontre.refine_auto)

    # ── Ce qu'un appelant ne peut pas fabriquer ──────────────────────────

    def test_drapeau_automatique_ne_s_ecrit_pas_par_rpc(self):
        for user in (self.simple, self.robot, self.organisatrice):
            with self.assertRaises(AccessError):
                self.rencontre.with_user(user).write({'refine_auto': True})

    def test_utilisateur_simple_ne_declenche_pas_l_avis(self):
        self._passe_auto_lancee()
        with self.assertRaises(AccessError):
            self.rencontre.with_user(self.simple).set_refine_state('error', 'Texte libre.')
        self.assertEqual(self.rencontre.refine_state, 'queued')
        self.assertFalse(self._activites())
        self.assertTrue(self.rencontre.refine_auto, "l'avis attend le vrai signal du pont")

    def test_utilisateur_simple_ne_recule_pas_le_signal(self):
        # Un `refine_date` reculé ferait déclarer perdue une passe vivante.
        self._passe_auto_lancee()
        with self.assertRaises(AccessError):
            self.rencontre.with_user(self.simple).write(
                {'refine_date': fields.Datetime.now() - timedelta(hours=2)})

    def test_drapeau_ne_se_fabrique_ni_par_create_ni_par_copy(self):
        Rencontre = self.env['meeting.record'].with_user(self.simple)
        vals = {'name': 'Fabrication', 'date': '2026-10-10 15:00:00',
                'organizer_id': self.organisatrice.id}
        with self.assertRaises(AccessError):
            Rencontre.create(dict(vals, refine_auto=True))
        with self.assertRaises(AccessError):
            Rencontre.with_context(default_refine_auto=True).create(vals)
        with self.assertRaises(AccessError):
            Rencontre.create(dict(vals, refine_state='queued', refine_date='2026-01-01 00:00:00'))
        propre = Rencontre.create(vals)
        with self.assertRaises(AccessError):
            propre.copy(default={'refine_auto': True})

    # ── La passe que le pont ne rend jamais ──────────────────────────────

    def test_passe_perdue_avisee_par_le_cron(self):
        self._passe_auto_lancee()
        self.rencontre.refine_date = fields.Datetime.now() - timedelta(minutes=40)
        self.assertEqual(self.env['meeting.record']._cron_bf_auto_refine_lost(), 1)
        self.assertEqual(self.rencontre.refine_state, 'error')
        act = self._activites()
        self.assertEqual(act.summary, 'Raffinage Gen en échec : réviser à la main')
        self.assertIn('Aucun signal du pont', act.note)
        self.assertEqual(self.env['meeting.record']._cron_bf_auto_refine_lost(), 0)

    def test_cron_laisse_une_passe_recente(self):
        self._passe_auto_lancee()
        self.rencontre.refine_date = fields.Datetime.now() - timedelta(minutes=25)
        self.env['meeting.record']._cron_bf_auto_refine_lost()
        self.assertEqual(self.rencontre.refine_state, 'queued')
        self.assertFalse(self._activites())

    def test_cron_ignore_une_passe_lancee_au_bouton(self):
        self.rencontre.with_user(self.organisatrice).action_refine_meeting()
        self.rencontre.refine_date = fields.Datetime.now() - timedelta(minutes=40)
        self.assertEqual(self.env['meeting.record']._cron_bf_auto_refine_lost(), 0)

    def test_envoi_ferme_l_activite(self):
        self._passe_auto_lancee()
        self.rencontre.report_state = 'reviewed'
        self.rencontre.set_refine_state('done', 'Passe terminée.')
        self.assertEqual(len(self._activites()), 1)
        self.rencontre.write({'report_state': 'sent'})
        self.assertFalse(self._activites())
        fait = self.env['mail.message'].search([
            ('model', '=', 'meeting.record'), ('res_id', '=', self.rencontre.id),
            ('mail_activity_type_id', '=', self.type_revision.id)])
        self.assertTrue(fait, "fermée, pas supprimée : le fil garde qu'elle a été faite")

    def test_passe_longue_reste_en_cours(self):
        self._passe_auto_lancee()
        # Les passes d'octobre 2026 vont jusqu'à 24 min : l'ancien défaut de
        # 20 min rendait le bouton avant la fin.
        self.rencontre.refine_date = fields.Datetime.now() - timedelta(minutes=25)
        self.rencontre.invalidate_recordset(['refine_in_progress'])
        self.assertTrue(self.rencontre.refine_in_progress)

    # ── Le locataire annoncé au pont ─────────────────────────────────────

    def test_locataire_de_la_base_et_non_bf_par_defaut(self):
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('bf_ai_bridge.tenant', 'autre')
        ICP.set_param('bf_meeting.bridge_tenant', False)
        self.assertEqual(self.rencontre._bridge_tenant(), 'autre')
        projet = self.env['project.project'].create({'name': 'Mandat du banc'})
        agenda = self.env['meeting.agenda'].with_context(skip_auto_refine=True).create(
            {'name': 'OdJ du banc', 'project_id': projet.id})
        self.assertEqual(agenda._bridge_tenant(), 'autre')

    def test_locataire_absent_refuse_au_lieu_de_viser_bf(self):
        ICP = self.env['ir.config_parameter'].sudo()
        for cle in ('bf_ai_bridge.tenant', 'bf_claude_chat.tenant',
                    'bf_meeting.bridge_tenant'):
            ICP.set_param(cle, False)
        with self.assertRaises(UserError):
            self.rencontre._bridge_tenant()

    def test_passe_auto_sans_locataire_avise_sans_lever(self):
        ICP = self.env['ir.config_parameter'].sudo()
        for cle in ('bf_ai_bridge.tenant', 'bf_claude_chat.tenant'):
            ICP.set_param(cle, False)
        avant = len(self.env.cr.postcommit._funcs)
        self.assertTrue(self._lancer())
        self.assertEqual(len(self.env.cr.postcommit._funcs), avant, "aucun fil vers le pont")
        self.assertEqual(self.rencontre.refine_state, 'error')
        self.assertFalse(self.rencontre.refine_auto)
        self.assertIn('locataire', self._activites().note)
