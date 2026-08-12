"""Thorough QA suite — exercises every feature advertised in README.md."""
import base64
import io
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

# 1x1 transparent PNG, used as a stand-in letterhead image in tests.
_TINY_PNG_B64 = (
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNk"
    b"YAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


@tagged("post_install", "-at_install")
class TestLetterWriter(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.signatory = cls.env["res.partner"].create(
            {"name": "QA Signataire", "function": "Contact"}
        )
        cls.company.write(
            {
                "letter_signatory_id": cls.signatory.id,
                "letter_signatory_function": "Directrice générale",
                "letter_header_note": "QA — mention d'en-tête",
                "letter_footer_html": "<p>QA — pied de page</p>",
            }
        )
        cls.partner = cls.env["res.partner"].create(
            {
                "name": "QA Destinataire Inc.",
                "is_company": True,
                "street": "123 rue QA",
                "city": "Montréal",
                "zip": "H2X 1Y4",
                "email": "qa-dest@example.invalid",
            }
        )
        cls.partner_no_email = cls.env["res.partner"].create(
            {"name": "QA Sans Courriel"}
        )

    # -- helpers -------------------------------------------------------
    def _make_template(self, body, subject="Objet {{ object.partner_id.name }}",
                       style="banner"):
        return self.env["letter.template"].create(
            {
                "name": "QA Template",
                "subject": subject,
                "body_html": body,
                "letterhead_style": style,
            }
        )

    def _make_letter(self, partner=None, template=None):
        vals = {"partner_id": (partner or self.partner).id}
        if template:
            vals["template_id"] = template.id
        return self.env["letter.document"].create(vals)

    def _make_pdf_bytes(self, text):
        """Build a minimal one-page PDF with reportlab."""
        from reportlab.pdfgen import canvas

        buf = io.BytesIO()
        c = canvas.Canvas(buf)
        c.drawString(72, 720, text)
        c.showPage()
        c.save()
        return buf.getvalue()

    # -- 1. Sequence + create defaults --------------------------------
    def test_01_sequence_and_create_defaults(self):
        letter = self._make_letter()
        self.assertTrue(letter.reference.startswith("LET-"),
                        "Référence doit suivre LET-AAAA-NNN")
        self.assertEqual(letter.recipient_name, self.partner.name,
                         "Nom destinataire auto-rempli")
        self.assertIn("123 rue QA", letter.recipient_address or "",
                      "Adresse destinataire auto-remplie")
        self.assertEqual(letter.signatory_id, self.signatory,
                         "Signataire par défaut hérité de la société")
        self.assertEqual(letter.signatory_function, "Directrice générale")
        self.assertEqual(letter.state, "draft")

    # -- 2. Inline {{ }} merge fields ---------------------------------
    def test_02_apply_template_inline_merge(self):
        tmpl = self._make_template(
            "<p>Bonjour {{ object.partner_id.name }},</p>"
            "<p>Réf. {{ object.reference }}.</p>"
        )
        letter = self._make_letter(template=tmpl)
        letter.action_apply_template()
        body = str(letter.body_html)
        self.assertIn(self.partner.name, body)
        self.assertIn(letter.reference, body)
        self.assertNotIn("{{", body, "Aucun jeton non rempli")
        self.assertEqual(letter.name, "Objet %s" % self.partner.name,
                         "Titre rendu depuis l'objet du modèle")

    # -- 3. QWeb <t t-out> merge fields -------------------------------
    def test_03_apply_template_qweb_merge(self):
        tmpl = self._make_template(
            "<p>Cher <t t-out=\"object.partner_id.name\"/>.</p>", subject="x"
        )
        letter = self._make_letter(template=tmpl)
        letter.action_apply_template()
        body = str(letter.body_html)
        self.assertIn(self.partner.name, body)
        self.assertNotIn("t-out", body, "QWeb second-pass a été exécuté")

    # -- 4. merge_warning compute -------------------------------------
    def test_04_merge_warning(self):
        letter = self._make_letter()
        letter.body_html = "<p>Bonjour {{ object.partner_id.name }}</p>"
        self.assertTrue(letter.merge_warning, "Jeton résiduel détecté")
        letter.body_html = "<p>Bonjour tout le monde</p>"
        self.assertFalse(letter.merge_warning, "Avertissement effacé")

    # -- 5. apply_template guard --------------------------------------
    def test_05_apply_template_requires_template(self):
        letter = self._make_letter()
        with self.assertRaises(UserError):
            letter.action_apply_template()

    # -- 6. Quicktext picker (append + prepend, token render) ---------
    def test_06_quicktext_picker(self):
        letter = self._make_letter()
        letter.body_html = "<p>Original</p>"
        qt = self.env["letter.quicktext"].create(
            {
                "name": "QA QT",
                "body_html": "<p>Ajout {{ object.partner_id.name }}</p>",
            }
        )
        picker = self.env["letter.quicktext.picker"].create(
            {"letter_id": letter.id, "quicktext_id": qt.id, "position": "append"}
        )
        picker.action_insert()
        body = str(letter.body_html)
        self.assertIn("Original", body)
        self.assertIn(self.partner.name, body, "Jeton du bloc rendu")
        self.assertLess(body.index("Original"), body.index("Ajout"),
                        "Bloc ajouté après le corps")
        picker2 = self.env["letter.quicktext.picker"].create(
            {"letter_id": letter.id, "quicktext_id": qt.id, "position": "prepend"}
        )
        picker2.action_insert()
        self.assertTrue(str(letter.body_html).startswith("<p>Ajout"),
                        "Bloc ajouté avant le corps")

    # -- 7. Workflow: finalize / sent / draft -------------------------
    def test_07_workflow(self):
        letter = self._make_letter()
        letter.body_html = False
        with self.assertRaises(UserError):
            letter.action_finalize()
        letter.body_html = "<p>Contenu de la lettre.</p>"
        letter.recipient_name = False
        letter.recipient_address = False
        letter.action_finalize()
        self.assertEqual(letter.state, "finalized")
        self.assertTrue(letter.recipient_name, "Nom figé à la finalisation")
        self.assertTrue(letter.recipient_address, "Adresse figée à la finalisation")
        letter.action_mark_sent()
        self.assertEqual(letter.state, "sent")
        letter.action_reset_draft()
        self.assertEqual(letter.state, "draft")

    # -- 8/9. Branded PDF (banner + classic) --------------------------
    # NB: under --test-enable, Odoo's _render_qweb_pdf returns HTML rather than
    # running wkhtmltopdf, so we assert on the rendered report content (which
    # also lets us QA the locked branded chrome). Real PDF byte output is
    # covered by the manual smoke test.
    def _assert_branded_report(self, letter):
        html = self.env["ir.actions.report"]._render_qweb_html(
            "bf_letter_writer.report_letter_document", letter.ids
        )[0].decode("utf-8", errors="ignore")
        self.assertIn("Corps de la lettre QA", html, "Corps de la lettre rendu")
        self.assertIn(self.company.name, html, "Nom de l'expéditeur dans l'en-tête")
        # `t-out` HTML-escapes the apostrophe, so match an apostrophe-free slice
        self.assertIn("QA — mention", html, "Mention d'en-tête rendue")
        self.assertIn("Directrice générale", html, "Fonction du signataire rendue")
        self.assertIn("QA — pied de page", html, "Pied de page rendu")
        self.assertIn(self.partner.name, html, "Bloc destinataire rendu")
        self.assertIn(letter.reference, html, "Référence rendue")
        # _get_pdf_binary must run end to end without raising and return bytes
        out = letter._get_pdf_binary()
        self.assertTrue(out, "Rendu du rapport non vide")
        return html

    def test_08_pdf_banner(self):
        letter = self._make_letter()
        letter.body_html = "<p>Corps de la lettre QA.</p>"
        letter.salutation = "Madame, Monsieur,"
        letter.closing = "Veuillez agréer nos salutations."
        html = self._assert_branded_report(letter)
        self.assertIn("letter-banner", html, "Style bannière appliqué")
        self.assertIn("Madame, Monsieur,", html, "Appel rendu")
        self.assertIn("Veuillez agréer nos salutations.", html, "Salutation finale rendue")

    def test_09_pdf_classic(self):
        letter = self._make_letter()
        letter.letterhead_style = "classic"
        letter.body_html = "<p>Corps de la lettre QA.</p>"
        html = self._assert_branded_report(letter)
        self.assertIn("letter-classic", html, "Style classique appliqué")

    # -- 10. Preview PDF ----------------------------------------------
    def test_10_preview_pdf(self):
        letter = self._make_letter()
        letter.body_html = "<p>Corps.</p>"
        action = letter.action_preview_pdf()
        self.assertEqual(action["type"], "ir.actions.act_url")
        self.assertIn("/web/content/", action["url"])
        self.assertTrue(letter.pdf_attachment_id, "Pièce jointe PDF enregistrée")

    # -- 11. Bulk merge wizard ----------------------------------------
    def test_11_bulk_merge(self):
        tmpl = self._make_template("<p>Bonjour {{ object.partner_id.name }}.</p>")
        p2 = self.env["res.partner"].create(
            {"name": "QA Deux", "email": "two@example.invalid"}
        )
        wizard = self.env["letter.merge.wizard"].create(
            {
                "template_id": tmpl.id,
                "recipient_ids": [(6, 0, [self.partner.id, p2.id])],
                "auto_finalize": True,
            }
        )
        wizard.action_create_letters()
        letters = wizard.created_letter_ids
        self.assertEqual(len(letters), 2, "Une lettre par destinataire")
        for letter in letters:
            self.assertEqual(letter.state, "finalized", "auto_finalize appliqué")
            self.assertIn(letter.partner_id.name, str(letter.body_html),
                          "Corps fusionné par destinataire")

    # -- 12. Bulk merge: default_get from contacts selection ----------
    def test_12_bulk_merge_default_get(self):
        wizard_model = self.env["letter.merge.wizard"].with_context(
            active_model="res.partner", active_ids=[self.partner.id]
        )
        defaults = wizard_model.default_get(["recipient_ids"])
        self.assertEqual(defaults.get("recipient_ids"), [(6, 0, [self.partner.id])])

    # -- 13. Send wizard ----------------------------------------------
    def test_13_send_wizard(self):
        letter = self._make_letter()
        letter.body_html = "<p>Corps.</p>"
        letter.action_finalize()
        wizard = self.env["letter.send.wizard"].create({"letter_id": letter.id})
        # onchange prefill (triggered by create override)
        self.assertIn(self.partner, wizard.recipient_ids)
        self.assertEqual(wizard.subject, letter.name)
        self.assertTrue(wizard.body)
        # branded wrapper
        wrapped = wizard._wrap_branded_body("<p>test</p>")
        self.assertIn("test", str(wrapped))
        self.assertIn(self.company.name, str(wrapped))
        # preview
        wizard.action_preview_pdf()
        self.assertIn("/web/content/", wizard.preview_url or "")
        # send (SMTP mocked)
        with patch(
            "odoo.addons.mail.models.mail_mail.MailMail.send",
            lambda self, *a, **k: None,
        ):
            wizard.action_send()
        self.assertEqual(letter.state, "sent", "Lettre marquée envoyée")
        self.assertTrue(
            letter.message_ids.filtered(lambda m: "courriel" in (m.body or "")),
            "Note de chatter publiée",
        )
        # no-email recipient guard
        letter2 = self._make_letter(partner=self.partner_no_email)
        letter2.body_html = "<p>x</p>"
        wiz2 = self.env["letter.send.wizard"].create({"letter_id": letter2.id})
        wiz2.recipient_ids = self.partner_no_email
        with self.assertRaises(UserError):
            wiz2.action_send()

    # -- 14. Optional integrations: persona + claude ------------------
    def test_14_integrations(self):
        letter = self._make_letter()
        # bf_persona is installed on the BF tenant -> claude button enabled
        self.assertTrue(letter.claude_available)
        Persona = self.env.get("contact.persona")
        if Persona is None:
            self.skipTest("bf_persona absent")
        try:
            persona = Persona.create(
                {
                    "partner_id": self.partner.id,
                    "preferred_salutation": "Bonjour cher partenaire,",
                    "closing_formula": "Bien à vous,",
                }
            )
        except Exception:
            self.skipTest("Schéma contact.persona incompatible avec le test")
        self.partner.invalidate_recordset()
        self.assertTrue(persona)
        letter2 = self._make_letter()
        letter2._apply_persona_defaults()
        self.assertEqual(letter2.salutation, "Bonjour cher partenaire,",
                         "Appel pré-rempli depuis le persona")
        self.assertEqual(letter2.closing, "Bien à vous,",
                         "Salutation finale pré-remplie depuis le persona")
        self.assertTrue(letter2.persona_available)

    # -- 15. Review with Claude ---------------------------------------
    def test_15_review_with_claude(self):
        letter = self._make_letter()
        action = letter.action_review_with_claude()
        self.assertEqual(action["type"], "ir.actions.client")
        self.assertEqual(action["tag"], "claude_chat_launch")
        self.assertIn("prompt", action["params"])
        self.assertIn(letter.reference, action["params"]["prompt"])

    # -- 16. res.company letterhead fields ----------------------------
    def test_16_company_fields(self):
        company_fields = self.env["res.company"]._fields
        for fname in (
            "letter_signatory_id",
            "letter_signatory_function",
            "letter_signature_image",
            "letter_header_note",
            "letter_footer_html",
        ):
            self.assertIn(fname, company_fields, "Champ %s sur res.company" % fname)

    # -- 17. Security: groups + rules ---------------------------------
    def test_17_security(self):
        self.assertTrue(
            self.env.ref("bf_letter_writer.group_letter_manager")
        )
        self.assertTrue(
            self.env.ref("bf_letter_writer.module_category_letter_writer")
        )
        rule_doc = self.env.ref("bf_letter_writer.rule_letter_document_company")
        self.assertEqual(rule_doc.model_id.model, "letter.document")
        rule_qt = self.env.ref("bf_letter_writer.rule_letter_quicktext_company")
        self.assertEqual(rule_qt.model_id.model, "letter.quicktext")
        # internal user can create letters
        access = self.env["ir.model.access"].search(
            [
                ("model_id.model", "=", "letter.document"),
                ("group_id", "=", self.env.ref("base.group_user").id),
            ]
        )
        self.assertTrue(access.perm_create, "Utilisateur interne peut créer")

    # -- 18. Report action + paperformat ------------------------------
    def test_18_report(self):
        report = self.env.ref("bf_letter_writer.action_report_letter_document")
        self.assertEqual(report.model, "letter.document")
        self.assertEqual(report.report_type, "qweb-pdf")
        self.assertEqual(report.binding_model_id.model, "letter.document")
        self.assertTrue(report.paperformat_id, "Format papier lié")

    # -- 19. Onboarding panel -----------------------------------------
    def test_19_onboarding(self):
        panel = self.env.ref("bf_letter_writer.bf_onboarding_panel")
        self.assertEqual(len(panel.step_ids), 3, "Trois étapes d'onboarding")
        # the close-panel helper method exists
        self.assertTrue(
            hasattr(self.env["onboarding.onboarding"],
                    "action_close_panel_bf_letter_writer")
        )

    # -- 20. Starter data ---------------------------------------------
    def test_20_starter_data(self):
        tmpl = self.env.ref("bf_letter_writer.template_general")
        self.assertEqual(len(tmpl.quicktext_ids), 3,
                         "Modèle de départ lié aux 3 blocs")
        self.assertEqual(
            self.env["letter.quicktext"].search_count(
                [("id", "in", tmpl.quicktext_ids.ids)]
            ),
            3,
        )

    # -- 21. letterhead_style copied from template --------------------
    def test_21_letterhead_style_from_template(self):
        tmpl = self._make_template("<p>x</p>", subject="x", style="classic")
        letter = self._make_letter(template=tmpl)
        # onchange in the form; replicate it
        letter._onchange_template_id()
        self.assertEqual(letter.letterhead_style, "classic")
        letter.action_apply_template()
        self.assertEqual(letter.letterhead_style, "classic")

    # -- 22. Quicktext company scoping --------------------------------
    def test_22_quicktext_company_scope(self):
        shared = self.env["letter.quicktext"].create(
            {"name": "QA partagé", "company_id": False}
        )
        scoped = self.env["letter.quicktext"].create(
            {"name": "QA société", "company_id": self.company.id}
        )
        self.assertFalse(shared.company_id, "Bloc partagé = sans société")
        self.assertEqual(scoped.company_id, self.company)

    # -- 23. Letterhead modes: image / pdf_overlay / plain ------------
    def test_23_letterhead_modes_render(self):
        self.company.letterhead_image = _TINY_PNG_B64
        for mode in ("image", "pdf_overlay", "plain"):
            letter = self._make_letter()
            letter.letterhead_style = mode
            letter.body_html = "<p>Corps mode %s.</p>" % mode
            html = self.env["ir.actions.report"]._render_qweb_html(
                "bf_letter_writer.report_letter_document", letter.ids
            )[0].decode("utf-8", errors="ignore")
            self.assertIn("Corps mode %s" % mode, html, "Corps rendu (%s)" % mode)
            # chrome-less modes never emit the generated header *div*
            # (the .letter-banner / .letter-classic CSS rules are always in
            #  the <style> block, so match the rendered element instead).
            self.assertNotIn('<div class="letter-banner">', html,
                             "Pas de bannière (%s)" % mode)
            self.assertNotIn('<div class="letter-classic">', html,
                             "Pas de classique (%s)" % mode)
            self.assertIn('class="letter-content letter-content-plain"', html,
                          "Corps décalé (%s)" % mode)
        # image mode embeds the uploaded background
        letter_img = self._make_letter()
        letter_img.letterhead_style = "image"
        letter_img.body_html = "<p>x</p>"
        html = self.env["ir.actions.report"]._render_qweb_html(
            "bf_letter_writer.report_letter_document", letter_img.ids
        )[0].decode("utf-8", errors="ignore")
        self.assertIn('class="letter-bg"', html, "Fond image inséré")

    # -- 24. PDF overlay stamping (PyPDF2) ----------------------------
    def test_24_pdf_overlay_stamp(self):
        from PyPDF2 import PdfReader

        letter = self._make_letter()
        letter.letterhead_style = "pdf_overlay"
        self.company.letterhead_pdf = base64.b64encode(
            self._make_pdf_bytes("LETTERHEAD")
        )
        body_pdf = self._make_pdf_bytes("BODY")
        stamped = letter._stamp_on_letterhead(body_pdf)
        self.assertTrue(stamped.startswith(b"%PDF"), "Résultat = PDF valide")
        reader = PdfReader(io.BytesIO(stamped))
        self.assertEqual(len(reader.pages), 1, "Une page estampée")

    # -- 25. Archive support ------------------------------------------
    def test_25_archive(self):
        letter = self._make_letter()
        letter.active = False
        self.assertFalse(letter.active)
        # archived letters drop out of the default search
        self.assertFalse(
            self.env["letter.document"].search([("id", "=", letter.id)])
        )
        self.assertIn(
            letter,
            self.env["letter.document"].search(
                [("id", "=", letter.id), ("active", "=", False)]
            ),
        )

    # -- 26. Auto-title from recipient --------------------------------
    def test_26_auto_title(self):
        letter = self.env["letter.document"].create(
            {"partner_id": self.partner.id}
        )
        self.assertEqual(letter.name, "Lettre à %s" % self.partner.name,
                         "Titre auto depuis le destinataire")
        # user-typed title is preserved
        letter2 = self.env["letter.document"].create(
            {"partner_id": self.partner.id, "name": "Mon titre à moi"}
        )
        self.assertEqual(letter2.name, "Mon titre à moi", "Titre saisi conservé")

    # -- 27. Download-PDF action --------------------------------------
    def test_27_download_pdf(self):
        letter = self._make_letter()
        letter.body_html = "<p>Corps.</p>"
        action = letter.action_download_pdf()
        self.assertEqual(action["type"], "ir.actions.act_url")
        self.assertIn("download=true", action["url"])
        self.assertTrue(letter.pdf_attachment_id, "PDF enregistré en pièce jointe")

    # -- 28. res.company letterhead-source fields ---------------------
    def test_28_company_letterhead_fields(self):
        company_fields = self.env["res.company"]._fields
        for fname in (
            "letter_default_mode",
            "letterhead_image",
            "letterhead_pdf",
            "letter_body_top_margin",
            "letter_body_bottom_margin",
        ):
            self.assertIn(fname, company_fields, "Champ %s sur res.company" % fname)

    # -- 29. Letterhead default: template > company > banner ----------
    def test_29_letterhead_default(self):
        self.company.letter_default_mode = "plain"
        letter = self.env["letter.document"].create(
            {"partner_id": self.partner.id}
        )
        self.assertEqual(letter.letterhead_style, "plain",
                         "Défaut hérité de la société")
        tmpl = self._make_template("<p>x</p>", subject="x", style="classic")
        letter2 = self.env["letter.document"].create(
            {"partner_id": self.partner.id, "template_id": tmpl.id}
        )
        self.assertEqual(letter2.letterhead_style, "classic",
                         "Le modèle prime sur le défaut de la société")
