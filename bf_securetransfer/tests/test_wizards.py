"""The two backend wizards: the secure-send composer and the journaled reveal.

Both are pure operator surface, and both hold a piece of the module's
confidentiality story:

* the send wizard is the only backend way to originate an OTP-gated message —
  it must refuse bad recipients BEFORE a transfer exists, honour the user
  group, and stamp the provenance the Loi 25 trail is read from;
* the reveal wizard is the only sanctioned path to the share token. The token
  IS the capability: merely opening the wizard must reveal nothing and journal
  nothing, and only the explicit confirmation may disclose it.

Nothing here touches the network: S3, SMTP and VoIP.ms are patched out.
"""
import ast
from contextlib import contextmanager
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from .common import LanguesActives

S3_MOD = "odoo.addons.bf_securetransfer.models.s3"
SMS_MOD = "odoo.addons.bf_securetransfer.models.sms"
MAIL_SEND = "odoo.addons.mail.models.mail_mail.MailMail.send"


@tagged("post_install", "-at_install")
class TestWizards(LanguesActives, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.brand = cls.env.ref("bf_securetransfer.brand_default")
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_ip", "500")
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_sender", "500")
        icp.set_param("bf_securetransfer.quota_daily_bytes_per_ip_mb", "1000000")

        cls.g_internal = cls.env.ref("base.group_user")
        cls.g_user = cls.env.ref("bf_securetransfer.group_securetransfer_user")
        cls.g_manager = cls.env.ref(
            "bf_securetransfer.group_securetransfer_manager")
        # The test's own user drives both wizards unless a test needs a
        # specific set of rights.
        cls.env.user.groups_id = [(4, cls.g_user.id), (4, cls.g_manager.id)]

        def _mk(login, groups, lang="en_US"):
            return cls.env["res.users"].create({
                "name": login, "login": login, "lang": lang,
                "email": "%s@example.com" % login,
                "groups_id": [(6, 0, [g.id for g in groups])],
            })

        # Sends: has the securetransfer user group; his language is NOT one of
        # the transfer locales (fallback case).
        cls.sender = _mk("st-wiz-sender", [cls.g_internal, cls.g_user])
        # Same rights, but a language the module actually ships.
        cls.sender_ca = _mk("st-wiz-sender-ca", [cls.g_internal, cls.g_user],
                            lang="en_CA")
        # Plain employee: no securetransfer right at all.
        cls.outsider = _mk("st-wiz-outsider", [cls.g_internal])
        # Reveal: manager.
        cls.manager = _mk("st-wiz-manager", [cls.g_internal, cls.g_manager])
        # A securetransfer user who is NOT a manager.
        cls.plain_user = _mk("st-wiz-plain", [cls.g_internal, cls.g_user])

    # ------------------------------------------------------------------ helpers
    def _refused(self, fn, *args, **kwargs):
        """Assert the call is refused with a UserError, and hand the exception
        back for message assertions.

        NB: plain try/except, never assertRaises — Odoo's assertRaises rolls
        its block back into a ``flush=False`` savepoint, which also discards
        the records the test created before entering it.
        """
        try:
            fn(*args, **kwargs)
        except UserError as exc:
            return exc
        self.fail("the call should have been refused")

    @contextmanager
    def _offline(self):
        """No SMTP, no S3, no VoIP.ms — the suite must run on an isolated box."""
        with patch(MAIL_SEND, lambda self, *a, **k: True), \
                patch(S3_MOD + ".head_object", return_value=None), \
                patch(S3_MOD + ".delete_keys", return_value=[]), \
                patch(SMS_MOD + ".send", return_value=False):
            yield

    _n = 0

    def _addr(self):
        """A unique recipient address per call."""
        type(self)._n += 1
        return "wiz-dest-%d@example.test" % self._n

    def _wizard(self, user=None, **overrides):
        vals = {
            "brand_id": self.brand.id,
            "message": "CONTENU-SENSIBLE",
            "otp_channel": "email",
            "sender_name": "Expéditeur test",
            "sender_email": "expediteur@example.test",
            "retention_days": 7,
        }
        vals.update(overrides)
        model = self.env["secure.transfer.send.wizard"]
        if user:
            model = model.with_user(user)
        return model.create(vals)

    def _brand(self, **overrides):
        vals = {
            "name": "Marque wizard",
            "company_id": self.brand.company_id.id,
        }
        vals.update(overrides)
        return self.env["secure.transfer.brand"].create(vals)

    def _transfer(self, **overrides):
        vals = {
            "sender_name": "Test Sender",
            "sender_email": "sender@example.com",
            "recipient_emails": self._addr(),
            "message": "Bonjour",
            "retention_days": 7,
        }
        vals.update(overrides)
        return self.env["secure.transfer"].api_create(
            self.brand, vals, "203.0.113.10", "test-suite/1.0", "fr_CA",
        )

    # ============================================================== send wizard
    # ------------------------------------------------------------------ defaults
    def test_default_get_falls_back_to_the_first_eligible_brand(self):
        """With no brand flagged as default, the composer must still open on a
        usable brand: an empty required brand_id greets the operator with a
        blank form he cannot submit."""
        Brand = self.env["secure.transfer.brand"]
        Brand.search([("is_default", "=", True)]).write({"is_default": False})
        # A drop page sorts first but is NOT eligible…
        self._brand(name="Dépôt wizard", slug="depot-wizard-defaut",
                    fixed_recipient="depot@example.com",
                    sequence=-20)
        # …so the first eligible general brand must be picked instead.
        general = self._brand(name="Générale wizard",
                              domain="wiz-general.example.test", sequence=-10)
        res = self.env["secure.transfer.send.wizard"].default_get(["brand_id"])
        self.assertEqual(res.get("brand_id"), general.id)

    def test_brand_field_domain_excludes_drop_pages(self):
        """A drop-page brand forces every send to its own owner. Offering it in
        the composer would silently redirect an operator's message to that
        person instead of the contacts he picked — and leave the general brand
        unreachable in a tenant that has both."""
        field = self.env["secure.transfer.send.wizard"]._fields["brand_id"]
        self.assertIn("fixed_recipient", field.domain or "")
        drop = self._brand(name="Dépôt wizard 2", slug="depot-wizard-domaine",
                           fixed_recipient="depot@example.com")
        eligible = self.env["secure.transfer.brand"].search(
            ast.literal_eval(field.domain))
        self.assertNotIn(drop, eligible)
        self.assertIn(self.brand, eligible, "the general brand must remain offered")

    def test_default_get_prefills_partners_from_active_ids(self):
        """Launched from a contact list, the composer must carry the selection
        over; otherwise the operator retypes recipients by hand and the whole
        "send securely from the contact you are looking at" flow is lost."""
        p1 = self.env["res.partner"].create({
            "name": "Wiz A", "email": "wiz-a@example.test"})
        p2 = self.env["res.partner"].create({
            "name": "Wiz B", "email": "wiz-b@example.test"})
        res = self.env["secure.transfer.send.wizard"].with_context(
            active_model="res.partner", active_ids=[p1.id, p2.id],
        ).default_get(["brand_id", "partner_ids"])
        self.assertEqual(res.get("partner_ids"), [(6, 0, [p1.id, p2.id])])

    def test_default_get_prefills_partner_from_a_single_active_id(self):
        """A contact FORM sends active_id only (no active_ids): reading just
        active_ids left the one-contact case — the common one — empty."""
        p = self.env["res.partner"].create({
            "name": "Wiz Solo", "email": "wiz-solo@example.test"})
        res = self.env["secure.transfer.send.wizard"].with_context(
            active_model="res.partner", active_id=p.id,
        ).default_get(["brand_id", "partner_ids"])
        self.assertEqual(res.get("partner_ids"), [(6, 0, [p.id])])

    def test_default_get_ignores_a_foreign_active_model(self):
        """Opened from any other record (a task, an invoice), the active_id
        must NOT be read as a contact id — that would prefill a random partner
        as recipient of a sensitive message."""
        transfer = self._transfer()
        res = self.env["secure.transfer.send.wizard"].with_context(
            active_model="secure.transfer", active_id=transfer.id,
        ).default_get(["brand_id", "partner_ids"])
        self.assertFalse(res.get("partner_ids"))

    # ------------------------------------------------------------------ sms hint
    def test_sms_configured_flag_follows_the_configuration(self):
        """The form hides the SMS channel behind this flag. Stuck True on an
        unconfigured tenant, the operator picks SMS and the send blows up at
        the last step; stuck False, a configured tenant loses the channel."""
        wiz = self._wizard()
        with patch(SMS_MOD + ".configured", return_value=True):
            wiz.invalidate_recordset(["sms_configured", "sms_hint"])
            self.assertTrue(wiz.sms_configured)
            self.assertIn("prêt", wiz.sms_hint)
        with patch(SMS_MOD + ".configured", return_value=False):
            wiz.invalidate_recordset(["sms_configured", "sms_hint"])
            self.assertFalse(wiz.sms_configured)
            self.assertIn("pas configuré", wiz.sms_hint)

    # ------------------------------------------------------------------ recipients
    def test_recipient_without_a_valid_email_is_refused(self):
        """A contact with no address would be dropped silently: the transfer
        would go out to fewer people than the operator picked, and he would
        never know."""
        partner = self.env["res.partner"].create({"name": "Sans courriel"})
        wiz = self._wizard(partner_ids=[(6, 0, partner.ids)])
        exc = self._refused(wiz._resolve_recipients)
        self.assertIn("Sans courriel", str(exc))

    def test_extra_emails_rejects_an_invalid_address(self):
        """A typo must stop the send, not create a transfer whose notification
        bounces while the operator believes the message was delivered."""
        wiz = self._wizard(extra_emails="bon@example.test, pas-une-adresse")
        exc = self._refused(wiz._resolve_recipients)
        self.assertIn("pas-une-adresse", str(exc))

    def test_extra_emails_accepts_semicolons_and_dedupes(self):
        """Outlook hands addresses over semicolon-separated, and the same
        person is often both a picked contact and a pasted address: a duplicate
        would mean two codes, two notifications, and a recipient count that
        does not match reality."""
        partner = self.env["res.partner"].create({
            "name": "Wiz Dup", "email": "Dup@Example.Test",
            "mobile": "514 555 4321"})
        wiz = self._wizard(
            partner_ids=[(6, 0, partner.ids)],
            extra_emails="DUP@example.test; autre@example.test ;",
        )
        emails, sms_map = wiz._resolve_recipients()
        self.assertEqual(emails, ["dup@example.test", "autre@example.test"])
        self.assertEqual(sms_map, {"dup@example.test": "5145554321"})

    def test_no_recipient_is_refused(self):
        """An empty recipient list would create an active transfer nobody was
        ever told about — a secret parked in storage for its whole retention."""
        wiz = self._wizard()
        self._refused(wiz._resolve_recipients)

    def test_more_than_ten_recipients_is_refused(self):
        """The ceiling is what keeps the composer from becoming a mailing tool
        (and the OTP journal readable). Ten must pass, eleven must not."""
        ten = ", ".join("bulk-%d@example.test" % i for i in range(10))
        emails, _sms = self._wizard(extra_emails=ten)._resolve_recipients()
        self.assertEqual(len(emails), 10)
        eleven = ", ".join("bulk-%d@example.test" % i for i in range(11))
        self._refused(self._wizard(extra_emails=eleven)._resolve_recipients)

    # ------------------------------------------------------------------ action_send
    def test_send_requires_the_securetransfer_user_group(self):
        """Any employee can reach a transient model's action over RPC. Without
        this guard, the whole staff can originate branded secure messages in
        the company's name."""
        wiz = self._wizard(extra_emails=self._addr())
        exc = self._refused(wiz.with_user(self.outsider).action_send)
        self.assertIn("réservée", str(exc))

    def test_send_refuses_an_empty_message(self):
        """Whitespace satisfies the required= attribute. An empty secure
        message means the recipient proves his identity to read nothing."""
        wiz = self._wizard(message="   ", extra_emails=self._addr())
        self._refused(wiz.action_send)

    def test_send_sets_the_optional_password(self):
        """The second factor is typed in the wizard and must land on the
        transfer: silently dropped, the operator hands out a password over the
        phone that the page will never ask for."""
        wiz = self._wizard(extra_emails=self._addr(), password="s3cret!")
        with self._offline():
            res = wiz.action_send()
        transfer = self.env["secure.transfer"].browse(res["res_id"])
        self.assertTrue(transfer.has_password)
        self.assertTrue(transfer._check_password("s3cret!"))
        self.assertFalse(transfer._check_password("wrong"))

    def test_send_marks_the_backend_provenance(self):
        """The access trail is the Loi 25 evidence. Without the backend stamp
        and the acting login, a message originated by an employee is
        indistinguishable from one an anonymous visitor dropped on the public
        form."""
        wiz = self._wizard(user=self.sender, extra_emails=self._addr())
        with self._offline():
            res = wiz.action_send()
        transfer = self.env["secure.transfer"].browse(res["res_id"]).sudo()
        self.assertEqual(transfer.ip_created, "backend")
        self.assertEqual(transfer.ua_created, "backend:%s" % self.sender.login)
        created = transfer.access_log_ids.filtered(
            lambda entry: entry.action == "created")
        self.assertEqual(len(created), 1, "creation must be journaled exactly once")
        self.assertEqual(created.actor, self.sender.login)

    def test_send_locale_follows_the_user_language(self):
        """The locale drives the branded page and every e-mail. Ignoring the
        operator's language sends an English-speaking client a French page."""
        wiz = self._wizard(user=self.sender_ca, extra_emails=self._addr())
        with self._offline():
            res = wiz.action_send()
        self.assertEqual(
            self.env["secure.transfer"].browse(res["res_id"]).locale, "en_CA")

    def test_send_locale_falls_back_to_french(self):
        """Odoo's default en_US is not a locale the module ships: stored as-is
        it would break the template lookup, so it must degrade to fr_CA."""
        self.assertEqual(self.sender.lang, "en_US")
        wiz = self._wizard(user=self.sender, extra_emails=self._addr())
        with self._offline():
            res = wiz.action_send()
        self.assertEqual(
            self.env["secure.transfer"].browse(res["res_id"]).locale, "fr_CA")

    # ============================================================ reveal wizard
    def test_reveal_default_get_resolves_the_transfer_both_ways(self):
        """The button passes default_transfer_id, a list action passes
        active_id. Losing either entry point opens the wizard on nothing and
        the manager can no longer read the link at all."""
        transfer = self._transfer()
        Wizard = self.env["secure.transfer.reveal.wizard"].with_user(self.manager)
        by_default = Wizard.with_context(
            default_transfer_id=transfer.id).default_get(["transfer_id"])
        self.assertEqual(by_default.get("transfer_id"), transfer.id)
        by_active = Wizard.with_context(
            active_id=transfer.id).default_get(["transfer_id"])
        self.assertEqual(by_active.get("transfer_id"), transfer.id)

    def test_reveal_default_get_without_a_transfer_is_refused(self):
        """Opened with no context (or on a deleted record), the wizard must
        say so instead of rendering an empty form whose Confirm button would
        crash on an empty recordset."""
        Wizard = self.env["secure.transfer.reveal.wizard"].with_user(self.manager)
        self._refused(Wizard.with_context({}).default_get, ["transfer_id"])
        self._refused(
            Wizard.with_context(default_transfer_id=0).default_get,
            ["transfer_id"])

    def test_reveal_default_get_requires_the_manager_group(self):
        """The share token is manager-only at rest. A read-only securetransfer
        user reaching this wizard would walk around that restriction and get
        the download capability for every client transfer."""
        Wizard = self.env["secure.transfer.reveal.wizard"]
        transfer = self._transfer()
        exc = self._refused(
            Wizard.with_user(self.plain_user).with_context(
                default_transfer_id=transfer.id).default_get, ["transfer_id"])
        self.assertIn("gestionnaires", str(exc))

    def test_opening_the_reveal_wizard_reveals_nothing(self):
        """THE test of this file. The token IS the capability: anyone holding
        the link downloads the files, no login required. So merely opening the
        wizard — or closing it without confirming — must neither put the URL
        in the form nor write link_revealed in the trail. Revealing on open
        would hand out the link on a misclick AND fill the Loi 25 journal with
        reveals that never happened, destroying its evidentiary value."""
        transfer = self._transfer()
        Wizard = self.env["secure.transfer.reveal.wizard"].with_user(self.manager)
        defaults = Wizard.with_context(
            default_transfer_id=transfer.id).default_get(
                ["transfer_id", "url", "revealed"])
        self.assertFalse(defaults.get("url"), "the link leaked into the defaults")
        # …and through the real UI path (create applies the same defaults).
        wiz = Wizard.with_context(default_transfer_id=transfer.id).create({})
        self.assertEqual(wiz.transfer_id, transfer)
        self.assertFalse(wiz.url, "the link leaked into the opened wizard")
        self.assertFalse(wiz.revealed)
        self.assertNotIn("link_revealed", transfer.access_log_ids.mapped("action"),
                         "opening the wizard journaled a reveal that never happened")

    def test_confirm_reveal_exposes_the_link_and_journals_it(self):
        """The confirmation is the whole point: it must actually produce the
        link (else the feature is dead) and it must leave the manager's name in
        the tamper-evident trail (else a leaked link can never be traced back
        to who took it out)."""
        transfer = self._transfer()
        wiz = self.env["secure.transfer.reveal.wizard"].with_user(
            self.manager).with_context(default_transfer_id=transfer.id).create({})
        action = wiz.action_confirm_reveal()
        self.assertEqual(action["res_model"], "secure.transfer.reveal.wizard")
        self.assertEqual(action["res_id"], wiz.id)
        self.assertTrue(wiz.revealed)
        self.assertIn("/s/" + transfer.sudo().token, wiz.url)
        revealed = transfer.access_log_ids.filtered(
            lambda entry: entry.action == "link_revealed")
        self.assertEqual(len(revealed), 1)
        self.assertEqual(revealed.actor, self.manager.login)

    def test_confirm_reveal_rechecks_the_manager_group(self):
        """A wizard is a live record: rights are re-read at confirm time. A
        manager demoted (or off-boarded) while the dialog sat open must be
        refused — checking only at open leaves an unbounded window during
        which a revoked account can still extract the token."""
        transfer = self._transfer()
        wiz = self.env["secure.transfer.reveal.wizard"].with_user(
            self.manager).with_context(default_transfer_id=transfer.id).create({})
        self.manager.write({"groups_id": [(3, self.g_manager.id)]})
        self.assertFalse(self.manager.has_group(
            "bf_securetransfer.group_securetransfer_manager"))
        exc = self._refused(wiz.with_user(self.manager).action_confirm_reveal)
        self.assertIn("gestionnaires", str(exc))
        # Read back in sudo: the demoted account has also lost ACL access to
        # the wizard model itself (defence in depth), so the check must not be
        # made through his own rights.
        self.assertFalse(wiz.sudo().url, "the link was written despite the refusal")
        self.assertNotIn("link_revealed",
                         transfer.access_log_ids.mapped("action"))

    def test_action_reveal_link_opens_the_wizard_on_the_transfer(self):
        """The form button must hand the transfer to the wizard through the
        context: without default_transfer_id the wizard opens on nothing and
        the manager gets "Aucun transfert sélectionné" instead of the link."""
        transfer = self._transfer()
        action = transfer.action_reveal_link()
        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["res_model"], "secure.transfer.reveal.wizard")
        self.assertEqual(action["target"], "new")
        self.assertEqual(action["context"]["default_transfer_id"], transfer.id)
