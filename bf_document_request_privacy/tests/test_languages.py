"""Each reader reads this module in their own language.

The source is written in English and ``i18n/fr_CA.po`` carries the French.
What these tests pin down:

- the paragraph this bridge adds to the register entry follows the language
  the base bridge writes the entry in (the company's), documents, states and
  policy names included, and a French company reads the French this paragraph
  has always had, word for word;
- the alignment note on a policy follows its reader, even when two readers
  share a transaction;
- field labels, the delivered purpose (as on a fresh installation) and the
  module's card in Apps reach a French reader through the catalogue;
- an update keeps the plain-language summary the organisation adapted.
"""
import importlib.util
from unittest.mock import patch

from lxml import etree

from odoo.tests import TransactionCase, tagged
from odoo.tools import file_path

REAL_PURGE = "odoo.addons.bf_securetransfer.models.secure_transfer.SecureTransfer._purge_s3"
MODULE = "bf_document_request_privacy"
VERSION = "18.0.1.1.0"


def _fake_purge(this, actor=None):
    this.file_ids.write({"state": "purged"})
    this.write({"state": "deleted"})
    return True


def _flat(value):
    return " ".join(str(value or "").split())


class LanguageCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ("en_US", "fr_CA"):
            cls.env["res.lang"]._activate_lang(code)
        # The French of the three modules, loaded even on a database installed
        # without it.
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_document_request", "bf_securetransfer_privacy", MODULE], ["fr_CA"])
        cls.Register = cls.env["privacy.destruction.register"]
        cls.brand = cls.env.ref("bf_securetransfer.brand_default")
        cls.calendar = cls.env.ref(f"{MODULE}.retention_document_request")
        cls.policy = cls.env["bf.document.expiry.policy"].create({
            "name": "Short — 24 h",
            "retention_days": 1,
            "retention_calendar_id": cls.calendar.id,
        })
        cls.policy.update_field_translations("name", {"fr_CA": "Courte — 24 h"})
        cls.doc = cls.env["bf.document.type"].create({
            "name": "Identity document",
            "code": "identity_lang_test",
            "privacy_pi_category": "identification",
        })
        cls.doc.update_field_translations("name", {"fr_CA": "Pièce d'identité"})
        cls.client = cls.env["res.partner"].create({"name": "Test client"})
        cls.requester = cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Requesting employee", "login": "bf_drp_lang_requester",
            "email": "requester@example.com",
        })

    def _request(self):
        transfer = self.env["secure.transfer"].create({
            "brand_id": self.brand.id,
            "sender_name": "Test client",
            "sender_email": "client@example.com",
            "recipient_emails": "requester@example.com",
            "bf_document_expiry_policy_id": self.policy.id,
            "company_id": self.env.company.id,
            "state": "expired",
        })
        self.env["secure.transfer.file"].create({
            "transfer_id": transfer.id, "filename": "id-front.jpg", "size": 10,
            "s3_key": "test/id-front.jpg",
        })
        request = self.env["bf.document.request"].create({
            "partner_id": self.client.id,
            "phone": "5145550142",
            "brand_id": self.brand.id,
            "transfer_id": transfer.id,
            "user_id": self.requester.id,
            "deadline": "2030-01-15 12:00:00",
            "line_ids": [(0, 0, {"document_type_id": self.doc.id})],
        })
        return request, transfer

    def _purge(self, transfer, reader_lang):
        with patch(REAL_PURGE, _fake_purge):
            transfer.with_context(lang=reader_lang)._purge_s3()
        return self.Register.search([
            ("res_model", "=", "secure.transfer"), ("res_id", "=", transfer.id)])

    def _category_label(self, lang):
        return dict(self.env["privacy.document.classification"]._fields["pi_category"]
                    ._description_selection(self.env(context={"lang": lang})))["identification"]

    def _migration(self, step):
        path = file_path(f"{MODULE}/migrations/{VERSION}/{step}-migrate.py")
        spec = importlib.util.spec_from_file_location(f"{MODULE}_{step}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.env.flush_all()
        module.migrate(self.env.cr, "18.0.1.0.2")
        self.env.invalidate_all()


@tagged("post_install", "-at_install", "bf_document_request_privacy")
class TestRegisterParagraphLanguage(LanguageCommon):

    def test_french_company_reads_the_paragraph_in_french_word_for_word(self):
        """Purge triggered in English, French company: the French of 18.0.1.0.2."""
        self.env.company.partner_id.lang = "fr_CA"
        request, transfer = self._request()
        entry = self._purge(transfer, "en_US")
        self.assertIn(
            f"Demande de pièces « {request.name} » : pièces réclamées à Test client "
            "par Requesting employee.\nPièces demandées : Pièce d'identité (Attendu)\n"
            "Échéance de dépôt : 2030-01-15 12:00:00",
            entry.document_description)
        self.assertEqual(
            entry.legal_basis,
            f"{self.calendar.legal_basis}\n\nDurée appliquée : 1 jour(s), selon la "
            "politique d'expiration « Courte — 24 h », rattachée à la règle de "
            f"conservation « {self.calendar.code} ».")
        self.assertEqual(entry.pi_categories, self._category_label("fr_CA"))
        # The base bridge's part is in the same language.
        self.assertIn("supprimé(s) du stockage objet", entry.document_description)

    def test_english_company_reads_the_paragraph_in_english(self):
        """Purge triggered in French, English company: English paragraph."""
        self.env.company.partner_id.lang = "en_US"
        request, transfer = self._request()
        entry = self._purge(transfer, "fr_CA")
        self.assertIn(
            f"Document request \"{request.name}\": documents requested from "
            "Test client by Requesting employee.\nRequested documents: "
            "Identity document (Expected)\nUpload deadline: 2030-01-15 12:00:00",
            entry.document_description)
        self.assertIn("Period applied: 1 day(s), under the expiry policy "
                      "\"Short — 24 h\"", entry.legal_basis)
        self.assertEqual(entry.pi_categories, self._category_label("en_US"))

    def test_undeclared_category_is_named_in_the_register_language(self):
        self.env.company.partner_id.lang = "fr_CA"
        request, transfer = self._request()
        self.doc.privacy_pi_category = False
        entry = self._purge(transfer, "en_US")
        self.assertEqual(entry.pi_categories, "catégorie non déclarée : Pièce d'identité")


@tagged("post_install", "-at_install", "bf_document_request_privacy")
class TestInterfaceLanguage(LanguageCommon):

    def test_alignment_note_follows_each_reader_in_one_transaction(self):
        code = self.calendar.code
        french = self.policy.with_context(lang="fr_CA").privacy_alignment_note
        english = self.policy.with_context(lang="en_US").privacy_alignment_note
        self.assertEqual(
            french, f"La règle « {code} » ne fixe pas de durée : c'est cette "
                    "politique qui la porte (1 jour(s)).")
        self.assertEqual(
            english, f"Rule \"{code}\" sets no period: this policy carries it (1 day(s)).")

    def test_field_labels(self):
        Fields = self.env["ir.model.fields"]
        expected = {
            ("bf.document.expiry.policy", "retention_calendar_id"):
                ("Retention rule", "Règle de conservation"),
            ("bf.document.expiry.policy", "retention_calendar_days"):
                ("Rule retention (days)", "Rétention de la règle (jours)"),
            ("bf.document.expiry.policy", "privacy_alignment_note"):
                ("Alignment", "Alignement"),
            ("bf.document.type", "privacy_pi_category"):
                ("Personal information category", "Catégorie de RP"),
        }
        for (model, fname), (english, french) in expected.items():
            field = Fields._get(model, fname)
            self.assertEqual(field.with_context(lang="en_US").field_description, english)
            self.assertEqual(field.with_context(lang="fr_CA").field_description, french)


@tagged("post_install", "-at_install", "bf_document_request_privacy")
class TestDeliveredDataLanguage(LanguageCommon):

    def _shipped(self, fname):
        tree = etree.parse(file_path(f"{MODULE}/data/privacy_purpose_data.xml"))
        return tree.find(f".//record[@id='purpose_document_request']/field[@name='{fname}']").text

    def _as_fresh_install(self, purpose):
        purpose.with_context(lang="en_US").write({
            "name": self._shipped("name"),
            "plain_language_summary": self._shipped("plain_language_summary"),
        })
        self.env.flush_all()
        self.env.cr.execute("""
            UPDATE privacy_purpose
               SET name = jsonb_build_object('en_US', name->'en_US'),
                   plain_language_summary = jsonb_build_object(
                       'en_US', plain_language_summary->'en_US')
             WHERE id = %s""", (purpose.id,))
        self.env.invalidate_all()

    def test_purpose_reaches_a_french_reader_through_the_catalogue(self):
        purpose = self.env.ref(f"{MODULE}.purpose_document_request")
        self._as_fresh_install(purpose)
        self.env["ir.module.module"]._load_module_terms([MODULE], ["fr_CA"])
        fr = purpose.with_context(lang="fr_CA")
        self.assertEqual(fr.name, "Collecte de pièces réclamées au client")
        summary = _flat(fr.plain_language_summary)
        self.assertIn("Pour compléter votre dossier, on vous demande de fournir "
                      "certaines pièces, par exemple une pièce d'identité", summary)
        self.assertIn("Votre adresse courriel ou votre numéro de mobile sert", summary)
        self.assertIn("Les images sont supprimées du stockage", summary)

    def test_update_keeps_the_adapted_summary(self):
        purpose = self.env.ref(f"{MODULE}.purpose_document_request")
        self._as_fresh_install(purpose)
        adapted = "<p>Notre texte, en un seul paragraphe.</p>"
        # Written straight in the column: the organisation's French, whatever
        # its structure.
        self.env.cr.execute("""
            UPDATE privacy_purpose
               SET plain_language_summary = plain_language_summary
                   || jsonb_build_object('fr_CA', %s::text)
             WHERE id = %s""", (adapted, purpose.id))
        # A name adapted in English only: no French key for the catalogue to
        # respect, so French readers read the English adaptation, as before.
        self.env.cr.execute("""
            UPDATE privacy_purpose SET name = jsonb_build_object('en_US', %s::text)
             WHERE id = %s""", ("Our own purpose name", purpose.id))
        self.env.invalidate_all()
        self._migration("post")
        self.env["ir.module.module"]._load_module_terms([MODULE], ["fr_CA"])
        self._migration("end")
        self.assertEqual(
            _flat(purpose.with_context(lang="fr_CA").plain_language_summary),
            _flat(adapted))
        self.assertEqual(purpose.with_context(lang="fr_CA").name, "Our own purpose name")

    def test_update_gives_the_module_card_the_catalogue_french(self):
        card = self.env.ref(f"base.module_{MODULE}")
        self.env.cr.execute("""
            UPDATE ir_module_module
               SET summary = summary || '{"fr_CA": "Ancien résumé"}'::jsonb
             WHERE id = %s""", (card.id,))
        self.env.invalidate_all()
        self._migration("pre")
        self.env["ir.module.module"]._load_module_terms([MODULE], ["fr_CA"], overwrite=True)
        self.assertEqual(card.with_context(lang="fr_CA").shortdesc,
                         "Demande de pièces : pont vie privée (Loi 25)")
        self.assertTrue(card.with_context(lang="fr_CA").summary.startswith(
            "Rattacher la politique d'expiration au calendrier de conservation"))
