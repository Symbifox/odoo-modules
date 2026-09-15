from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPersonaCx(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.company = cls.env["res.partner"].create({"name": "Exemple Inc.", "is_company": True})
        cls.nadia = cls.env["res.partner"].create({
            "name": "Nadia Exemple", "email": "nadia@exemple.example", "parent_id": cls.company.id,
        })
        cls.persona = cls.env["contact.persona"].create({"partner_id": cls.nadia.id})
        cls.Feedback = cls.env["bf.cx.feedback"]

    def _feedback(self, score, comment="", days=10, kind="nps", partner=None):
        return self.Feedback.create({
            "partner_id": (partner or self.nadia).id, "kind": kind, "source": "manual",
            "score": score, "score_max": 10.0, "comment": comment,
            "date": fields.Date.today() - timedelta(days=days),
        })

    def _composer(self, partner):
        return self.env["mail.compose.message"].create({
            "composition_mode": "comment", "model": "res.partner", "res_ids": str([partner.id]),
            "partner_ids": [(6, 0, partner.ids)], "body": "<p>x</p>",
        })

    def test_detractor_shows_in_banner_gen_and_health(self):
        self._feedback(6, "Les échanges courriels sont trop longs, on se perd dans le but.")
        hint = str(self._composer(self.nadia).persona_hint_html)
        self.assertIn("NPS 6/10 (détracteur)", hint)
        self.assertIn("« Les échanges courriels sont trop longs", hint)
        self.assertIn("NPS 6/10 (détracteur)", self.persona.claude_context_summary)
        # Stored right away by the create hook, without waiting for the cron.
        self.assertEqual(self.persona.relationship_health, "watch")
        self.assertIn("détracteur", self.persona.health_reason)

    def test_promoter_is_context_not_a_warning(self):
        self._feedback(10, "Parfait")
        self.assertNotIn("⚠", str(self._composer(self.nadia).persona_hint_html))
        self.assertIn("Expérience client : NPS 10/10 (promoteur)", self.persona.claude_context_summary)
        self.assertNotEqual(self.persona.relationship_health, "watch")

    def test_open_complaint_degrades_and_closing_it_recovers(self):
        complaint = self.env["bf.cx.complaint"].create({
            "name": "Facture en double", "partner_id": self.nadia.id,
            "description": "<p>x</p>",
        })
        self.assertEqual(self.persona.relationship_health, "degraded")
        self.assertIn("plainte ouverte", self.persona.health_reason)
        self.assertIn('color:#b30000;">⚠ plainte ouverte', str(self._composer(self.nadia).persona_hint_html))
        complaint.write({"state": "closed"})
        self.assertNotEqual(self.persona.relationship_health, "degraded")

    def test_exclusion_flag_survives_an_older_bf_cx(self):
        # Read through whichever exclusion field the installed bf_cx has, and
        # stay quiet when it has none.
        self.assertFalse(self.persona.cx_excluded)
        if "bf_cx_exclude" in self.env["res.partner"]._fields:
            self.nadia.bf_cx_exclude = True
            self.persona.invalidate_recordset(["cx_excluded"])
            self.assertTrue(self.persona.cx_excluded)
            self.assertIn("Ne pas solliciter", self.persona.claude_context_summary)

    def test_old_feedback_no_longer_speaks(self):
        self._feedback(3, "Mauvais", days=400)
        self.assertNotIn("NPS", str(self._composer(self.nadia).persona_hint_html))

    def test_latest_feedback_wins(self):
        self._feedback(4, "Déçu", days=60)
        self._feedback(9, "Bien mieux", days=5)
        self.assertNotIn("⚠", str(self._composer(self.nadia).persona_hint_html))

    def test_company_persona_reads_its_people(self):
        company_persona = self.env["contact.persona"].create({"partner_id": self.company.id})
        self._feedback(5, "Moyen")
        self.assertIn("NPS 5/10", company_persona.claude_context_summary)
