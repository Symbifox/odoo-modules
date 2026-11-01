import re
from unittest.mock import patch

from odoo.tests import HttpCase, TransactionCase, tagged

from ..models.survey_user_input import _CODE_ALPHABET, _CODE_LENGTH


# post_install: at_install would run on a PARTIAL registry (our dependency
# chain only), while the database schema carries every installed module's
# columns - a partner insert then misses account's NOT NULL autopost_bills.
@tagged("post_install", "-at_install")
class CertificateCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.write({
            "report_brand_primary": "#1A2B3C",
            "report_brand_dark": "#0D0E0F",
            "bf_certificate_signatory_name": "Signa Tory",
            "bf_certificate_signatory_title": "President",
        })
        cls.survey = cls.env["survey.survey"].create({
            "title": "Branded Certificate Course",
            "certification": True,
            "scoring_type": "scoring_without_answers",
            "scoring_success_min": 0,
            "users_login_required": True,
            "certification_report_layout": "bf_brand",
        })
        cls.partner_en = cls.env["res.partner"].create({
            "name": "Enid Certified", "email": "enid@example.test",
            "lang": "en_US",
        })

    def _passed_input(self, partner=None, **extra):
        user_input = self.env["survey.user_input"].create({
            "survey_id": self.survey.id,
            "partner_id": (partner or self.partner_en).id,
            "email": (partner or self.partner_en).email,
            **extra,
        })
        user_input._mark_done()
        return user_input

    def _render(self, user_input):
        html, _type = self.env["ir.actions.report"]._render_qweb_html(
            "survey.certification_report_view", user_input.ids)
        return html.decode()

    def test_code_assigned_on_success(self):
        user_input = self._passed_input()
        self.assertTrue(user_input.scoring_success)
        code = user_input.bf_certificate_code
        self.assertEqual(len(code), _CODE_LENGTH)
        self.assertTrue(all(c in _CODE_ALPHABET for c in code))
        self.assertEqual(
            user_input.bf_certificate_code_display,
            "-".join(code[i:i + 4] for i in range(0, 12, 4)))
        # the path follows the RESOLVED language: on a database where the
        # partner's language is not installed, the fallback language decides
        path = ("certificat"
                if user_input._bf_certificate_lang().startswith("fr")
                else "certificate")
        self.assertIn("/%s/%s" % (path, code), user_input.bf_certificate_url)
        # idempotent: marking done again keeps the number
        user_input._mark_done()
        self.assertEqual(user_input.bf_certificate_code, code)

    def test_no_code_for_test_entries(self):
        user_input = self._passed_input(test_entry=True)
        self.assertFalse(user_input.bf_certificate_code)

    def test_normalize(self):
        norm = self.env["survey.user_input"]._bf_normalize_certificate_code
        self.assertEqual(norm("2345-cdfg-hjkm"), "2345CDFGHJKM")
        self.assertEqual(norm(" 2345 CDFG HJKM "), "2345CDFGHJKM")
        self.assertEqual(norm("too-short"), "")
        self.assertEqual(norm(False), "")

    def test_branded_render(self):
        user_input = self._passed_input()
        html = self._render(user_input)
        self.assertIn("#1A2B3C", html, "brand primary colour missing")
        self.assertIn("#0D0E0F", html, "brand dark colour missing")
        self.assertIn("Enid Certified", html)
        self.assertIn("Branded Certificate Course", html)
        self.assertIn("Signa Tory", html)
        self.assertIn(user_input.bf_certificate_code_display, html)
        self.assertIn("/report/barcode/", html, "QR code missing")
        # completion date, not the attempt's start date
        self.assertNotIn("DEMO_CERTIFIED_NAME", html)

    def test_render_in_participant_language(self):
        fr = self.env["res.lang"]._activate_lang("fr_CA")
        if not fr:
            self.skipTest("fr_CA cannot be activated")
        partner = self.env["res.partner"].create({
            "name": "Fernande Attestée", "email": "fernande@example.test",
            "lang": "fr_CA",
        })
        user_input = self._passed_input(partner=partner)
        html = self._render(user_input)
        # the date formats in the participant's language even when the
        # printer is set to English (babel does this without a catalogue)
        month = user_input.end_datetime.strftime("%m")
        months_fr = {"01": "janvier", "02": "février", "03": "mars", "04": "avril",
                     "05": "mai", "06": "juin", "07": "juillet", "08": "août",
                     "09": "septembre", "10": "octobre", "11": "novembre", "12": "décembre"}
        self.assertIn(months_fr[month], html)
        self.assertIn("/certificat/", user_input.bf_certificate_url)

    def test_failed_certification_renders_failure(self):
        self.survey.scoring_success_min = 100
        user_input = self.env["survey.user_input"].create({
            "survey_id": self.survey.id,
            "partner_id": self.partner_en.id,
        })
        user_input._mark_done()
        self.assertFalse(user_input.scoring_success)
        self.assertFalse(user_input.bf_certificate_code)
        html = self._render(user_input)
        self.assertIn("not passed", html)

    def test_other_layouts_keep_core_template(self):
        self.survey.certification_report_layout = "modern_blue"
        user_input = self._passed_input()
        html = self._render(user_input)
        self.assertIn("certification-wrapper", html, "core layout should render")
        self.assertNotIn("#1A2B3C", html)

    def test_company_from_course_website(self):
        if "slide.slide" not in self.env or "website" not in self.env:
            self.skipTest("eLearning not installed")
        other_company = self.env["res.company"].create({"name": "Other Brand Inc"})
        other_company.write({"report_brand_primary": "#99AA11"})
        website = self.env["website"].create({
            "name": "Other Brand Site", "company_id": other_company.id,
        })
        channel = self.env["slide.channel"].create({
            "name": "Other Brand Channel", "website_id": website.id,
        })
        self.env["slide.slide"].create({
            "name": "Certification slide",
            "channel_id": channel.id,
            "slide_category": "certification",
            "survey_id": self.survey.id,
            "is_published": False,
        })
        user_input = self._passed_input()
        self.assertEqual(user_input._bf_certificate_company(), other_company)
        html = self._render(user_input)
        self.assertIn("#99AA11", html)

    def test_certificate_date_in_learner_timezone(self):
        # 2026-03-10 23:30 UTC is already March 11th in Auckland (the
        # company's day) but still March 10th in New York (the learner's):
        # the diploma is dated the learner's day.
        self.company.partner_id.tz = "Pacific/Auckland"
        partner = self.env["res.partner"].create({
            "name": "Nica Newyork", "email": "nica@example.test",
            "lang": "en_US", "tz": "America/New_York",
        })
        user_input = self._passed_input(partner=partner)
        user_input.end_datetime = "2026-03-10 23:30:00"
        vals = user_input._bf_certificate_values()
        self.assertIn("March 10", vals["date"],
                      "the learner's day must win over the company's")
        # without a learner timezone, the company's day decides
        partner.tz = False
        vals = user_input._bf_certificate_values()
        self.assertIn("March 11", vals["date"])

    def test_company_prefers_channel_with_website(self):
        if "slide.slide" not in self.env or "website" not in self.env:
            self.skipTest("eLearning not installed")
        # first slide by id sits on a channel WITHOUT a website: the later
        # slide whose channel has one must win anyway (it carries a brand)
        orphan_channel = self.env["slide.channel"].create({"name": "No Site Channel"})
        orphan_channel.website_id = False
        self.env["slide.slide"].create({
            "name": "No site slide", "channel_id": orphan_channel.id,
            "slide_category": "certification", "survey_id": self.survey.id,
            "is_published": False,
        })
        sited_company = self.env["res.company"].create({"name": "Sited Brand Inc"})
        website = self.env["website"].create({
            "name": "Sited Brand Site", "company_id": sited_company.id,
        })
        sited_channel = self.env["slide.channel"].create({
            "name": "Sited Channel", "website_id": website.id,
        })
        self.env["slide.slide"].create({
            "name": "Sited slide", "channel_id": sited_channel.id,
            "slide_category": "certification", "survey_id": self.survey.id,
            "is_published": False,
        })
        user_input = self._passed_input()
        slide = user_input._bf_certificate_slide()
        self.assertEqual(slide.channel_id, sited_channel)
        self.assertEqual(user_input._bf_certificate_company(), sited_company)

    def test_send_mail_guard_only_for_survey_model(self):
        # The layout guard repairs survey's forced light layout for OUR
        # templates only: a template of another model that declares its own
        # layout still gets exactly what its caller asked for.
        MailTemplate = type(self.env["mail.template"])
        orig = MailTemplate.send_mail_batch
        seen = {}

        def capture(tmpl, res_ids, **kw):
            seen[tmpl.model] = kw.get("email_layout_xmlid")
            return orig(tmpl, res_ids, **kw)

        partner_tpl = self.env["mail.template"].create({
            "name": "Other model with a declared layout",
            "model_id": self.env["ir.model"]._get_id("res.partner"),
            "subject": "x",
            "body_html": "<p>x</p>",
            "email_to": "nobody@example.test",
            "email_layout_xmlid": "mail.mail_notification_layout",
        })
        survey_tpl = self.env.ref(
            "bf_survey_certificate.mail_template_certification_branded")
        user_input = self._passed_input()
        with patch.object(MailTemplate, "send_mail_batch", capture):
            partner_tpl.send_mail(
                self.partner_en.id,
                email_layout_xmlid="mail.mail_notification_light")
            survey_tpl.send_mail(
                user_input.id,
                email_layout_xmlid="mail.mail_notification_light")
        self.assertEqual(seen["res.partner"], "mail.mail_notification_light",
                         "a foreign template must not be touched by the guard")
        self.assertEqual(seen["survey.user_input"],
                         "bf_onboarding_base.bf_mail_layout")

    def test_branded_email(self):
        template = self.env.ref("bf_survey_certificate.mail_template_certification_branded")
        self.survey.certification_mail_template_id = template
        user_input = self._passed_input()
        mail = self.env["mail.mail"].sudo().search(
            [("model", "=", "survey.user_input"), ("res_id", "=", user_input.id)],
            order="id desc", limit=1)
        self.assertTrue(mail, "certification email not created")
        self.assertIn(self.partner_en.email, mail.email_to)
        self.assertIn(user_input.bf_certificate_url, mail.body_html)
        self.assertTrue(mail.attachment_ids, "certificate PDF not attached")


    def test_codes_are_distinct(self):
        # three successes in one transaction: three different random codes
        # (kills the "constant code" mutant that per-test transactions hide)
        codes = set()
        for i in range(3):
            partner = self.env["res.partner"].create({
                "name": "Distincte %d" % i,
                "email": "distincte%d@example.test" % i,
            })
            codes.add(self._passed_input(partner=partner).bf_certificate_code)
        self.assertEqual(len(codes), 3)


@tagged("post_install", "-at_install")
class CertificateVerifyHttpCase(HttpCase):

    def test_verify_page(self):
        survey = self.env["survey.survey"].create({
            "title": "HTTP Verified Course",
            "certification": True,
            "scoring_type": "scoring_without_answers",
            "scoring_success_min": 0,
            "users_login_required": True,
            "certification_report_layout": "bf_brand",
        })
        partner = self.env["res.partner"].create({
            "name": "Hubert Vérifié", "email": "hubert@example.test",
            "lang": "en_US",
        })
        user_input = self.env["survey.user_input"].create({
            "survey_id": survey.id, "partner_id": partner.id,
        })
        user_input._mark_done()
        self.env.cr.flush()
        code = user_input.bf_certificate_code

        resp = self.url_open("/certificate/%s" % code)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Hubert Vérifié", resp.text)
        self.assertIn("HTTP Verified Course", resp.text)
        self.assertIn("authentic", resp.text)

        # dashes and case are forgiven
        dashed = "-".join(code[i:i + 4] for i in range(0, 12, 4)).lower()
        resp = self.url_open("/certificat/%s" % dashed)
        self.assertEqual(resp.status_code, 200)

        # a wrong number answers 404 and never hints at anything
        wrong = ("2" * 12) if code != "2" * 12 else ("3" * 12)
        resp = self.url_open("/certificate/%s" % wrong)
        self.assertEqual(resp.status_code, 404)
        self.assertNotIn("Hubert", resp.text)

        # a malformed number answers exactly like a wrong one
        resp = self.url_open("/certificate/nope")
        self.assertEqual(resp.status_code, 404)
        # a private answer: no cache may keep it
        self.assertEqual(resp.headers.get("Cache-Control"), "no-store")

    def test_verify_page_without_partner_masks_email(self):
        survey = self.env["survey.survey"].create({
            "title": "Anonymous Course",
            "certification": True,
            "scoring_type": "scoring_without_answers",
            "scoring_success_min": 0,
            "certification_report_layout": "bf_brand",
        })
        user_input = self.env["survey.user_input"].create({
            "survey_id": survey.id, "email": "hidden@example.test",
        })
        user_input._mark_done()
        self.env.cr.flush()
        resp = self.url_open("/certificate/%s" % user_input.bf_certificate_code)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("Cache-Control"), "no-store")
        # the public page never prints an email address, in any form
        # (not "hidden" alone: the page's own CSS says "overflow: hidden")
        self.assertNotIn("hidden@", resp.text)
        self.assertNotIn("example.test", resp.text)
        self.assertIn("H***", resp.text)

    def test_verify_page_language_follows_route(self):
        # one record, two routes: /certificat answers entirely in French,
        # /certificate entirely in English, whatever the learner's language
        fr = self.env["res.lang"]._activate_lang("fr_CA")
        if not fr:
            self.skipTest("fr_CA cannot be activated")
        survey = self.env["survey.survey"].create({
            "title": "Bilingual Route Course",
            "certification": True,
            "scoring_type": "scoring_without_answers",
            "scoring_success_min": 0,
            "users_login_required": True,
            "certification_report_layout": "bf_brand",
        })
        partner = self.env["res.partner"].create({
            "name": "Renée Bilingue", "email": "renee@example.test",
            "lang": "fr_CA",
        })
        user_input = self.env["survey.user_input"].create({
            "survey_id": survey.id, "partner_id": partner.id,
        })
        user_input._mark_done()
        self.env.cr.flush()
        code = user_input.bf_certificate_code
        month_en = user_input.end_datetime.strftime("%B")
        resp = self.url_open("/certificate/%s" % code)
        self.assertIn(month_en, resp.text, "English route must format the date in English")
        resp = self.url_open("/certificat/%s" % code)
        self.assertNotIn(month_en, resp.text, "French route must not show the English month")
