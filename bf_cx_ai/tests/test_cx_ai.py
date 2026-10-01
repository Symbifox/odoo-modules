from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

BRIDGE = "odoo.addons.bf_ai_bridge.models.bf_ai_bridge.BfAiBridge"


@tagged("post_install", "-at_install")
class TestCxAi(TransactionCase):
    """18.0.1.2.0 : l'analyse passe par /cx/analyze, jamais par /chat."""

    def _feedback(self, comment="Le délai de réponse était beaucoup trop long."):
        partner = self.env["res.partner"].create({"name": "Client CX IA"})
        return self.env["bf.cx.feedback"].create({
            "partner_id": partner.id, "kind": "nps", "score": 3, "score_max": 10,
            "source": "manual", "comment": comment})

    def test_analysis_uses_dedicated_endpoint(self):
        fb = self._feedback()
        answer = {"data": {"sentiment": "negatif", "themes": ["Délais"], "summary": "Délai trop long."}}
        with patch(BRIDGE + ".available", return_value=True), \
                patch(BRIDGE + ".call", return_value=answer) as call:
            self.assertTrue(fb._bf_cx_ai_analyze_one())
        endpoint, payload = call.call_args[0][0], call.call_args[0][1]
        self.assertEqual(endpoint, "/cx/analyze")
        self.assertEqual(payload["commentaire"], fb.comment)
        self.assertNotIn("message", payload)
        self.assertEqual(fb.sentiment, "negatif")
        self.assertEqual(fb.ai_summary, "Délai trop long.")
        if "theme_ids" in fb._fields:
            self.assertIn("Délais", fb.theme_ids.mapped("name"))

    def test_bridge_error_leaves_a_note(self):
        fb = self._feedback()
        with patch(BRIDGE + ".available", return_value=True), \
                patch(BRIDGE + ".call", return_value={"error": "délai"}):
            self.assertFalse(fb._bf_cx_ai_analyze_one())
        self.assertFalse(fb.sentiment)
        self.assertTrue(fb.message_ids.filtered(lambda m: "Analyse IA" in (m.body or "")))

    def test_vocabulary_shares_helpdesk_themes(self):
        if "helpdesk.theme" not in self.env:
            self.skipTest("bf_helpdesk absent")
        self.env["helpdesk.theme"].create({"name": "Imprimantes"})
        self.assertIn("Imprimantes", self.env["bf.cx.feedback"]._bf_cx_ai_theme_vocabulary())

    def test_no_promise_that_data_stays_on_the_server(self):
        """Le pont transmet le commentaire au modèle d'IA : aucun texte ne doit dire le contraire."""
        helps = [f.help for model in ("bf.cx.feedback", "res.config.settings")
                 for f in self.env[model]._fields.values() if f.help]
        views = self.env["ir.ui.view"].sudo().search([("arch_db", "ilike", "Analyser")])
        texts = " ".join(helps) + " ".join(v.arch_db for v in views)
        self.assertNotIn("ne quitte le serveur", texts)
        self.assertNotIn("leaves the server", texts)

