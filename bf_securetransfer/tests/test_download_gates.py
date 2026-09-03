"""Watermarked delivery, the download notice, and the "what is downloadable"
predicate.

The watermark is the one place where Odoo streams the bytes itself instead of
redirecting to S3, so every branch that decides *not* to stamp is a branch that
changes what the recipient receives. And every stamping failure must degrade to
the plain file: a filigrane is a nice-to-have, a download is the product.

S3 is patched throughout (same convention as ``test_lifecycle``): the suite must
run on a build without boto3 and without any reachable endpoint.
"""
import io
from unittest.mock import MagicMock, patch

from odoo.tests import TransactionCase, tagged

from .common import LanguesActives

S3_MOD = "odoo.addons.bf_securetransfer.models.s3"
PDF_MOD = "odoo.addons.bf_securetransfer.models.pdf_watermark"


def _minimal_pdf():
    """A real one-page PDF, built with the same reportlab the module ships."""
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    doc = canvas.Canvas(buf)
    doc.drawString(72, 720, "QA")
    doc.showPage()
    doc.save()
    return buf.getvalue()


@tagged("post_install", "-at_install")
class TestDownloadGates(LanguesActives, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.brand = cls.env.ref("bf_securetransfer.brand_default")
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_ip", "500")
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_sender", "500")
        icp.set_param("bf_securetransfer.quota_daily_bytes_per_ip_mb", "1000000")

    def _transfer(self, **overrides):
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
        sizes = {f.s3_key: int(f.size) for f in transfer.file_ids}

        def _head(env, key):
            if key in sizes:
                return {"size": sizes[key], "etag": "etag-" + key[-8:]}
            return None
        return _head

    def _active(self, filename="doc.pdf", size=4096, **overrides):
        t = self._transfer(**overrides)
        t._register_file(filename, size)
        with patch(S3_MOD + ".head_object", side_effect=self._head_for(t)):
            t.action_finalize()
        return t

    # ------------------------------------------------------------------ downloadable predicate
    def test_downloadable_excludes_infected_and_errored(self):
        """The listing page, the per-file route and the burn accounting all
        read this one predicate. If it ever offered an infected file, the
        scanner verdict would be decorative."""
        t = self._transfer()
        good = t._register_file("ok.pdf", 1024)
        infected = t._register_file("bad.pdf", 1024)
        errored = t._register_file("broken.pdf", 1024)
        with patch(S3_MOD + ".head_object", side_effect=self._head_for(t)):
            t.action_finalize()
        infected.scanned = "infected"
        errored.state = "error"
        offered = t._downloadable_files()
        self.assertIn(good, offered)
        self.assertNotIn(infected, offered)
        self.assertNotIn(errored, offered)

    def test_downloadable_requires_verified_state(self):
        """A file still uploading has no confirmed bytes on S3 — offering it
        would hand out a truncated object."""
        t = self._transfer()
        f = t._register_file("late.pdf", 1024)
        with patch(S3_MOD + ".head_object", side_effect=self._head_for(t)):
            t.action_finalize()
        self.assertIn(f, t._downloadable_files())
        f.state = "uploading"
        self.assertNotIn(f, t._downloadable_files())

    # ------------------------------------------------------------------ watermark gating
    def test_no_watermark_when_brand_disables_it(self):
        """Returning None is what keeps the direct-to-S3 redirect: the bytes
        never touch Odoo for brands that did not ask for stamping."""
        t = self._active()
        self.brand.watermark_downloads = False
        self.assertIsNone(t._stamped_download_bytes(t.file_ids))

    def test_no_watermark_on_non_pdf(self):
        """Stamping a .zip would corrupt it."""
        t = self._active(filename="archive.zip")
        self.brand.watermark_downloads = True
        self.assertIsNone(t._stamped_download_bytes(t.file_ids))

    def test_no_watermark_above_size_ceiling(self):
        """Stamping loads the whole PDF in memory. Above the ceiling Odoo
        declines rather than risking an OOM on a shared worker."""
        t = self._active(filename="huge.pdf", size=1024)
        self.brand.watermark_downloads = True
        t.file_ids.size_confirmed = t._WATERMARK_MAX_BYTES + 1
        self.assertIsNone(t._stamped_download_bytes(t.file_ids))

    def test_watermark_stamps_when_all_conditions_met(self):
        """The nominal stamped path: Odoo fetches the object and returns
        bytes that are a valid, different PDF."""
        t = self._active(filename="contrat.pdf")
        self.brand.watermark_downloads = True
        src = _minimal_pdf()
        client = MagicMock()
        client.get_object.return_value = {"Body": io.BytesIO(src)}
        with patch(S3_MOD + ".client", return_value=client), \
                patch(S3_MOD + ".params", return_value={"bucket": "b"}):
            out = t._stamped_download_bytes(t.file_ids, ip="203.0.113.7")
        self.assertIsNotNone(out, "the nominal path must produce bytes")
        self.assertTrue(out.startswith(b"%PDF"), "output must still be a PDF")
        self.assertNotEqual(out, src, "the stamp must actually change the file")

    def test_watermark_failure_degrades_to_plain_download(self):
        """A stamping failure must never cost the recipient the download.
        This is the whole reason the caller treats None as 'serve plain'."""
        t = self._active(filename="contrat.pdf")
        self.brand.watermark_downloads = True
        client = MagicMock()
        client.get_object.side_effect = Exception("S3 exploded")
        with patch(S3_MOD + ".client", return_value=client), \
                patch(S3_MOD + ".params", return_value={"bucket": "b"}):
            self.assertIsNone(t._stamped_download_bytes(t.file_ids))

    def test_watermark_failure_on_corrupt_pdf_degrades(self):
        """An encrypted or corrupt PDF makes reportlab raise — same rule."""
        t = self._active(filename="corrupt.pdf")
        self.brand.watermark_downloads = True
        client = MagicMock()
        client.get_object.return_value = {"Body": io.BytesIO(b"not a pdf")}
        with patch(S3_MOD + ".client", return_value=client), \
                patch(S3_MOD + ".params", return_value={"bucket": "b"}):
            self.assertIsNone(t._stamped_download_bytes(t.file_ids))

    # ------------------------------------------------------------------ watermark content
    def test_watermark_names_the_single_recipient(self):
        """With exactly one recipient, the stamp can name them — that is the
        point of the filigrane: a leaked page points back to who received it."""
        t = self._active(recipient_emails="seul@example.com")
        lines = t._watermark_lines(t.file_ids, ip="203.0.113.7")
        joined = " ".join(lines)
        self.assertIn("seul@example.com", joined)

    def test_watermark_falls_back_to_ip_with_several_recipients(self):
        """With several recipients no single name is true, so naming one would
        be a lie printed on a document. The requesting IP is used instead."""
        t = self._active(recipient_emails="a@x.test, b@y.test")
        joined = " ".join(t._watermark_lines(t.file_ids, ip="203.0.113.7"))
        self.assertIn("203.0.113.7", joined)
        self.assertNotIn("a@x.test", joined)
        self.assertNotIn("b@y.test", joined)

    def test_watermark_without_ip_stays_generic(self):
        """No recipient to name and no IP to fall back on: the stamp must not
        invent an identity."""
        t = self._active(recipient_emails="a@x.test, b@y.test")
        joined = " ".join(t._watermark_lines(t.file_ids, ip=None))
        self.assertNotIn("a@x.test", joined)
        self.assertTrue(joined.strip(), "the stamp must still carry something")

    # ------------------------------------------------------------------ download notice
    def test_notice_fires_once_on_the_first_download_only(self):
        """The notice is per transfer, not per file: a 10-file transfer must
        not burst 10 notices into the sender's inbox."""
        t = self._transfer(recipient_emails="dest@example.com")
        t._register_file("a.pdf", 1024)
        t._register_file("b.pdf", 1024)
        with patch(S3_MOD + ".head_object", side_effect=self._head_for(t)):
            t.action_finalize()
        t.notify_on_download = True
        with patch.object(type(t), "_notify_download") as notice:
            t._register_download(t.file_ids[0], "203.0.113.1", "ua")
            t._register_download(t.file_ids[1], "203.0.113.1", "ua")
        self.assertEqual(notice.call_count, 1)

    def test_no_notice_without_a_sender_address(self):
        """Link-only mode has no sender mailbox to notify."""
        t = self._transfer()
        t._register_file("a.pdf", 1024)
        with patch(S3_MOD + ".head_object", side_effect=self._head_for(t)):
            t.action_finalize()
        t.notify_on_download = True
        t.sender_email = False
        with patch.object(type(t), "_notify_download") as notice:
            t._register_download(t.file_ids, "203.0.113.1", "ua")
        notice.assert_not_called()

    def test_notice_is_journaled(self):
        """The sender notice is an event on the transfer; the Loi 25 trail has
        to show it was sent."""
        t = self._active()
        t.notify_on_download = True
        tmpl = self.env.ref("bf_securetransfer.mail_template_download_notice",
                            raise_if_not_found=False)
        if not tmpl:
            self.skipTest("download notice template not installed")
        with patch("odoo.addons.mail.models.mail_template.MailTemplate.send_mail",
                   lambda *a, **k: True):
            t._notify_download()
        self.assertIn("notified", t.access_log_ids.mapped("action"))

    def test_missing_notice_template_is_not_fatal(self):
        """A tenant that never loaded the template must still be able to serve
        downloads — the notice is optional, the download is not."""
        t = self._active()
        with patch.object(type(self.env["ir.model.data"]), "_xmlid_lookup",
                          side_effect=ValueError("gone")):
            try:
                t._notify_download()
            except Exception as exc:  # pragma: no cover - guard assertion
                self.fail("a missing template must not raise: %s" % exc)
