"""« Activer » propose de publier une version.

Sans version publiée, ``current_version`` reste vide et le PDF affiche
« Brouillon (non publié) », même sur un document actif. Le bouton « Activer »
ouvre donc l'assistant de publication quand le document n'a aucune version
publiée; un appel programmatique, lui, active toujours sans détour.
"""

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import KnowledgeCase


@tagged('post_install', '-at_install')
class TestReleaseWizard(KnowledgeCase):

    def _wizard(self, document, activate):
        action = document._action_open_release_wizard(activate=activate)
        return self.env[action['res_model']].with_context(
            **action['context']).create({})

    def test_bouton_activer_sans_version_ouvre_assistant(self):
        action = self.doc_c.with_context(pkm_propose_release=True).action_set_active()
        self.assertEqual(action['res_model'], 'project.document.release.wizard')
        self.assertTrue(action['context']['default_activate'])
        self.assertEqual(self.doc_c.state, 'draft')

    def test_bouton_activer_avec_version_active_direct(self):
        result = self.doc_a.with_context(pkm_propose_release=True).action_set_active()
        self.assertFalse(result)
        self.assertEqual(self.doc_a.state, 'active')

    def test_appel_programmatique_inchange(self):
        self.doc_c.action_set_active()
        self.assertEqual(self.doc_c.state, 'active')
        self.assertFalse(self.doc_c.version_ids)

    def test_activer_et_publier(self):
        wizard = self._wizard(self.doc_c, activate=True)
        self.assertEqual(wizard.version_number, '1.0')
        self.assertEqual(wizard.change_type, 'major')
        wizard.action_release()
        self.assertEqual(self.doc_c.state, 'active')
        self.assertEqual(self.doc_c.current_version, '1.0')
        self.assertEqual(self.doc_c.latest_version_id.state, 'released')

    def test_activer_sans_publier(self):
        wizard = self._wizard(self.doc_c, activate=True)
        wizard.action_activate_only()
        self.assertEqual(self.doc_c.state, 'active')
        self.assertFalse(self.doc_c.version_ids)
        self.assertFalse(self.doc_c.current_version)

    def test_publier_depuis_document_deja_actif(self):
        self.doc_c.action_set_active()
        wizard = self._wizard(self.doc_c, activate=False)
        wizard.action_release()
        self.assertEqual(self.doc_c.current_version, '1.0')

    def test_reprend_version_en_preparation(self):
        draft = self.env['project.document.version'].create({
            'document_id': self.doc_c.id, 'version_number': '0.9',
        })
        wizard = self._wizard(self.doc_c, activate=True)
        self.assertEqual(wizard.pending_version_id, draft)
        self.assertEqual(wizard.version_number, '0.9')
        wizard.version_number = '1.0'
        wizard.action_release()
        self.assertEqual(len(self.doc_c.version_ids), 1)
        self.assertEqual(draft.state, 'released')
        self.assertEqual(self.doc_c.current_version, '1.0')

    def test_numero_suivant_propose(self):
        self.ver_a2.state = 'superseded'
        self.ver_a1.state = 'superseded'
        wizard = self._wizard(self.doc_a, activate=False)
        self.assertEqual(wizard.version_number, '2.1')

    def test_numero_deja_pris_refuse(self):
        wizard = self._wizard(self.doc_a, activate=False)
        wizard.version_number = '2.0'
        with self.assertRaises(UserError):
            wizard.action_release()
