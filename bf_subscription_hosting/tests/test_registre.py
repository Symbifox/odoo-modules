"""Les boutons d'abonnement du domaine ne s'affichent qu'à qui a le registre.

Depuis bf_subscription 18.0.1.10.0, le registre des abonnements s'attribue au
lieu de venir avec le statut d'employé. Un gestionnaire d'hébergement qui ne
l'a pas ne doit pas voir « Créer un abonnement » : le bouton mènerait à une
erreur d'accès.
"""
from odoo import Command
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestBoutonsSelonLeRegistre(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env['res.users'].with_context(no_reset_password=True)
        base = [cls.env.ref('base.group_user').id,
                cls.env.ref('hosting_management.group_hosting_manager').id]
        cls.sans = Users.create({'name': 'Hébergement sans registre', 'login': 'heberg_sans_registre',
                                 'groups_id': [Command.set(base)]})
        cls.avec = Users.create({'name': 'Hébergement avec registre', 'login': 'heberg_avec_registre',
                                 'groups_id': [Command.set(base + [
                                     cls.env.ref('bf_subscription.group_subscription_user').id])]})

    def _arch(self, user):
        return self.env['hosting.domain'].with_user(user).get_views([(False, 'form')])['views']['form']['arch']

    def test_sans_le_registre_aucun_bouton(self):
        arch = self._arch(self.sans)
        self.assertNotIn('action_create_subscription', arch)
        self.assertNotIn('action_view_subscription', arch)

    def test_temoin_avec_le_registre_les_boutons(self):
        arch = self._arch(self.avec)
        self.assertIn('action_create_subscription', arch)
        self.assertIn('action_view_subscription', arch)
