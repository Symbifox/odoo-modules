"""18.0.1.2.0: the starter catalogue's identifiers change, the records do not.

Before 18.0.1.2.0 the delivered policies and document types had identifiers
named after one use case. The pre-migration gives them neutral ones. What an
organisation must find after the update is exactly what it had: the same
records (no duplicate next to them), with the values it adjusted, in every
language, and nothing deleted.

These tests turn a fresh install back into a 18.0.1.1.1 database (former
identifiers, values adjusted by the organisation, a value stored under en_US
alone as a French-only database does), then replay what an update does: the
pre-migration, the data files, the catalogue.
"""
import importlib.util
import json

from odoo.tests import TransactionCase, tagged
from odoo.tools import convert_file
from odoo.tools.misc import file_path

MODULE = "bf_document_request"
# new identifier: former identifier (18.0.1.1.1)
FORMER = {
    "policy_short_24h": "policy_vendeur_48h",
    "policy_long_30d": "policy_directrice_45j",
    "doc_type_identity": "doc_type_permis",
    "doc_type_address": "doc_type_residence",
    "doc_type_insurance": "doc_type_assurance",
    "doc_type_income": "doc_type_talon",
}
DATA = ("data/bf_document_expiry_policy_data.xml", "data/bf_document_type_data.xml")


def _pre_migration():
    path = file_path(f"{MODULE}/migrations/18.0.1.2.0/pre-migrate.py")
    spec = importlib.util.spec_from_file_location("bf_document_request_pre_1_2_0", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@tagged("post_install", "-at_install")
class TestCatalogueMigration(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ("en_US", "fr_CA"):
            cls.env["res.lang"]._activate_lang(code)

    def _raw(self, record, fname):
        record.flush_recordset([fname])
        self.env.cr.execute(
            f'SELECT "{fname}" FROM "{record._table}" WHERE id = %s', (record.id,))
        return self.env.cr.fetchone()[0]

    def _set_raw(self, record, fname, value):
        self.env.cr.execute(
            f'UPDATE "{record._table}" SET "{fname}" = %s::jsonb WHERE id = %s',
            (json.dumps(value), record.id))
        record.invalidate_recordset([fname])

    def _as_18_0_1_1_1(self):
        """The fresh install, turned back into a 18.0.1.1.1 database."""
        records = {}
        IMD = self.env["ir.model.data"]
        for new, former in FORMER.items():
            records[new] = self.env.ref(f"{MODULE}.{new}")
            # A database already migrated keeps the former name as a second
            # identifier: back to one name, the former one.
            IMD.search([("module", "=", MODULE), ("name", "=", former)]).unlink()
            IMD.search([("module", "=", MODULE), ("name", "=", new)]).name = former
        identity, income = records["doc_type_identity"], records["doc_type_income"]
        # Adjusted by the organisation in a French-only database: one key.
        self._set_raw(identity, "name", {"en_US": "Permis de conduire (recto-verso)"})
        self._set_raw(identity, "instructions", {"en_US": "Les deux faces, à plat."})
        # Adjusted differently per language.
        self._set_raw(income, "name", {"en_US": "Pay stub", "fr_CA": "Talon de paie récent"})
        short = records["policy_short_24h"]
        self._set_raw(short, "name", {"en_US": "Front desk — 24 h", "fr_CA": "Front desk — 24 h"})
        short.retention_days = 1
        records["policy_long_30d"].retention_days = 7
        self.env.flush_all()
        return records

    def _update(self):
        """What `-u` does next: the pre-migration, the data files (noupdate),
        the catalogue."""
        _pre_migration().migrate(self.env.cr, "18.0.1.1.1")
        self.env.invalidate_all()
        self.env.registry.clear_cache()
        for path in DATA:
            convert_file(self.env, MODULE, path, {}, mode="update", noupdate=True)
        self.env["ir.module.module"]._load_module_terms([MODULE], ["fr_CA"], overwrite=True)
        self.env.invalidate_all()

    def _snapshot(self, records):
        return {
            new: {fname: self._raw(rec, fname) for fname in (
                ("name", "retention_days") if rec._name == "bf.document.expiry.policy"
                else ("name", "instructions", "code"))}
            for new, rec in records.items()
        }

    def test_no_duplicate_and_the_same_records(self):
        records = self._as_18_0_1_1_1()
        counts = (self.env["bf.document.type"].with_context(active_test=False).search_count([]),
                  self.env["bf.document.expiry.policy"].with_context(active_test=False).search_count([]))
        self._update()
        self.assertEqual(
            (self.env["bf.document.type"].with_context(active_test=False).search_count([]),
             self.env["bf.document.expiry.policy"].with_context(active_test=False).search_count([])),
            counts, "the update created records next to the existing ones")
        for new, rec in records.items():
            self.assertEqual(self.env.ref(f"{MODULE}.{new}"), rec)

    def test_the_former_identifier_still_names_the_same_record(self):
        # Modules extending this one may still reference it in their own data.
        records = self._as_18_0_1_1_1()
        self._update()
        for new, former in FORMER.items():
            self.assertEqual(self.env.ref(f"{MODULE}.{former}"), records[new])

    def test_adjusted_values_are_never_rewritten(self):
        records = self._as_18_0_1_1_1()
        before = self._snapshot(records)
        self._update()
        after = self._snapshot(records)
        for new in records:
            for fname, value in before[new].items():
                if isinstance(value, dict):
                    # Every key kept as it was; a key added for a language
                    # carries what that language's readers saw: the en_US value.
                    for lang, text in value.items():
                        self.assertEqual(after[new][fname][lang], text, (new, fname, lang))
                    for lang in set(after[new][fname]) - set(value):
                        self.assertEqual(after[new][fname][lang], value["en_US"], (new, fname, lang))
                else:
                    self.assertEqual(after[new][fname], value, (new, fname))

    def test_a_french_reader_does_not_get_the_new_catalogue_over_a_value(self):
        records = self._as_18_0_1_1_1()
        self._update()
        identity = records["doc_type_identity"].with_context(lang="fr_CA")
        self.assertEqual(identity.name, "Permis de conduire (recto-verso)")
        self.assertEqual(identity.instructions, "Les deux faces, à plat.")
        self.assertEqual(records["doc_type_income"].with_context(lang="fr_CA").name,
                         "Talon de paie récent")
        self.assertEqual(records["policy_short_24h"].with_context(lang="fr_CA").name,
                         "Front desk — 24 h")

    def test_a_record_without_equivalent_is_kept(self):
        self._as_18_0_1_1_1()
        legacy = self.env["bf.document.type"].create({"name": "Legacy document", "code": "legacy"})
        self.env["ir.model.data"].create({
            "module": MODULE, "name": "doc_type_legacy", "model": "bf.document.type",
            "res_id": legacy.id, "noupdate": True})
        self._update()
        self.assertTrue(legacy.exists())
        self.assertEqual(self.env.ref(f"{MODULE}.doc_type_legacy"), legacy)

    def test_a_rerun_changes_nothing(self):
        records = self._as_18_0_1_1_1()
        self._update()
        rows = self.env["ir.model.data"].search_count([("module", "=", MODULE)])
        snapshot = self._snapshot(records)
        self._update()
        self.assertEqual(self.env["ir.model.data"].search_count([("module", "=", MODULE)]), rows)
        self.assertEqual(self._snapshot(records), snapshot)

    def test_a_fresh_install_gets_the_generic_catalogue_in_both_languages(self):
        # Whatever this database holds (fresh or migrated), the records are
        # created again from the data files, as a fresh install does.
        IMD = self.env["ir.model.data"]
        for new, former in FORMER.items():
            rec = self.env.ref(f"{MODULE}.{new}")
            if "code" in rec._fields:
                rec.code = f"{rec.code}_before"
            IMD.search([("module", "=", MODULE), ("name", "in", (new, former))]).unlink()
        self.env.registry.clear_cache()
        for path in DATA:
            convert_file(self.env, MODULE, path, {}, mode="init", noupdate=True)
        self.env["ir.module.module"]._load_module_terms([MODULE], ["fr_CA"], overwrite=True)
        self.env.invalidate_all()
        identity = self.env.ref(f"{MODULE}.doc_type_identity")
        self.assertEqual(identity.with_context(lang="en_US").name, "Identity document")
        self.assertEqual(identity.with_context(lang="fr_CA").name, "Pièce d'identité")
        short = self.env.ref(f"{MODULE}.policy_short_24h")
        self.assertEqual(short.with_context(lang="fr_CA").name, "Courte (24 h)")
        self.assertEqual(short.retention_days, 1)
        self.assertEqual(self.env.ref(f"{MODULE}.policy_long_30d").retention_days, 30)

    def test_the_module_card_drops_stale_translations(self):
        card = self.env.ref("base.module_" + MODULE)
        self._set_raw(card, "shortdesc", {
            "en_US": card.with_context(lang="en_US").shortdesc,
            "fr_CA": "Ancien nom périmé"})
        _pre_migration().migrate(self.env.cr, "18.0.1.1.1")
        self.env["ir.module.module"]._load_module_terms([MODULE], ["fr_CA"], overwrite=True)
        card.invalidate_recordset(["shortdesc"])
        self.assertEqual(card.with_context(lang="fr_CA").shortdesc,
                         "Transfert de fichiers : demande de pièces")
