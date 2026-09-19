# Part of bf_oe2oc. See LICENSE file for full copyright and licensing details.
"""Tests for reading the export file.

A bad file has to fail loudly and say what it expected. The worst outcome is a
file that loads quietly and leaves the person believing their Enterprise data
is in there.
"""

import base64
import json

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


def encode(payload):
    return base64.b64encode(json.dumps(payload).encode("utf-8"))


def sample(tables=None):
    return {
        "format": "odoo18-ee2ce/leftovers",
        "format_version": 1,
        "generated_at": "2026-09-18T23:00:00+00:00",
        "source": {"dump": "dump.sql", "db_name": "ancienne-base",
                   "version": "saas~18.3"},
        "target_db": "nouvelle-base",
        "tables": tables if tables is not None else {
            "helpdesk_ticket": {
                "columns": ["id", "name", "description"],
                "row_count": 2,
                "truncated": False,
                "rows": [[1, "Imprimante", "<p>Bourrage</p>"],
                         [2, "RPV", None]],
            },
            "social_stream_post": {
                "columns": ["id", "message"],
                "row_count": 1,
                "truncated": True,
                "rows": [[1, "Publication"]],
            },
        },
    }


@tagged("post_install", "-at_install")
class TestBundleLoading(TransactionCase):
    def _bundle(self, payload, name="Reprise d'essai"):
        return self.env["bf.oe2oc.bundle"].create({
            "name": name,
            "file": encode(payload),
            "file_name": "enterprise-leftovers.json",
        })

    def test_loads_tables_and_rows(self):
        bundle = self._bundle(sample())
        bundle.action_load()
        self.assertEqual(bundle.state, "loaded")
        self.assertEqual(bundle.source_db, "ancienne-base")
        self.assertEqual(bundle.target_db, "nouvelle-base")
        self.assertEqual(bundle.table_count, 2)
        self.assertEqual(bundle.row_count, 3)

        tickets = bundle.table_ids.filtered(lambda t: t.name == "helpdesk_ticket")
        self.assertEqual(tickets.row_count, 2)
        self.assertEqual(tickets.columns, ["id", "name", "description"])
        self.assertEqual(sorted(tickets.row_ids.mapped("source_id")), [1, 2])

    def test_truncation_is_carried_through(self):
        bundle = self._bundle(sample())
        bundle.action_load()
        social = bundle.table_ids.filtered(lambda t: t.name == "social_stream_post")
        self.assertTrue(social.truncated)

    def test_table_without_handler_is_kept_not_lost(self):
        bundle = self._bundle(sample())
        bundle.action_load()
        social = bundle.table_ids.filtered(lambda t: t.name == "social_stream_post")
        self.assertFalse(social.target_model)
        self.assertEqual(social.state, "kept")
        self.assertEqual(social.row_count, 1)

    def test_reloading_replaces_rather_than_doubles(self):
        bundle = self._bundle(sample())
        bundle.action_load()
        bundle.action_load()
        self.assertEqual(bundle.table_count, 2)
        self.assertEqual(bundle.row_count, 3)

    def test_rejects_another_format(self):
        payload = sample()
        payload["format"] = "quelque-chose-dautre"
        bundle = self._bundle(payload)
        with self.assertRaises(UserError) as caught:
            bundle.action_load()
        self.assertIn("odoo18-ee2ce/leftovers", str(caught.exception))

    def test_rejects_a_future_format_version(self):
        payload = sample()
        payload["format_version"] = 99
        bundle = self._bundle(payload)
        with self.assertRaises(UserError):
            bundle.action_load()

    def test_rejects_something_that_is_not_json(self):
        bundle = self.env["bf.oe2oc.bundle"].create({
            "name": "Pas du JSON",
            "file": base64.b64encode(b"<html>oops</html>"),
        })
        with self.assertRaises(UserError) as caught:
            bundle.action_load()
        self.assertIn("JSON", str(caught.exception))

    def test_rejects_a_file_too_large_to_be_leftovers(self):
        from ..models.bf_oe2oc_bundle import MAX_ROWS  # noqa: F401
        rows = [[i, f"n{i}"] for i in range(MAX_ROWS + 1)]
        payload = sample({"mail_message": {
            "columns": ["id", "name"], "row_count": len(rows),
            "truncated": False, "rows": rows}})
        bundle = self._bundle(payload)
        with self.assertRaises(UserError) as caught:
            bundle.action_load()
        # Ne jamais affirmer sur du texte traduit : le même message sort en
        # anglais dès que la langue de la session change, et l'essai tombe
        # pour une raison qui n'a rien à voir avec ce qu'il vérifie.
        self.assertIn(f"{MAX_ROWS:,}".replace(",", "\u202f"),
                      str(caught.exception).replace("\u00a0", "\u202f").replace(" ", "\u202f"))

    def test_a_row_with_no_id_column_still_loads(self):
        payload = sample({"helpdesk_sla_status": {
            "columns": ["ticket_id", "status"], "row_count": 1,
            "truncated": False, "rows": [[4, "reached"]]}})
        bundle = self._bundle(payload)
        bundle.action_load()
        self.assertEqual(bundle.row_count, 1)
        self.assertEqual(bundle.table_ids.row_ids.source_id, 0)


@tagged("post_install", "-at_install")
class TestRehoming(TransactionCase):
    def setUp(self):
        super().setUp()
        if "helpdesk.ticket" not in self.env:
            self.skipTest("helpdesk_mgmt n'est pas installé sur cette base")
        self.bundle = self.env["bf.oe2oc.bundle"].create({
            "name": "Reprise d'essai",
            "file": encode(sample()),
        })
        self.bundle.action_load()
        self.tickets = self.bundle.table_ids.filtered(
            lambda t: t.name == "helpdesk_ticket")

    def test_rehome_creates_the_records(self):
        before = self.env["helpdesk.ticket"].search_count([])
        self.tickets.action_rehome()
        after = self.env["helpdesk.ticket"].search_count([])
        self.assertEqual(after - before, 2)
        self.assertEqual(self.tickets.state, "rehomed")
        self.assertEqual(self.tickets.rehomed_count, 2)

    def test_rows_point_at_what_they_became(self):
        self.tickets.action_rehome()
        row = self.tickets.row_ids.filtered(lambda r: r.source_id == 1)
        self.assertEqual(row.state, "rehomed")
        self.assertTrue(row.target_id)
        ticket = self.env["helpdesk.ticket"].browse(row.target_id)
        self.assertEqual(ticket.name, "Imprimante")

    def test_rehoming_twice_does_not_duplicate(self):
        self.tickets.action_rehome()
        count = self.env["helpdesk.ticket"].search_count([])
        self.tickets.action_rehome()
        self.assertEqual(self.env["helpdesk.ticket"].search_count([]), count)

    def test_a_table_without_handler_refuses_politely(self):
        social = self.bundle.table_ids.filtered(
            lambda t: t.name == "social_stream_post")
        with self.assertRaises(UserError) as caught:
            social.action_rehome()
        self.assertIn("social_stream_post", str(caught.exception))

    def test_preview_writes_nothing(self):
        before = self.env["helpdesk.ticket"].search_count([])
        action = self.tickets.action_preview()
        self.assertEqual(action["res_model"], "bf.oe2oc.preview")
        self.assertIn("Imprimante", action["context"]["default_body"])
        self.assertEqual(self.env["helpdesk.ticket"].search_count([]), before)

    def test_one_bad_row_does_not_cost_the_others(self):
        """Each row is created in its own savepoint.

        Patching `create` is the only deterministic way to fail exactly one
        row: anything data-driven either gets filtered out by the handler or
        gets accepted by Postgres.
        """
        Ticket = type(self.env["helpdesk.ticket"])
        original = Ticket.create

        def create(self, vals_list):
            wanted = vals_list if isinstance(vals_list, list) else [vals_list]
            if any(v.get("name") == "Imprimante" for v in wanted):
                raise ValueError("refus volontaire pour le test")
            return original(self, vals_list)

        self.patch(Ticket, "create", create)
        self.tickets.action_rehome()

        failed = self.tickets.row_ids.filtered(lambda r: r.state == "failed")
        rehomed = self.tickets.row_ids.filtered(lambda r: r.state == "rehomed")
        self.assertEqual(len(failed), 1, "le rang fautif devait échouer seul")
        self.assertEqual(len(rehomed), 1, "son voisin devait tout de même passer")
        self.assertIn("refus volontaire", failed.message)
        self.assertEqual(self.tickets.state, "partial")

    def test_bundle_closes_when_everything_mappable_is_done(self):
        self.bundle.action_rehome_all()
        self.assertEqual(self.bundle.state, "done")


@tagged("post_install", "-at_install")
class TestUnavailableTarget(TransactionCase):
    """A handler can point at a model this instance never installed.

    That is one table fewer to handle here, not a reason to refuse the whole
    batch -- which is what the first version did.
    """

    def setUp(self):
        super().setUp()
        if "helpdesk.ticket" not in self.env:
            self.skipTest("helpdesk_mgmt n'est pas installé sur cette base")
        payload = sample({
            "helpdesk_ticket": {
                "columns": ["id", "name", "description"], "row_count": 1,
                "truncated": False, "rows": [[1, "Billet", "<p>x</p>"]]},
            "sign_template": {
                "columns": ["id", "name"], "row_count": 1,
                "truncated": False, "rows": [[1, "Entente type"]]},
        })
        self.bundle = self.env["bf.oe2oc.bundle"].create({
            "name": "Cible manquante", "file": encode(payload)})
        self.bundle.action_load()
        self.sign = self.bundle.table_ids.filtered(
            lambda t: t.name == "sign_template")

    def test_availability_is_reported_per_table(self):
        tickets = self.bundle.table_ids.filtered(
            lambda t: t.name == "helpdesk_ticket")
        self.assertTrue(tickets.target_available)
        # bf_sign may or may not be installed; assert the flag matches reality.
        self.assertEqual(self.sign.target_available,
                         "bf.sign.field.template" in self.env)

    def test_batch_skips_the_unavailable_and_does_the_rest(self):
        if self.sign.target_available:
            self.skipTest("bf_sign est installé ici : rien à sauter")
        before = self.env["helpdesk.ticket"].search_count([])
        self.bundle.action_rehome_all()
        self.assertEqual(self.env["helpdesk.ticket"].search_count([]) - before, 1)
        self.assertEqual(self.sign.state, "kept")
        self.assertEqual(self.bundle.state, "done")

    def test_a_single_unavailable_table_says_so_plainly(self):
        if self.sign.target_available:
            self.skipTest("bf_sign est installé ici")
        with self.assertRaises(UserError) as caught:
            self.sign.action_rehome()
        self.assertIn("bf.sign.field.template", str(caught.exception))
