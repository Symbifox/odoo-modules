"""La migration 18.0.13.3.4 fige le texte actuel des versions publiées sans copie.

Une version publiée avant que son corps ne soit rédigé dans Odoo reçoit, comme
copie figée, le texte du document tel qu'il est aujourd'hui.

Le jeu d'essai sème le cas tel qu'il existe en production — un document publié
alors qu'il pointait vers Nextcloud, puis basculé en corps interne — et, autour,
tout ce que la migration ne doit pas toucher : une version déjà figée, une
version remplacée sans copie, une version en préparation, un document resté
externe.
"""

import importlib.util
import logging
from pathlib import Path

from odoo.tests import TransactionCase

RACINE = Path(__file__).resolve().parent.parent
JOURNAL = 'odoo.addons.project_knowledge_matrix.models.document_version_body'
RAPPORT_DOCUMENT = 'project_knowledge_matrix.report_document_body'

TEXTE_ACTUEL = 'TEXTE-ACTUEL rédigé dans Odoo après la publication.'
TEXTE_FIGE = 'TEXTE-FIGE publié normalement.'


def _migration():
    chemin = RACINE / 'migrations' / '18.0.13.3.4' / 'post-migrate.py'
    spec = importlib.util.spec_from_file_location('pkm_migration_13_3_4', chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestMigrationGel(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Document = cls.env['project.document']
        Version = cls.env['project.document.version']
        Section = cls.env['project.document.section']
        cls.type_politique = cls.env['project.document.type'].create({
            'name': 'Procédure d\'essai (migration)',
            'code': 'ESSAI-MIGRATION',
            'is_internal': True,
        })

        def basculer(doc, texte):
            doc.body_source = 'internal'
            Section.create({
                'document_id': doc.id, 'code': 'PROCEDURE', 'name': 'Procédure',
                'sequence': 10, 'content': '<p>%s</p>' % texte,
            })
            Section.create({
                'document_id': doc.id, 'code': 'GOUVERNANCE', 'name': 'Gouvernance',
                'sequence': 90, 'content_kind': 'computed', 'render_key': 'governance',
            })

        # Le cas de production : publié en externe, rédigé dans Odoo ensuite.
        cls.doc_herite = Document.create({
            'name': 'Procédure publiée avant sa rédaction', 'code': 'ESSAI-M1',
            'type_id': cls.type_politique.id,
        })
        cls.v_herite = Version.create({
            'document_id': cls.doc_herite.id, 'version_number': '2025.11'})
        cls.v_herite.action_release()
        basculer(cls.doc_herite, TEXTE_ACTUEL)
        partenaire = cls.env['res.partner'].create({'name': 'Destinataire d\'essai'})
        cls.distribution = cls.env['project.document.distribution'].create({
            'version_id': cls.v_herite.id, 'recipient_type': 'partner',
            'partner_id': partenaire.id, 'state': 'pending',
        })

        # Deux versions publiées en externe : seule la courante est figée.
        cls.doc_deux = Document.create({
            'name': 'Procédure à deux versions', 'code': 'ESSAI-M2',
            'type_id': cls.type_politique.id,
        })
        cls.v_remplacee = Version.create({
            'document_id': cls.doc_deux.id, 'version_number': '1.0'})
        cls.v_remplacee.action_release()
        cls.v_courante = Version.create({
            'document_id': cls.doc_deux.id, 'version_number': '2.0'})
        cls.v_courante.action_release()
        basculer(cls.doc_deux, TEXTE_ACTUEL)
        cls.v_en_preparation = Version.create({
            'document_id': cls.doc_deux.id, 'version_number': '2.1'})

        # Déjà figée par une vraie publication : on n'y touche pas.
        cls.doc_fige = Document.create({
            'name': 'Politique publiée normalement', 'code': 'ESSAI-M3',
            'type_id': cls.type_politique.id, 'body_source': 'internal',
        })
        Section.create({
            'document_id': cls.doc_fige.id, 'code': 'POLITIQUE', 'name': 'Politique',
            'content': '<p>%s</p>' % TEXTE_FIGE,
        })
        cls.v_fige = Version.create({
            'document_id': cls.doc_fige.id, 'version_number': '1.0'})
        cls.v_fige.action_release()

        # Resté externe : son corps vit sur Nextcloud, rien à figer.
        cls.doc_externe = Document.create({
            'name': 'Guide externe', 'code': 'ESSAI-M4',
            'type_id': cls.type_politique.id,
        })
        cls.v_externe = Version.create({
            'document_id': cls.doc_externe.id, 'version_number': '1.0'})
        cls.v_externe.action_release()

    def _jouer(self):
        _migration().migrate(self.env.cr, '18.0.13.3.3')
        self.env.invalidate_all()

    def test_la_version_publiee_sans_copie_recoit_le_texte_actuel(self):
        publiee_le = self.v_herite.release_date
        self.assertFalse(self.v_herite.section_ids)
        self.assertTrue(self.doc_herite.has_unpublished_changes)

        self._jouer()

        self.assertEqual(self.v_herite.state, 'released')
        self.assertEqual(self.v_herite.release_date, publiee_le)
        self.assertEqual(
            sorted(self.v_herite.section_ids.mapped('code')),
            ['GOUVERNANCE', 'PROCEDURE'])
        procedure = self.v_herite.section_ids.filtered(lambda s: s.code == 'PROCEDURE')
        self.assertIn(TEXTE_ACTUEL, procedure.content)
        gouvernance = self.v_herite.section_ids.filtered(lambda s: s.code == 'GOUVERNANCE')
        self.assertIn('2025.11', gouvernance.content)
        self.assertEqual(self.v_herite.body_hash, self.doc_herite.body_hash)
        self.assertTrue(self.v_herite.frozen_date)
        self.assertFalse(self.doc_herite.has_unpublished_changes)
        # La distribution n'a pas bougé.
        self.assertEqual(self.distribution.state, 'pending')
        self.assertEqual(self.distribution.version_id, self.v_herite)

    def test_le_pdf_ne_dit_plus_non_fige(self):
        avant = self.env['ir.actions.report']._render_qweb_html(
            RAPPORT_DOCUMENT, self.doc_herite.ids)[0].decode()
        self.assertIn('pas été figé', avant)
        self._jouer()
        apres = self.env['ir.actions.report']._render_qweb_html(
            RAPPORT_DOCUMENT, self.doc_herite.ids)[0].decode()
        self.assertNotIn('pas été figé', apres)
        self.assertIn(TEXTE_ACTUEL, apres)
        self.assertEqual(
            self.doc_herite._report_print_context()['mode'], 'frozen')

    def test_rien_d_autre_n_est_touche(self):
        copie_figee = self.v_fige.section_ids
        empreinte = self.v_fige.body_hash
        self._jouer()
        self.assertEqual(self.v_fige.section_ids, copie_figee)
        self.assertEqual(self.v_fige.body_hash, empreinte)
        self.assertFalse(self.v_remplacee.section_ids)
        self.assertEqual(self.v_remplacee.state, 'superseded')
        self.assertFalse(self.v_en_preparation.section_ids)
        self.assertEqual(self.v_en_preparation.state, 'draft')
        self.assertFalse(self.v_externe.section_ids)
        self.assertTrue(self.v_courante.section_ids)

    def test_aucun_courriel_ni_message(self):
        messages = self.env['mail.message'].search_count([])
        courriels = self.env['mail.mail'].search_count([])
        self._jouer()
        self.assertEqual(self.env['mail.message'].search_count([]), messages)
        self.assertEqual(self.env['mail.mail'].search_count([]), courriels)

    def test_une_ligne_de_journal_par_document_fige(self):
        # ⚠️ `logging.INFO`, jamais la chaîne 'INFO' : Odoo rebaptise le
        # niveau 25 « INFO », et `assertLogs(..., 'INFO')` écoute alors au
        # niveau 25 — il ne verrait rien, et `assertNoLogs` passerait à vide.
        with self.assertLogs(JOURNAL, logging.INFO) as journal:
            self._jouer()
        lignes = [l for l in journal.output if 'corps figé' in l]
        self.assertEqual(len(lignes), 2, lignes)
        self.assertTrue(any('ESSAI-M1 v2025.11' in l for l in lignes), lignes)
        self.assertTrue(any('ESSAI-M2 v2.0' in l for l in lignes), lignes)

    def test_rejouee_elle_ne_cree_rien(self):
        self._jouer()
        copies = self.env['project.document.version.section'].search_count([])
        figees = self.env['project.document.version']._freeze_unfrozen_released()
        self.assertFalse(figees)
        with self.assertNoLogs(JOURNAL, logging.INFO):
            self._jouer()
        self.assertEqual(
            self.env['project.document.version.section'].search_count([]), copies)
