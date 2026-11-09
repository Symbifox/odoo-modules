"""The delivered register texts follow the company's language (18.0.1.2.1).

The register fields of privacy_consent are not translatable: one value for
every reader. A French company gets the French text of each field it has not
adapted; an English company keeps the English source; an adapted text is
never overwritten.

18.0.1.2.2 adds three things: a text this module delivered in French BEFORE
18.0.1.2.0 is not an adaptation but the `noupdate` freeze, so its wording is
refreshed whatever the company's language; a translatable field whose SOURCE
term was overwritten with French by 18.0.1.2.1 gets its English source back,
the catalogue carrying the French; and the catalogue is only ever reloaded
where it can add that French without replacing anybody's own text.
"""
import json
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_securetransfer_privacy import hooks

# The purpose summary as this module delivered it BEFORE 18.0.1.2.0 — the data
# file's CDATA, indentation included. Every database installed before that
# release holds exactly this, and must keep it: it says the same words as the
# French 18.0.1.2.1 wrote into the source term, so only an exact comparison
# tells the two apart.
LEGACY_SUMMARY = (
    "\n                <p>Lorsqu'un document vous est remis par lien sécurisé, le\n"
    "                fichier est déposé sur un stockage chiffré et le lien qui y\n"
    "                mène expire à une date fixée d'avance. Votre adresse courriel\n"
    "                sert à vous adresser ce lien et à consigner qui a téléchargé\n"
    "                quoi, et quand.</p>\n"
    "                <p>Passé la date d'expiration, le fichier est supprimé du\n"
    "                stockage. Le journal des accès et la fiche du transfert sont\n"
    "                conservés sans le contenu, parce qu'ils font la preuve de ce\n"
    "                qui a été remis.</p>\n            ")

# An adapted summary with one paragraph MORE than the source: the shape that a
# catalogue reload replaces, on a field translated term by term.
ADAPTED_SUMMARY = ("<p>Notre premier paragraphe.</p><p>Notre deuxième.</p>"
                   "<p>Et un troisième, que la source n'a pas.</p>")


@tagged("post_install", "-at_install")
class TestFrenchTexts(TransactionCase):

    module = hooks.MODULE

    def _apply(self):
        return hooks.apply_french(self.env)

    def _french(self):
        return hooks.FRENCH

    def setUp(self):
        super().setUp()
        self.env["res.lang"]._activate_lang("fr_CA")
        self.env["res.lang"]._activate_lang("en_US")
        self.company_partner = self.env.ref("base.main_company").partner_id
        # Back to the delivered English source, whatever the database holds.
        self.source = hooks.delivered_texts(f"{self.module}/data/privacy_purpose_data.xml")
        for xmlid, values in self._french().items():
            record = self.env.ref(f"{self.module}.{xmlid}")
            record.write({field: self.source[(xmlid, field)] for field in values})

    def _value(self, xmlid, field):
        return hooks._norm(self.env.ref(f"{self.module}.{xmlid}")[field])

    def _seed_legacy(self):
        """Puts back, word for word, what this module delivered before 1.2.0."""
        seeded = []
        for xmlid, values in hooks.LEGACY.items():
            record = self.env.ref(f"{self.module}.{xmlid}")
            for field, olds in values.items():
                record.write({field: olds[0]})
                seeded.append((xmlid, field))
        return seeded

    def test_a_french_company_gets_the_french_texts(self):
        self.company_partner.lang = "fr_CA"
        written = self._apply()
        expected = sum(len(values) for values in self._french().values())
        self.assertEqual(len(written), expected)
        for xmlid, values in self._french().items():
            for field, text in values.items():
                self.assertEqual(self._value(xmlid, field), hooks._norm(text), f"{xmlid}.{field}")
        self.assertEqual(self._apply(), [], "a second pass writes nothing")

    def test_an_english_company_keeps_the_english_source(self):
        self.company_partner.lang = "en_US"
        self.assertEqual(self._apply(), [])
        for xmlid, values in self._french().items():
            for field in values:
                self.assertEqual(self._value(xmlid, field),
                                 hooks._norm(self.source[(xmlid, field)]))

    def test_an_adapted_text_is_never_overwritten(self):
        self.company_partner.lang = "fr_CA"
        xmlid, values = next(iter(self._french().items()))
        field = next(iter(values))
        record = self.env.ref(f"{self.module}.{xmlid}")
        adapted = "Texte adapté par l'organisation."
        record.write({field: adapted})
        written = self._apply()
        self.assertNotIn(f"{xmlid}.{field}", written)
        self.assertEqual(self._value(xmlid, field), hooks._norm(adapted))

    # === 18.0.1.2.2 ===

    def test_the_former_french_wording_is_refreshed(self):
        """A text frozen by `noupdate` takes the current French."""
        self.company_partner.lang = "fr_CA"
        seeded = self._seed_legacy()
        self.assertTrue(seeded, "LEGACY must name at least one field")
        written = self._apply()
        for xmlid, field in seeded:
            self.assertIn(f"{xmlid}.{field}", written)
            self.assertEqual(self._value(xmlid, field),
                             hooks._norm(self._french()[xmlid][field]))

    def test_the_former_french_wording_is_refreshed_whatever_the_company_language(self):
        """A French value proves the register is French: only the wording moves.

        A database that carries this module's French texts under an
        English-speaking company would otherwise keep the 18.0.1.1.x vocabulary
        for good, while the rest of the fleet moved on.
        """
        self.company_partner.lang = "en_US"
        seeded = self._seed_legacy()
        written = self._apply()
        for xmlid, field in seeded:
            self.assertIn(f"{xmlid}.{field}", written)
            self.assertEqual(self._value(xmlid, field),
                             hooks._norm(self._french()[xmlid][field]))

    def test_a_text_that_is_neither_delivered_nor_source_is_left_alone(self):
        """Neither the English source nor a former French text: it is theirs."""
        self.company_partner.lang = "en_US"
        xmlid = next(iter(hooks.LEGACY))
        field = next(iter(hooks.LEGACY[xmlid]))
        record = self.env.ref(f"{self.module}.{xmlid}")
        record.write({field: "Règle adaptée par l'organisation."})
        self.assertNotIn(f"{xmlid}.{field}", self._apply())
        self.assertEqual(self._value(xmlid, field),
                         hooks._norm("Règle adaptée par l'organisation."))

    def test_no_translatable_field_goes_through_the_hook(self):
        """🔴 Writing a `translate=True` field here lands French in the SOURCE
        term, which matches no catalogue key and shows French to every reader.
        That is the defect 18.0.1.2.2 repairs: it must not come back."""
        for xmlid, values in self._french().items():
            record = self.env.ref(f"{self.module}.{xmlid}")
            for field in values:
                self.assertFalse(
                    record._fields[field].translate,
                    f"{record._name}.{field} is translatable: its French belongs "
                    f"to the catalogue, not to hooks.FRENCH")

    def test_a_company_without_a_language_is_read_from_the_installed_ones(self):
        """Nothing would ever be written in French otherwise, on any release."""
        self.company_partner.lang = False
        lang = type(self.env["res.lang"])
        with patch.object(lang, "get_installed",
                          return_value=[("fr_CA", "Français (CA)")]):
            self.assertTrue(hooks.company_speaks_french(self.env))
        with patch.object(lang, "get_installed",
                          return_value=[("fr_CA", "Français (CA)"), ("en_CA", "English")]):
            self.assertFalse(hooks.company_speaks_french(self.env))


@tagged("post_install", "-at_install")
class TestSourceTerms(TransactionCase):
    """18.0.1.2.1 wrote French into the source term of the purpose summary."""

    module = hooks.MODULE

    def setUp(self):
        super().setUp()
        self.env["res.lang"]._activate_lang("fr_CA")
        self.source = hooks.delivered_texts()
        self.key = ("purpose_securetransfer", "privacy.purpose", "plain_language_summary")
        self.french = hooks.CLOBBERED_SOURCES[self.key]
        self.record = self.env.ref(f"{self.module}.purpose_securetransfer")
        self.field = self.key[2]
        self.delivered = self.source[("purpose_securetransfer", self.field)]

    def _source_term(self):
        return hooks._norm(self.record.with_context(lang="en_US")[self.field])

    def _read(self, lang):
        return hooks._norm(self.record.with_context(lang=lang)[self.field])

    def _clobber(self):
        """Reproduces what 18.0.1.2.1 did: the French in the SOURCE term.

        ⚠️ `write` under `lang="en_US"`, not `update_field_translations`: the
        field is translated term by term, and that method then expects
        `{lang: {old_term: new_term}}`.
        """
        self.record.with_context(lang="en_US").write({self.field: self.french})

    def _end_step(self):
        """What the `end` step of the migration does: the gate, then the reload."""
        if not hooks.sources_awaiting_catalogue(self.env):
            return False
        langs = [code for code, _name in self.env["res.lang"].get_installed()
                 if code != "en_US"]
        self.env["ir.module.module"]._load_module_terms([self.module], langs, overwrite=True)
        return True

    def test_the_english_source_comes_back(self):
        self._clobber()
        self.assertEqual(self._source_term(), hooks._norm(self.french))
        self.assertEqual(hooks.restore_sources(self.env),
                         [f"purpose_securetransfer.{self.field}"])
        self.assertEqual(self._source_term(), hooks._norm(self.delivered))
        self.assertEqual(hooks.restore_sources(self.env), [],
                         "a second pass writes nothing")

    def test_the_french_delivered_before_1_2_0_is_left_alone(self):
        """🔴 The regression this test exists for: the two French texts say the
        same words, and a whitespace-insensitive comparison took the register of
        the databases installed before 18.0.1.2.0 from French to English."""
        self.record.with_context(lang="en_US").write({self.field: LEGACY_SUMMARY})
        self.assertEqual(hooks._norm(LEGACY_SUMMARY), hooks._norm(self.french),
                         "the two texts must still normalise the same, or this "
                         "test no longer guards anything")
        self.assertEqual(hooks.restore_sources(self.env), [])
        self.assertEqual(self._source_term(), hooks._norm(LEGACY_SUMMARY))

    def test_a_source_term_nobody_clobbered_is_left_alone(self):
        self.record.with_context(lang="en_US").write({self.field: "<p>Our own wording.</p>"})
        self.assertEqual(hooks.restore_sources(self.env), [])
        self.assertEqual(self._source_term(), hooks._norm("<p>Our own wording.</p>"))

    def _write_in_column(self, source_term, translation):
        """The two values side by side, which the ORM alone cannot produce here.

        Writing a translation of a different shape through the ORM resets the
        source term with it, so the state `{en_US: …, fr_CA: …}` is written
        straight to the column — a release, or a catalogue, can leave it.
        """
        self.env.cr.execute(
            "UPDATE privacy_purpose SET plain_language_summary = %s::jsonb WHERE id = %s",
            [json.dumps({"en_US": source_term, "fr_CA": translation}), self.record.id])
        self.record.invalidate_recordset([self.field])

    def test_a_field_somebody_translated_is_left_alone(self):
        """A term-by-term translation shares no term with the English source:
        handing the source back would take that translation with it."""
        self._write_in_column(self.french, "<p>Notre traduction à nous.</p>")
        self.assertFalse(hooks._nobody_translated(self.record, self.field))
        self.assertEqual(hooks.restore_sources(self.env), [])
        self.assertIn("nous", self._read("fr_CA"))
        self.assertEqual(self._source_term(), hooks._norm(self.french))

    def test_a_french_the_catalogue_cannot_serve_keeps_its_french(self):
        """One catalogue is shipped. A fr_FR reader handed the English source
        would have nothing to translate it: the French already stored stays."""
        self._clobber()
        lang = type(self.env["res.lang"])
        with patch.object(lang, "get_installed",
                          return_value=[("fr_FR", "Français (FR)"), ("en_CA", "English")]):
            self.assertFalse(hooks._catalogue_can_serve_the_french_readers(self.env))
            self.assertEqual(hooks.restore_sources(self.env), [])
        self.assertEqual(self._source_term(), hooks._norm(self.french))

    # === the `end` step ===

    def test_the_end_step_binds_the_catalogue_to_the_restored_source(self):
        """The whole point of the restore: the fr_CA reader gets the French of
        the catalogue, and the English reader the English source."""
        self._clobber()
        self.assertTrue(hooks.restore_sources(self.env))
        self.assertEqual(hooks.sources_awaiting_catalogue(self.env),
                         [f"purpose_securetransfer.{self.field}"])
        self.assertTrue(self._end_step(), "the gate must let the reload through")
        self.assertIn("Lorsqu'un document", self._read("fr_CA"))
        self.assertIn("When a document", self._source_term())

    def test_the_end_step_leaves_an_adapted_translation_alone(self):
        """🔴 Measured at the bench: reloading the catalogue with
        `overwrite=True` REPLACES an adapted translation of this field when its
        shape differs from the source — the protection the 18.0.1.2.0 migration
        describes holds for a field translated in one block, not for this one.
        The gate is what keeps their text."""
        self._write_in_column(self.delivered, ADAPTED_SUMMARY)
        theirs = self._read("fr_CA")
        self.assertIn("troisième", theirs)
        self.assertEqual(self._source_term(), hooks._norm(self.delivered),
                         "the source is the delivered English: only the "
                         "translation stands between them and the catalogue")
        self.assertEqual(hooks.restore_sources(self.env), [])
        self.assertEqual(hooks.sources_awaiting_catalogue(self.env), [],
                         "nothing may be reloaded over a text of their own")
        self.assertFalse(self._end_step(), "the gate must hold the reload back")
        self.assertEqual(self._read("fr_CA"), theirs)

    def test_the_gate_stays_shut_on_a_source_that_was_never_restored(self):
        self._clobber()
        self.assertEqual(hooks.sources_awaiting_catalogue(self.env), [])
