"""L'avis d'assignation des tâches nées d'un compte rendu.

Le Meeting Processor et la revue Gen créent les tâches d'un compte rendu sous
leur propre compte : Odoo envoie alors à l'assigné un courriel « Vous avez été
assigné à » par tâche. Sur une base réelle, des dizaines par semaine, tous
pour la même personne.

Ces tests gardent trois promesses :

* **coché, une tâche créée avec un compte rendu n'avise pas**, et l'assigné
  reste abonné : seul le courriel tombe ;
* **décoché, ou sans compte rendu, Odoo avise comme avant** : le réglage ne
  touche rien d'autre que ce qu'il nomme ;
* **une réassignation faite plus tard avise toujours** : c'est une vraie
  assignation, faite par quelqu'un.
"""

from odoo import Command
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestAvisAssignation(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        # Celui qui crée : un compte de service, comme le processeur.
        cls.robot = new_test_user(
            cls.env, login='robot-essai', name='Processeur du banc',
            groups='base.group_user,project.group_project_user')
        # Ceux qu'on assigne : de vraies personnes, avisées par courriel.
        cls.premiere = new_test_user(
            cls.env, login='premiere-essai', name='Première Personne',
            email='premiere@essai.test', notification_type='email',
            groups='base.group_user,project.group_project_user')
        cls.deuxieme = new_test_user(
            cls.env, login='deuxieme-essai', name='Deuxième Personne',
            email='deuxieme@essai.test', notification_type='email',
            groups='base.group_user,project.group_project_user')
        cls.projet = cls.env['project.project'].create({'name': 'Mandat du banc'})
        cls.compte_rendu = cls.env['meeting.record'].create({
            'name': 'Rencontre du banc',
            'date': '2026-09-30 18:00:00',
            'duration_minutes': 30,
            'project_id': cls.projet.id,
        })

    def _regler(self, coche):
        self.company.meeting_task_assign_quiet = coche

    def _creer(self, vals_list):
        return self.env['project.task'].with_user(self.robot).create(vals_list)

    def _vals(self, nom, user, avec_cr=True):
        vals = {
            'name': nom,
            'project_id': self.projet.id,
            'user_ids': [Command.set(user.ids)],
        }
        if avec_cr:
            vals['meeting_id'] = self.compte_rendu.id
        return vals

    def _avis(self, tache, user):
        return self.env['mail.message'].search_count([
            ('model', '=', 'project.task'),
            ('res_id', '=', tache.id),
            ('message_type', '=', 'user_notification'),
            ('partner_ids', 'in', user.partner_id.ids),
        ])

    # ── Ce que le réglage fait taire ─────────────────────────────────────

    def test_coche_tache_du_cr_n_avise_pas(self):
        self._regler(True)
        tache = self._creer([self._vals('Action du CR', self.premiere)])
        self.assertEqual(self._avis(tache, self.premiere), 0)

    def test_coche_l_assigne_reste_abonne(self):
        """Le courriel tombe, l'abonnement reste : la suite du fil lui
        parvient comme avant."""
        self._regler(True)
        tache = self._creer([self._vals('Action du CR', self.premiere)])
        self.assertIn(self.premiere.partner_id, tache.message_partner_ids)
        self.assertEqual(tache.user_ids, self.premiere)

    # ── Ce qu'il ne touche pas ───────────────────────────────────────────

    def test_decoche_tache_du_cr_avise(self):
        self._regler(False)
        tache = self._creer([self._vals('Action du CR', self.premiere)])
        self.assertEqual(self._avis(tache, self.premiere), 1)

    def test_coche_tache_sans_cr_avise(self):
        self._regler(True)
        tache = self._creer([self._vals('Tâche ordinaire', self.premiere, avec_cr=False)])
        self.assertEqual(self._avis(tache, self.premiere), 1)

    def test_lot_mele_garde_l_ordre_et_n_avise_que_hors_cr(self):
        """Un seul appel, deux sortes de tâches : chacune suit sa règle, et le
        lot revient dans l'ordre demandé."""
        self._regler(True)
        taches = self._creer([
            self._vals('Premier, hors CR', self.premiere, avec_cr=False),
            self._vals('Deuxième, du CR', self.premiere),
            self._vals('Troisième, hors CR', self.deuxieme, avec_cr=False),
        ])
        self.assertEqual(taches.mapped('name'),
                         ['Premier, hors CR', 'Deuxième, du CR', 'Troisième, hors CR'])
        self.assertEqual(self._avis(taches[0], self.premiere), 1)
        self.assertEqual(self._avis(taches[1], self.premiere), 0)
        self.assertEqual(self._avis(taches[2], self.deuxieme), 1)

    def test_le_lot_rendu_ne_porte_pas_le_silence(self):
        """Le contexte qui fait taire ne doit pas suivre les tâches rendues :
        un appelant qui les réassigne dans la foulée avise normalement."""
        self._regler(True)
        tache = self._creer([self._vals('Action du CR', self.premiere)])
        self.assertFalse(tache.env.context.get('mail_auto_subscribe_no_notify'))

    def test_reassignation_plus_tard_avise(self):
        self._regler(True)
        tache = self._creer([self._vals('Action du CR', self.premiere)])
        tache.with_user(self.robot).write({'user_ids': [Command.link(self.deuxieme.id)]})
        self.assertEqual(self._avis(tache, self.deuxieme), 1)

    def test_le_reglage_suit_la_societe_du_projet(self):
        """Une société qui n'a rien coché avise, même quand celle de
        l'appelant a coché."""
        self._regler(True)
        autre = self.env['res.company'].create({'name': 'Société du banc'})
        self.robot.company_ids |= autre
        self.premiere.company_ids |= autre
        projet = self.env['project.project'].create({
            'name': 'Mandat de l\'autre société', 'company_id': autre.id})
        tache = self.env['project.task'].with_user(self.robot).with_context(
            allowed_company_ids=[self.company.id, autre.id]).create({
                'name': 'Action du CR, autre société',
                'project_id': projet.id,
                'meeting_id': self.compte_rendu.id,
                'user_ids': [Command.set(self.premiere.ids)],
            })
        self.assertEqual(self._avis(tache, self.premiere), 1)

    # ── Où le réglage se montre ──────────────────────────────────────────

    def test_gen_installe_suit_le_registre(self):
        self.assertEqual(self.company.meeting_gen_installed,
                         'claude.chat.session' in self.env)
