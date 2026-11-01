import json
import re
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import HttpCase, new_test_user, tagged

from odoo.addons.bf_property_access.tests.common import AccessCase
from odoo.addons.bf_property_lobby.models.bf_property_lobby_kiosk import _clean_number


@tagged("post_install", "-at_install")
class TestLobby(AccessCase, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.alice.mobile = "514 321-0101"
        cls.portal_alice = new_test_user(cls.env, login="alice_lobby", groups="base.group_portal",
                                         partner_id=cls.alice.id, password="alice_lobby-essai",
                                         tz="America/Toronto")

    def setUp(self):
        super().setUp()
        self.make_point()
        self.own(self.alice, self.unit1)
        self.own(self.bruno, self.unit2)
        self.sync()
        self.ra = self.resident(self.alice, self.unit1)
        self.rb = self.resident(self.bruno, self.unit2)
        self.kiosk = self.env["bf.property.lobby.kiosk"].with_user(self.manager).create(
            {"name": "Entrée nord", "building_id": self.building.id, "user_id": self.concierge.id})
        action = self.kiosk.action_pair()
        self.url = action["params"]["message"].split(self.kiosk.get_base_url(), 1)[1]
        self.token = self.url.rsplit("/", 1)[1]

    def rpc(self, path, **params):
        r = self.url_open(self.url + path, data=json.dumps({"jsonrpc": "2.0", "method": "call",
                                                            "params": params}),
                          headers={"Content-Type": "application/json"})
        return r.json()["result"]

    def test_directory_shows_only_consenting_residents_and_never_a_number(self):
        self.ra._bf_set_lobby(True, "A. Exemple", True)
        page = self.url_open(self.url).text
        self.assertIn("A. Exemple", page)
        self.assertNotIn("Bruno Exemple", page)
        self.assertNotIn("3210101", page.replace(" ", "").replace("-", ""))
        self.assertNotIn("Alice Exemple", page, "le libellé choisi, pas le nom au dossier")

    def test_wrong_token_or_archived_kiosk_is_not_found(self):
        self.assertEqual(self.url_open("/lobby/%d/%s" % (self.kiosk.id, "x" * 43)).status_code, 404)
        self.kiosk.with_user(self.manager).active = False
        self.assertEqual(self.url_open(self.url).status_code, 404)

    def test_call_notifies_the_responsible_without_a_phone_line(self):
        self.ra._bf_set_lobby(True, "A. Exemple", True)
        result = self.rpc("/call", resident_id=self.ra.id)
        self.assertEqual(result["status"], "notified")
        call = self.kiosk.call_ids
        self.assertEqual((call.resident_id, call.outcome), (self.ra, "notified"))
        self.assertEqual(self.kiosk.activity_ids.user_id, self.concierge)

    def test_calls_are_throttled(self):
        self.ra._bf_set_lobby(True, "A. Exemple", True)
        self.assertEqual(self.rpc("/call", resident_id=self.ra.id)["status"], "notified")
        self.assertEqual(self.rpc("/call", resident_id=self.ra.id)["status"], "throttled")
        for _i in range(6):
            self.rpc("/delivery")
        self.assertEqual(self.rpc("/delivery")["status"], "throttled")

    def test_unlisted_or_other_building_resident_cannot_be_called(self):
        self.assertEqual(self.rpc("/call", resident_id=self.rb.id)["status"], "refused")
        other = self.env["bf.property.building"].create({"name": "Ailleurs (banc)", "organisation_id": self.org.id})
        unit = self.env["bf.property.unit"].create({"name": "9", "building_id": other.id})
        self.own(self.chloe, unit)
        self.sync()
        rc = self.Resident.search([("partner_id", "=", self.chloe.id)])
        rc._bf_set_lobby(True, "C", True)
        self.assertEqual(self.rpc("/call", resident_id=rc.id)["status"], "refused")
        self.assertEqual(self.rpc("/call", resident_id="abc")["status"], "refused")

    def test_consent_is_given_by_the_resident_on_the_portal(self):
        self.authenticate("alice_lobby", "alice_lobby-essai")
        page = self.url_open("/my/property/access").text
        self.assertIn("Annuaire de l'entrée", page)
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        self.url_open("/my/property/access/lobby", data={
            "csrf_token": csrf, "resident_id": str(self.ra.id), "listed": "1",
            "label": "Famille Exemple", "reachable": "1"})
        self.assertTrue(self.ra.lobby_listed)
        self.assertEqual(self.ra.lobby_label, "Famille Exemple")
        self.assertTrue(self.ra.lobby_consent_date)
        self.url_open("/my/property/access/lobby", data={
            "csrf_token": csrf, "resident_id": str(self.rb.id), "listed": "1", "label": "Pirate"})
        self.assertFalse(self.rb.lobby_listed, "pas la fiche d'un autre")

    def test_reachability_needs_consent_and_a_valid_number(self):
        self.assertEqual(self.ra._bf_lobby_number(), "")
        self.ra._bf_set_lobby(True, "A", True)
        self.assertEqual(self.ra._bf_lobby_number(), "5143210101")
        self.ra._bf_set_lobby(False, "A", True)
        self.assertFalse(self.ra.lobby_reachable, "retiré de l'annuaire, plus joignable")
        self.assertEqual(_clean_number("+1 (514) 321-0101"), "5143210101")
        self.assertEqual(_clean_number("514 555-1212"), "")
        self.assertEqual(_clean_number("12"), "")

    def test_only_a_manager_pairs_and_the_token_is_never_readable(self):
        with self.assertRaises(AccessError):
            self.kiosk.with_user(self.concierge).action_pair()
        self.assertNotIn("token_hash", self.kiosk.with_user(self.manager).read()[0])
        self.kiosk.action_pair()
        self.assertEqual(self.url_open(self.url).status_code, 404, "l'ancienne adresse ne marche plus")

    def test_call_journal_is_purged(self):
        self.org.access_log_retention_days = 30
        self.rpc("/delivery")
        call = self.kiosk.call_ids
        self.env.cr.execute("UPDATE bf_property_lobby_call SET create_date = %s WHERE id = %s",
                            [fields.Datetime.now() - timedelta(days=31), call.id])
        self.env["bf.property.lobby.call"]._cron_purge()
        self.assertFalse(call.exists())

    # ── Relecture 1 du lot 6 (2026-10-31) ──

    def test_r1_i2_costly_or_foreign_numbers_are_never_dialled(self):
        for raw in ("1-900-555-0199", "1 876 555 0199", "000-000-0000", "911-555-1212", "514 976-1234"):
            self.assertEqual(_clean_number(raw), "", raw)
        self.assertEqual(_clean_number("438 321-0199"), "4383210199")

    def test_r1_i3_nobody_consents_for_the_resident(self):
        with self.assertRaises(UserError):
            self.ra.with_user(self.manager).write({"lobby_listed": True, "lobby_label": "Forcé"})
        self.ra.sudo().write({"lobby_listed": True, "lobby_label": "Sans date"})
        self.assertNotIn("Sans date", self.url_open(self.url).text, "pas de consentement daté, pas d'annuaire")

    def test_r1_i4_the_activity_names_the_unit_and_is_purged(self):
        self.org.access_log_retention_days = 30
        self.ra._bf_set_lobby(True, "A. Exemple", False)
        self.rpc("/call", resident_id=self.ra.id)
        act = self.kiosk.activity_ids
        self.assertIn("101", act.summary)
        self.assertNotIn("Alice", act.summary)
        self.assertNotIn("A. Exemple", act.summary)
        act.with_user(self.concierge).action_feedback(feedback="vu")
        msg = self.kiosk.message_ids.filtered("mail_activity_type_id")
        self.assertTrue(msg)
        self.env.cr.execute("UPDATE mail_message SET date = %s WHERE id IN %s",
                            [fields.Datetime.now() - timedelta(days=31), tuple(msg.ids)])
        self.env["bf.property.lobby.call"]._cron_purge()
        self.assertFalse(msg.exists())

    def test_r1_m4_one_open_activity_per_request(self):
        for _i in range(3):
            self.rpc("/delivery")
        self.assertEqual(len(self.kiosk.activity_ids), 1)

    def test_r1_i5_m2_page_refreshes_and_speaks_the_companys_language(self):
        self.building.company_id.partner_id.lang = "en_CA"
        page = self.url_open(self.url).text
        self.assertNotIn('http-equiv="refresh"', page, "une page d'erreur ne doit pas remplacer la tablette")
        self.assertIn("setInterval", page)
        self.assertIn('lang="en-CA"', page)

    def test_r1_m1_absurd_ids_never_return_a_stack(self):
        r = self.url_open(self.url + "/call", data='{"jsonrpc":"2.0","method":"call","params":{"resident_id":1e400}}',
                          headers={"Content-Type": "application/json"})
        body = r.json()
        self.assertEqual(body["result"]["status"], "refused")
        self.assertNotIn("debug", json.dumps(body))

    def test_r1_m5_unlisting_forgets_the_label(self):
        self.ra._bf_set_lobby(True, "A. Exemple", True)
        self.ra._bf_set_lobby(False, "A. Exemple", True)
        self.assertFalse(self.ra.lobby_label)

    def test_r1_m6_an_archived_tablet_still_lets_the_resident_withdraw(self):
        self.ra._bf_set_lobby(True, "A. Exemple", False)
        self.kiosk.with_user(self.manager).active = False
        self.authenticate("alice_lobby", "alice_lobby-essai")
        self.assertIn("Annuaire de l'entrée", self.url_open("/my/property/access").text)


    # ── Relecture 2 du lot 6 (2026-10-31) ──

    def test_r2_i2_creating_or_importing_cannot_consent(self):
        with self.assertRaises(UserError):
            self.Resident.with_user(self.manager).create({
                "partner_id": self.chloe.id, "building_id": self.building.id,
                "lobby_listed": True, "lobby_label": "Forcé"})

    def test_r2_m4_a_manager_withdraws_on_request_but_never_lists(self):
        self.ra._bf_set_lobby(True, "A. Exemple", True)
        self.ra.with_user(self.manager).action_lobby_withdraw()
        self.assertFalse(self.ra.lobby_listed)
        self.assertFalse(self.ra.lobby_label)
        with self.assertRaises(AccessError):
            self.rb.with_user(self.concierge).action_lobby_withdraw()

    def test_r2_m6_no_purge_is_said_plainly(self):
        self.org.access_log_retention_days = 0
        self.authenticate("alice_lobby", "alice_lobby-essai")
        page = self.url_open("/my/property/access").text
        self.assertNotIn("90 jours", page)

    def test_r2_activity_type_is_the_tablets_own(self):
        self.rpc("/delivery")
        self.assertEqual(self.kiosk.activity_ids.activity_type_id,
                         self.env.ref("bf_property_lobby.mail_activity_lobby"))


    # ── Défauts relevés par le guide (2026-10-31) ──

    def test_g_page_strings_are_translatable_and_doctype_kept(self):
        view = self.env.ref("bf_property_lobby.lobby_page")
        terms = view._fields["arch_db"].get_trans_terms(view.arch_db)
        for attendu in ("Appeler", "Livraison", "Nom ou logement", "Réessayez dans un instant."):
            self.assertIn(attendu, terms)
        self.assertTrue(self.url_open(self.url).text.lstrip().startswith("<!DOCTYPE html>"))

    def test_g_tab_only_where_a_tablet_exists(self):
        self.assertTrue(self.ra.lobby_available)
        other = self.env["bf.property.building"].create({"name": "Sans tablette",
                                                         "organisation_id": self.org.id})
        self.assertFalse(self.Resident.new({"building_id": other.id}).lobby_available)

    def test_g_efvp_names_the_tablet(self):
        extras = self.org._bf_access_efvp_extras()
        self.assertEqual(len(extras), 1)
        self.assertIn("Entrée nord", extras[0]["title"])


    # ── Option « Aussi par courriel » ──

    def lobby_mails(self):
        return self.env["mail.mail"].sudo().search([("model", "=", "bf.property.lobby.kiosk"),
                                                    ("res_id", "=", self.kiosk.id)])

    def test_e_no_email_unless_the_option_is_set(self):
        self.rpc("/delivery")
        self.assertTrue(self.kiosk.activity_ids)
        self.assertFalse(self.lobby_mails())

    def test_e_option_emails_the_responsible_once_with_the_unit_only(self):
        self.kiosk.with_user(self.manager).notify_by_email = True
        self.concierge.email = "concierge@example.com"
        self.ra._bf_set_lobby(True, "A. Exemple", False)
        self.rpc("/call", resident_id=self.ra.id)
        self.rpc("/call", resident_id=self.ra.id)
        mails = self.lobby_mails()
        self.assertEqual(len(mails), 1, "un courriel par activité nouvelle, pas par pression")
        self.assertIn("concierge@example.com", mails.email_to)
        self.assertIn(self.unit1.name, mails.subject)
        body = mails.body_html
        for absent in ("Alice", "A. Exemple", "3210101"):
            self.assertNotIn(absent, body)
        self.assertIn("/mail/view?model=bf.property.lobby.kiosk", body)
        self.assertFalse(mails.author_id == self.env.ref("base.public_partner"))


    # ── Mutantes survivantes de la QA du 2026-10-31 ──

    def test_m_l3_a_departed_resident_leaves_the_directory(self):
        self.ra._bf_set_lobby(True, "A. Exemple", True)
        self.ra.with_user(self.manager)._bf_confirm_departure(self.today)
        self.assertNotIn("A. Exemple", self.url_open(self.url).text)

    def test_m_l4_no_call_without_a_dated_consent(self):
        self.ra.sudo().write({"lobby_listed": True, "lobby_label": "Sans date"})
        self.assertEqual(self.rpc("/call", resident_id=self.ra.id)["status"], "refused")
        self.assertFalse(self.kiosk.call_ids)

    def test_m_l5_the_seventh_request_of_the_minute_waits(self):
        for _i in range(6):
            self.assertEqual(self.rpc("/delivery")["status"], "notified")
        self.assertEqual(self.rpc("/delivery")["status"], "throttled")

    def test_m_l9_the_activity_speaks_the_responsibles_language(self):
        self.concierge.lang = "fr_CA"
        # La tablette parle anglais (langue de la requête) ; le responsable lit le français.
        self.kiosk.sudo().with_context(lang="en_CA")._bf_delivery()
        summary = self.kiosk.activity_ids.summary
        self.assertTrue(summary.startswith("Livraison"), summary)

    def test_m_l10_the_purge_spares_the_tablets_other_activities(self):
        self.org.access_log_retention_days = 30
        other = self.kiosk.activity_schedule("mail.mail_activity_data_todo", user_id=self.manager.id,
                                             summary="Changer la pile")
        self.env.cr.execute("UPDATE mail_activity SET create_date = %s WHERE id = %s",
                            [fields.Datetime.now() - timedelta(days=31), other.id])
        self.env["bf.property.lobby.call"]._cron_purge()
        self.assertTrue(other.exists())

    def test_m_l11_a_retention_of_zero_never_purges(self):
        self.org.access_log_retention_days = 0
        self.rpc("/delivery")
        call = self.kiosk.call_ids
        self.env.cr.execute("UPDATE bf_property_lobby_call SET create_date = %s WHERE id = %s",
                            [fields.Datetime.now() - timedelta(days=400), call.id])
        self.env["bf.property.lobby.call"]._cron_purge()
        self.assertTrue(call.exists())

    def test_m_l12_premium_or_caribbean_numbers_are_refused_outside_555(self):
        for raw in ("1-900-321-0199", "1 876 321 0199", "1 242 321 0199"):
            self.assertEqual(_clean_number(raw), "", raw)

    def test_m_l15_the_tablet_page_is_neither_indexed_nor_cached(self):
        r = self.url_open(self.url)
        self.assertEqual(r.headers.get("X-Robots-Tag"), "noindex, nofollow")
        self.assertEqual(r.headers.get("Cache-Control"), "no-store")
        self.assertEqual(r.headers.get("Referrer-Policy"), "no-referrer")


    # ── Défauts trouvés au parcours joué au navigateur (2026-11-01) ──

    def test_p_answers_follow_the_company_not_the_browser(self):
        self.env["res.lang"]._activate_lang("en_CA")
        self.kiosk.sudo().company_id.partner_id.lang = "en_US"  # sans catalogue : la source française
        r = self.url_open(self.url + "/delivery", data=json.dumps({"jsonrpc": "2.0", "method": "call", "params": {}}),
                          headers={"Content-Type": "application/json", "Accept-Language": "en-CA"})
        self.assertNotIn("notified", r.json()["result"]["message"].lower(),
                         "la réponse suit la langue de la société, pas celle du navigateur")

    def test_p_manager_withdrawal_posts_one_true_message(self):
        self.ra._bf_set_lobby(True, "A. Exemple", False)
        before = len(self.ra.message_ids)
        self.ra.with_user(self.manager).action_lobby_withdraw()
        new = self.ra.message_ids[: len(self.ra.message_ids) - before]
        self.assertEqual(len(new), 1)
        self.assertNotIn("au portail", new.body)
        self.assertIn("demande du résident", new.body)
