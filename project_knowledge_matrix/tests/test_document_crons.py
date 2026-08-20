"""Entretien quotidien des documents — les trois passes fondues en une tâche.

Deux choses doivent tenir : la fusion ne doit pas rendre le module plus fragile
que les trois tâches séparées qu'elle remplace, et les rappels de révision ne
doivent plus étouffer les alertes d'expiration.
"""

from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import TransactionCase


class DocumentCronCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.type_doc = cls.env['project.document.type'].create({
            'name': 'Type cron', 'code': 'TEST-CRON',
        })
        cls.proprietaire = cls.env['res.users'].create({
            'name': 'Propriétaire d\'essai', 'login': 'proprio.essai.pkm',
        })
        cls.aujourdhui = fields.Date.today()

    def _document(self, code, **kwargs):
        valeurs = {
            'name': f'Document {code}',
            'code': code,
            'type_id': self.type_doc.id,
            'state': 'active',
            'owner_id': self.proprietaire.id,
        }
        valeurs.update(kwargs)
        return self.env['project.document'].create(valeurs)


class TestReviewAndExpirationReminders(DocumentCronCase):

    def test_a_document_with_both_dates_gets_both_activities(self):
        """Le cas majoritaire du parc, et celui qui ne marchait pas.

        Les deux échéances partageaient un drapeau par palier. La passe traitait
        les révisions d'abord, marquait le document, et la recherche
        d'expiration l'excluait aussitôt sur ce même drapeau : l'alerte
        d'expiration n'était jamais émise.
        """
        doc = self._document(
            'TEST-DEUX-DATES',
            review_date=self.aujourdhui + timedelta(days=50),
            expiration_date=self.aujourdhui + timedelta(days=80),
        )

        self.env['project.document']._cron_check_document_reviews()

        notes = ' '.join(doc.activity_ids.mapped('note'))
        self.assertIn('Rappel de révision', notes)
        self.assertIn("Alerte d'expiration", notes)
        self.assertTrue(doc.review_reminder_sent_90)
        self.assertTrue(doc.expiration_reminder_sent_90)

    def test_the_two_flags_are_independent(self):
        """Une échéance seule ne marque que son propre drapeau."""
        revision_seule = self._document(
            'TEST-REV', review_date=self.aujourdhui + timedelta(days=50))
        expiration_seule = self._document(
            'TEST-EXP', expiration_date=self.aujourdhui + timedelta(days=50))

        self.env['project.document']._cron_check_document_reviews()

        self.assertTrue(revision_seule.review_reminder_sent_90)
        self.assertFalse(revision_seule.expiration_reminder_sent_90)
        self.assertFalse(expiration_seule.review_reminder_sent_90)
        self.assertTrue(expiration_seule.expiration_reminder_sent_90)

    def test_a_second_run_does_not_repeat_the_reminder(self):
        doc = self._document(
            'TEST-REPEAT', review_date=self.aujourdhui + timedelta(days=50))
        Document = self.env['project.document']

        Document._cron_check_document_reviews()
        premier_compte = len(doc.activity_ids)
        Document._cron_check_document_reviews()

        self.assertEqual(len(doc.activity_ids), premier_compte)

    def test_the_thresholds_fire_as_the_deadline_approaches(self):
        """Un document à 5 jours d'échéance déclenche les quatre paliers.

        Les recherches n'ont pas de borne basse : tout ce qui est dû dans les 90
        jours entre dans le palier 90, dans les 60 dans le palier 60, etc. Un
        document proche récolte donc les quatre en une passe.
        """
        doc = self._document(
            'TEST-PALIERS', review_date=self.aujourdhui + timedelta(days=5))

        self.env['project.document']._cron_check_document_reviews()

        self.assertTrue(doc.review_reminder_sent_90)
        self.assertTrue(doc.review_reminder_sent_60)
        self.assertTrue(doc.review_reminder_sent_30)
        self.assertTrue(doc.review_reminder_sent_7)
        self.assertEqual(len(doc.activity_ids), 4)

    def test_a_past_deadline_raises_nothing(self):
        """La borne ``> aujourd'hui`` exclut le passé, par conception."""
        doc = self._document(
            'TEST-PASSE', review_date=self.aujourdhui - timedelta(days=1))
        self.env['project.document']._cron_check_document_reviews()
        self.assertFalse(doc.activity_ids)

    def test_an_archived_document_raises_nothing(self):
        doc = self._document(
            'TEST-ARCHIVE', state='archived',
            review_date=self.aujourdhui + timedelta(days=10))
        self.env['project.document']._cron_check_document_reviews()
        self.assertFalse(doc.activity_ids)

    def test_marking_reviewed_rearms_review_but_not_expiration(self):
        """Réviser un document ne repousse pas sa date d'expiration.

        Donc son drapeau d'expiration ne doit pas être réarmé : sinon l'alerte
        repartirait à chaque révision, pour une échéance qui n'a pas bougé.
        """
        doc = self._document(
            'TEST-REARME',
            review_date=self.aujourdhui + timedelta(days=50),
            expiration_date=self.aujourdhui + timedelta(days=80),
        )
        self.env['project.document']._cron_check_document_reviews()
        self.assertTrue(doc.review_reminder_sent_90)
        self.assertTrue(doc.expiration_reminder_sent_90)

        doc.action_mark_reviewed()

        self.assertFalse(doc.review_reminder_sent_90)
        self.assertTrue(doc.expiration_reminder_sent_90)


class TestDailyMaintenance(DocumentCronCase):

    def test_the_single_cron_runs_the_three_passes(self):
        appelees = []
        Document = self.env['project.document']
        Distribution = self.env['project.document.distribution']

        with patch.object(type(Distribution), '_cron_check_pending_acknowledgments',
                          lambda self: appelees.append('accuses')), \
             patch.object(type(Document), '_cron_check_document_reviews',
                          lambda self: appelees.append('revisions')), \
             patch.object(type(Document), '_cron_check_outdated_client_docs',
                          lambda self: appelees.append('obsoletes')):
            Document._cron_document_maintenance()

        self.assertEqual(appelees, ['accuses', 'revisions', 'obsoletes'])

    def test_a_failing_pass_does_not_stop_the_others(self):
        """C'est la contrepartie de la fusion.

        Trois tâches planifiées, c'était trois transactions : l'échec de l'une
        laissait les deux autres travailler. Une seule tâche sans point de
        reprise perdrait cette propriété en silence.
        """
        appelees = []
        Document = self.env['project.document']
        Distribution = self.env['project.document.distribution']

        def qui_leve(self):
            appelees.append('revisions')
            raise ValueError('panne simulée dans la passe des révisions')

        with patch.object(type(Distribution), '_cron_check_pending_acknowledgments',
                          lambda self: appelees.append('accuses')), \
             patch.object(type(Document), '_cron_check_document_reviews', qui_leve), \
             patch.object(type(Document), '_cron_check_outdated_client_docs',
                          lambda self: appelees.append('obsoletes')):
            Document._cron_document_maintenance()

        self.assertEqual(appelees, ['accuses', 'revisions', 'obsoletes'])

    def test_a_failing_pass_leaves_no_half_written_state(self):
        """Le point de reprise défait ce que la passe fautive avait écrit."""
        doc = self._document(
            'TEST-REPRISE', review_date=self.aujourdhui + timedelta(days=50))
        Document = self.env['project.document']

        def ecrit_puis_leve(self):
            self.env['project.document'].search([
                ('code', '=', 'TEST-REPRISE')]).write({'external_url': 'perdu'})
            raise ValueError('panne après écriture')

        with patch.object(type(Document), '_cron_check_document_reviews',
                          ecrit_puis_leve):
            Document._cron_document_maintenance()

        self.assertNotEqual(doc.external_url, 'perdu')

    def test_the_maintenance_pass_actually_creates_the_reminders(self):
        """Bout en bout : la tâche unique produit ce que les trois produisaient.

        Les deux dates sont posées entre 60 et 90 jours, donc chacune ne tombe
        que dans le palier 90 : deux activités, pas une de plus.
        """
        doc = self._document(
            'TEST-BOUT-EN-BOUT',
            review_date=self.aujourdhui + timedelta(days=80),
            expiration_date=self.aujourdhui + timedelta(days=85),
        )
        self.env['project.document']._cron_document_maintenance()
        self.assertEqual(len(doc.activity_ids), 2)


class TestCronInventory(TransactionCase):
    """Ce que le module doit planifier, et rien de plus."""

    def test_the_merged_crons_are_gone_and_the_new_one_is_there(self):
        entretien = self.env.ref(
            'project_knowledge_matrix.ir_cron_document_daily_maintenance',
            raise_if_not_found=False)
        self.assertTrue(entretien, "La tâche d'entretien quotidien est absente")
        self.assertTrue(entretien.active)
        self.assertEqual((entretien.interval_number, entretien.interval_type), (1, 'days'))

        for ancienne in ('ir_cron_document_pending_acknowledgments',
                         'ir_cron_document_review_expiration',
                         'ir_cron_document_outdated_client_docs'):
            with self.subTest(cron=ancienne):
                self.assertFalse(
                    self.env.ref(f'project_knowledge_matrix.{ancienne}',
                                 raise_if_not_found=False),
                    f'{ancienne} tourne encore à côté de l\'entretien quotidien : '
                    'chaque activité serait créée deux fois.',
                )

    def test_every_cron_of_the_module_points_at_a_method_that_exists(self):
        """Un cron dont la méthode a été renommée échoue en silence, une fois par jour."""
        donnees = self.env['ir.model.data'].search([
            ('module', '=', 'project_knowledge_matrix'),
            ('model', '=', 'ir.cron'),
        ])
        crons = self.env['ir.cron'].browse(donnees.mapped('res_id')).exists()
        self.assertTrue(crons, 'Aucune tâche planifiée recensée pour le module')

        fautifs = []
        for cron in crons:
            code = (cron.code or '').strip()
            if not code.startswith('model.') or not code.endswith('()'):
                continue
            methode = code[len('model.'):-2]
            modele = self.env.get(cron.model_id.model)
            if modele is None or not hasattr(modele, methode):
                fautifs.append(f'{cron.cron_name} → {cron.model_id.model}.{methode}')
        self.assertFalse(fautifs, 'Tâches pointant une méthode absente : %s' % fautifs)
