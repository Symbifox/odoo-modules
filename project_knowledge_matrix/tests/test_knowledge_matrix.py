"""Matrices et éléments — le vrai produit, celui qui reste après le découpage.

C'est le sous-système que tout déploiement du module emploie, donc le seul
dont une régression touche à coup sûr un utilisateur.
"""

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase


class MatrixCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.projet = cls.env['project.project'].create({'name': 'Projet matrice d\'essai'})
        cls.section = cls.env['project.knowledge.section'].create({
            'name': 'Section d\'essai', 'code': 'TESTSEC',
        })
        cls.matrice = cls.env['project.knowledge.matrix'].create({
            'name': 'Matrice d\'essai', 'project_id': cls.projet.id,
        })

    def _element(self, decision_id, state='pending', **kwargs):
        valeurs = {
            'decision_id': decision_id,
            'name': f'Élément {decision_id}',
            'matrix_id': self.matrice.id,
            'section_id': self.section.id,
            'state': state,
        }
        valeurs.update(kwargs)
        return self.env['project.knowledge.item'].create(valeurs)


class TestMatrixStatistics(MatrixCase):

    def test_statistics_on_a_hand_counted_matrix(self):
        """Six éléments, dont un hors décompte.

        ``na`` sort du dénominateur ; ``done`` et ``accepted`` comptent tous les
        deux comme complétés. Donc 5 éléments retenus, 2 complétés, 2 en
        attente, 40 % d'avancement.
        """
        self._element('A1', 'done')
        self._element('A2', 'accepted')
        self._element('A3', 'pending')
        self._element('A4', 'pending')
        self._element('A5', 'in_progress')
        self._element('A6', 'na')

        self.assertEqual(self.matrice.item_count, 5)
        self.assertEqual(self.matrice.completed_count, 2)
        self.assertEqual(self.matrice.pending_count, 2)
        self.assertAlmostEqual(self.matrice.progress, 40.0, places=4)

    def test_an_empty_matrix_shows_zero_progress(self):
        self.assertEqual(self.matrice.item_count, 0)
        self.assertEqual(self.matrice.progress, 0.0)

    def test_a_matrix_of_only_na_items_does_not_divide_by_zero(self):
        self._element('B1', 'na')
        self._element('B2', 'na')
        self.assertEqual(self.matrice.item_count, 0)
        self.assertEqual(self.matrice.progress, 0.0)

    def test_statistics_follow_a_state_change(self):
        element = self._element('C1', 'pending')
        self.assertEqual(self.matrice.completed_count, 0)
        element.action_done()
        self.assertEqual(self.matrice.completed_count, 1)
        self.assertAlmostEqual(self.matrice.progress, 100.0, places=4)


class TestDecisionId(MatrixCase):

    def test_decision_id_is_upper_cased_on_create(self):
        element = self._element('in55')
        self.assertEqual(element.decision_id, 'IN55')

    def test_decision_id_is_upper_cased_on_write(self):
        element = self._element('D1')
        element.decision_id = 'd2'
        self.assertEqual(element.decision_id, 'D2')

    def test_a_malformed_decision_id_is_refused(self):
        for mauvais in ('1A', 'A-1', 'A 1', 'A1B', '@1'):
            with self.subTest(decision_id=mauvais):
                with self.assertRaises(ValidationError):
                    self._element(mauvais)

    def test_well_formed_decision_ids_are_accepted(self):
        for bon in ('A1', 'B6', 'IN55'):
            with self.subTest(decision_id=bon):
                self.assertTrue(self._element(bon))


class TestItemStateMachine(MatrixCase):

    def test_the_action_buttons_move_the_state(self):
        element = self._element('E1')
        element.action_start()
        self.assertEqual(element.state, 'in_progress')
        element.action_done()
        self.assertEqual(element.state, 'done')
        element.action_reset()
        self.assertEqual(element.state, 'pending')

    def test_toggle_na_goes_both_ways(self):
        element = self._element('E2')
        element.action_toggle_na()
        self.assertEqual(element.state, 'na')
        element.action_toggle_na()
        self.assertNotEqual(element.state, 'na')

    def test_the_decision_track(self):
        element = self._element('E3')
        element.action_propose()
        self.assertEqual(element.state, 'proposed')
        element.action_accept()
        self.assertEqual(element.state, 'accepted')
        element.action_supersede()
        self.assertEqual(element.state, 'superseded')
        self.assertTrue(element.superseded_by_id)

    def test_superseding_gives_the_successor_a_free_decision_id(self):
        """Le successeur ne peut pas reprendre l'identifiant du prédécesseur.

        ``decision_id`` est unique par matrice. La copie telle quelle violait la
        contrainte : le bouton « Remplacer » levait une erreur de base de
        données à chaque appel. Le successeur prend maintenant le premier
        numéro libre du même préfixe.
        """
        self._element('G1')
        self._element('G2')
        element = self._element('G3')

        element.action_supersede()
        successeur = element.superseded_by_id

        self.assertTrue(successeur)
        self.assertEqual(successeur.decision_id, 'G4')
        self.assertEqual(successeur.state, 'pending')
        self.assertEqual(successeur.supersedes_id, element)

    def test_superseding_twice_does_not_collide(self):
        element = self._element('H1')
        element.action_supersede()
        premier = element.superseded_by_id
        premier.action_supersede()
        second = premier.superseded_by_id
        self.assertEqual(premier.decision_id, 'H2')
        self.assertEqual(second.decision_id, 'H3')

    def test_superseding_ignores_another_matrix(self):
        """L'unicité est par matrice : un H1 ailleurs ne décale pas la suite."""
        autre_matrice = self.env['project.knowledge.matrix'].create({
            'name': 'Autre matrice', 'project_id': self.projet.id,
        })
        for identifiant in ('J1', 'J2', 'J3'):
            self.env['project.knowledge.item'].create({
                'decision_id': identifiant, 'name': f'Ailleurs {identifiant}',
                'matrix_id': autre_matrice.id, 'section_id': self.section.id,
            })
        element = self._element('J1')
        element.action_supersede()
        self.assertEqual(element.superseded_by_id.decision_id, 'J2')

    def test_a_state_change_closes_the_open_follow_up_activities(self):
        """Compléter un élément ferme le rappel qui le poursuivait.

        Sans ça, le cron de suivi continue de relancer sur un élément réglé —
        et le drapeau ``followup_activity_created`` l'empêche d'en recréer un
        quand il redeviendra pertinent.
        """
        element = self._element('F1')
        type_activite = self.env.ref(
            'project_knowledge_matrix.mail_activity_type_knowledge_overdue',
            raise_if_not_found=False,
        )
        if not type_activite:
            self.skipTest('Type d\'activité de suivi absent de cette base')

        element.activity_schedule(
            'project_knowledge_matrix.mail_activity_type_knowledge_overdue',
            user_id=self.env.user.id,
        )
        element.followup_activity_created = True
        self.assertTrue(element.activity_ids)

        element.state = 'done'

        self.assertFalse(element.activity_ids)
        self.assertFalse(element.followup_activity_created)
