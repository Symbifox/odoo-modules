"""Language of the XP ledger: the earner's, not the actor's.

The XP description is stored on the transaction and read in the Fox Quest
ledger by the person who earned it. Before the source switched to English it
was French for everyone; it is now written in the earner's language, whoever
resolved the complaint.
"""
from types import SimpleNamespace

from odoo import fields
from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_cx_gamification.models.lang import reader_lang


@tagged("post_install", "-at_install")
class TestXpLedgerLanguage(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_cx_gamification"], ["fr_CA"], overwrite=True
        )
        cls.env["ir.config_parameter"].sudo().set_param(
            "bf_gamification.gamification_enabled", "True"
        )
        rule = cls.env.ref("bf_cx_gamification.xp_rule_cx_complaint_resolved")
        rule.active = True
        cls.earner = cls.env["res.users"].create({
            "name": "Earner fr_CA",
            "login": "earner.cx.xp@example.test",
            "lang": "fr_CA",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.complaint = cls.env["bf.cx.complaint"].create({
            "name": "Plainte langue XP",
            "description": "<p>Délai</p>",
            "date_received": fields.Datetime.now(),
            "user_id": cls.earner.id,
        })

    def test_complaint_xp_reads_in_the_earner_language(self):
        # Resolved by someone working in English.
        self.complaint.with_context(lang="en_US").write({"state": "resolved"})
        transaction = self.env["bf.gamification.xp.transaction"].search(
            [("reference", "=", f"bf.cx.complaint,{self.complaint.id}")]
        )
        self.assertEqual(len(transaction), 1)
        self.assertTrue(transaction.description.startswith("Plainte résolue : "))

    def test_reader_lang_skips_a_language_that_is_not_installed(self):
        self.assertEqual(reader_lang(self.earner), "fr_CA")
        # A language that is no longer installed (the selection would refuse
        # it on a real user): the company's language takes over.
        company = SimpleNamespace(partner_id=SimpleNamespace(lang="fr_CA"))
        gone = SimpleNamespace(env=self.env, lang="xx_XX", company_id=company)
        self.assertEqual(reader_lang(gone), "fr_CA")
