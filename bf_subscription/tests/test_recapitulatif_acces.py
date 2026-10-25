"""Un récapitulatif ne se lit que dans sa société, et seul un gestionnaire l'envoie.

Le récapitulatif lit en sudo les abonnements de sa société. Sans règle sur le
récapitulatif lui-même, un interne d'une autre société pouvait se l'envoyer (bouton
d'essai) ou l'imprimer, et un simple lecteur faisait partir l'envoi aux destinataires.

Rien ne sort : l'envoi est remplacé par une doublure.
"""
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestRecapitulatifAcces(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.company = cls.env.company
        cls.autre = cls.env['res.company'].create({'name': 'Autre société essai'})
        Users = cls.env['res.users'].with_context(no_reset_password=True)
        interne = cls.env.ref('base.group_user').id
        cls.destinataire = Users.create({
            'name': 'Destinataire récap', 'login': 'destinataire_recap_acces',
            'email': 'destinataire@recap.invalid', 'groups_id': [Command.set([interne])],
        })
        cls.lecteur = Users.create({
            'name': 'Lecteur récap', 'login': 'lecteur_recap_acces',
            'email': 'lecteur@recap.invalid', 'groups_id': [Command.set([interne])],
        })
        cls.gestionnaire = Users.create({
            'name': 'Gestionnaire récap', 'login': 'gestionnaire_recap_acces',
            'email': 'gestionnaire@recap.invalid',
            'groups_id': [Command.set([interne, cls.env.ref('bf_subscription.group_subscription_manager').id])],
        })
        cls.ailleurs = Users.create({
            'name': 'Interne ailleurs', 'login': 'interne_ailleurs_recap_acces',
            'email': 'ailleurs@recap.invalid', 'groups_id': [Command.set([interne])],
            'company_id': cls.autre.id, 'company_ids': [Command.set([cls.autre.id])],
        })
        cls.digest = cls.env['subscription.digest'].create({
            'name': 'Récap accès', 'company_id': cls.company.id,
            'user_ids': [Command.set([cls.destinataire.id])],
        })

    def _envoi(self):
        # On observe l'appel d'envoi, pas le courriel : l'assertRaises d'Odoo annule ce
        # qui a été écrit avant l'erreur, alors que le vrai SMTP, lui, serait déjà parti.
        return patch.object(type(self.env['subscription.digest']), '_send_digest', autospec=True)

    def test_autre_societe_ne_lit_ni_ne_s_envoie_le_recapitulatif(self):
        digest = self.digest.with_user(self.ailleurs)
        with self.assertRaises(AccessError):
            digest.read(['name'])
        with self._envoi() as envoi:
            with self.assertRaises(AccessError):
                digest.action_send_test()
        envoi.assert_not_called()

    def test_lecteur_ne_fait_pas_partir_l_envoi(self):
        digest = self.digest.with_user(self.lecteur)
        digest.read(['name'])  # il le voit : même société
        with self._envoi() as envoi:
            with self.assertRaises(AccessError):
                digest.action_send_now()
        envoi.assert_not_called()

    def test_gestionnaire_envoie(self):
        with self._envoi() as envoi:
            self.digest.with_user(self.gestionnaire).action_send_now()
        envoi.assert_called_once()
