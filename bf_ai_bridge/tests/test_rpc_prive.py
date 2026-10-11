"""Le pont ne s'atteint pas par RPC.

`call` et `stream` étaient publiques : un compte portail les appelait par
XML-RPC ou /web/dataset/call_kw, et Odoo postait au pont ce qu'il voulait.
Ces essais passent par le même filtre que le RPC (`get_public_method`).
"""
from odoo.exceptions import AccessError
from odoo.service.model import get_public_method
from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestPontPrive(TransactionCase):

    def test_call_et_stream_ne_sont_pas_publiques(self):
        portail = new_test_user(self.env, login='portail-pont-prive',
                                groups='base.group_portal')
        modele = self.env['bf.ai.bridge'].with_user(portail)
        for nom in ('call', 'stream'):
            with self.assertRaises(AccessError, msg=nom):
                get_public_method(modele, nom)

    def test_le_code_serveur_les_appelle_toujours(self):
        self.assertTrue(callable(self.env['bf.ai.bridge'].call))
        self.assertTrue(callable(self.env['bf.ai.bridge'].stream))
