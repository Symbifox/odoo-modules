# Part of bf_oe2oc. See LICENSE file for full copyright and licensing details.
"""Tests for the re-homing handlers.

The interesting cases are all about not trusting the source: a column that is
not there, a foreign key pointing at a record that did not survive, a
selection value the target does not offer.
"""

from odoo.tests import TransactionCase, tagged

from ..models.handlers import handler_for, untranslate


@tagged("post_install", "-at_install")
class TestUntranslate(TransactionCase):
    def test_plain_string_untouched(self):
        self.assertEqual(untranslate("Billet"), "Billet")

    def test_picks_the_requested_language(self):
        raw = '{"en_US": "Ticket", "fr_CA": "Billet"}'
        self.assertEqual(untranslate(raw, "fr_CA"), "Billet")

    def test_falls_back_to_english(self):
        raw = '{"en_US": "Ticket", "nl_NL": "Kaartje"}'
        self.assertEqual(untranslate(raw, "fr_CA"), "Ticket")

    def test_falls_back_to_anything_present(self):
        raw = '{"nl_NL": "Kaartje"}'
        self.assertEqual(untranslate(raw, "fr_CA"), "Kaartje")

    def test_html_content_is_not_mistaken_for_a_translation(self):
        # Braces at both ends, but not JSON: must come back whole.
        raw = "{not json at all}"
        self.assertEqual(untranslate(raw), raw)

    def test_json_that_is_not_a_translation_is_left_alone(self):
        raw = '{"count": 3}'
        self.assertEqual(untranslate(raw), raw)

    def test_non_string_untouched(self):
        self.assertEqual(untranslate(42), 42)
        self.assertIsNone(untranslate(None))


@tagged("post_install", "-at_install")
class TestHelpdeskHandler(TransactionCase):
    def setUp(self):
        super().setUp()
        self.handler = handler_for("helpdesk_ticket")
        if self.handler.target_model not in self.env:
            self.skipTest("helpdesk_mgmt n'est pas installé sur cette base")

    def test_maps_the_columns_it_knows(self):
        columns = ["id", "name", "description", "priority"]
        row = [7, "Imprimante en panne", "<p>Bourrage</p>", "2"]
        values, notes = self.handler.prepare(self.env, columns, row)
        self.assertEqual(values["name"], "Imprimante en panne")
        self.assertEqual(values["description"], "<p>Bourrage</p>")
        self.assertEqual(values["priority"], "2")
        self.assertFalse(notes)

    def test_absent_columns_are_simply_not_mapped(self):
        values, notes = self.handler.prepare(self.env, ["id", "name"], [1, "X"])
        self.assertEqual(values["name"], "X")
        self.assertNotIn("partner_id", values)
        self.assertFalse(notes)

    def test_required_field_gets_its_fallback(self):
        values, _notes = self.handler.prepare(self.env, ["id"], [1])
        # helpdesk.ticket refuses an empty name or description.
        self.assertTrue(values["name"])
        self.assertTrue(values["description"])

    def test_dangling_reference_is_dropped_with_a_reason(self):
        columns = ["id", "name", "partner_id"]
        row = [1, "X", 99999999]
        values, notes = self.handler.prepare(self.env, columns, row)
        self.assertNotIn("partner_id", values)
        self.assertTrue(any("partner_id" in n for n in notes))

    def test_live_reference_is_kept(self):
        partner = self.env["res.partner"].create({"name": "Client repris"})
        columns = ["id", "name", "partner_id"]
        values, notes = self.handler.prepare(self.env, columns, [1, "X", partner.id])
        self.assertEqual(values["partner_id"], partner.id)
        self.assertFalse(notes)

    def test_unknown_selection_value_is_refused(self):
        columns = ["id", "name", "priority"]
        values, notes = self.handler.prepare(self.env, columns, [1, "X", "9"])
        self.assertNotIn("priority", values)
        self.assertTrue(any("priority" in n for n in notes))

    def test_the_values_actually_create_a_record(self):
        columns = ["id", "name", "description", "priority"]
        values, _notes = self.handler.prepare(
            self.env, columns, [1, "Billet", "<p>Corps</p>", "1"])
        ticket = self.env[self.handler.target_model].create(values)
        self.assertEqual(ticket.name, "Billet")


@tagged("post_install", "-at_install")
class TestKnowledgeHandler(TransactionCase):
    def setUp(self):
        super().setUp()
        self.handler = handler_for("knowledge_article")
        if self.handler.target_model not in self.env:
            self.skipTest("document_page n'est pas installé sur cette base")

    def test_translated_name_is_flattened(self):
        columns = ["id", "name", "body"]
        row = [3, '{"en_US": "Daily ops", "fr_CA": "Gestion quotidienne"}',
               "<p>Texte</p>"]
        values, _notes = self.handler.prepare(self.env, columns, row, "fr_CA")
        self.assertEqual(values["name"], "Gestion quotidienne")
        self.assertEqual(values["content"], "<p>Texte</p>")

    def test_creates_a_page(self):
        columns = ["id", "name", "body"]
        values, _notes = self.handler.prepare(
            self.env, columns, [1, "Article", "<p>x</p>"])
        page = self.env[self.handler.target_model].create(values)
        self.assertEqual(page.name, "Article")


@tagged("post_install", "-at_install")
class TestRegistry(TransactionCase):
    def test_unknown_table_has_no_handler(self):
        self.assertIsNone(handler_for("une_table_inventee"))

    def test_every_handler_targets_a_declared_model(self):
        from ..models.handlers import HANDLERS
        for name, handler in HANDLERS.items():
            self.assertTrue(handler.target_model, f"{name} sans modèle d'arrivée")
            self.assertTrue(handler.label, f"{name} sans libellé")
            self.assertTrue(handler.caveat, f"{name} sans mention de ses limites")
