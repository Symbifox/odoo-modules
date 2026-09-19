# Part of bf_oe2oc. See LICENSE file for full copyright and licensing details.
"""Tests for the post-migration checks.

Each check has to be able to say "I looked and found something" as clearly as
"I looked and found nothing" -- and, when it could not look at all, to say that
instead of quietly passing.
"""

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestChecks(TransactionCase):
    def _check(self, key):
        check = self.env["bf.oe2oc.check"].search([("key", "=", key)], limit=1)
        self.assertTrue(check, f"le contrôle « {key} » n'est pas installé")
        return check

    # ── web.base.url ──────────────────────────────────────────────────────

    def test_base_url_flags_the_neutralized_value(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "web.base.url", "http://localhost:8069")
        check = self._check("base_url")
        check.action_run()
        self.assertEqual(check.state, "warn")
        self.assertIn("localhost", check.result)
        self.assertTrue(check.checked_on)

    def test_base_url_passes_on_a_real_domain(self):
        param = self.env["ir.config_parameter"].sudo()
        param.set_param("web.base.url", "https://erp.exemple.ca")
        param.set_param("web.base.url.freeze", "False")
        check = self._check("base_url")
        check.action_run()
        self.assertEqual(check.state, "ok")

    def test_base_url_flags_a_frozen_url(self):
        param = self.env["ir.config_parameter"].sudo()
        param.set_param("web.base.url", "https://erp.exemple.ca")
        param.set_param("web.base.url.freeze", "True")
        check = self._check("base_url")
        check.action_run()
        self.assertEqual(check.state, "warn")
        # Le nom du paramètre traverse les langues ; le mot « gelé » non.
        self.assertIn("web.base.url.freeze", check.result)

    # ── Langues ───────────────────────────────────────────────────────────

    def test_languages_flags_a_code_used_but_inactive(self):
        partner = self.env["res.partner"].create({"name": "Contact"})
        # A language code nobody activated, carried by a real record.
        self.env.cr.execute(
            "UPDATE res_partner SET lang = %s WHERE id = %s", ("nl_NL", partner.id))
        self.env["res.partner"].invalidate_model(["lang"])
        check = self._check("languages")
        check.action_run()
        self.assertEqual(check.state, "warn")
        self.assertIn("nl_NL", check.result)

    # ── Séquences ─────────────────────────────────────────────────────────

    def test_sequences_notices_one_left_behind(self):
        partner = self.env["res.partner"].create({"name": "Repère"})
        self.env.cr.execute(
            "SELECT setval(pg_get_serial_sequence('res_partner', 'id'), %s, true)",
            (max(partner.id - 5, 1),))
        check = self._check("sequences")
        check.action_run()
        self.assertEqual(check.state, "warn")
        self.assertIn("res_partner", check.result)

    def test_sequences_passes_once_caught_up(self):
        self.env.cr.execute(
            "SELECT setval(pg_get_serial_sequence('res_partner', 'id'), "
            "(SELECT MAX(id) FROM res_partner), true)")
        check = self._check("sequences")
        check.action_run()
        # Other tables in the list may legitimately be behind on a test DB, so
        # assert on the one we just fixed rather than on the overall verdict.
        self.assertNotIn("res_partner (", check.result or "")

    # ── Serveurs d'envoi ──────────────────────────────────────────────────

    def test_mail_servers_flags_an_active_one(self):
        self.env["ir.mail_server"].create({
            "name": "Sortie d'essai", "smtp_host": "smtp.exemple.ca"})
        check = self._check("mail_servers")
        check.action_run()
        self.assertEqual(check.state, "warn")

    # ── Reprise ───────────────────────────────────────────────────────────

    def test_leftovers_warns_when_nothing_was_deposited(self):
        self.env["bf.oe2oc.bundle"].search([]).unlink()
        check = self._check("leftovers")
        check.action_run()
        self.assertEqual(check.state, "warn")
        self.assertIn("enterprise-leftovers.json", check.result)

    # ── Mécanique commune ─────────────────────────────────────────────────

    def test_manual_check_is_not_run_automatically(self):
        check = self._check("dbfilter")
        self.assertFalse(check.automatic)
        check.action_run()
        self.assertEqual(check.state, "todo")
        self.assertFalse(check.checked_on)

    def test_manual_check_can_be_marked_done(self):
        check = self._check("dbfilter")
        check.action_mark_done()
        self.assertEqual(check.state, "done")
        self.assertTrue(check.checked_on)

    def test_an_unknown_key_reports_unverifiable_rather_than_passing(self):
        check = self.env["bf.oe2oc.check"].create({
            "name": "Contrôle sans code", "key": "cle_inexistante"})
        check.action_run()
        self.assertEqual(check.state, "na")

    def test_run_all_touches_every_automatic_check(self):
        checks = self.env["bf.oe2oc.check"].search([("automatic", "=", True)])
        checks.write({"state": "todo", "checked_on": False})
        self.env["bf.oe2oc.check"].action_run_all()
        self.assertFalse(checks.filtered(lambda c: not c.checked_on))


@tagged("post_install", "-at_install")
class TestInvalidLocales(TransactionCase):
    def _check(self):
        return self.env["bf.oe2oc.check"].search(
            [("key", "=", "invalid_locales")], limit=1)

    def test_flags_a_timezone_that_does_not_exist(self):
        partner = self.env["res.partner"].create({"name": "Contact"})
        # The constat lists the first few offenders alphabetically, so a value
        # that sorts to the front is the one that proves detection on a
        # database that may already carry others.
        self.env.cr.execute(
            "UPDATE res_partner SET tz = %s WHERE id = %s",
            ("Antarctica/Nulle_Part", partner.id))
        self.env["res.partner"].invalidate_model(["tz"])
        check = self._check()
        check.action_run()
        self.assertEqual(check.state, "warn")
        self.assertIn("res.partner.tz", check.result)
        self.assertIn("Antarctica/Nulle_Part", check.result)

    def test_flags_a_language_that_does_not_exist(self):
        partner = self.env["res.partner"].create({"name": "Contact"})
        self.env.cr.execute(
            "UPDATE res_partner SET lang = %s WHERE id = %s", ("Aa_AA", partner.id))
        self.env["res.partner"].invalidate_model(["lang"])
        check = self._check()
        check.action_run()
        self.assertEqual(check.state, "warn")
        self.assertIn("res.partner.lang", check.result)
        self.assertIn("Aa_AA", check.result)

    def test_an_inactive_but_real_language_is_not_flagged_here(self):
        """That is the other check's job, and it is a lesser problem."""
        lang = self.env["res.lang"].with_context(active_test=False).search(
            [("active", "=", False)], limit=1)
        if not lang:
            self.skipTest("toutes les langues sont actives sur cette base")
        partner = self.env["res.partner"].create({"name": "Contact"})
        self.env.cr.execute(
            "UPDATE res_partner SET lang = %s WHERE id = %s", (lang.code, partner.id))
        self.env["res.partner"].invalidate_model(["lang"])
        check = self._check()
        check.action_run()
        self.assertNotIn(lang.code, check.result or "")
