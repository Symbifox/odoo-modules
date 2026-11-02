"""Each reader reads this module in their own language.

The source is written in English and ``i18n/fr_CA.po`` carries the French:
Odoo never translates to en_US, so a user set to English only reads English
because the source is. What these tests pin down:

- an English user reads English, a French user reads the French these screens
  have always shown, word for word;
- the client's public page follows the visitor's browser (Accept-Language),
  JSON answers and error messages included;
- the email and the text messages follow the CLIENT's language, whoever sends
  them;
- what is written with nobody's session behind it (a scheduled job, the
  client's page) follows its reader: the requester for chatter messages, the
  transfer's language for the access-log notes printed on its certificate.

Every test sets the languages it needs: the database may be French only
(a scheduled job then runs in French) or English only (a public CI).
"""
import html
import json
from datetime import datetime, timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.modules.module import get_manifest
from odoo.tests import HttpCase, TransactionCase, tagged
from odoo.tools import email_normalize

_SMS = "odoo.addons.bf_securetransfer.models.sms"
_FILE = ("odoo.addons.bf_securetransfer.models.secure_transfer_file"
         ".SecureTransferFile")
MODULE = "bf_document_request"


class LanguageCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ("en_US", "fr_CA"):
            cls.env["res.lang"]._activate_lang(code)
        # This module's French, loaded even on a database installed without it.
        cls.env["ir.module.module"]._load_module_terms([MODULE], ["fr_CA"])
        cls.brand = cls.env["secure.transfer.brand"].search([], limit=1)
        groups = [
            (4, cls.env.ref("base.group_user").id),
            (4, cls.env.ref("bf_securetransfer.group_securetransfer_user").id),
        ]
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        cls.user_fr = Users.create({
            "name": "Employée francophone", "login": "bf_dr_lang_fr",
            "email": "employee.fr@example.com", "lang": "fr_CA", "groups_id": groups,
        })
        cls.user_en = Users.create({
            "name": "English employee", "login": "bf_dr_lang_en",
            "email": "employee.en@example.com", "lang": "en_US", "groups_id": groups,
        })
        Partner = cls.env["res.partner"]
        cls.client_fr = Partner.create({
            "name": "Cliente francophone", "email": "client.fr@example.com",
            "mobile": "514-555-0101", "lang": "fr_CA",
        })
        cls.client_en = Partner.create({
            "name": "English client", "email": "client.en@example.com",
            "mobile": "514-555-0102", "lang": "en_US",
        })
        cls.doc = cls.env["bf.document.type"].create({"name": "Identity document"})
        cls.doc.update_field_translations("name", {"fr_CA": "Pièce d'identité"})

    def _request(self, client, user, channel="email", **extra):
        vals = {
            "partner_id": client.id,
            "channel": channel,
            "email": client.email,
            "phone": client.mobile,
            "brand_id": self.brand.id,
            "user_id": user.id,
            "line_ids": [(0, 0, {"document_type_id": self.doc.id})],
        }
        vals.update(extra)
        return self.env["bf.document.request"].create(vals)

    def _as(self, user, record):
        """``record`` as ``user`` sees it in the interface: their own language."""
        return record.with_user(user).with_context(lang=user.lang)

    def _send(self, req, user):
        """Sends as ``user``; returns the text messages sent, if any."""
        with patch(_SMS + ".configured", return_value=True), \
             patch(_SMS + ".send", return_value=True) as sent:
            self._as(user, req).action_send()
        return [call.args[2] for call in sent.call_args_list]

    def _link_mail(self, req):
        """The last link email to the request's address, its body unescaped."""
        mail = self.env["mail.mail"].sudo().search(
            [("email_to", "=", req.email)], order="id desc", limit=1)
        return mail.subject, html.unescape(mail.body_html)

    def _notes(self, req, action):
        return req.sudo().transfer_id.access_log_ids.filtered(
            lambda entry: entry.action == action).mapped("note")


@tagged("post_install", "-at_install")
class TestInterfaceLanguage(LanguageCommon):

    def test_an_english_user_reads_english(self):
        Request = self.env["bf.document.request"].with_context(lang="en_US")
        fields_en = Request.fields_get(["partner_id", "state", "deadline"])
        self.assertEqual(fields_en["partner_id"]["string"], "Client")
        self.assertEqual(fields_en["deadline"]["string"], "Deadline")
        self.assertIn(("partial", "Partially received"), fields_en["state"]["selection"])
        menu = self.env.ref(MODULE + ".menu_bf_document_requests").with_context(lang="en_US")
        self.assertEqual(menu.name, "Document requests")
        req = self._request(self.client_en, self.user_en, line_ids=[])
        with self.assertRaisesRegex(UserError, "Add at least one document to request"):
            self._as(self.user_en, req).action_send()

    def test_a_french_user_reads_the_french_labels(self):
        Request = self.env["bf.document.request"].with_context(lang="fr_CA")
        labels = Request.fields_get(["partner_id", "channel", "state", "deadline", "message"])
        self.assertEqual(labels["partner_id"]["string"], "Client")
        self.assertEqual(labels["channel"]["string"], "Envoi par")
        self.assertEqual(labels["deadline"]["string"], "Échéance")
        self.assertEqual(labels["message"]["string"], "Mot au client")
        self.assertIn(("partial", "Partiellement reçue"), labels["state"]["selection"])
        self.assertIn(("sms", "SMS"), labels["channel"]["selection"])
        log_actions = dict(self.env["secure.transfer.access.log"].with_context(
            lang="fr_CA").fields_get(["action"])["action"]["selection"])
        self.assertEqual(log_actions["bf_document_request_sent"],
                         "Demande de pièces envoyée (courriel ou SMS)")
        ref = lambda xmlid: self.env.ref(MODULE + "." + xmlid).with_context(lang="fr_CA")
        self.assertEqual(ref("menu_bf_document_requests").name, "Demandes de pièces")
        self.assertEqual(ref("menu_bf_document_expiry_policy").name, "Expiration des pièces")
        self.assertEqual(ref("cron_bf_document_expire_requests").name,
                         "Demande de pièces : fermer les demandes hors délai")
        self.assertEqual(self.env["ir.model"].with_context(lang="fr_CA")._get(
            "bf.document.request").name, "Demande de pièces")
        arch = Request.get_views([(False, "form")])["views"]["form"]["arch"]
        self.assertIn("Le dernier courriel du lien n'a pas pu partir", arch)
        self.assertIn("Certificat d'accès (PDF)", arch)
        req = self._request(self.client_fr, self.user_fr, line_ids=[])
        with self.assertRaisesRegex(UserError, "Ajoutez au moins une pièce à demander."):
            self._as(self.user_fr, req).action_send()

    def test_the_module_card_in_apps_is_translated(self):
        card = self.env.ref("base.module_" + MODULE)
        self.assertEqual(card.with_context(lang="en_US").shortdesc,
                         "Secure Transfer: Document Requests")
        self.assertEqual(card.with_context(lang="fr_CA").shortdesc,
                         "Transfert de fichiers — demande de pièces")


@tagged("post_install", "-at_install")
class TestClientLanguage(LanguageCommon):
    """The email and the text messages are read by the client: their language,
    not the sender's, nor the scheduled job's."""

    def test_the_email_follows_the_client_not_the_sender(self):
        req = self._request(self.client_en, self.user_fr, deadline=fields.Datetime.now() + timedelta(days=2))
        self._send(req, self.user_fr)
        subject, body = self._link_mail(req)
        self.assertIn("document(s) to upload", subject)
        self.assertIn("Upload my documents", body)
        self.assertIn("Identity document", body)
        self.assertIn(" at ", body.split("Please upload them before", 1)[1][:60])
        req = self._request(self.client_fr, self.user_en, deadline=fields.Datetime.now() + timedelta(days=2))
        self._send(req, self.user_en)
        subject, body = self._link_mail(req)
        self.assertIn("document(s) à déposer", subject)
        self.assertIn("Déposer mes documents", body)
        self.assertIn("Pièce d'identité", body)
        self.assertIn(" à ", body.split("À déposer avant le", 1)[1][:60])

    def test_the_reminder_email_follows_the_client(self):
        req = self._request(self.client_fr, self.user_en)
        self._send(req, self.user_en)
        self._as(self.user_en, req).action_resend()
        self.assertIn("Rappel", self._link_mail(req)[0])

    def test_a_client_without_a_language_reads_the_language_of_their_address(self):
        anonymous = self.env["res.partner"].create({
            "name": "No language", "email": "client.en@example.com", "lang": False})
        req = self._request(anonymous, self.user_fr)
        self._send(req, self.user_fr)
        # The contact who owns the address reads English.
        self.assertIn("document(s) to upload", self._link_mail(req)[0])

    def test_the_text_message_follows_the_client_not_the_sender(self):
        req = self._request(self.client_en, self.user_fr, channel="sms")
        body, = self._send(req, self.user_fr)
        self.assertIn("document(s) to upload", body)
        req = self._request(self.client_fr, self.user_en, channel="sms")
        body, = self._send(req, self.user_en)
        self.assertIn("document(s) à déposer", body)

    def test_the_manual_text_reminder_follows_the_client(self):
        req = self._request(self.client_fr, self.user_en, channel="sms")
        self._send(req, self.user_en)
        with patch(_SMS + ".send", return_value=True) as sent:
            self._as(self.user_en, req).action_resend()
        self.assertIn("Rappel", sent.call_args.args[2])


@tagged("post_install", "-at_install")
class TestScheduledJobLanguage(LanguageCommon):
    """A scheduled job runs as OdooBot, in the database's language: whatever
    it writes must follow its reader instead."""

    def setUp(self):
        super().setUp()
        self.brand.write({
            "bf_document_reminder_enabled": True,
            "bf_document_reminder_delay_hours": 1,
            "bf_document_reminder_max": 2,
        })

    def _due(self, req):
        req.sudo().write({"sent_at": fields.Datetime.now() - timedelta(hours=3),
                          "last_reminder_at": False})

    def _cron(self, lang):
        with patch(_SMS + ".configured", return_value=True), \
             patch(_SMS + ".send", return_value=True) as sent:
            self.env["bf.document.request"].with_context(lang=lang)._cron_send_reminders()
        return [call.args[2] for call in sent.call_args_list]

    def test_the_automatic_text_reminder_follows_the_client(self):
        req = self._request(self.client_en, self.user_fr, channel="sms")
        self._send(req, self.user_fr)
        self._due(req)
        bodies = self._cron("fr_CA")
        self.assertTrue(bodies)
        self.assertIn("Reminder:", bodies[0])

    def test_the_reminder_message_and_note_follow_the_requester(self):
        req = self._request(self.client_en, self.user_fr, channel="sms")
        self._send(req, self.user_fr)
        self._due(req)
        self._cron("en_US")
        self.assertIn("Relance automatique envoyée par SMS (1 sur 2).",
                      req.message_ids[0].body)
        note, = self._notes(req, "bf_document_reminder_sent")
        self.assertTrue(note.startswith("Relance automatique 1/2 par SMS au"), note)

        req = self._request(self.client_fr, self.user_en, channel="sms")
        self._send(req, self.user_en)
        self._due(req)
        self._cron("fr_CA")
        self.assertIn("Automatic reminder sent by text message (1 of 2).",
                      req.message_ids[0].body)
        note, = self._notes(req, "bf_document_reminder_sent")
        self.assertTrue(note.startswith("Automatic reminder 1/2 by text message to"), note)

    def test_the_expiry_note_follows_the_transfer(self):
        req = self._request(self.client_en, self.user_fr,
                            deadline=fields.Datetime.now() + timedelta(hours=1))
        self._send(req, self.user_fr)
        req.sudo().deadline = fields.Datetime.now() - timedelta(hours=1)
        self.env["bf.document.request"].with_context(lang="en_US")._cron_expire_requests()
        note, = self._notes(req, "bf_document_request_expired")
        self.assertTrue(note.startswith("Échéance passée le"), note)


@tagged("post_install", "-at_install")
class TestRequesterLanguage(LanguageCommon):

    def test_the_transfer_speaks_the_requesters_language(self):
        req = self._request(self.client_fr, self.user_en)
        self._send(req, self.user_en)
        # An active English language: en_CA when the database has it (the
        # base's own value, the one its earlier releases translate), else
        # en_US. See TestTransferLocaleWithoutEnCa.
        actives = [code for code, _name in self.env["res.lang"].get_installed()]
        self.assertEqual(req.sudo().transfer_id.locale,
                         "en_CA" if "en_CA" in actives else "en_US")
        note, = self._notes(req, "bf_document_request_sent")
        self.assertTrue(note.startswith("Request "), note)
        req = self._request(self.client_en, self.user_fr)
        self._send(req, self.user_fr)
        self.assertEqual(req.sudo().transfer_id.locale, "fr_CA")
        note, = self._notes(req, "bf_document_request_sent")
        self.assertTrue(note.startswith("Demande "), note)

    def test_the_submission_message_follows_the_requester_not_the_visitor(self):
        req = self._request(self.client_en, self.user_fr)
        self._send(req, self.user_fr)
        line = req.line_ids
        # The client's page calls the model in the VISITOR's language.
        visitor = req.sudo().with_context(lang="en_US")
        visitor._register_capture(line, "photo.jpg", 1024)
        with patch(_FILE + "._verify_on_s3", return_value=True):
            visitor._confirm_capture(line)
            visitor._submit()
        self.assertIn("Le client a déposé 1 document(s).", req.message_ids[0].body)
        note, = self._notes(req, "bf_document_doc_received")
        self.assertTrue(note.startswith("« Pièce d'identité » déposé"), note)
        note, = self._notes(req, "bf_document_request_completed")
        self.assertTrue(note.startswith("Demande "), note)


@tagged("post_install", "-at_install")
class TestPublicPageLanguage(LanguageCommon, HttpCase):

    def _sent(self, requester):
        req = self._request(self.client_en, requester)
        self._send(req, requester)
        return req

    def _page(self, req, accept):
        response = self.url_open("/d/%s" % req.sudo().token,
                                 headers={"Accept-Language": accept})
        self.assertEqual(response.status_code, 200)
        return html.unescape(response.text)

    def _json(self, req, path, accept, **params):
        response = self.url_open(
            "/d/%s/%s" % (req.sudo().token, path),
            data='{"jsonrpc": "2.0", "method": "call", "params": %s}' % (
                __import__("json").dumps(params)),
            headers={"Content-Type": "application/json", "Accept-Language": accept})
        return response.json()["result"]

    def test_the_page_shows_the_deadline_in_the_requester_time_zone(self):
        # The visitor is anonymous: without an explicit time zone, the
        # datetime widget printed UTC (22:14 for a 17:14 deadline in Quebec).
        self.user_fr.tz = "America/Montreal"
        req = self._request(self.client_en, self.user_fr)
        req.deadline = datetime(2026, 11, 4, 22, 14)
        self._send(req, self.user_fr)
        for accept, word in (("fr-CA,fr;q=0.9", "novembre"), ("en-CA,en;q=0.9", "November")):
            page = self._page(req, accept)
            deadline = page.split('class="dr-deadline"', 1)[1][:300]
            self.assertIn("17:14", deadline)
            self.assertIn(word, deadline)
            self.assertNotIn("22:14", page)

    def test_the_page_follows_accept_language(self):
        req = self._sent(self.user_fr)
        french = self._page(req, "fr-CA,fr;q=0.9")
        self.assertTrue(french.lstrip().startswith("<!DOCTYPE html>"))
        for text in ("<title>Vos documents —", "Vos documents", "Terminer et envoyer",
                     "Prendre en photo", "Pièce d'identité", 'lang="fr"'):
            self.assertIn(text, french)
        # Text of the template itself, not composed in Python: it is only
        # translated if the view is (no doctype in its arch).
        self.assertIn("Photographiez chaque document demandé ci-dessous", french)
        self.assertIn("JavaScript est requis pour déposer", french)
        english = self._page(req, "en-CA,en;q=0.9")
        for text in ("<title>Your documents —", "Your documents", "Finish and send",
                     "Take a photo", "Identity document", 'lang="en"'):
            self.assertIn(text, english)
        self.assertIn("Photograph each document requested below", english)
        self.assertIn("JavaScript is required to upload", english)
        self.assertNotIn("Vos documents", english)

    def test_the_page_assets_change_address_with_each_release(self):
        # Cached for a week by the browser: without the release in their
        # address, a client who opens the link again after an upgrade runs
        # the former script against the new page.
        req = self._sent(self.user_fr)
        page = self._page(req, "en")
        version = get_manifest(MODULE)["version"]
        self.assertIn('src="/bf_document_request/static/src/js/'
                      'bf_document_request.js?v=%s"' % version, page)
        self.assertIn('href="/bf_document_request/static/src/css/'
                      'bf_document_request.css?v=%s"' % version, page)

    def test_the_closed_page_follows_accept_language(self):
        english = html.unescape(self.url_open(
            "/d/" + "0" * 32, headers={"Accept-Language": "en"}).text)
        self.assertIn("This link is no longer valid.", english)
        french = html.unescape(self.url_open(
            "/d/" + "1" * 32, headers={"Accept-Language": "fr"}).text)
        self.assertIn("Ce lien n'est plus valide.", french)

    def test_json_answers_follow_accept_language(self):
        req = self._sent(self.user_fr)
        line = req.line_ids
        english = self._json(req, "presign", "en", line_id=line.id, size=0)
        self.assertEqual(english["message"], "Empty or unreadable file.")
        french = self._json(req, "presign", "fr", line_id=line.id, size=0)
        self.assertEqual(french["message"], "Fichier vide ou illisible.")
        # A message raised by the model while serving the route.
        too_big = 65 * 1024 * 1024
        english = self._json(req, "presign", "en", line_id=line.id, size=too_big)
        self.assertEqual(english["message"], "File too large (maximum 64 MB).")
        french = self._json(req, "presign", "fr", line_id=line.id, size=too_big)
        self.assertEqual(french["message"], "Fichier trop volumineux (maximum 64 Mo).")

    def test_json_answers_follow_the_page_not_a_session_language(self):
        # An employee who opens the client's link in a browser where they are
        # logged in has a session in their own language: the answers must still
        # follow the page, which follows Accept-Language.
        req = self._sent(self.user_fr)
        self.user_fr.password = "bf-dr-lang-fr-password"
        self.authenticate("bf_dr_lang_fr", "bf-dr-lang-fr-password")
        english = self._json(req, "presign", "en", line_id=req.line_ids.id, size=0)
        self.assertEqual(english["message"], "Empty or unreadable file.")

    def test_the_opened_page_note_follows_the_transfer_not_the_visitor(self):
        req = self._sent(self.user_fr)
        self._page(req, "en")
        note, = self._notes(req, "bf_document_request_opened")
        self.assertEqual(note, "Page de dépôt ouverte")


def _lang_for_email_as_in_earlier_bases(self, email, fallback=None):
    """``_lang_for_email`` as earlier releases of the base wrote it: the
    matching contact's language, else the transfer's locale AS IS, never
    checked against the active languages."""
    email = email_normalize(email or "") or ""
    if email:
        partner = self.env["res.partner"].sudo().search(
            [("email", "=ilike", email)], limit=1)
        if partner and partner.lang:
            return partner.lang
    return self.locale or "fr_CA"


@tagged("post_install", "-at_install")
class TestTransferLocaleWithoutEnCa(LanguageCommon, HttpCase):
    """A database with English but without en_CA (fr_CA and en_US active).

    The transfer's locale must be an ACTIVE language. Earlier releases of
    ``bf_securetransfer`` use it as is: the access certificate is rendered
    ``with_context(lang=locale)``, and so is the client's receipt when their
    address matches no contact with a language. Odoo refuses a language that
    is not active (« Invalid language code »): the certificate failed every
    time, and the client's "Finish and send" was refused. The recent base
    hides it by mapping the locale to an active language, hence the earlier
    base's behaviour is reproduced here.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Lang = cls.env["res.lang"].with_context(active_test=False)
        en_ca = Lang.search([("code", "=", "en_CA")])
        if en_ca.active:
            # Odoo refuses to deactivate a language someone still uses.
            for model in ("res.users", "res.partner"):
                cls.env[model].with_context(active_test=False).search(
                    [("lang", "=", "en_CA")]).write({"lang": "en_US"})
            if "website" in cls.env:
                en_us = Lang.search([("code", "=", "en_US")])
                for site in cls.env["website"].search([("language_ids", "in", en_ca.ids)]):
                    vals = {"language_ids": [(3, en_ca.id), (4, en_us.id)]}
                    if site.default_lang_id == en_ca:
                        vals["default_lang_id"] = en_us.id
                    site.write(vals)
            en_ca.active = False
        cls.actives = [code for code, _name in cls.env["res.lang"].get_installed()]
        cls.walk_in = cls.env["res.partner"].create({
            "name": "Walk-in client", "mobile": "514-555-0103", "lang": False,
        })

    def test_en_ca_is_really_inactive(self):
        self.assertNotIn("en_CA", self.actives)
        self.assertIn("en_US", self.actives)

    def test_an_english_requester_gets_an_active_english_locale(self):
        req = self._request(self.client_fr, self.user_en)
        self._send(req, self.user_en)
        transfer = req.sudo().transfer_id
        self.assertIn(transfer.locale, self.actives)
        self.assertTrue(transfer.locale.startswith("en"), transfer.locale)
        # What the earlier base does with it: no « Invalid language code ».
        self.assertEqual(transfer.with_context(lang=transfer.locale).env.lang,
                         transfer.locale)
        req = self._request(self.client_en, self.user_fr)
        self._send(req, self.user_fr)
        self.assertEqual(req.sudo().transfer_id.locale, "fr_CA")

    def test_the_certificate_renders_in_the_transfers_locale(self):
        req = self._request(self.client_fr, self.user_en)
        self._send(req, self.user_en)
        line = req.line_ids
        req.sudo()._register_capture(line, "photo.jpg", 1024)
        with patch(_FILE + "._verify_on_s3", return_value=True):
            req.sudo()._confirm_capture(line)
        transfer = req.sudo().transfer_id
        # The earlier base: ``doc.with_context(lang=doc.locale)``.
        report = self.env["ir.actions.report"].with_context(lang=transfer.locale)
        body = report._render_qweb_html(
            "bf_securetransfer.report_access_certificate", transfer.ids)[0].decode()
        self.assertIn("SHA-256", body)
        self.assertIn(transfer.name, body)

    def _rpc(self, req, path, **params):
        response = self.url_open(
            "/d/%s/%s" % (req.sudo().token, path),
            data=json.dumps({"jsonrpc": "2.0", "method": "call", "params": params}),
            headers={"Content-Type": "application/json", "Accept-Language": "en"})
        body = response.json()
        self.assertNotIn("error", body, body)
        return body["result"]

    def test_the_client_journey_reaches_the_end_without_a_receipt(self):
        """Through the public routes, up to the submission, for a client at an
        address no contact owns, on the earlier base. Since 18.0.1.2.2 the
        client receives no receipt: it carried the download link of their own
        documents."""
        address = "walk-in.nobody@example.com"
        req = self._request(self.walk_in, self.user_en, email=address)
        self._send(req, self.user_en)
        self.env.cr.flush()
        Mail = self.env["mail.mail"].sudo()
        before = Mail.search_count([("email_to", "ilike", address)])
        line = req.line_ids
        plan = {"url": "https://storage.example/put", "headers": {}}
        Transfer = type(self.env["secure.transfer"])
        with patch(_FILE + ".presign_simple", return_value=plan), \
             patch(_FILE + "._verify_on_s3", return_value=True), \
             patch(_FILE + "._s3_delete"), \
             patch.object(Transfer, "_lang_for_email",
                          _lang_for_email_as_in_earlier_bases):
            first = self._rpc(req, "presign", line_id=line.id, size=1024,
                              filename="front.jpg")
            self.assertEqual(first.get("url"), plan["url"], first)
            self.assertEqual(self._rpc(req, "uploaded", line_id=line.id),
                             {"ok": True, "state": "received"})
            self.assertEqual(self._rpc(req, "submit"), {"ok": True})
        self.env.invalidate_all()
        self.assertEqual(req.state, "received")
        self.assertEqual(
            Mail.search_count([("email_to", "ilike", address)]), before,
            "no receipt goes to the client")
        self.assertTrue(Mail.search_count([("email_to", "ilike", self.user_en.email)]),
                        "the requester receives the link")
