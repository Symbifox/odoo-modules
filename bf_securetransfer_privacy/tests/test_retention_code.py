"""The retention code is an identifier, one for the whole fleet (18.0.1.2.2).

The register cites the rule and a verifier looks its code up in this module's
documentation, so the code the data file declares must be the code the
databases carry. 18.0.1.2.0 translated it to English at the file; `noupdate="1"`
left every database already posed with the former code, and the fleet carried
two identifiers for one rule.
"""
from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_securetransfer_privacy import hooks

RULE = "bf_securetransfer_privacy.retention_securetransfer"


@tagged("post_install", "-at_install")
class TestRetentionCode(TransactionCase):

    def setUp(self):
        super().setUp()
        self.rule = self.env.ref(RULE)
        self.delivered = hooks.delivered_texts()[("retention_securetransfer", "code")]
        # ⚠️ An essay never assumes the state of the database. Any OTHER rule
        # already carrying the delivered code would make the repair stand down,
        # and these tests would read that as a defect. Such a rule is moved
        # aside inside the test transaction, which is rolled back.
        others = self.env["privacy.retention.calendar"].with_context(
            active_test=False).search([
                ("code", "=", self.delivered), ("id", "!=", self.rule.id)])
        for index, other in enumerate(others):
            other.write({"code": f"ST-ESSAI-{index}"})

    def _twin_carrying_the_delivered_code(self):
        """A second rule on the delivered code, the clash the repair must see.

        🔴 `flush_all` between the two writes: without it the INSERT of the twin
        leaves before the UPDATE of the rule, and Postgres refuses the
        duplicate `(code, company_id)` — the test would fail on its own setup.
        """
        self.rule.write({"code": hooks.CODE_TO_REPAIR})
        self.env.flush_all()
        return self.rule.copy({"code": self.delivered,
                               "company_id": self.rule.company_id.id})

    def test_the_delivered_code_is_the_one_the_fleet_carries(self):
        """A guard against translating the identifier again."""
        self.assertEqual(self.delivered, "ST-CONTENU")

    def test_the_repair_brings_the_former_english_code_back(self):
        self.rule.write({"code": hooks.CODE_TO_REPAIR})
        self.assertTrue(hooks.repair_code(self.env))
        self.assertEqual(self.rule.code, self.delivered)
        self.assertFalse(hooks.repair_code(self.env), "a second pass writes nothing")

    def test_a_code_the_organisation_chose_is_left_alone(self):
        self.rule.write({"code": "ST-MAISON"})
        self.assertFalse(hooks.repair_code(self.env))
        self.assertEqual(self.rule.code, "ST-MAISON")

    def test_the_repair_stands_down_rather_than_break_the_unique_code(self):
        """`unique(code, company_id)`: the repair must not raise on a clash."""
        twin = self._twin_carrying_the_delivered_code()
        self.assertFalse(hooks.repair_code(self.env))
        self.assertEqual(self.rule.code, hooks.CODE_TO_REPAIR)
        self.assertEqual(twin.code, self.delivered)

    def test_a_rule_without_a_company_counts_as_a_clash(self):
        """🔴 `unique(code, company_id)` does NOT stop this pair: Postgres counts
        two NULL companies as distinct. And a rule without a company is read by
        every company, so it doubles the identifier for all of them."""
        self.rule.write({"code": hooks.CODE_TO_REPAIR})
        self.env.flush_all()
        twin = self.rule.copy({"code": self.delivered, "company_id": False})
        self.assertFalse(twin.company_id)
        self.assertFalse(hooks.repair_code(self.env))
        self.assertEqual(self.rule.code, hooks.CODE_TO_REPAIR)

    def test_a_rule_that_has_no_company_itself_sees_every_clash(self):
        """The mirror case, measured at the bench: the module's rule without a
        company, the clashing code in a company. The database would take the
        pair; the repair must not offer it."""
        self.rule.write({"code": hooks.CODE_TO_REPAIR, "company_id": False})
        self.env.flush_all()
        company = self.env.ref("base.main_company")
        twin = self.rule.copy({"code": self.delivered, "company_id": company.id})
        self.assertEqual(twin.company_id, company)
        self.assertFalse(hooks.repair_code(self.env))
        self.assertEqual(self.rule.code, hooks.CODE_TO_REPAIR)

    def test_an_archived_rule_counts_as_a_clash(self):
        """An archived twin still holds the code: the constraint ignores `active`."""
        twin = self._twin_carrying_the_delivered_code()
        twin.action_archive() if hasattr(twin, "action_archive") else twin.write({"active": False})
        self.assertFalse(hooks.repair_code(self.env))
        self.assertEqual(self.rule.code, hooks.CODE_TO_REPAIR)

    def test_a_register_entry_follows_the_rule_across_the_rename(self):
        """The register links by id: the entry stays attached, whatever the code.

        That is what makes the repair safe on a register that cannot be
        rewritten: nothing in an entry quotes the code.
        """
        self.rule.write({"code": hooks.CODE_TO_REPAIR})
        entry = self.env["privacy.destruction.register"].create({
            "destruction_date": "2026-11-08 12:00:00",
            "destroyed_by_id": self.env.user.id,
            "approved_by_id": self.env.user.id,
            "res_model": "secure.transfer",
            "res_name": "TRF-ESSAI",
            "document_description": "Essai : une entrée rattachée à la règle.",
            "pi_categories": "Contenu déposé par l'expéditeur.",
            "destruction_method": "delete",
            "destruction_scope": "partial",
            "res_field": "fichiers déposés (stockage objet)",
            "legal_basis": "Art. 23 LPRPSP.",
            "retention_calendar_id": self.rule.id,
            "subject_count": 1,
        })
        hash_before = entry.verification_hash
        self.assertTrue(hooks.repair_code(self.env))
        entry.invalidate_recordset()
        self.assertEqual(entry.retention_calendar_id, self.rule)
        self.assertEqual(entry.retention_calendar_id.code, self.delivered)
        self.assertEqual(
            entry.verification_hash, hash_before,
            "the code is not in the sealed payload: the chain must not move")
        self.assertEqual(
            entry.verification_hash, entry._compute_verification_hash(),
            "the entry still verifies after the rename")
