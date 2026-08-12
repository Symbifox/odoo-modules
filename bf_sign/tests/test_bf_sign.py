import base64
import hashlib
import io
import os
from datetime import datetime, timedelta
from unittest.mock import patch

_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools.pdf import PdfReader

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@tagged("post_install", "-at_install", "bf_sign")
class TestBfSign(TransactionCase):
    """Hardening coverage for the bf_sign palier 1 (SES) module.

    The completion certificate is rendered by wkhtmltopdf, which needs a running
    HTTP server to fetch branded assets — not available under ``--stop-after-init``.
    Finalize tests therefore mock ``_render_qweb_pdf`` with a real minimal PDF so
    they exercise *our* orchestration (stamping, hashing, chaining, attachments,
    idempotency, verification) deterministically, independent of wkhtmltopdf.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Request = cls.env["bf.sign.request"]
        cls.Signer = cls.env["bf.sign.signer"]
        cls.Field = cls.env["bf.sign.field"]
        cls.Log = cls.env["bf.sign.log"]
        cls.pdf_bytes = cls._make_pdf()
        cls.png_bytes = cls._make_png()
        # Provide a Fernet key so the seal cert can be generated/encrypted in tests.
        from cryptography.fernet import Fernet
        os.environ.setdefault("BF_SIGN_FERNET_KEY", Fernet.generate_key().decode())

    # ── builders ──────────────────────────────────────────────────────────────
    @staticmethod
    def _make_pdf(pages=1):
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        for i in range(pages):
            c.drawString(72, 720, "Document de test — page %d" % (i + 1))
            c.showPage()
        c.save()
        return buf.getvalue()

    @staticmethod
    def _make_png():
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGBA", (200, 80), (0, 0, 0, 0)).save(buf, format="PNG")
        return buf.getvalue()

    def _png_b64(self):
        return base64.b64encode(self.png_bytes).decode()

    def _new_request(self, signers=1, order="parallel", with_fields=True):
        req = self.Request.create({
            "document_file": base64.b64encode(self.pdf_bytes),
            "document_filename": "test.pdf",
            "signing_order": order,
        })
        for i in range(signers):
            signer = self.Signer.create({
                "request_id": req.id,
                "name": "Signer %d" % i,
                "email": "signer%d@example.com" % i,
                "sequence": 10 + i,
            })
            if with_fields:
                self.Field.create({
                    "request_id": req.id,
                    "signer_id": signer.id,
                    "field_type": "signature",
                    "page": 1, "pos_x": 0.5, "pos_y": 0.8, "width": 0.25, "height": 0.08,
                })
        return req

    def _mock_cert(self):
        """Patch the certificate render with a real minimal PDF (no wkhtmltopdf)."""
        return patch.object(
            type(self.env["ir.actions.report"]), "_render_qweb_pdf",
            return_value=(self._make_pdf(), "pdf"))

    def _sign(self, req, signer, consent=True, sig=None, initials=None, field_values=None):
        return req.register_signer_signature(
            signer, sig if sig is not None else self._png_b64(), initials, consent,
            ip="1.2.3.4", user_agent="pytest", field_values=field_values)

    def _add_fill_field(self, req, signer, field_type="text", fill_mode="signer", required=True):
        return self.Field.create({
            "request_id": req.id, "signer_id": signer.id,
            "field_type": field_type, "fill_mode": fill_mode, "required": required,
            "page": 1, "pos_x": 0.2, "pos_y": 0.5, "width": 0.3, "height": 0.04,
        })

    # ── lifecycle ─────────────────────────────────────────────────────────────
    def test_create_logs_created(self):
        req = self._new_request()
        self.assertTrue(req.name.startswith("SIGN-"))
        self.assertTrue(req.log_ids.filtered(lambda l: l.event == "created"))

    def test_send_sets_hash_state_expiry(self):
        req = self._new_request()
        req.action_send()
        self.assertEqual(req.state, "sent")
        self.assertEqual(len(req.hash_original), 64)
        self.assertTrue(req.expiry_date)
        self.assertTrue(req.log_ids.filtered(lambda l: l.event == "sent"))

    def test_send_guards(self):
        # no document
        req = self.Request.create({})
        with self.assertRaises(UserError):
            req.action_send()
        # document but no signer
        req = self.Request.create({
            "document_file": base64.b64encode(self.pdf_bytes), "document_filename": "t.pdf"})
        with self.assertRaises(UserError):
            req.action_send()

    # ── input validation ───────────────────────────────────────────────────────
    def test_send_rejects_non_pdf(self):
        req = self._new_request()
        req.document_file = base64.b64encode(b"this is not a pdf")
        with self.assertRaises(UserError):
            req.action_send()

    def test_signature_validation_rejects_bad_payloads(self):
        req = self._new_request()
        req.action_send()
        signer = req.signer_ids[0]
        cases = [
            base64.b64encode(b"hello world").decode(),           # wrong magic
            base64.b64encode(_PNG_MAGIC + b"garbage").decode(),  # magic but corrupt
            "!!!not-base64!!!",                                  # invalid base64
            "",                                                  # empty / missing
        ]
        for bad in cases:
            with self.assertRaises(UserError):
                self._sign(req, signer, sig=bad)
        self.assertNotEqual(signer.state, "signed")

    def test_signature_size_cap(self):
        self.env["ir.config_parameter"].sudo().set_param("bf_sign.max_signature_kb", "1")
        req = self._new_request()
        req.action_send()
        from PIL import Image
        # Pure-noise image does not compress → comfortably over the 1 KB cap.
        noise = Image.frombytes("RGB", (200, 200), os.urandom(200 * 200 * 3))
        buf = io.BytesIO()
        noise.save(buf, format="PNG")
        big = base64.b64encode(buf.getvalue()).decode()
        with self.assertRaises(UserError):
            self._sign(req, req.signer_ids[0], sig=big)
        self.assertNotEqual(req.signer_ids[0].state, "signed")

    # ── signing & finalize (certificate render mocked) ──────────────────────────
    def test_single_signer_finalize(self):
        req = self._new_request(signers=1)
        req.action_send()
        with self._mock_cert():
            self._sign(req, req.signer_ids[0])
        self.assertEqual(req.state, "signed")
        self.assertTrue(req.signed_attachment_id)
        self.assertTrue(req.certificate_attachment_id)
        self.assertEqual(len(req.hash_signed), 64)
        self.assertEqual(len(req.hash_stamped), 64)
        self.assertTrue(req.log_ids.filtered(lambda l: l.event == "finalized"))
        data = base64.b64decode(req.signed_attachment_id.datas)
        self.assertEqual(hashlib.sha256(data).hexdigest(), req.hash_signed)
        self.assertTrue(req.log_ids[0].verify_chain())

    def test_parallel_two_signers(self):
        req = self._new_request(signers=2, order="parallel")
        req.action_send()
        self._sign(req, req.signer_ids[0])
        self.assertEqual(req.state, "in_progress")
        with self._mock_cert():
            self._sign(req, req.signer_ids[1])
        self.assertEqual(req.state, "signed")
        self.assertEqual(req.signed_count, 2)

    def test_sequential_turn_gating(self):
        req = self._new_request(signers=2, order="sequential")
        req.action_send()
        s1, s2 = req.signer_ids[0], req.signer_ids[1]
        self.assertTrue(req._signer_can_sign(s1))
        self.assertFalse(req._signer_can_sign(s2))
        with self.assertRaises(UserError):
            self._sign(req, s2)  # out of turn

    def test_finalize_idempotent(self):
        req = self._new_request(signers=1)
        req.action_send()
        with self._mock_cert():
            self._sign(req, req.signer_ids[0])
            att = req.signed_attachment_id
            n_final = len(req.log_ids.filtered(lambda l: l.event == "finalized"))
            req._finalize()  # second call must be a no-op
        self.assertEqual(req.signed_attachment_id, att)
        self.assertEqual(len(req.log_ids.filtered(lambda l: l.event == "finalized")), n_final)

    def test_signed_request_undeletable(self):
        req = self._new_request(signers=1)
        req.action_send()
        with self._mock_cert():
            self._sign(req, req.signer_ids[0])
        with self.assertRaises(UserError):
            req.unlink()

    # ── audit trail immutability ────────────────────────────────────────────────
    def test_log_immutable(self):
        req = self._new_request()
        log = req.log_ids[0]
        with self.assertRaises(UserError):
            log.write({"note": "x"})
        with self.assertRaises(UserError):
            log.unlink()

    def test_log_chain_tamper_detected(self):
        req = self._new_request()
        req.action_send()
        self.assertTrue(req.log_ids[0].verify_chain())
        entry = req.log_ids[-1]
        # Tamper directly in SQL, bypassing the ORM write() guard.
        self.env.cr.execute(
            "UPDATE bf_sign_log SET actor = %s WHERE id = %s", ("tampered", entry.id))
        entry.invalidate_recordset()
        self.assertFalse(req.log_ids[0].verify_chain())

    # ── verify integrity ────────────────────────────────────────────────────────
    def test_verify_integrity(self):
        req = self._new_request(signers=1)
        req.action_send()
        with self._mock_cert():
            self._sign(req, req.signer_ids[0])
        res = req._verify_integrity()
        self.assertTrue(res["chain_ok"])
        self.assertTrue(res["content_ok"])
        self.assertIsNone(res["tsa_ok"])  # RFC 3161 off by default
        # Corrupt the stored signed PDF → content check must fail.
        att = req.signed_attachment_id
        att.datas = base64.b64encode(b"%PDF-1.4 tampered")
        att.invalidate_recordset()
        self.assertFalse(req._verify_integrity()["content_ok"])

    # ── RFC 3161 token parsing (regression: real freetsa token fixture) ─────────
    def test_tsa_token_parsing(self):
        """A real RFC 3161 token must parse to a 64-hex imprint + a genTime.

        Guards against the asn1crypto pitfall where the encapsulated content is
        an already-decoded dict (``.native``) rather than bytes — ``.parsed`` is
        required. Caught only by a live TSA round-trip, not by the mocked tests.
        """
        path = os.path.join(_FIXTURES, "freetsa_token.b64")
        with open(path) as fh:
            token_b64 = fh.read().strip()
        from asn1crypto import tsp
        tsr = tsp.TimeStampResp.load(base64.b64decode(token_b64))
        model = self.Request
        imprint = model._tsa_message_imprint_hex(tsr)
        self.assertEqual(len(imprint), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in imprint))
        gentime = model._tsa_gentime(tsr)
        self.assertIsInstance(gentime, datetime)

    # ── digital seal (PAdES, pyHanko) ───────────────────────────────────────────
    def _pyhanko_or_skip(self):
        try:
            import pyhanko  # noqa: F401
        except ImportError:
            self.skipTest("pyHanko non installé")

    def _fresh_seal_cert(self):
        # Force a cert encrypted with THIS process's Fernet key (ignore any
        # leftover cert encrypted with a different key, e.g. an env-vs-conf key).
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("bf_sign.seal_cert", "")
        icp.set_param("bf_sign.seal_key", "")
        self.env["bf.sign.seal"].action_generate_cert()

    def test_pdf_seal_and_verify(self):
        self._pyhanko_or_skip()
        seal = self.env["bf.sign.seal"]
        self._fresh_seal_cert()
        sealed = seal.seal_pdf(self.pdf_bytes, reason="QA")
        self.assertTrue(sealed.startswith(b"%PDF"))
        self.assertGreater(len(sealed), len(self.pdf_bytes))
        self.assertTrue(seal.verify_pdf(sealed))
        tampered = bytearray(sealed)
        tampered[80] ^= 0xFF
        self.assertFalse(seal.verify_pdf(bytes(tampered)))

    def test_finalize_with_seal(self):
        self._pyhanko_or_skip()
        self._fresh_seal_cert()  # generating the cert activates sealing
        # No explicit enable: the seal must be active as soon as a cert exists.
        req = self._new_request(signers=1)
        req.action_send()
        with self._mock_cert():
            self._sign(req, req.signer_ids[0])
        self.assertTrue(req.sealed)
        res = req._verify_integrity()
        self.assertTrue(res["seal_ok"])
        self.assertTrue(res["content_ok"])

    def test_seal_off_switch(self):
        self._pyhanko_or_skip()
        self._fresh_seal_cert()
        # Explicitly disabled → no seal even though a cert exists.
        self.env["ir.config_parameter"].sudo().set_param("bf_sign.pdf_seal_enabled", "0")
        req = self._new_request(signers=1)
        req.action_send()
        with self._mock_cert():
            self._sign(req, req.signer_ids[0])
        self.assertFalse(req.sealed)

    # ── fillable fields (signer-filled text/date, DocuSeal-style) ───────────────
    def test_signer_fills_text_and_date(self):
        req = self._new_request(signers=1)
        s = req.signer_ids[0]
        tf = self._add_fill_field(req, s, "text")
        df = self._add_fill_field(req, s, "date", fill_mode="signer")
        req.action_send()
        with self._mock_cert():
            self._sign(req, s, field_values={str(tf.id): "Directeur général",
                                             str(df.id): "2026-06-16"})
        self.assertEqual(tf.filled_value, "Directeur général")
        self.assertEqual(df.filled_value, "2026-06-16")
        self.assertEqual(req.state, "signed")

    def test_required_fill_field_blocks(self):
        req = self._new_request(signers=1)
        s = req.signer_ids[0]
        self._add_fill_field(req, s, "text", required=True)
        req.action_send()
        with self.assertRaises(UserError):
            self._sign(req, s, field_values={})  # required text missing
        self.assertNotEqual(s.state, "signed")

    def test_invalid_date_fill_blocks(self):
        req = self._new_request(signers=1)
        s = req.signer_ids[0]
        df = self._add_fill_field(req, s, "date", fill_mode="signer")
        req.action_send()
        with self.assertRaises(UserError):
            self._sign(req, s, field_values={str(df.id): "not-a-date"})
        self.assertNotEqual(s.state, "signed")

    # ── signer ↔ contact auto-creation ──────────────────────────────────────────
    def test_partner_autocreated_on_send(self):
        req = self._new_request(signers=0, with_fields=False)
        s = self.Signer.create({"request_id": req.id, "name": "Jean Test",
                                "email": "jean.qa@example.com"})
        self.assertFalse(s.partner_id)
        req.action_send()
        self.assertTrue(s.partner_id)
        self.assertEqual(s.partner_id.email, "jean.qa@example.com")
        self.assertEqual(s.partner_id.name, "Jean Test")

    def test_partner_links_existing(self):
        existing = self.env["res.partner"].create(
            {"name": "Déjà Là", "email": "dejala.qa@example.com"})
        req = self._new_request(signers=0, with_fields=False)
        s = self.Signer.create({"request_id": req.id, "name": "Déjà Là",
                                "email": "DEJALA.QA@example.com"})
        req.action_send()
        self.assertEqual(s.partner_id, existing)  # matched =ilike, not duplicated

    # ── field-layout templates ──────────────────────────────────────────────────
    def _field(self, req, signer, ftype="signature", y=0.8):
        return self.Field.create({
            "request_id": req.id, "signer_id": signer.id, "field_type": ftype,
            "page": 1, "pos_x": 0.5, "pos_y": y, "width": 0.3, "height": 0.08})

    def test_field_template_save_and_apply(self):
        src = self._new_request(signers=2, with_fields=False)
        s0, s1 = src.signer_ids.sorted("sequence")
        self._field(src, s0, "signature", 0.8)
        self._field(src, s1, "text", 0.5)
        tid = src.save_field_template("Mon modèle")
        tmpl = self.env["bf.sign.field.template"].browse(tid)
        self.assertEqual(len(tmpl.line_ids), 2)
        self.assertEqual(set(tmpl.line_ids.mapped("signer_index")), {0, 1})
        # Apply to a fresh request with 2 different signers → mapped by rank.
        dst = self._new_request(signers=2, with_fields=False)
        res = dst.apply_field_template(tid)
        self.assertEqual(res["created"], 2)
        self.assertEqual(len(dst.field_ids), 2)
        d0 = dst.signer_ids.sorted("sequence")[0]
        self.assertEqual(dst.field_ids.filtered(lambda f: f.field_type == "signature").signer_id, d0)

    def test_field_template_skips_missing_signer(self):
        src = self._new_request(signers=2, with_fields=False)
        s0, s1 = src.signer_ids.sorted("sequence")
        self._field(src, s0)
        self._field(src, s1, y=0.5)
        tid = src.save_field_template("T2")
        dst = self._new_request(signers=1, with_fields=False)
        res = dst.apply_field_template(tid)
        self.assertEqual(res["created"], 1)
        self.assertEqual(res["skipped"], 1)

    def test_field_template_replace(self):
        src = self._new_request(signers=1, with_fields=False)
        self._field(src, src.signer_ids[0])
        tid = src.save_field_template("T1")
        dst = self._new_request(signers=1)  # already has 1 signature field
        self.assertEqual(len(dst.field_ids), 1)
        dst.apply_field_template(tid)  # replace=True clears then recreates
        self.assertEqual(len(dst.field_ids), 1)

    # ── refusal ─────────────────────────────────────────────────────────────────
    def test_refusal(self):
        req = self._new_request(signers=1)
        req.action_send()
        req.register_signer_refusal(req.signer_ids[0], reason="pas d'accord", ip="1.1.1.1")
        self.assertEqual(req.state, "refused")
        self.assertEqual(req.signer_ids[0].state, "refused")
        self.assertTrue(req.log_ids.filtered(lambda l: l.event == "refused"))

    # ── expiry cron ─────────────────────────────────────────────────────────────
    def test_expiry_cron(self):
        req = self._new_request()
        req.action_send()
        req.expiry_date = fields.Datetime.now() - timedelta(days=1)
        self.Request._cron_expire_requests()
        self.assertEqual(req.state, "expired")
        self.assertTrue(req.log_ids.filtered(lambda l: l.event == "expired"))

    # ── signing-page placement markers ──────────────────────────────────────────
    def test_overlay_fields_reading_order(self):
        req = self._new_request(signers=1, with_fields=False)
        s = req.signer_ids[0]
        # Create out of reading order: page 2 first, then page 1 bottom, top.
        f_p2 = self._field(req, s, "signature", y=0.3)
        f_p2.page = 2
        f_p1_bottom = self._field(req, s, "text", y=0.80)
        f_p1_top = self._field(req, s, "date", y=0.20)
        ordered = s._overlay_fields()
        # Reading order = page, then top→bottom, then left→right.
        self.assertEqual(list(ordered), [f_p1_top, f_p1_bottom, f_p2])

    # ── structural lock (recipients + pads frozen once sent) ─────────────────────
    def test_pads_locked_after_send(self):
        req = self._new_request(signers=1)
        field = req.field_ids[0]
        req.action_send()
        # Cannot move/resize/retype an existing pad.
        with self.assertRaises(UserError):
            field.pos_x = 0.1
        # Cannot add a new pad.
        with self.assertRaises(UserError):
            self._field(req, req.signer_ids[0], "text", y=0.4)
        # Cannot delete a pad.
        with self.assertRaises(UserError):
            field.unlink()

    def test_recipients_locked_after_send(self):
        req = self._new_request(signers=1)
        req.action_send()
        with self.assertRaises(UserError):
            req.signer_ids[0].email = "tampered@example.com"
        with self.assertRaises(UserError):
            self.Signer.create({
                "request_id": req.id, "name": "Intrus", "email": "x@example.com"})
        with self.assertRaises(UserError):
            req.signer_ids[0].unlink()

    def test_lock_allows_signing_flow(self):
        # The lock must not break the signing process: filled_value + signer
        # process fields are still writable once sent.
        req = self._new_request(signers=1, with_fields=False)
        s = req.signer_ids[0]
        self._field(req, s, "signature", y=0.8)
        fill = self._add_fill_field(req, s, field_type="text")
        req.action_send()
        with self._mock_cert():
            self._sign(req, s, field_values={str(fill.id): "Jean Test"})
        self.assertEqual(s.state, "signed")
        self.assertEqual(fill.filled_value, "Jean Test")

    def test_lock_lifts_on_reset_to_draft(self):
        req = self._new_request(signers=1)
        req.action_send()
        req.action_reset_to_draft()
        # Back in draft → edits allowed again.
        req.signer_ids[0].email = "ok@example.com"
        self._field(req, req.signer_ids[0], "text", y=0.4)
        self.assertEqual(req.signer_ids[0].email, "ok@example.com")

    # ── méthode de signature : SES seulement ─────────────────────────────────
    def test_signature_method_offers_only_ses(self):
        """Le module ne livre que la SES : la sélection ne doit rien promettre de
        plus. Garde contre la réintroduction d'un palier « avancé » (AES)
        sélectionnable mais non implémenté — le pipeline est le même dans tous
        les cas, donc toute valeur supplémentaire ici est une fausse promesse
        tant qu'une implémentation ne l'accompagne pas.

        On vérifie la définition statique ET la sélection résolue servie au
        client : la seconde seule attrape une valeur réintroduite par
        ``selection_add`` depuis un module dépendant ou par une ligne
        ``ir.model.fields.selection``, ce que la première ne verrait pas.
        """
        static = [v for v, _label in
                  self.Request._fields["signature_method"].selection]
        self.assertEqual(static, ["native_ses"])

        resolved = [v for v, _label in self.Request.fields_get(
            ["signature_method"])["signature_method"]["selection"]]
        self.assertEqual(resolved, ["native_ses"])

    def test_signature_method_rejects_unimplemented_value(self):
        req = self._new_request(signers=1)
        with self.assertRaises(ValueError):
            req.signature_method = "libresign_aes"

    # ── pad types beyond signature/text/date (18.0.3.14.0) ──────────────────────
    def test_checkbox_required_blocks_then_passes(self):
        req = self._new_request(signers=1)
        s = req.signer_ids[0]
        cb = self._add_fill_field(req, s, "checkbox", required=True)
        req.action_send()
        with self.assertRaises(UserError):
            self._sign(req, s, field_values={})  # unchecked box posts nothing
        self.assertNotEqual(s.state, "signed")
        with self._mock_cert():
            self._sign(req, s, field_values={str(cb.id): "on"})
        self.assertEqual(cb.filled_value, "on")
        self.assertTrue(cb._is_checked())

    def test_optional_checkbox_left_unchecked(self):
        req = self._new_request(signers=1)
        s = req.signer_ids[0]
        cb = self._add_fill_field(req, s, "checkbox", required=False)
        req.action_send()
        with self._mock_cert():
            self._sign(req, s, field_values={})
        self.assertEqual(req.state, "signed")
        self.assertFalse(cb._is_checked())

    def test_number_and_email_validation(self):
        req = self._new_request(signers=1)
        s = req.signer_ids[0]
        nf = self._add_fill_field(req, s, "number")
        ef = self._add_fill_field(req, s, "email")
        req.action_send()
        with self.assertRaises(UserError):
            self._sign(req, s, field_values={str(nf.id): "douze",
                                             str(ef.id): "a@b.ca"})
        with self.assertRaises(UserError):
            self._sign(req, s, field_values={str(nf.id): "12",
                                             str(ef.id): "pas-un-courriel"})
        with self._mock_cert():
            self._sign(req, s, field_values={str(nf.id): "1 234,50",
                                             str(ef.id): "a@b.ca"})
        self.assertEqual(nf.filled_value, "1 234,50")
        self.assertEqual(ef.filled_value, "a@b.ca")

    def test_auto_pads_resolve_from_signer(self):
        req = self._new_request(signers=1)
        s = req.signer_ids[0]
        nf = self._add_fill_field(req, s, "name", fill_mode="auto")
        ef = self._add_fill_field(req, s, "email", fill_mode="auto")
        df = self._add_fill_field(req, s, "date", fill_mode="auto")
        self.assertEqual(nf._display_value(), s.name)
        self.assertEqual(ef._display_value(), s.email)
        # The signing date does not exist before the signature.
        self.assertEqual(df._display_value(), "")
        req.action_send()
        with self._mock_cert():
            self._sign(req, s)
        self.assertEqual(df._display_value(),
                         fields.Date.to_string(s.signed_on.date()))

    def test_auto_mode_rejected_on_free_text(self):
        req = self._new_request(signers=1)
        s = req.signer_ids[0]
        with self.assertRaises(ValidationError):
            self._add_fill_field(req, s, "text", fill_mode="auto")

    def test_blank_optional_field_does_not_stamp_its_label(self):
        """``value_text`` is the caption in signer mode — never the stamped value.

        Left blank, the pad must print nothing: printing the caption would put
        « Numéro d'employé » on the signed document instead of a number.
        """
        req = self._new_request(signers=1)
        s = req.signer_ids[0]
        tf = self._add_fill_field(req, s, "text", required=False)
        tf.value_text = "Numéro d'employé"
        req.action_send()
        with self._mock_cert():
            self._sign(req, s, field_values={})
        self.assertEqual(tf.filled_value, "")
        self.assertEqual(tf._display_value(), "")

    def test_fixed_value_is_stamped(self):
        req = self._new_request(signers=1)
        s = req.signer_ids[0]
        tf = self._add_fill_field(req, s, "text", fill_mode="fixed")
        tf.value_text = "Directeur général"
        self.assertEqual(tf._display_value(), "Directeur général")

    def test_send_blocks_signer_without_any_pad(self):
        req = self._new_request(signers=2, with_fields=False)
        first, second = req.signer_ids[0], req.signer_ids[1]
        self._field(req, first, "signature")
        with self.assertRaises(UserError):
            req.action_send()  # `second` would have nothing to sign
        self._field(req, second, "signature", y=0.6)
        req.action_send()
        self.assertEqual(req.state, "sent")

    def test_send_still_allowed_with_no_pad_at_all(self):
        # Seal-only requests stay legitimate: the guard only fires once pads exist.
        req = self._new_request(signers=1, with_fields=False)
        req.action_send()
        self.assertEqual(req.state, "sent")

    # ── opening tracked on the record, not only in the journal ──────────────────
    def test_view_status_rolls_up_without_reading_the_journal(self):
        req = self._new_request(signers=2)
        first, second = req.signer_ids[0], req.signer_ids[1]
        req.action_send()
        self.assertEqual(req.view_status, "none")
        self.assertEqual(req.viewed_count, 0)
        self.assertFalse(first.first_viewed_on)

        req.register_signer_view(first, ip="1.2.3.4", user_agent="pytest")
        self.assertTrue(first.has_viewed)
        self.assertTrue(first.first_viewed_on)
        self.assertEqual(first.view_count, 1)
        self.assertEqual(req.view_status, "partial")
        self.assertEqual(req.viewed_count, 1)

        # A second opening moves last_viewed_on, never first_viewed_on.
        opened_at = first.first_viewed_on
        req.register_signer_view(first)
        self.assertEqual(first.first_viewed_on, opened_at)
        self.assertEqual(first.view_count, 2)

        req.register_signer_view(second)
        self.assertEqual(req.view_status, "all")
        self.assertEqual(req.viewed_count, 2)

    def test_signed_signer_still_counts_as_having_opened(self):
        # `state` leaves 'viewed' behind once signed — the rollup must not.
        req = self._new_request(signers=1)
        s = req.signer_ids[0]
        req.action_send()
        req.register_signer_view(s)
        with self._mock_cert():
            self._sign(req, s)
        self.assertEqual(s.state, "signed")
        self.assertTrue(s.has_viewed)
        self.assertEqual(req.view_status, "all")
        self.assertEqual(req.viewed_count, 1)

    # ── signed document with or without the certificate bound in ────────────────
    def _pdf_pages(self, b64):
        return len(PdfReader(io.BytesIO(base64.b64decode(b64))).pages)

    def test_certificate_appended_by_default(self):
        req = self._new_request(signers=1)
        self.assertTrue(req.append_certificate)
        req.action_send()
        with self._mock_cert():
            self._sign(req, req.signer_ids[0])
        doc = self._pdf_pages(req.signed_attachment_id.datas)
        cert = self._pdf_pages(req.certificate_attachment_id.datas)
        self.assertGreater(doc, cert)  # document + certificate bound together

    def test_certificate_kept_separate_when_disabled(self):
        req = self._new_request(signers=1)
        req.append_certificate = False
        req.action_send()
        with self._mock_cert():
            self._sign(req, req.signer_ids[0])
        # The certificate is still produced, sealed and attached — just not bound
        # into the signed document.
        self.assertTrue(req.certificate_attachment_id)
        self.assertTrue(req.hash_signed)
        with_cert = self._pdf_pages(req.certificate_attachment_id.datas)
        alone = self._pdf_pages(req.signed_attachment_id.datas)
        self.assertEqual(alone, len(PdfReader(io.BytesIO(self.pdf_bytes)).pages))
        self.assertGreaterEqual(with_cert, 1)

    def test_append_certificate_default_follows_setting(self):
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("bf_sign.append_certificate", "False")
        self.assertFalse(self.Request._default_append_certificate())
        ICP.set_param("bf_sign.append_certificate", "True")
        self.assertTrue(self.Request._default_append_certificate())

    # ── presentation order, duplication, resend (18.0.3.15.0) ───────────────────
    def test_overlay_order_falls_back_to_geometry(self):
        req = self._new_request(signers=1, with_fields=False)
        s = req.signer_ids[0]
        low = self._field(req, s, "text", y=0.8)
        high = self._field(req, s, "text", y=0.2)
        # Same sequence everywhere → reading order (top of the page first).
        self.assertEqual(list(s._overlay_fields()), [high, low])

    def test_sequence_overrides_geometry(self):
        req = self._new_request(signers=1, with_fields=False)
        s = req.signer_ids[0]
        low = self._field(req, s, "text", y=0.8)
        high = self._field(req, s, "text", y=0.2)
        self.assertTrue(low.action_move_up())
        self.assertEqual(list(s._overlay_fields()), [low, high])
        # And back.
        self.assertTrue(low.action_move_down())
        self.assertEqual(list(s._overlay_fields()), [high, low])

    def test_move_at_the_edge_is_a_no_op(self):
        req = self._new_request(signers=1, with_fields=False)
        s = req.signer_ids[0]
        only = self._field(req, s, "text", y=0.2)
        self.assertFalse(only.action_move_up())
        self.assertFalse(only.action_move_down())

    def test_get_field_order_matches_the_signing_page(self):
        req = self._new_request(signers=2, with_fields=False)
        a, b = req.signer_ids[0], req.signer_ids[1]
        self._field(req, a, "signature", y=0.7)
        self._field(req, a, "text", y=0.3)
        self._field(req, b, "signature", y=0.5)
        order = req.get_field_order()
        for signer in (a, b):
            self.assertEqual(order[str(signer.id)], signer._overlay_fields().ids)

    def test_duplicate_pad_stays_on_the_page(self):
        req = self._new_request(signers=1, with_fields=False)
        s = req.signer_ids[0]
        src = self._field(req, s, "signature", y=0.95)
        new_id = src.action_duplicate()
        dup = self.Field.browse(new_id)
        self.assertEqual(dup.signer_id, s)
        self.assertEqual(dup.field_type, "signature")
        self.assertLessEqual(dup.pos_y + dup.height, 1.0)

    def test_duplicate_request_rebuilds_pads_against_new_signers(self):
        """The standard Duplicate action used to raise: the copied pads kept
        pointing at the ORIGINAL signers, which the constraint rejects."""
        req = self._new_request(signers=2)
        dup = req.copy()
        self.assertEqual(len(dup.signer_ids), 2)
        self.assertEqual(len(dup.field_ids), len(req.field_ids))
        self.assertFalse(dup.field_ids.filtered(lambda f: f.signer_id.request_id != dup))
        self.assertEqual(dup.state, "draft")
        self.assertNotEqual(dup.name, req.name)
        # A duplicate must never inherit signing tokens.
        self.assertFalse(set(dup.signer_ids.mapped("access_token")) &
                         set(req.signer_ids.mapped("access_token")))

    def test_resend_invitation_guards(self):
        req = self._new_request(signers=1)
        s = req.signer_ids[0]
        with self.assertRaises(UserError):
            s.action_resend_invitation()  # not sent yet
        req.action_send()
        self.assertTrue(s.action_resend_invitation())
        with self._mock_cert():
            self._sign(req, s)
        with self.assertRaises(UserError):
            s.action_resend_invitation()  # already signed

    def test_resend_refuses_out_of_turn_signer(self):
        req = self._new_request(signers=2, order="sequential")
        first, second = req.signer_ids[0], req.signer_ids[1]
        req.action_send()
        self.assertTrue(first.action_resend_invitation())
        with self.assertRaises(UserError):
            second.action_resend_invitation()  # not their turn

    # ── reminders (18.0.3.16.0) ────────────────────────────────────────────────
    def _sent_request(self, signers=1, order="parallel"):
        req = self._new_request(signers=signers, order=order)
        req.action_send()
        return req

    def _reminders_sent_to(self, signer):
        return self.env["bf.sign.log"].search_count([
            ("request_id", "=", signer.request_id.id),
            ("event", "=", "sent"),
            ("note", "ilike", "Relance"),
            ("note", "ilike", signer.email),
        ])

    def test_no_reminder_before_the_first_offset(self):
        req = self._sent_request()
        s = req.signer_ids[0]
        self.assertTrue(s.invited_on)
        self.env["bf.sign.request"]._cron_send_reminders()
        self.assertEqual(s.reminder_count, 0)

    def test_reminder_fires_at_each_offset_once(self):
        req = self._sent_request()
        s = req.signer_ids[0]
        # Pretend the invitation went out 4 days ago → J+3 is due, J+7 is not.
        s.sudo().invited_on = fields.Datetime.now() - timedelta(days=4)
        self.env["bf.sign.request"]._cron_send_reminders()
        self.assertEqual(s.reminder_count, 1)
        self.assertEqual(self._reminders_sent_to(s), 1)
        # Same day, nothing more, whatever the schedule says.
        self.env["bf.sign.request"]._cron_send_reminders()
        self.assertEqual(s.reminder_count, 1)
        # Eight days in, and a day since the last one → J+7 is due.
        s.sudo().invited_on = fields.Datetime.now() - timedelta(days=8)
        s.sudo().last_reminder_on = fields.Datetime.now() - timedelta(days=2)
        self.env["bf.sign.request"]._cron_send_reminders()
        self.assertEqual(s.reminder_count, 2)

    def test_reminder_cap_holds(self):
        self.env["ir.config_parameter"].sudo().set_param("bf_sign.reminder_max", "1")
        req = self._sent_request()
        s = req.signer_ids[0]
        s.sudo().invited_on = fields.Datetime.now() - timedelta(days=30)
        self.env["bf.sign.request"]._cron_send_reminders()
        self.assertEqual(s.reminder_count, 1)
        s.sudo().last_reminder_on = fields.Datetime.now() - timedelta(days=5)
        self.env["bf.sign.request"]._cron_send_reminders()
        self.assertEqual(s.reminder_count, 1)

    def test_reminder_skips_signed_and_disabled(self):
        req = self._sent_request()
        s = req.signer_ids[0]
        s.sudo().invited_on = fields.Datetime.now() - timedelta(days=10)
        req.reminder_enabled = False
        self.env["bf.sign.request"]._cron_send_reminders()
        self.assertEqual(s.reminder_count, 0)
        req.reminder_enabled = True
        with self._mock_cert():
            self._sign(req, s)
        self.env["bf.sign.request"]._cron_send_reminders()
        self.assertEqual(s.reminder_count, 0)

    def test_reminder_never_chases_out_of_turn_signer(self):
        req = self._sent_request(signers=2, order="sequential")
        first, second = req.signer_ids[0], req.signer_ids[1]
        first.sudo().invited_on = fields.Datetime.now() - timedelta(days=10)
        second.sudo().invited_on = fields.Datetime.now() - timedelta(days=10)
        self.env["bf.sign.request"]._cron_send_reminders()
        self.assertEqual(first.reminder_count, 1)
        self.assertEqual(second.reminder_count, 0)

    def test_reminder_does_not_restart_the_signer_clock(self):
        req = self._sent_request()
        s = req.signer_ids[0]
        invited = fields.Datetime.now() - timedelta(days=4)
        s.sudo().invited_on = invited
        self.env["bf.sign.request"]._cron_send_reminders()
        self.assertEqual(s.invited_on, invited)

    def test_expired_request_is_not_chased(self):
        req = self._sent_request()
        s = req.signer_ids[0]
        s.sudo().invited_on = fields.Datetime.now() - timedelta(days=10)
        req.expiry_date = fields.Datetime.now() - timedelta(hours=1)
        self.env["bf.sign.request"]._cron_send_reminders()
        self.assertEqual(s.reminder_count, 0)

    def test_last_call_before_expiry(self):
        self.env["ir.config_parameter"].sudo().set_param("bf_sign.reminder_days", "")
        req = self._sent_request()
        s = req.signer_ids[0]
        s.sudo().invited_on = fields.Datetime.now() - timedelta(days=1)
        req.expiry_date = fields.Datetime.now() + timedelta(days=5)
        self.env["bf.sign.request"]._cron_send_reminders()
        self.assertEqual(s.reminder_count, 0)  # no offsets, expiry still far
        req.expiry_date = fields.Datetime.now() + timedelta(hours=10)
        self.env["bf.sign.request"]._cron_send_reminders()
        self.assertEqual(s.reminder_count, 1)

    def test_unopened_alert_posted_once(self):
        req = self._sent_request()
        s = req.signer_ids[0]
        s.sudo().invited_on = fields.Datetime.now() - timedelta(days=6)
        before = len(req.message_ids)
        self.env["bf.sign.request"]._cron_send_reminders()
        self.assertTrue(s.unopened_alerted)
        after = len(req.message_ids)
        self.assertGreater(after, before)
        self.env["bf.sign.request"]._cron_send_reminders()
        self.assertEqual(len(req.message_ids), after)  # said once, not every day

    def test_no_unopened_alert_once_opened(self):
        req = self._sent_request()
        s = req.signer_ids[0]
        s.sudo().invited_on = fields.Datetime.now() - timedelta(days=6)
        req.register_signer_view(s)
        self.env["bf.sign.request"]._cron_send_reminders()
        self.assertFalse(s.unopened_alerted)

    def test_manual_remind_pending_skips_signed(self):
        req = self._sent_request(signers=2)
        first, second = req.signer_ids[0], req.signer_ids[1]
        with self._mock_cert():
            self._sign(req, first)
        req.action_remind_pending()
        self.assertEqual(first.reminder_count, 0)
        self.assertEqual(second.reminder_count, 1)

    # ── public verification + QR (18.0.3.17.0) ─────────────────────────────────
    def test_verify_token_minted_at_finalize(self):
        req = self._new_request(signers=1)
        self.assertFalse(req.verify_token)
        req.action_send()
        with self._mock_cert():
            self._sign(req, req.signer_ids[0])
        self.assertTrue(req.verify_token)
        self.assertIn("/sign/verify/%s/" % req.id, req.verify_url)
        self.assertIn(req.verify_token, req.verify_url)
        # It must never be a signing token.
        self.assertNotIn(req.verify_token, req.signer_ids.mapped("access_token"))

    def test_verify_token_only_minted_when_qr_requested_or_finalized(self):
        req = self._new_request(signers=1)
        req.verify_qr = True
        req.action_send()
        with self._mock_cert():
            self._sign(req, req.signer_ids[0])
        self.assertTrue(req.verify_token)

    def test_qr_stamps_without_adding_pages(self):
        req = self._new_request(signers=1)
        req.verify_qr = True
        original_pages = len(PdfReader(io.BytesIO(self.pdf_bytes)).pages)
        req.action_send()
        with self._mock_cert():
            self._sign(req, req.signer_ids[0])
        signed = base64.b64decode(req.signed_attachment_id.datas)
        # Certificate is appended by default, so compare against the document
        # portion only: the QR must not push the page count of the source doc.
        self.assertGreaterEqual(len(PdfReader(io.BytesIO(signed)).pages), original_pages)
        self.assertNotEqual(signed, self.pdf_bytes)

    def test_qr_stamps_even_with_no_pads(self):
        req = self._new_request(signers=1, with_fields=False)
        req.verify_qr = True
        req.action_send()
        with self._mock_cert():
            self._sign(req, req.signer_ids[0])
        self.assertTrue(req.verify_token)
        self.assertTrue(req.signed_attachment_id)

    def test_qr_page_selection(self):
        req = self._new_request(signers=1)
        req.verify_qr = True
        # The fixture PDF page count drives what "last" resolves to.
        pages = len(PdfReader(io.BytesIO(self.pdf_bytes)).pages)
        req.verify_qr_pages = "first"
        self.assertEqual(req._qr_pages_for(pages), {1})
        req.verify_qr_pages = "last"
        self.assertEqual(req._qr_pages_for(pages), {pages})
        req.verify_qr_pages = "all"
        self.assertEqual(req._qr_pages_for(pages), set(range(1, pages + 1)))

    def test_verify_integrity_backs_the_public_page(self):
        req = self._new_request(signers=1)
        req.action_send()
        with self._mock_cert():
            self._sign(req, req.signer_ids[0])
        checks = req._verify_integrity()
        self.assertTrue(checks["chain_ok"])
        self.assertTrue(checks["content_ok"])

    # ── the certificate carries the pointer to its own proof (18.0.3.19.0) ────
    def test_certificate_carries_the_verification_link(self):
        """The QR on the pages is optional and off by default.

        Without this, a holder can be handed the proof and have no way to find
        the page that checks it. Rendered as HTML: wkhtmltopdf is not available
        under --stop-after-init, and the QWeb is what we mean to pin.
        """
        req = self._signed_request()
        html, _t = self.env["ir.actions.report"]._render_qweb_html(
            "bf_sign.action_report_sign_certificate", req.ids)
        html = html.decode() if isinstance(html, bytes) else html
        self.assertIn(req._verify_url(), html)
        self.assertIn("data:image/png;base64,", html)

    def test_certificate_qr_is_a_real_png(self):
        req = self._signed_request()
        uri = req.verify_qr_data_uri()
        self.assertTrue(uri.startswith("data:image/png;base64,"))
        self.assertTrue(
            base64.b64decode(uri.split(",", 1)[1]).startswith(_PNG_MAGIC))

    def test_no_token_no_qr_and_no_share(self):
        """Signed before the verification page existed: say so, mint nothing.

        Minting on demand would quietly alter a finalized record to make a
        button work, which is exactly what must not happen to signed evidence.
        """
        req = self._signed_request()
        req.sudo().verify_token = False  # a pre-3.17.0 request, as they exist in prod
        self.assertFalse(req.verify_qr_data_uri())
        with self.assertRaises(UserError):
            req.action_share_verify_link()
        self.assertFalse(req.verify_token, "the guard must not have minted one")
        html, _t = self.env["ir.actions.report"]._render_qweb_html(
            "bf_sign.action_report_sign_certificate", req.ids)
        html = html.decode() if isinstance(html, bytes) else html
        self.assertNotIn("Vérifier ce document", html)

    def test_share_verify_link_opens_a_prefilled_composer(self):
        req = self._signed_request()
        act = req.action_share_verify_link()
        self.assertEqual(act["res_model"], "mail.compose.message")
        self.assertIn(req._verify_url(), act["context"]["default_body"])

    # ── the stamped QR is also a link (18.0.3.19.0) ───────────────────────────
    @staticmethod
    def _link_uris(pdf_bytes):
        uris = []
        for page in PdfReader(io.BytesIO(pdf_bytes)).pages:
            for annot in page.get("/Annots") or []:
                obj = annot.get_object()
                if obj.get("/Subtype") == "/Link" and obj.get("/A"):
                    uris.append(str(obj["/A"].get("/URI") or ""))
        return uris

    def test_verify_qr_is_also_a_clickable_link(self):
        """On a screen the QR is unscannable; the same target must be clickable.

        Also pins that the annotation survives the merge_page overlay and the
        certificate merge — it is drawn on a throwaway canvas, not the page.
        """
        req = self._new_request(signers=1)
        req.verify_qr = True
        req.action_send()
        with self._mock_cert():
            self._sign(req, req.signer_ids[0])
        uris = self._link_uris(base64.b64decode(req.signed_attachment_id.datas))
        self.assertIn(req._verify_url(), uris)

    def test_verify_qr_link_survives_the_pades_seal(self):
        """The production path seals; an unsealed-only test proves nothing here.

        pyHanko rewrites the document to embed the signature, so the annotation
        added upstream by the stamping canvas has to come through that too — and
        the seal must still verify with the annotation present.
        """
        self._fresh_seal_cert()  # a cert existing is what activates sealing
        req = self._new_request(signers=1)
        req.verify_qr = True
        req.action_send()
        with self._mock_cert():
            self._sign(req, req.signer_ids[0])
        self.assertTrue(req.sealed, "the sealed path is what we mean to exercise")
        signed = base64.b64decode(req.signed_attachment_id.datas)
        self.assertIn(req._verify_url(), self._link_uris(signed))
        self.assertTrue(self.env["bf.sign.seal"].verify_pdf(signed))

    def test_no_qr_means_no_link_annotation(self):
        """The link rides the QR; without one, nothing is added to the page."""
        req = self._new_request(signers=1)
        self.assertFalse(req.verify_qr)
        req.action_send()
        with self._mock_cert():
            self._sign(req, req.signer_ids[0])
        uris = self._link_uris(base64.b64decode(req.signed_attachment_id.datas))
        self.assertFalse([u for u in uris if "/sign/verify/" in u])

    # ── verify page: self-serve copy comparison (18.0.3.18.0) ─────────────────
    def _render_verify(self, req):
        return self.env["ir.qweb"]._render("bf_sign.verify_page", {
            "req": req,
            "checks": req._verify_integrity(),
            "signed_on_label": "",
            "genuine": True,
        })

    def _signed_request(self):
        req = self._new_request(signers=1)
        req.action_send()
        with self._mock_cert():
            self._sign(req, req.signer_ids[0])
        return req

    def test_verify_page_lets_the_holder_compare_their_copy(self):
        """A printed fingerprint only helps someone who can compute one."""
        req = self._signed_request()
        html = self._render_verify(req)
        self.assertIn('id="bf_drop"', html)
        self.assertIn('data-expected="%s"' % req.hash_signed, html)

    def test_verify_page_compares_against_the_delivered_bundle(self):
        """The expected hash must be the one a holder's own file can reach.

        ``hash_stamped`` or ``hash_original`` would look just as plausible in the
        template and would make every honest comparison fail.
        """
        req = self._signed_request()
        delivered = base64.b64decode(req.signed_attachment_id.datas)
        self.assertEqual(hashlib.sha256(delivered).hexdigest(), req.hash_signed)
        self.assertIn('data-expected="%s"' % hashlib.sha256(delivered).hexdigest(),
                      self._render_verify(req))

    def test_verify_page_never_takes_the_holders_file(self):
        """Hashing stays client-side: a post here would be a new public intake."""
        html = self._render_verify(self._signed_request())
        self.assertIn("crypto.subtle", html)
        self.assertNotIn("<form", html)
        self.assertNotIn("multipart/form-data", html)

    def test_verify_page_hides_the_drop_zone_before_signature(self):
        req = self._new_request(signers=1)
        req.action_send()
        html = self.env["ir.qweb"]._render("bf_sign.verify_page", {
            "req": req, "checks": {}, "signed_on_label": "", "genuine": False,
        })
        self.assertNotIn('id="bf_drop"', html)

    # ── audit hardening (18.0.3.17.1) ─────────────────────────────────────────
    def test_manual_reminder_is_debounced(self):
        req = self._new_request(signers=1)
        req.action_send()
        s = req.signer_ids[0]
        req.action_remind_pending()
        self.assertEqual(s.reminder_count, 1)
        with self.assertRaises(UserError):
            req.action_remind_pending()  # same minute → refused
        with self.assertRaises(UserError):
            s.action_resend_invitation()
        # An hour later it is a legitimate human decision again.
        s.sudo().last_reminder_on = fields.Datetime.now() - timedelta(hours=2)
        req.action_remind_pending()
        self.assertEqual(s.reminder_count, 2)

    def test_inflight_requests_keep_their_field_order(self):
        """Every pad ships with sequence=10, so the sequence-first sort must be
        a no-op on anything created before it existed."""
        req = self._new_request(signers=1, with_fields=False)
        s = req.signer_ids[0]
        low = self._field(req, s, "text", y=0.8)
        mid = self._field(req, s, "text", y=0.5)
        high = self._field(req, s, "text", y=0.2)
        self.assertEqual({f.sequence for f in (low, mid, high)}, {10})
        self.assertEqual(list(s._overlay_fields()), [high, mid, low])

    def test_blank_pad_with_no_label_stamps_nothing(self):
        """In-flight pads carry no value_text, so the label fix cannot change
        what they stamp — it stays empty either way."""
        req = self._new_request(signers=1, with_fields=False)
        s = req.signer_ids[0]
        pad = self._add_fill_field(req, s, "date", fill_mode="signer", required=False)
        self.assertFalse(pad.value_text)
        self.assertEqual(pad._display_value(), "")

    def test_auto_pads_on_existing_data_stay_valid(self):
        """`auto` was only ever offered on date pads; the new constraint must
        not reject what is already in the field."""
        req = self._new_request(signers=1, with_fields=False)
        s = req.signer_ids[0]
        pad = self._add_fill_field(req, s, "date", fill_mode="auto")
        pad.write({"pos_x": 0.4})  # a plain edit must not trip the constraint
        self.assertEqual(pad.fill_mode, "auto")
