"""Routes publiques de signature : les portes tiennent pour tous les gestes.

Le refus ne doit sauter ni le code courriel ni l'ordre de signature, et le
document n'est plus servi après annulation, expiration ou refus.
"""
import base64
import io

from odoo.tests import HttpCase, tagged

from .common import BaseNeuve


@tagged("post_install", "-at_install", "bf_sign")
class TestBfSignPublicRoutes(BaseNeuve, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from reportlab.pdfgen import canvas
        buf = io.BytesIO()
        c = canvas.Canvas(buf)
        c.drawString(72, 720, "Document de test")
        c.showPage()
        c.save()
        cls.pdf_b64 = base64.b64encode(buf.getvalue())

    def _request(self, signers=2, order="parallel", otp=False):
        req = self.env["bf.sign.request"].create({
            "document_file": self.pdf_b64,
            "document_filename": "test.pdf",
            "signing_order": order,
            "require_signer_otp": otp,
        })
        for i in range(signers):
            signer = self.env["bf.sign.signer"].create({
                "request_id": req.id, "name": "Signer %d" % i,
                "email": "signer%d@example.com" % i, "sequence": 10 + i,
            })
            self.env["bf.sign.field"].create({
                "request_id": req.id, "signer_id": signer.id,
                "field_type": "signature", "page": 1, "pos_x": 0.5,
                "pos_y": 0.8, "width": 0.25, "height": 0.08,
            })
        req.action_send()
        return req

    def _url(self, req, signer, suffix=""):
        return "/sign/%s/%s%s" % (req.id, signer.access_token, suffix)

    def _refuse(self, req, signer):
        return self.url_open(self._url(req, signer, "/refuse"),
                             data={"reason": "non"}, allow_redirects=False)

    def test_refuse_requires_the_email_code(self):
        req = self._request(otp=True)
        signer = req.signer_ids[0]
        self._refuse(req, signer)
        self.assertNotEqual(req.state, "refused")
        self.assertNotEqual(signer.state, "refused")

    def test_refuse_respects_the_signing_order(self):
        req = self._request(order="sequential")
        second = req.signer_ids.sorted("sequence")[1]
        self._refuse(req, second)
        self.assertNotEqual(req.state, "refused")

    def test_refuse_still_works_for_the_current_signer(self):
        req = self._request(order="sequential")
        first = req.signer_ids.sorted("sequence")[0]
        self._refuse(req, first)
        self.assertEqual(req.state, "refused")

    def test_document_is_served_while_open(self):
        req = self._request()
        res = self.url_open(self._url(req, req.signer_ids[0], "/document"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers.get("Content-Type"), "application/pdf")

    def test_document_not_served_once_cancelled_expired_or_refused(self):
        for state in ("cancelled", "expired", "refused"):
            req = self._request()
            req.sudo().state = state
            res = self.url_open(self._url(req, req.signer_ids[0], "/document"))
            self.assertEqual(res.status_code, 404, state)

    # ── page de vérification : l'heure de chaque signature en UTC ──
    def test_verify_page_lists_each_signature_time_in_utc(self):
        from datetime import datetime
        from unittest.mock import patch
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGBA", (200, 80), (0, 0, 0, 0)).save(buf, format="PNG")
        png = base64.b64encode(buf.getvalue()).decode()
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_sign.certificate_tz", "America/Toronto")
        req = self._request(signers=2)
        Report = type(self.env["ir.actions.report"])
        with patch.object(Report, "_render_qweb_pdf",
                          return_value=(base64.b64decode(self.pdf_b64), "pdf")):
            for signer in req.signer_ids:
                req.register_signer_signature(signer, png, None, True,
                                              ip="1.2.3.4", user_agent="pytest")
        self.assertEqual(req.state, "signed")
        first, second = req.signer_ids.sorted("sequence")
        first.sudo().signed_on = datetime(2026, 10, 20, 7, 40, 46)
        second.sudo().signed_on = datetime(2026, 10, 20, 8, 1, 2)
        url = req._verify_url().split("/sign/verify/", 1)[1]
        page = lambda: self.url_open("/sign/verify/" + url).text
        html = page()
        self.assertIn("2026-10-20 07:40:46 UTC", html)
        self.assertIn("2026-10-20 03:40:46 HAE (UTC−04:00)", html)
        self.assertNotIn("(UTC−04:00))", html)  # pas de parenthèses emboîtées
        self.assertIn("2026-10-20 08:01:02 UTC", html)
        self.assertNotIn("émis avant une correction", html)
        # Un certificat scellé avant 18.0.3.29.0 : la page lève la contradiction.
        req.sudo().cert_times_utc = False
        self.assertIn("émis avant une correction", page())

