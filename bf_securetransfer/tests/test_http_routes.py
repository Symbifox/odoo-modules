"""Public HTTP surface: the 12 anonymous routes of controllers/main.py and
controllers/upload_api.py, driven through real requests (HttpCase).

The model suite (test_lifecycle) proves the rules; this one proves the ROUTES
actually apply them — a guard that only exists in the model is worth nothing if
the controller forgets to call it. Covered here: the honeypot, the _api_guard
error/savepoint contract, the uniform 404s, the password and recipient-OTP
gates, the security headers, the kill-switch, /to/<slug> and cross-transfer
file isolation.

No network, ever: every S3 touchpoint is patched on
``odoo.addons.bf_securetransfer.models.s3`` exactly like test_lifecycle does,
and the nominal download asserts the presign was called with a stub.
"""
from unittest.mock import patch

from odoo.tests import HttpCase, tagged
from odoo.tools import mute_logger

from odoo.addons.bf_securetransfer.controllers import main as st_main
from odoo.addons.bf_securetransfer.controllers.main import HONEYPOT_FIELD

from .common import BaseNeuve

S3_MOD = "odoo.addons.bf_securetransfer.models.s3"
API_MOD = "odoo.addons.bf_securetransfer.controllers.upload_api"
MAIL_SEND = "odoo.addons.mail.models.mail_mail.MailMail.send"
MB = 1024 * 1024

# Every module-level sliding-window limiter. They live in the worker process,
# not in the database, so they survive the test rollback and leak between test
# methods: a suite that burns the 20-failure token budget makes every later
# lookup return None and turns unrelated tests into inexplicable 404s.
_LIMITERS = (
    st_main._create_limiter,
    st_main._finalize_limiter,
    st_main._token_fail_limiter,
    st_main._password_fail_limiter,
    st_main._report_limiter,
    st_main._otp_fail_limiter,
    st_main._otp_send_limiter,
    st_main._otp_cooldown_limiter,
    st_main._upload_ops_limiter,
)


@tagged("post_install", "-at_install")
class TestSecureTransferHttpRoutes(BaseNeuve, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.brand = cls.env.ref("bf_securetransfer.brand_default")
        # The public pages resolve their brand from the Host header; in
        # HttpCase that is 127.0.0.1, which matches no domain, so everything
        # lands on the default brand. Keep it plain: watermarking would make
        # the download route fetch the object instead of redirecting.
        cls.brand.sudo().write({"watermark_downloads": False})
        icp = cls.env["ir.config_parameter"].sudo()
        # Roomy quotas/budgets so the suite never trips anti-abuse counters.
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_ip", "500")
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_sender", "500")
        icp.set_param("bf_securetransfer.quota_daily_bytes_per_ip_mb", "1000000")
        icp.set_param("bf_securetransfer.rate_create_per_hour", "500")
        icp.set_param("bf_securetransfer.public_upload_enabled", "1")
        icp.set_param("bf_securetransfer.require_recipient_otp", "0")
        icp.set_param("bf_securetransfer.require_sender_otp", "0")

    def setUp(self):
        super().setUp()
        for limiter in _LIMITERS:
            with limiter._lock:
                limiter._data.clear()

    # ------------------------------------------------------------- fixtures
    def _create(self, **overrides):
        vals = {
            "sender_name": "Test Sender",
            "sender_email": "sender@example.com",
            "recipient_emails": "dest@example.com",
            "message": "Bonjour",
            "retention_days": 7,
        }
        vals.update(overrides)
        return self.env["secure.transfer"].api_create(
            self.brand, vals, "203.0.113.10", "test-suite/1.0", "fr_CA",
        )

    def _head_for(self, transfer):
        """head_object stub returning the declared size for any of the
        transfer's keys (finalize verification passes)."""
        sizes = {f.s3_key: int(f.size) for f in transfer.file_ids}

        def _head(env, key):
            if key in sizes:
                return {"size": sizes[key], "etag": "etag-" + key[-8:]}
            return None
        return _head

    def _active_transfer(self, filename="doc.pdf", password=None, **overrides):
        t = self._create(**overrides)
        t._register_file(filename, 4096)
        with patch(S3_MOD + ".head_object", side_effect=self._head_for(t)):
            t.action_finalize(password=password)
        self.assertEqual(t.state, "active")
        return t

    def _drop_brand(self, **overrides):
        # The slug is UNIQUE across brands and the tenant already publishes
        # a personal slug and "securetransfer" — never reuse a name a tenant owns.
        vals = {
            "name": "Dépôt test HTTP",
            "slug": "depot-test-http-routes",
            "fixed_recipient": "depot@example.com",
            "fixed_recipient_name": "Depot",
            "company_id": self.brand.company_id.id,
        }
        vals.update(overrides)
        return self.env["secure.transfer.brand"].create(vals)

    # ------------------------------------------------------------- transport
    def _rpc(self, path, params=None):
        """POST a JSON-RPC envelope the way st_upload.js does."""
        response = self.opener.post(
            self.base_url() + path,
            json={"jsonrpc": "2.0", "method": "call",
                  "params": params or {}, "id": 1},
            timeout=30,
        )
        self.env.invalidate_all()
        return response

    def _result(self, response):
        """The `result` payload, asserting the JSON-RPC layer itself did not
        fault (a fault carries the traceback in `error.data.debug`)."""
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotIn(
            "error", payload,
            "the JSON-RPC layer faulted instead of returning a safe error: %s"
            % payload)
        return payload["result"]

    def _open(self, path, **kw):
        response = self.url_open(path, **kw)
        self.env.invalidate_all()
        return response

    def _count(self, model):
        self.env.invalidate_all()
        return self.env[model].sudo().search_count([])

    # ------------------------------------------------------------- honeypot
    def test_honeypot_returns_a_decoy_and_stores_nothing(self):
        """If a filled honeypot ever created a real draft, every bot sweeping
        the page would burn the IP quota and fill the table with junk — and the
        decoy must look exactly like a success, or the trap is detectable."""
        before = self._count("secure.transfer")
        result = self._result(self._rpc("/secrets/api/create", {
            HONEYPOT_FIELD: "http://spam.example.test",
            "sender_email": "bot@spam.test",
            "recipient_emails": "victim@example.com",
            "message": "achetez maintenant",
        }))
        self.assertNotIn("error", result)
        # same shape as a real success: token + limits, nothing missing
        self.assertRegex(result["upload_token"], r"^[0-9a-f]{32}$")
        self.assertIn("limits", result)
        self.assertEqual(
            result["limits"], self.brand._effective_limits(),
            "the decoy limits must be the real ones — a different payload "
            "tells the bot it hit the trap")
        self.assertEqual(self._count("secure.transfer"), before)
        self.assertFalse(self.env["secure.transfer"].sudo().search_count(
            [("upload_token", "=", result["upload_token"])]))

    def test_honeypot_token_is_dead_on_every_later_call(self):
        """The decoy must be a throwaway: if it resolved to anything, the bot
        would get a working upload channel out of the trap."""
        decoy = self._result(self._rpc("/secrets/api/create", {
            HONEYPOT_FIELD: "x", "sender_email": "bot@spam.test",
        }))["upload_token"]
        result = self._result(self._rpc(
            "/secrets/api/%s/presign" % decoy,
            {"filename": "rapport.pdf", "size": 4096}))
        self.assertEqual(result["error"], "not_found")

    # ------------------------------------------------------------ _api_guard
    def test_api_guard_maps_a_user_error_to_invalid(self):
        """A refused file must reach the uploader as an actionable message.
        Without the UserError branch the JSON-RPC layer would fault instead,
        and st_upload.js would show the generic "something went wrong"."""
        draft = self._create()
        result = self._result(self._rpc(
            "/secrets/api/%s/presign" % draft.sudo().upload_token,
            {"filename": "malware.exe", "size": 4096}))
        self.assertEqual(result["error"], "invalid")
        self.assertIn("exe", result["message"])

    @mute_logger(API_MOD)
    def test_api_guard_hides_the_crash_and_rolls_the_row_back(self):
        """Two contracts in one request: an unexpected crash must never ship a
        traceback (or a bucket name) to an anonymous caller, and the savepoint
        must undo the file row api_presign wrote BEFORE talking to S3 —
        otherwise every failed attempt leaves a committed "pending" ghost that
        eats a max_files slot and blocks action_finalize with no hint why."""
        draft = self._create()
        before = self._count("secure.transfer.file")
        with patch(S3_MOD + ".presign_put",
                   side_effect=RuntimeError("bucket=SECRET-INTERNAL-DETAIL")):
            response = self._rpc(
                "/secrets/api/%s/presign" % draft.sudo().upload_token,
                {"filename": "rapport.pdf", "size": 4096})
        result = self._result(response)
        self.assertEqual(result["error"], "server_error")
        self.assertNotIn("SECRET-INTERNAL-DETAIL", response.text)
        self.assertNotIn("Traceback", response.text)
        self.assertNotIn("presign_put", response.text)
        self.assertEqual(self._count("secure.transfer.file"), before,
                         "a ghost file row survived the failed presign")

    # ------------------------------------------------------------ 404 uniformity
    def test_unknown_and_malformed_tokens_are_one_single_404(self):
        """Any difference (status, body, timing-visible branch) between "no
        such token", "wrong length" and "not hex" is an oracle a fuzzer uses to
        confirm a token shape. They must be one indistinguishable answer."""
        bodies = set()
        for token in ("0" * 32, "trop-court", "z" * 32, "ABCDEF" * 6,
                      "9f8e7d6c5b4a39281706f5e4d3c2b1a0"):
            response = self._open("/s/%s" % token)
            self.assertEqual(response.status_code, 404, token)
            bodies.add(response.text)
        self.assertEqual(len(bodies), 1,
                         "the 404s differ between malformed and unknown tokens")

    def test_draft_and_cancelled_transfers_404_like_a_stranger(self):
        """A draft token is a real 32-hex token that resolves to a real row.
        Serving it anything other than the stranger's 404 would confirm the
        token exists — and a harvested draft would leak its own metadata."""
        stranger = self._open("/s/%s" % ("a" * 32))
        self.assertEqual(stranger.status_code, 404)
        draft = self._create()
        cancelled = self._active_transfer()
        cancelled.sudo().state = "cancelled"
        for transfer, label in ((draft, "draft"), (cancelled, "cancelled")):
            response = self._open("/s/%s" % transfer.sudo().token)
            self.assertEqual(response.status_code, 404, label)
            self.assertEqual(response.text, stranger.text, label)

    # ------------------------------------------------------------ password gate
    def test_password_gate_unlocks_and_stays_scoped_to_one_transfer(self):
        """The unlock flag is a session key built from the transfer id. If it
        were global, unlocking any transfer you legitimately hold a password
        for would open every other password-protected link in the same
        browser."""
        first = self._active_transfer(filename="secret-a.pdf", password="s3cret!")
        second = self._active_transfer(filename="secret-b.pdf", password="autre!")
        token_a, token_b = first.sudo().token, second.sudo().token

        locked = self._open("/s/%s" % token_a)
        self.assertEqual(locked.status_code, 200)
        self.assertIn("st-password-input", locked.text)
        self.assertNotIn("secret-a.pdf", locked.text,
                         "the file listing leaked through the password gate")

        unlock = self._open("/s/%s/unlock" % token_a,
                            data={"password": "s3cret!"}, allow_redirects=False)
        self.assertEqual(unlock.status_code, 303)
        self.assertTrue(unlock.headers["Location"].endswith("/s/%s" % token_a),
                        unlock.headers.get("Location"))

        opened = self._open("/s/%s" % token_a)
        self.assertEqual(opened.status_code, 200)
        self.assertNotIn("st-password-input", opened.text)
        self.assertIn("secret-a.pdf", opened.text)

        # …and the very same session still faces the gate on the other one
        other = self._open("/s/%s" % token_b)
        self.assertIn("st-password-input", other.text)
        self.assertNotIn("secret-b.pdf", other.text)

    def test_password_gate_refuses_a_wrong_password(self):
        """A wrong password must come back to the gate with pw_error=1 and no
        session flag — never a redirect to the open page."""
        transfer = self._active_transfer(filename="prive.pdf", password="s3cret!")
        token = transfer.sudo().token
        refused = self._open("/s/%s/unlock" % token,
                             data={"password": "pas-le-bon"},
                             allow_redirects=False)
        self.assertEqual(refused.status_code, 303)
        self.assertTrue(
            refused.headers["Location"].endswith("/s/%s?pw_error=1" % token),
            refused.headers.get("Location"))
        still_locked = self._open("/s/%s" % token)
        self.assertIn("st-password-input", still_locked.text)
        self.assertNotIn("prive.pdf", still_locked.text)
        self.assertIn("password_fail", transfer.access_log_ids.mapped("action"))

    # ------------------------------------------------------------ recipient OTP
    def test_recipient_otp_gate_renders_neither_message_nor_filenames(self):
        """The gate exists to hold the CONTENT, not just the bytes: rendering
        the cover message or the file names before the code is verified hands
        the payload to whoever forwarded or intercepted the link."""
        transfer = self._create(message="Le NIP du dossier est 4417")
        transfer.force_recipient_otp = True
        transfer._register_file("contrat-secret.pdf", 4096)
        with patch(S3_MOD + ".head_object",
                   side_effect=self._head_for(transfer)):
            transfer.action_finalize()
        page = self._open("/s/%s" % transfer.sudo().token)
        self.assertEqual(page.status_code, 200)
        self.assertIn("st-otp-email", page.text)  # request stage rendered
        self.assertNotIn("4417", page.text)
        self.assertNotIn("contrat-secret.pdf", page.text)

    # ------------------------------------------------------- abuse reporting
    def test_the_report_link_is_offered_on_every_view_of_the_page(self):
        """⚠ The form used to live INSIDE the content branch of the template,
        so it only appeared once every gate was through. That made it useless
        exactly where it matters: an unsolicited transfer is reported BEFORE it
        is opened. Worse for a recipient-code transfer — which is every send
        composed from the backend — the content page is only reachable by
        requesting a code, so its recipient never saw the link at all and had
        to play along with the sender to be able to complain about him."""
        gated = self._create(message="non sollicité")
        gated.force_recipient_otp = True
        gated._register_file("piece.pdf", 4096)
        with patch(S3_MOD + ".head_object", side_effect=self._head_for(gated)):
            gated.action_finalize()
        page = self._open("/s/%s" % gated.sudo().token)
        self.assertIn("st-otp-email", page.text, "the OTP gate must be rendered")
        self.assertIn("st-report-form", page.text)

        locked = self._active_transfer(password="hunter2")
        page = self._open("/s/%s" % locked.sudo().token)
        self.assertIn("st-password-input", page.text,
                      "the password gate must be rendered")
        self.assertIn("st-report-form", page.text)

        # …and it is still there once the content is visible.
        open_one = self._active_transfer()
        page = self._open("/s/%s" % open_one.sudo().token)
        self.assertIn("st-report-form", page.text)

    def test_a_gated_transfer_can_actually_be_reported(self):
        """Rendering the button is half of it: the route must accept the post
        without the visitor ever holding a code, and suspend the transfer."""
        transfer = self._create(message="non sollicité")
        transfer.force_recipient_otp = True
        transfer._register_file("piece.pdf", 4096)
        with patch(S3_MOD + ".head_object", side_effect=self._head_for(transfer)):
            transfer.action_finalize()
        token = transfer.sudo().token
        with patch(MAIL_SEND, lambda self, *a, **k: True):
            response = self._open("/s/%s/report" % token,
                                  data={"reason": "je ne connais pas"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(transfer.sudo().state, "suspended")
        self.assertIn("abuse_report",
                      transfer.sudo().access_log_ids.mapped("action"))

    def test_recipient_otp_gate_never_serves_the_bytes(self):
        """Without a verified session the download route must redirect and
        stop there — not HEAD the object, not presign it. A presign handed out
        "just before" the redirect is a usable capability."""
        transfer = self._create()
        transfer.force_recipient_otp = True
        transfer._register_file("contrat-secret.pdf", 4096)
        with patch(S3_MOD + ".head_object",
                   side_effect=self._head_for(transfer)):
            transfer.action_finalize()
        token = transfer.sudo().token
        with patch(S3_MOD + ".head_object") as head, \
                patch(S3_MOD + ".presign_get") as presign:
            response = self._open("/s/%s/dl/%d" % (token, transfer.file_ids.id),
                                  allow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.headers["Location"].endswith("/s/%s" % token),
                        response.headers.get("Location"))
        head.assert_not_called()
        presign.assert_not_called()
        self.assertNotIn("download", transfer.access_log_ids.mapped("action"))

    # ------------------------------------------------------------ security headers
    def test_public_pages_carry_the_hardening_headers(self):
        """These pages are anonymous and link-addressed: without CSP +
        X-Frame-Options they can be framed for clickjacking, and any injected
        markup would be free to call out. The headers are the whole
        containment story — nothing else on these pages provides it."""
        transfer = self._active_transfer()
        brand = self._drop_brand()
        paths = ("/secrets", "/to/%s" % brand.slug, "/s/%s" % transfer.sudo().token)
        for path in paths:
            response = self._open(path)
            self.assertEqual(response.status_code, 200, path)
            csp = response.headers.get("Content-Security-Policy", "")
            self.assertIn("default-src 'none'", csp, path)
            self.assertIn("frame-ancestors 'none'", csp, path)
            self.assertIn("base-uri 'none'", csp, path)
            self.assertIn("script-src 'self'", csp, path)
            self.assertEqual(response.headers.get("X-Frame-Options"), "DENY", path)
            self.assertEqual(
                response.headers.get("X-Content-Type-Options"), "nosniff", path)
            self.assertEqual(
                response.headers.get("Referrer-Policy"),
                "strict-origin-when-cross-origin", path)

    # ------------------------------------------------------------ kill-switch
    def test_kill_switch_closes_both_the_page_and_the_api(self):
        """Hiding the page is not disabling the service: the JSON API is a
        separate entry point and a scripted client keeps posting to it. Both
        must refuse, and nothing may be created while the switch is off."""
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("bf_securetransfer.public_upload_enabled", "0")
        self.addCleanup(
            icp.set_param, "bf_securetransfer.public_upload_enabled", "1")

        page = self._open("/secrets")
        self.assertEqual(page.status_code, 200)
        self.assertIn("st-unavailable", page.text)
        self.assertNotIn('id="st-config"', page.text)
        self.assertNotIn('id="st-upload-panel"', page.text)

        before = self._count("secure.transfer")
        result = self._result(self._rpc(
            "/secrets/api/create", {"sender_email": "s@example.com"}))
        self.assertEqual(result["error"], "disabled")
        self.assertEqual(self._count("secure.transfer"), before)

    # ----------------------------------------------- doctype & translation
    def test_every_public_page_carries_the_doctype(self):
        """The doctype is injected by _render_page, not written in the arch
        (Odoo refuses to translate a view whose arch starts with one). A page
        rendered through a bare request.render would ship no doctype at all and
        drop the browser into quirks mode."""
        transfer = self._active_transfer()
        brand = self._drop_brand()
        pages = ("/secrets", "/to/%s" % brand.slug,
                 "/s/%s" % transfer.sudo().token,
                 "/s/%s" % ("0" * 32))  # unknown token → unavailable page
        for path in pages:
            response = self._open(path)
            self.assertTrue(
                response.text.lstrip().lower().startswith("<!doctype html>"),
                "%s does not start with a doctype" % path)

    def test_the_public_page_speaks_english_to_an_english_visitor(self):
        """The regression guard for the whole translation chain.

        These pages exported ZERO translatable terms for months — Odoo skips a
        view whose arch begins with a doctype, silently, so the .po could say
        anything and an en_CA visitor still got a French page. Put the doctype
        back in the arch and this test is what says so.
        """
        if not self.env["res.lang"].search(
                [("code", "=", "en_CA"), ("active", "=", True)], limit=1):
            self.skipTest("en_CA is not installed on this database")
        arch = self.env.ref("bf_securetransfer.page_upload").with_context(
            lang="en_CA").arch_db
        self.assertIn("Up to 10 addresses", arch,
                      "the upload page was not translated at all")
        self.assertNotIn("Jusqu'à 10 adresses", arch)
        # …and the French visitor keeps his page.
        arch_fr = self.env.ref("bf_securetransfer.page_upload").with_context(
            lang="fr_CA").arch_db
        self.assertIn("Jusqu'à 10 adresses", arch_fr)

    # -------------------------------------------------- public form options
    def test_upload_page_offers_every_option_the_model_supports(self):
        """An option that only exists in the backend does not exist for the
        people who actually use the service — the public form IS the product.
        The recipient code in particular was reachable only from the internal
        composer, so a visitor could not hold their own send behind a code.

        ⚠ ``allow_burn`` is posé explicitement : il est FAUX sur une marque par
        défaut fraîchement installée, donc ce test échouait sur une base neuve
        pour une raison qui n'a rien à voir avec ce qu'il vérifie (il ne passait
        que sur une copie de locataire, où la case est cochée). Le test doit
        créer les conditions qu'il éprouve."""
        self.brand.sudo().write({"allow_burn": True})
        self.addCleanup(self.brand.sudo().write, {"allow_burn": False})
        page = self._open("/secrets")
        self.assertEqual(page.status_code, 200)
        for dom_id in ("st-subject", "st-expiry", "st-password", "st-burn",
                       "st-recipient-otp", "st-max-downloads", "st-notify"):
            self.assertIn('id="%s"' % dom_id, page.text,
                          "the public form is missing the %s option" % dom_id)

    def test_download_page_titles_itself_with_the_subject(self):
        """L'objet n'existe que s'il est VU : sur la page de téléchargement, il
        prend le titre, et le libellé générique se replie au-dessus."""
        t = self._active_transfer(subject="Baux 2026")
        page = self._open("/s/%s" % t.sudo().token)
        self.assertEqual(page.status_code, 200)
        self.assertIn('class="st-title">Baux 2026', page.text)
        self.assertIn('class="st-eyebrow"', page.text)

    def test_code_page_says_the_code_is_bound_to_this_browser(self):
        """Si ça casse : le destinataire qui ouvre le lien sur son téléphone et
        lit le code sur son ordinateur se fait refuser sans comprendre — le défi
        vit dans la session (`st_otp_chal_*`), pas sur le transfert."""
        t = self._active_transfer(subject="Sujet")
        t.sudo().force_recipient_otp = True
        self._open("/s/%s" % t.sudo().token)
        self._open("/s/%s/otp-request" % t.sudo().token,
                   data={"email": "dest@example.com"})
        page = self._open("/s/%s" % t.sudo().token)
        self.assertEqual(page.status_code, 200)
        self.assertIn("Un code à 6 chiffres", page.text,
                      "la page n'est pas à l'étape de saisie")
        self.assertIn("ce navigateur", page.text,
                      "la page ne dit pas que le code est lié à ce navigateur")

    def test_download_page_keeps_the_subject_out_of_the_tab_title(self):
        """Le <head> est partagé avec les portes (mot de passe, code) : un lien
        transféré ne doit pas livrer l'objet de l'expéditeur à qui le détient
        avant d'avoir passé la porte."""
        t = self._active_transfer(subject="Dossier Tremblay", password="s3cr3t!")
        page = self._open("/s/%s" % t.sudo().token)
        self.assertEqual(page.status_code, 200)
        self.assertNotIn("Dossier Tremblay", page.text)

    def test_upload_page_shows_the_instance_gate_as_ticked_and_locked(self):
        """When the instance holds every transfer behind a code, an empty
        checkbox would read as "not protected" — and a sender could believe
        unticking it opts out of a gate they cannot opt out of."""
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("bf_securetransfer.require_recipient_otp", "1")
        self.addCleanup(
            icp.set_param, "bf_securetransfer.require_recipient_otp", "0")
        page = self._open("/secrets")
        self.assertRegex(
            page.text,
            r'id="st-recipient-otp"[^>]*checked',
            "the forced recipient code is not reflected on the page")
        self.assertRegex(page.text, r'id="st-recipient-otp"[^>]*disabled')

    def test_finalize_arms_the_recipient_code_from_the_public_form(self):
        """The whole point of putting the option on the page: what the visitor
        ticks must actually gate the download, not just decorate the form."""
        draft = self._create()
        draft._register_file("doc.pdf", 4096)
        with patch(S3_MOD + ".head_object", side_effect=self._head_for(draft)):
            self._result(self._rpc(
                "/secrets/api/%s/finalize" % draft.upload_token,
                {"sender_email": "sender@example.com",
                 "recipient_emails": "dest@example.com",
                 "force_recipient_otp": True}))
        draft.invalidate_recordset()
        self.assertTrue(draft.force_recipient_otp)
        self.assertTrue(draft._recipient_otp_required())

    def test_finalize_cannot_disarm_the_instance_gate(self):
        """A client is free to skip the field entirely. Reading its absence —
        or a false — as an opt-out would let a scripted sender walk around an
        instance-wide requirement."""
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("bf_securetransfer.require_recipient_otp", "1")
        self.addCleanup(
            icp.set_param, "bf_securetransfer.require_recipient_otp", "0")
        draft = self._create()
        draft._register_file("doc.pdf", 4096)
        with patch(S3_MOD + ".head_object", side_effect=self._head_for(draft)):
            self._result(self._rpc(
                "/secrets/api/%s/finalize" % draft.upload_token,
                {"sender_email": "sender@example.com",
                 "recipient_emails": "dest@example.com",
                 "force_recipient_otp": False}))
        draft.invalidate_recordset()
        self.assertTrue(draft._recipient_otp_required())

    def test_finalize_caps_the_download_budget(self):
        """The budget is a ceiling the sender sets on their own transfer; an
        unbounded value posted by a client would turn a confidentiality option
        into a counter nobody watches."""
        draft = self._create()
        draft._register_file("doc.pdf", 4096)
        with patch(S3_MOD + ".head_object", side_effect=self._head_for(draft)):
            self._result(self._rpc(
                "/secrets/api/%s/finalize" % draft.upload_token,
                {"sender_email": "sender@example.com",
                 "recipient_emails": "dest@example.com",
                 "max_downloads": 10 ** 9, "notify_on_download": True}))
        draft.invalidate_recordset()
        self.assertEqual(
            draft.max_downloads,
            self.brand._effective_limits()["max_download_budget"])
        self.assertTrue(draft.notify_on_download)

    # ------------------------------------------------------------ /to/<slug>
    def test_unknown_slug_is_a_404(self):
        """An unpublished slug must not render a branded page: /to/ would
        otherwise become a way to enumerate which brands exist."""
        response = self._open("/to/aucune-page-publiee-ici-12345")
        self.assertEqual(response.status_code, 404)

    def test_drop_page_renders_locked_to_its_recipient(self):
        """A drop page hides the recipient field and names its owner. If
        drop_mode came back False the visitor would be offered a free
        recipient box on a page meant to reach exactly one address."""
        brand = self._drop_brand()
        response = self._open("/to/%s" % brand.slug)
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="st-upload-panel"', response.text)
        self.assertIn('"drop_slug"', response.text)
        self.assertIn(brand.slug, response.text)
        self.assertIn("Depot", response.text)          # the forced label
        self.assertNotIn('id="st-recipients"', response.text)

    def test_slug_page_without_fixed_recipient_stays_an_open_page(self):
        """Guard against over-correcting: a slug is only an address, not a
        lock. A brand with no fixed_recipient must still offer the recipient
        field."""
        brand = self._drop_brand(
            name="Page ouverte test", slug="page-ouverte-test-http",
            fixed_recipient=False, fixed_recipient_name=False)
        response = self._open("/to/%s" % brand.slug)
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="st-recipients"', response.text)

    # ------------------------------------------------------------ isolation
    def test_json_api_refuses_a_file_id_from_another_transfer(self):
        """file_id is a bare integer from the client. Resolved globally instead
        of within the caller's own transfer, it would let any upload_token
        holder drive the multipart plumbing of somebody else's draft."""
        mine = self._create()
        theirs = self._create(sender_email="autre@example.com")
        foreign = theirs._register_file("leur-fichier.zip", 100 * MB)
        result = self._result(self._rpc(
            "/secrets/api/%s/multipart/initiate" % mine.sudo().upload_token,
            {"file_id": foreign.id}))
        self.assertEqual(result["error"], "invalid_file")

    def test_download_route_refuses_a_file_id_from_another_transfer(self):
        """Same confusion on the public side: holding one valid share token
        must not turn into "download file #N of any transfer"."""
        mine = self._active_transfer(filename="a-moi.pdf")
        theirs = self._active_transfer(filename="a-eux.pdf",
                                       sender_email="autre@example.com")
        with patch(S3_MOD + ".head_object") as head, \
                patch(S3_MOD + ".presign_get") as presign:
            response = self._open(
                "/s/%s/dl/%d" % (mine.sudo().token, theirs.file_ids.id),
                allow_redirects=False)
        self.assertEqual(response.status_code, 404)
        head.assert_not_called()
        presign.assert_not_called()

    # ------------------------------------------------------------ nominal path
    def test_nominal_download_redirects_to_the_presigned_url(self):
        """The happy path the gates above must not have broken: an integrity
        re-check, then a 302 straight to S3 (the bytes never touch Odoo) and
        exactly one accounted download."""
        transfer = self._active_transfer(filename="rapport.pdf")
        rec_file = transfer.file_ids
        signed = "https://bucket.example.test/signed-object?X-Amz-Signature=abc"
        with patch(S3_MOD + ".head_object",
                   return_value={"size": int(rec_file.size),
                                 "etag": rec_file.etag}), \
                patch(S3_MOD + ".presign_get", return_value=signed):
            response = self._open(
                "/s/%s/dl/%d" % (transfer.sudo().token, rec_file.id),
                allow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], signed)
        self.env.invalidate_all()
        self.assertEqual(transfer.download_count, 1)
        self.assertEqual(rec_file.download_count, 1)
        self.assertIn("download", transfer.access_log_ids.mapped("action"))

    @mute_logger("odoo.addons.bf_securetransfer.controllers.main")
    def test_download_refuses_an_object_whose_etag_moved(self):
        """The ETag pinned at finalize is re-checked on every download: a
        re-PUT under a still-valid presign (malware swap) must end on the
        neutral page, never on a redirect to the tampered object."""
        transfer = self._active_transfer(filename="rapport.pdf")
        rec_file = transfer.file_ids
        with patch(S3_MOD + ".head_object",
                   return_value={"size": int(rec_file.size),
                                 "etag": "etag-DIFFERENT"}), \
                patch(S3_MOD + ".presign_get") as presign:
            response = self._open(
                "/s/%s/dl/%d" % (transfer.sudo().token, rec_file.id),
                allow_redirects=False)
        self.assertEqual(response.status_code, 200)
        self.assertIn("st-unavailable", response.text)
        presign.assert_not_called()
        self.assertIn("integrity_mismatch",
                      transfer.access_log_ids.mapped("action"))
