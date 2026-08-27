# -*- coding: utf-8 -*-
"""La garde de pré-vol est le seul point qui empêche une publication à
moitié prête. Elle doit refuser pour la bonne raison, et laisser passer
quand tout est en ordre."""

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPreflight(TransactionCase):

    def setUp(self):
        super().setUp()
        self.calendar = self.env["bf.editorial.calendar"].create({
            "name": "Banc d'essai",
            "cadence_days": 4,
            "word_floor": 100,
            "require_all_langs": "no",
        })
        self.stage_draft = self.env.ref("bf_editorial.stage_draft")
        self.entry = self.env["bf.editorial.entry"].create({
            "name": "Entrée d'essai",
            "calendar_id": self.calendar.id,
            "stage_id": self.stage_draft.id,
        })

    def test_qa_non_passee_bloque(self):
        problems = self.entry._preflight_problems()
        self.assertTrue(any("QA" in p for p in problems))

    def test_publication_refusee_leve_une_erreur_explicite(self):
        with self.assertRaises(UserError) as caught:
            self.entry.action_publish()
        self.assertIn("QA", str(caught.exception))

    def test_reste_bloquant_retient(self):
        self.entry.qa_state = "clean"
        self.env["bf.editorial.checklist"].create({
            "entry_id": self.entry.id,
            "name": "Visuels à produire",
            "is_blocking": True,
        })
        self.entry.invalidate_recordset()
        self.assertTrue(
            any("reste" in p for p in self.entry._preflight_problems())
        )

    def test_reste_non_bloquant_laisse_passer(self):
        self.entry.qa_state = "clean"
        self.env["bf.editorial.checklist"].create({
            "entry_id": self.entry.id,
            "name": "Blurbs",
            "is_blocking": False,
        })
        self.entry.invalidate_recordset()
        self.assertFalse(
            any("reste" in p for p in self.entry._preflight_problems())
        )

    def test_dependance_non_publiee_bloque(self):
        amont = self.env["bf.editorial.entry"].create({
            "name": "Doit sortir avant",
            "calendar_id": self.calendar.id,
            "stage_id": self.stage_draft.id,
        })
        self.entry.depends_on_ids = amont
        self.entry.invalidate_recordset()
        self.assertTrue(self.entry.is_blocked)
        self.assertIn("Doit sortir avant", self.entry.blocking_summary)

    def test_entree_prete_passe(self):
        self.entry.qa_state = "clean"
        self.entry.invalidate_recordset()
        self.assertEqual(self.entry._preflight_problems(), [])
        self.assertTrue(self.entry.preflight_ok)

    def test_publication_pose_la_date_et_l_etape(self):
        self.entry.qa_state = "clean"
        self.entry.invalidate_recordset()
        self.entry.action_publish()
        self.assertTrue(self.entry.published_date)
        self.assertTrue(self.entry.stage_id.is_closing)

    def test_cron_ne_publie_pas_une_entree_non_prete(self):
        """Le point le plus important du module : une date approuvée ne passe
        pas outre la garde. Sinon un article part sans sa traduction."""
        self.entry.scheduled_publish_date = "2000-01-01 00:00:00"
        self.env["bf.editorial.entry"]._cron_publish_scheduled()
        self.assertFalse(self.entry.published_date)
        self.assertTrue(self.entry.activity_ids)

    def test_cron_publie_une_entree_prete(self):
        self.entry.qa_state = "clean"
        self.entry.scheduled_publish_date = "2000-01-01 00:00:00"
        self.entry.invalidate_recordset()
        self.env["bf.editorial.entry"]._cron_publish_scheduled()
        self.assertTrue(self.entry.published_date)
