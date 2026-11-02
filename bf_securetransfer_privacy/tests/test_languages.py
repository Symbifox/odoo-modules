"""Each reader reads this module in their own language.

The source is written in English and ``i18n/fr_CA.po`` carries the French:
Odoo never translates to en_US, so a user set to English only reads English
because the source is. What these tests pin down:

- the register entry is written in the company's language, whoever or
  whatever triggers the purge (a scheduled job, an employee in another
  language), and a French company reads the French this entry has always
  had, word for word;
- the delivered purpose reaches a French reader through the catalogue, as on
  a fresh installation;
- an update keeps the plain-language summary the organisation adapted, and
  gives the module's card in Apps the catalogue's French.

Every test sets the languages it needs: the database may be French only or
English only.
"""
import importlib.util
from unittest.mock import patch

from lxml import etree

from odoo.tests import TransactionCase, tagged
from odoo.tools import file_path

REAL_PURGE = "odoo.addons.bf_securetransfer.models.secure_transfer.SecureTransfer._purge_s3"
MODULE = "bf_securetransfer_privacy"
VERSION = "18.0.1.2.0"


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
        # This module's French, loaded even on a database installed without it.
        cls.env["ir.module.module"]._load_module_terms([MODULE], ["fr_CA"])
        cls.Register = cls.env["privacy.destruction.register"]
        cls.brand = cls.env.ref("bf_securetransfer.brand_default")

    def _transfer(self):
        transfer = self.env["secure.transfer"].create({
            "brand_id": self.brand.id,
            "sender_name": "Test sender",
            "sender_email": "sender@example.com",
            "recipient_emails": "one@example.com",
            "company_id": self.env.company.id,
            # An expired transfer, as the retention job finds it.
            "state": "expired",
        })
        for name in ("signed-contract.pdf", "appendix.xlsx"):
            self.env["secure.transfer.file"].create({
                "transfer_id": transfer.id, "filename": name, "size": 10,
                "s3_key": f"test/{name}",
            })
        return transfer

    def _purge(self, transfer, reader_lang, actor=None):
        """Purges as a session in ``reader_lang`` would, and returns the entry."""
        with patch(REAL_PURGE, _fake_purge):
            transfer.with_context(lang=reader_lang)._purge_s3(actor=actor)
        return self.Register.search([
            ("res_model", "=", "secure.transfer"), ("res_id", "=", transfer.id)])

    def _migration(self, step):
        path = file_path(f"{MODULE}/migrations/{VERSION}/{step}-migrate.py")
        spec = importlib.util.spec_from_file_location(f"{MODULE}_{step}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.env.flush_all()
        module.migrate(self.env.cr, "18.0.1.1.0")
        self.env.invalidate_all()


@tagged("post_install", "-at_install", "bf_securetransfer_privacy")
class TestRegisterLanguage(LanguageCommon):

    def test_french_company_reads_its_entry_in_french_word_for_word(self):
        """A purge triggered in English, for a French company: French entry."""
        self.env.company.partner_id.lang = "fr_CA"
        transfer = self._transfer()
        entry = self._purge(transfer, "en_US", actor="retention job")
        self.assertEqual(len(entry), 1)
        self.assertEqual(
            entry.document_description,
            f"Transfert sécurisé « {transfer.name} » : 2 fichier(s) supprimé(s) du "
            "stockage objet (transfert expiré, purgé par le cron de rétention).\n"
            "Fichiers : signed-contract.pdf, appendix.xlsx\n"
            "Déclencheur consigné au journal d'accès : retention job")
        self.assertEqual(
            entry.pi_categories,
            "Contenu déposé par l'expéditeur : non classifié, catégories inconnues")
        self.assertEqual(
            entry.legal_basis,
            "Durée de conservation du transfert sécurisé, appliquée par le cron de "
            "purge du module. Art. 23 LPRPSP : le renseignement est détruit lorsque "
            "la fin pour laquelle il a été recueilli est accomplie.")
        self.assertEqual(entry.res_field, "fichiers déposés (stockage objet)")

    def test_english_company_reads_its_entry_in_english(self):
        """A purge triggered in French, for an English company: English entry."""
        self.env.company.partner_id.lang = "en_US"
        transfer = self._transfer()
        entry = self._purge(transfer, "fr_CA")
        self.assertEqual(
            entry.document_description,
            f"Secure transfer \"{transfer.name}\": 2 file(s) deleted from object "
            "storage (expired transfer, purged by the retention job).\n"
            "Files: signed-contract.pdf, appendix.xlsx")
        self.assertEqual(entry.res_field, "uploaded files (object storage)")
        self.assertIn("Section 23", entry.legal_basis)

    def test_abandoned_draft_is_described_in_the_company_language(self):
        self.env.company.partner_id.lang = "fr_CA"
        transfer = self._transfer()
        transfer.state = "draft"
        entry = self._purge(transfer, "en_US")
        self.assertIn("(brouillon abandonné, récolté par le ménage automatique)",
                      entry.document_description)

    def test_inactive_company_language_falls_back_on_the_current_one(self):
        """A company language that is not active never reaches the context."""
        self.env.company.partner_id.flush_recordset()
        self.env.cr.execute("UPDATE res_partner SET lang = 'xx_XX' WHERE id = %s",
                            (self.env.company.partner_id.id,))
        self.env.company.partner_id.invalidate_recordset(["lang"])
        transfer = self._transfer()
        self.assertEqual(transfer.with_context(lang="fr_CA")._privacy_register_lang(), "fr_CA")
        entry = self._purge(transfer, "fr_CA")
        self.assertIn("supprimé(s) du stockage objet", entry.document_description)


@tagged("post_install", "-at_install", "bf_securetransfer_privacy")
class TestDeliveredDataLanguage(LanguageCommon):

    def _shipped_summary(self):
        tree = etree.parse(file_path(f"{MODULE}/data/privacy_purpose_data.xml"))
        node = tree.find(".//record[@id='purpose_securetransfer']"
                         "/field[@name='plain_language_summary']")
        return node.text

    def _as_fresh_install(self, purpose):
        """Only the shipped English, as a new installation would hold."""
        purpose.with_context(lang="en_US").write({
            "name": "File delivery by secure transfer",
            "plain_language_summary": self._shipped_summary(),
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
        purpose = self.env.ref(f"{MODULE}.purpose_securetransfer")
        self._as_fresh_install(purpose)
        self.env["ir.module.module"]._load_module_terms([MODULE], ["fr_CA"])
        fr = purpose.with_context(lang="fr_CA")
        self.assertEqual(fr.name, "Remise de fichiers par transfert sécurisé")
        summary = _flat(fr.plain_language_summary)
        self.assertIn("Lorsqu'un document vous est remis par lien sécurisé", summary)
        self.assertIn("Passé la date d'expiration, le fichier est supprimé", summary)
        en = _flat(purpose.with_context(lang="en_US").plain_language_summary)
        self.assertIn("When a document is delivered to you through a secure link", en)

    def test_update_keeps_the_adapted_summary(self):
        """A French summary adapted by the organisation survives the update,
        even with a paragraph more than the shipped one, and so does a name
        adapted in English only."""
        purpose = self.env.ref(f"{MODULE}.purpose_securetransfer")
        self._as_fresh_install(purpose)
        adapted = ("<p>Notre texte, premier paragraphe.</p><p>Deuxième.</p>"
                   "<p>Troisième, ajouté par l'organisation.</p>")
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
        # What every update does right after the `post` step.
        self.env["ir.module.module"]._load_module_terms([MODULE], ["fr_CA"])
        self._migration("end")
        self.assertEqual(
            _flat(purpose.with_context(lang="fr_CA").plain_language_summary),
            _flat(adapted))
        self.assertEqual(purpose.with_context(lang="fr_CA").name, "Our own purpose name")

    def test_update_gives_the_module_card_the_catalogue_french(self):
        """A stale French key on the module's card gives way to the catalogue."""
        card = self.env.ref(f"base.module_{MODULE}")
        self.env.cr.execute("""
            UPDATE ir_module_module
               SET shortdesc = shortdesc || '{"fr_CA": "Ancien nom"}'::jsonb
             WHERE id = %s""", (card.id,))
        self.env.invalidate_all()
        self._migration("pre")
        self.env["ir.module.module"]._load_module_terms([MODULE], ["fr_CA"], overwrite=True)
        self.assertEqual(card.with_context(lang="fr_CA").shortdesc,
                         "Transfert sécurisé : pont vie privée (Loi 25)")
        self.assertEqual(card.with_context(lang="en_US").shortdesc,
                         "Secure Transfer: Privacy Bridge (Quebec Law 25)")
