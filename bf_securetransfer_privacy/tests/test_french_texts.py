"""The delivered register texts follow the company's language (18.0.1.2.1).

The register fields of privacy_consent are not translatable: one value for
every reader. A French company gets the French text of each field it has not
adapted; an English company keeps the English source; an adapted text is
never overwritten."""
from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_securetransfer_privacy import hooks


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
        self.company_partner = self.env.ref("base.main_company").partner_id
        # Back to the delivered English source, whatever the database holds.
        self.source = hooks.delivered_texts(f"{self.module}/data/privacy_purpose_data.xml")
        for xmlid, values in self._french().items():
            record = self.env.ref(f"{self.module}.{xmlid}")
            record.write({field: self.source[(xmlid, field)] for field in values})

    def _value(self, xmlid, field):
        return hooks._norm(self.env.ref(f"{self.module}.{xmlid}")[field])

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
        adapted = "<p>Texte adapté par l'organisation.</p>" if field == "plain_language_summary" \
            else "Texte adapté par l'organisation."
        record.write({field: adapted})
        written = self._apply()
        self.assertNotIn(f"{xmlid}.{field}", written)
        self.assertEqual(self._value(xmlid, field), hooks._norm(adapted))
