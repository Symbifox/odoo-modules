"""The register says what left, says what stayed, and refuses to say it when it did not.

⚠️ The register's records are written in English like the rest of the module, and read in
each person's language through i18n/fr_CA.po: a destruction entry lands in a client's legal
register, and a French-only bridge would put French entries in an English-speaking
client's register.
"""
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from odoo.addons.bf_route.tests.common import MONDAY, RouteCase

HERE = {"latitude": 45.52, "longitude": -73.58, "accuracy": 12}


@tagged("post_install", "-at_install")
class TestRoutePrivacy(RouteCase):

    def setUp(self):
        super().setUp()
        self.Register = self.env["privacy.destruction.register"]
        self.company.bf_route_position_mode = "stops"
        self.env["bf.route.notice.ack"].with_user(self.worker).app_acknowledge(
            self.company.bf_route_notice_version)

    def marked_long_ago(self, days=91):
        day = self.make_day()
        day.with_user(self.worker).app_start()
        stop = day.stop_ids.sorted("sequence")[0]
        stop.with_user(self.worker).app_mark("done", position=HERE)
        stop.done_at = fields.Datetime.now() - timedelta(days=days)
        return stop

    # ------------------------------------------------------------------
    # What the register declares
    # ------------------------------------------------------------------
    def test_the_purpose_and_the_rule_are_in_the_register(self):
        purpose = self.env.ref("bf_route_privacy.purpose_route_position")
        self.assertFalse(purpose.requires_consent,
                         "s. 8.1 asks for information, not for consent")
        policy = self.env.ref("bf_route_privacy.retention_policy_route_position")
        self.assertEqual(policy.retention_days, 90)
        calendar = self.env.ref("bf_route_privacy.retention_route_position")
        self.assertEqual(calendar.code, "ROU-001")
        self.assertEqual(calendar.applies_to_model, "bf.route.day.stop")
        self.assertIn("8.1", calendar.legal_basis)

    def test_the_calendar_rule_is_written_in_the_language_of_the_register(self):
        """🔴 ROU-001 was the only English rule among 26 French ones, and unfixable.

        `privacy.retention.calendar` carries no `translate=True` field: not `name`, not
        `description`, not `legal_basis`, not `notes`. No catalogue can reach them, and the
        module's `.pot` holds no entry for the model. An English rule in that register would
        have been the one about employees' GPS, in the document a privacy officer shows an
        inspector. Same for the retention policy's own text fields.
        """
        calendar = self.env.ref("bf_route_privacy.retention_route_position")
        self.assertEqual(calendar.name, "Position relevée à l'arrêt d'une route")
        self.assertIn("au plus 90 jours", calendar.description)
        self.assertIn("LPRPSP", calendar.legal_basis)
        policy = self.env.ref("bf_route_privacy.retention_policy_route_position")
        self.assertTrue(policy.data_categories.startswith("Latitude, longitude et précision"))

    def test_the_kilometres_derived_from_the_readings_carry_their_own_rule(self):
        """🔴 The first writing said the figure "follows the company's kilometre register".

        No rule of that calendar covered vehicles or route days, so
        the sentence pointed at nothing, which is the defect this module refuses elsewhere
        with another hat on. Six years, the term the tax law sets for books and records.
        """
        rule = self.env.ref("bf_route_privacy.retention_route_road_measured")
        self.assertEqual(rule.code, "ROU-002")
        self.assertEqual(rule.active_retention_years, 6)
        self.assertEqual(rule.applies_to_model, "bf.route.day")
        self.assertEqual(rule.document_type, "tax")
        self.assertFalse(rule.company_id)
        # ⚠️ And it says out loud that nothing applies it yet, rather than implying it does.
        self.assertIn("Aucune automatisation", rule.notes)
        policy = self.env.ref("bf_route_privacy.retention_policy_route_road_measured")
        self.assertEqual(policy.retention_days, 2190)
        self.assertFalse(policy.company_id)

    def test_the_three_uses_are_each_declared(self):
        """🔴 The kilometres measured from the readings were nowhere in the register.

        Two purposes were declared: the reading itself, and placing a customer from it. The
        day's kilometres are a third use of the same readings, with its own end (comparing
        what is claimed with what was driven, in a register a tax authority reads), and a
        use nobody declared is a use nobody can object to.
        """
        codes = {purpose.code for purpose in self.env["privacy.purpose"].search(
            [("code", "like", "route_")])}
        self.assertIn("route_position", codes)
        self.assertIn("route_place_from_position", codes)
        self.assertIn("route_road_measured", codes)
        measured = self.env.ref("bf_route_privacy.purpose_route_road_measured")
        self.assertIn("distance, not a place", measured.plain_language_summary)

    def test_the_register_records_belong_to_no_single_company(self):
        """🔴 The watcher sweeps EVERY company, archived ones included.

        Created without company_id in 18.0.1.0.0, the four records landed on whichever
        company happened to be current at install time, whichever that was.
        The four other companies would then have had their positions erased under a rule
        their own register could not show.
        """
        for xmlid in ("purpose_route_position", "purpose_route_place_from_position",
                      "purpose_route_road_measured", "retention_policy_route_position",
                      "retention_route_position"):
            record = self.env.ref("bf_route_privacy.%s" % xmlid)
            self.assertFalse(record.company_id, "%s belongs to one company" % xmlid)

    # ------------------------------------------------------------------
    # The declared term and the applied term
    # ------------------------------------------------------------------
    def test_a_company_cannot_keep_positions_longer_than_declared(self):
        """The only direction that makes the declaration false."""
        with self.assertRaises(ValidationError):
            self.company.bf_route_position_days = 365

    def test_keeping_them_for_less_is_minimisation_and_needs_nothing(self):
        """⚠️ The rule is a ceiling, not a floor, and an earlier form got that wrong.

        Section 23 of Law 25 asks that information be destroyed once the purpose it was
        collected for is accomplished. Destroying sooner cannot breach that, and the entry
        names the term it actually applied, so the register stays true. Demanding equality
        refused a company doing MORE than it had promised, and told an administrator to go
        and edit a rule shared by five companies, which would have falsified it for the four
        others. The cheapest way out of a refusal nobody can act on is to leave the whole
        feature off.
        """
        self.company.bf_route_position_days = 1
        self.assertEqual(self.company.bf_route_position_days, 1)
        self.company.bf_route_position_days = 30
        self.assertEqual(self.company.bf_route_position_days, 30)

    def test_turning_positions_on_under_the_declared_term_is_accepted(self):
        other = self.env["res.company"].create({"name": "Thirty Co",
                                                "bf_route_position_days": 30})
        other.bf_route_position_mode = "stops"
        self.assertEqual(other.bf_route_position_mode, "stops")

    def test_turning_positions_on_above_the_declared_term_is_refused(self):
        other = self.env["res.company"].create({"name": "Greedy Co",
                                                "bf_route_position_days": 400})
        with self.assertRaises(ValidationError):
            other.bf_route_position_mode = "stops"

    def test_a_company_that_reads_no_position_is_left_alone(self):
        """Nothing is declared about a company that reads none, so nothing is constrained."""
        other = self.env["res.company"].create({"name": "Off Co"})
        other.bf_route_position_days = 3000
        self.assertEqual(other.bf_route_position_days, 3000)

    def test_nothing_declared_means_nothing_read(self):
        """🔴 A missing, archived or zero-day policy let the check pass in silence.

        The processing then began with an empty register, which is the one thing this module
        exists to prevent. Same doctrine as the destruction entry: better a feature
        postponed than an attestation nobody can back.
        """
        policy = self.env.ref("bf_route_privacy.retention_policy_route_position").sudo()
        other = self.env["res.company"].create({"name": "Undeclared Co",
                                                "bf_route_position_days": 30})
        policy.active = False
        with self.assertRaises(ValidationError, msg="an archived policy declares nothing"):
            other.bf_route_position_mode = "stops"
        policy.active = True
        policy.retention_days = 0
        with self.assertRaises(ValidationError, msg="and neither does a zero-day one"):
            other.bf_route_position_mode = "stops"
        policy.retention_days = 90
        other.bf_route_position_mode = "stops"
        self.assertEqual(other.bf_route_position_mode, "stops",
                         "and it goes through once a term is declared")

    # ------------------------------------------------------------------
    # What an erasure writes
    # ------------------------------------------------------------------
    def test_an_erasure_leaves_an_entry_in_partial_scope(self):
        stop = self.marked_long_ago()
        before = self.Register.search_count([])
        self.env["bf.route.day.stop"]._purge_positions()
        entry = self.Register.search([("res_model", "=", "bf.route.day.stop"),
                                      ("res_id", "=", stop.id)])
        self.assertEqual(self.Register.search_count([]), before + 1)
        self.assertEqual(entry.destruction_scope, "partial",
                         "the stop lives on, short of its position")
        self.assertEqual(entry.res_field, "latitude, longitude, accuracy")
        self.assertEqual(entry.destruction_method, "delete")
        self.assertEqual(entry.retention_calendar_id,
                         self.env.ref("bf_route_privacy.retention_route_position"))
        self.assertIn("8.1", entry.legal_basis)
        self.assertTrue(entry.verification_hash, "the entry is sealed")

    def test_the_entry_names_everything_that_outlives_the_position(self):
        """🔴 An entry that lists only what was destroyed reads as though nothing was left.

        Two survivors were missing, and both are derived from the readings rather than from
        the stop: the kilometres measured for the day, which stay on the day and are printed
        in the kilometre register, and a customer's place accepted from a reading, which is
        never erased because it has become the customer's own address.
        """
        stop = self.marked_long_ago()
        self.env["bf.route.day.stop"]._purge_positions()
        entry = self.Register.search([("res_model", "=", "bf.route.day.stop"),
                                      ("res_id", "=", stop.id)])
        said = entry.document_description
        self.assertIn("Not covered, and still held", said)
        self.assertIn("notice", said, "the proof of the prior information of s. 8.1")
        self.assertIn("kilometres of the day", said)
        self.assertIn("customer's place", said)
        # ⚠️ Naming a survivor without naming the regime it falls under leaves the reader
        # where the first writing of this module left them: told that something is kept,
        # with no way to find out for how long or under what rule. Both codes appear, the
        # one the erasure cites and the one its derivative falls under.
        self.assertIn("ROU-001", said)
        self.assertIn("ROU-002", said)

    def test_the_entry_names_the_term_that_was_applied(self):
        stop = self.marked_long_ago()
        self.env["bf.route.day.stop"]._purge_positions()
        entry = self.Register.search([("res_model", "=", "bf.route.day.stop"),
                                      ("res_id", "=", stop.id)])
        self.assertIn("90 day(s)", entry.document_description)
        self.assertIn("ROU-001", entry.document_description)

    def test_the_sealed_time_says_which_clock_it_is_on(self):
        """⚠️ An unlabelled timestamp is UTC: a stop marked at 14:00 Eastern sealed as 18:00.

        The entry is immutable, so this is the last place in the module to stop being
        careful about time zones, and it has to be fixed before the first sealing.
        """
        stop = self.marked_long_ago()
        self.env["bf.route.day.stop"]._purge_positions()
        entry = self.Register.search([("res_model", "=", "bf.route.day.stop"),
                                      ("res_id", "=", stop.id)])
        said = entry.document_description
        tz = self.route.sudo()._tz()
        self.assertIn("(%s)" % tz, said, "the clock is named")
        self.assertIn(fields.Datetime.context_timestamp(
            stop.sudo().with_context(tz=str(tz)), stop.done_at).strftime("%Y-%m-%d %H:%M:%S"),
            said)

    def test_nothing_erased_writes_nothing(self):
        before = self.Register.search_count([])
        self.env["bf.route.day.stop"]._purge_positions()
        self.assertEqual(self.Register.search_count([]), before,
                         "an empty pass does not write to the register")

    # ------------------------------------------------------------------
    # The refusal, and what it costs
    # ------------------------------------------------------------------
    def test_a_position_still_there_is_not_attested(self):
        """The guard: no attestation without an erasure."""
        stop = self.marked_long_ago()
        before = self.Register.search_count([])
        with self.assertRaises(UserError) as caught:
            stop._bf_route_positions_erased(self.company, 90)
        # ⚠️ It has to be THIS module's guard that fires, named in the message. The base
        # register has a guard of its own, so an assertion that merely says "something
        # raised" passes even with this one removed, and the register would then be the only
        # thing standing between a live position and a certificate of its destruction.
        said = str(caught.exception)
        self.assertIn("refuses to attest", said)
        self.assertIn(stop.display_name, said)
        self.assertEqual(self.Register.search_count([]), before)

    def test_the_erasure_mark_is_required(self):
        stop = self.marked_long_ago()
        stop.sudo().with_context(bf_route_auto=True).write(
            {"latitude": 0.0, "longitude": 0.0, "accuracy": 0.0})
        with self.assertRaises(UserError):
            stop._bf_route_positions_erased(self.company, 90)

    def stale_stop_elsewhere(self):
        """A stop with an expired position, in a second company.

        \u26a0\ufe0f Written directly rather than marked from the phone: what is under test is the
        purge's isolation between companies, not the marking path, and a second company
        would otherwise need its own mode, its own notice and its own acknowledgement.
        """
        elsewhere = self.env["res.company"].create({"name": "Elsewhere Inc"})
        # The truck follows the route: a vehicle of the old company would be refused.
        self.truck.company_id = False
        route = self.route.sudo().copy({"name": "Elsewhere route"})
        route.company_id = elsewhere
        day = self.env["bf.route.day"].sudo().with_context(
            bf_route_copy_stops=True).create({"route_id": route.id, "date": MONDAY})
        stop = day.stop_ids.sorted("sequence")[0]
        stop.with_context(bf_route_auto=True).write({
            "latitude": 45.1, "longitude": -73.1, "accuracy": 10,
            "done_at": fields.Datetime.now() - timedelta(days=91)})
        self.assertEqual(stop.company_id, elsewhere)
        return stop

    def test_one_company_s_refusal_does_not_keep_the_others_positions(self):
        """\U0001f534 The purge used to run every company inside ONE savepoint.

        This bridge raises on purpose when it cannot verify an erasure, and `_cron_watch`
        isolates the JOB, not the company: a single unverifiable stop therefore undid the
        erasure of every company, including the ones already written and checked. Every
        fifteen minutes, silently, for as long as that one stop stayed unverifiable.
        """
        mine = self.marked_long_ago()
        theirs = self.stale_stop_elsewhere()
        original = type(self.env["bf.route.day.stop"])._bf_route_positions_erased

        def refuse_for_my_company(records, company, days):
            if company == self.company:
                raise UserError("the register cannot attest this one")
            return original(records, company, days)

        with patch.object(type(self.env["bf.route.day.stop"]),
                          "_bf_route_positions_erased", refuse_for_my_company):
            self.env["bf.route.day.stop"]._purge_positions()
        self.assertTrue(mine.sudo().latitude,
                        "the refused company keeps its position, as it must")
        self.assertFalse(mine.sudo().position_erased,
                         "and nothing claims it was erased")
        self.assertFalse(theirs.sudo().latitude,
                         "the other company's position left all the same")
        self.assertTrue(self.Register.search_count(
            [("res_model", "=", "bf.route.day.stop"), ("res_id", "=", theirs.id)]),
            "and its erasure is in the register")

    def test_every_model_a_rule_names_is_classifiable(self):
        """A rule whose model cannot be classified is a rule nobody can attach to a record.

        ROU-001 names `bf.route.day.stop`, ROU-002 names `bf.route.day`, and the notice as
        read is the proof of the prior information: all three are classifiable.
        """
        models = self.env["privacy.document.classification"]._privacy_classifiable_models()
        self.assertIn("bf.route.day.stop", models)
        self.assertIn("bf.route.day", models)
        self.assertIn("bf.route.notice.ack", models)
        for xmlid in ("retention_route_position", "retention_route_road_measured"):
            rule = self.env.ref("bf_route_privacy.%s" % xmlid)
            self.assertIn(rule.applies_to_model, models,
                          "%s names a model nobody can classify" % rule.code)

    def test_the_notice_as_read_outlives_the_position(self):
        """The proof of the prior information does not die with what it made lawful."""
        self.marked_long_ago()
        self.env["bf.route.day.stop"]._purge_positions()
        self.assertTrue(self.env["bf.route.notice.ack"].search_count(
            [("user_id", "=", self.worker.id)]))
