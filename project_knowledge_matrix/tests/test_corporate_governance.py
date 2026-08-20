"""Gouvernance corporative.

Résolutions, administrateurs, dirigeants et échéances de conformité forment un
sous-système autonome. Ce qui doit survivre à toute évolution du module, c'est
la numérotation des résolutions, le calcul de statut des échéances et les
périodes de mandat.
"""

from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase


class TestCorporateResolution(TransactionCase):

    def _creer(self, **kwargs):
        valeurs = {
            'name': 'Résolution d\'essai',
            'resolution_type': 'board',
            'meeting_date': fields.Date.today(),
        }
        valeurs.update(kwargs)
        return self.env['corporate.resolution'].create(valeurs)

    def test_a_resolution_gets_a_reference_from_the_sequence(self):
        """La référence sort d'``ir.sequence``, pas d'un compteur maison.

        La séquence est une donnée du module. Un déplacement du modèle qui
         l'oublie laisse toutes les résolutions suivantes en « New ».
        """
        resolution = self._creer()
        self.assertTrue(resolution.sequence)
        self.assertNotEqual(resolution.sequence, 'New')

    def test_two_resolutions_do_not_share_a_reference(self):
        premiere = self._creer()
        seconde = self._creer(name='Deuxième résolution')
        self.assertNotEqual(premiere.sequence, seconde.sequence)

    def test_an_explicit_reference_is_kept(self):
        resolution = self._creer(sequence='RES-MANUELLE-1')
        self.assertEqual(resolution.sequence, 'RES-MANUELLE-1')

    def test_state_actions(self):
        resolution = self._creer()
        self.assertEqual(resolution.status, 'draft')
        resolution.action_propose()
        self.assertEqual(resolution.status, 'proposed')
        resolution.action_adopt()
        self.assertEqual(resolution.status, 'adopted')
        resolution.action_reset_draft()
        self.assertEqual(resolution.status, 'draft')
        resolution.action_reject()
        self.assertEqual(resolution.status, 'rejected')


class TestCorporateCompliance(TransactionCase):

    def _creer(self, jours, **kwargs):
        valeurs = {
            'name': 'Échéance d\'essai',
            'event_type': 'annual_declaration',
            'due_date': fields.Date.today() + timedelta(days=jours),
        }
        valeurs.update(kwargs)
        return self.env['corporate.compliance.event'].create(valeurs)

    def test_status_follows_the_due_date(self):
        """Les quatre statuts, sur les quatre côtés de la frontière des 30 jours."""
        self.assertEqual(self._creer(-1).status, 'overdue')
        self.assertEqual(self._creer(0).status, 'due_soon')
        self.assertEqual(self._creer(30).status, 'due_soon')
        self.assertEqual(self._creer(31).status, 'upcoming')

    def test_completing_an_event_wins_over_the_due_date(self):
        evenement = self._creer(-90)
        self.assertEqual(evenement.status, 'overdue')
        evenement.action_complete()
        self.assertEqual(evenement.status, 'completed')
        evenement.action_reset()
        self.assertEqual(evenement.status, 'overdue')


class TestCorporateMandates(TransactionCase):
    """Administrateurs et dirigeants : ``is_active`` est un calcul stocké."""

    def _partenaire(self, nom):
        return self.env['res.partner'].create({'name': nom})

    def test_a_director_without_end_date_is_active(self):
        administrateur = self.env['corporate.director'].create({
            'partner_id': self._partenaire('Administratrice Un').id,
            'appointment_date': fields.Date.today() - timedelta(days=365),
        })
        self.assertTrue(administrateur.is_active)

    def test_a_director_whose_mandate_ended_is_inactive(self):
        administrateur = self.env['corporate.director'].create({
            'partner_id': self._partenaire('Administratrice Deux').id,
            'appointment_date': fields.Date.today() - timedelta(days=365),
            'end_date': fields.Date.today() - timedelta(days=1),
        })
        self.assertFalse(administrateur.is_active)

    def test_an_officer_without_end_date_is_active(self):
        dirigeant = self.env['corporate.officer'].create({
            'partner_id': self._partenaire('Dirigeant Un').id,
            'title': 'president',
            'appointment_date': fields.Date.today() - timedelta(days=365),
        })
        self.assertTrue(dirigeant.is_active)

    def test_an_officer_whose_mandate_ended_is_inactive(self):
        dirigeant = self.env['corporate.officer'].create({
            'partner_id': self._partenaire('Dirigeant Deux').id,
            'title': 'secretary',
            'appointment_date': fields.Date.today() - timedelta(days=365),
            'end_date': fields.Date.today() - timedelta(days=1),
        })
        self.assertFalse(dirigeant.is_active)
