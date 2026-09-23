"""Relecture adverse du 2026-09-22 : un essai par constat corrigé."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

from ..models import bf_calculator_rates as rates_module

NBSP = "\u00a0"
GET = "odoo.addons.bf_calculator.models.bf_calculator_rates.requests.get"


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


VALET = {"observations": [{"d": "2026-09-22", "FXUSDCAD": {"v": "1.4064"}}]}


@tagged("post_install", "-at_install")
class TestReview(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.alice = new_test_user(cls.env, "rev_alice", groups="base.group_user,base.group_partner_manager",
                                  lang="fr_CA")
        cls.bob = new_test_user(cls.env, "rev_bob", groups="base.group_user", lang="fr_CA")
        cls.portal = new_test_user(cls.env, "rev_portal", groups="base.group_portal", lang="fr_CA")
        cls.env["ir.config_parameter"].sudo().set_param("bf_calculator.boc_rates", "")

    def setUp(self):
        super().setUp()
        rates_module._last_failure.clear()

    def A(self):
        return self.env["bf.calculator.entry"].with_user(self.alice).with_context(lang="fr_CA")

    # 1. message_id et user_id ne se posent pas par RPC
    def test_message_id_cannot_be_written(self):
        entry = self.A().browse(self.A().calc_record("1 + 1")["entry"]["id"])
        some_message = self.env["mail.message"].sudo().search([], limit=1)
        with self.assertRaises(AccessError):
            entry.write({"message_id": some_message.id})
        with self.assertRaises(AccessError):
            self.A().create({"expression": "1", "message_id": some_message.id})

    def test_user_id_cannot_be_moved(self):
        entry = self.A().browse(self.A().calc_record("1 + 1")["entry"]["id"])
        with self.assertRaises(AccessError):
            entry.write({"user_id": self.bob.id})
        self.A().calc_record("taux = 5")
        var = self.env["bf.calculator.variable"].with_user(self.alice).search([("name", "=", "taux")])
        with self.assertRaises(AccessError):
            var.write({"user_id": self.bob.id})

    # 2. la note (taux, date) arrive au chatter
    def test_note_reaches_the_chatter(self):
        partner = self.env["res.partner"].create({"name": "Note au chatter"})
        with patch(GET, return_value=FakeResponse(VALET)):
            entry_id = self.A().calc_record("100 USD en CAD")["entry"]["id"]
        wizard = self.env["bf.calculator.post"].with_user(self.alice).with_context(lang="fr_CA").create({
            "entry_ids": [(6, 0, [entry_id])], "target_reference": f"res.partner,{partner.id}"})
        wizard.action_post()
        body = partner.message_ids[:1].body
        self.assertIn("Banque du Canada", body)
        self.assertIn("2026-09-22", body)

    # 3. taxe inversée : les taxes affichées refacturent le total, sinon on le dit
    def test_reverse_tax_is_consistent(self):
        Entry = self.A()
        comps = [("TPS", Decimal("5")), ("TVQ", Decimal("9.975"))]
        cent = Decimal("0.01")
        inexact = 0
        for i in range(10000, 11001):
            total = Decimal(i) * cent
            base, taxes, back, exact = Entry._tax_breakdown(total, comps, "reverse")
            refacture = base + sum((base * r / 100).quantize(cent, rounding="ROUND_HALF_UP")
                                   for _n, r in comps)
            self.assertEqual(refacture, back)
            if exact:
                self.assertEqual(back, total, total)
            else:
                inexact += 1
        self.assertGreater(inexact, 0)  # certains totaux n'ont aucune base exacte
        res = Entry.calc_tax("100,10", "default_qc", "reverse")
        if res["inexact"]:
            self.assertNotEqual(res["inexact"], f"100,10{NBSP}$")

    # 5. panne : une seule tentative par délai de répit ; pas de lecture pour un mot banal
    def test_rates_backoff(self):
        with patch(GET, side_effect=OSError("réseau")) as mocked:
            self.A().calc_evaluate("100 USD en CAD")
            self.A().calc_evaluate("100 USD en CAD")
        self.assertEqual(mocked.call_count, 1)

    def test_no_rate_lookup_for_ordinary_words(self):
        with patch(GET) as mocked:
            self.A().calc_evaluate("3 (ans) * 5")
            self.A().calc_evaluate("2 par jour")
            self.A().calc_evaluate("2 hrs * 100")
        mocked.assert_not_called()

    # 7, 8, 9, 10, 11, 12, 17
    def test_bounds_and_edge_cases(self):
        A = self.A()
        self.assertFalse(A.calc_evaluate("9" * 60)["ok"])
        self.assertFalse(A.calc_record("9" * 60)["ok"])
        self.assertFalse(A.calc_memory("add", "NaN")["ok"])
        self.assertFalse(A.calc_memory("add", "Infinity")["ok"])
        self.assertFalse(A.calc_dates("add", "9999-12-20", n=5, business=True)["ok"])
        self.assertFalse(A.calc_dates("add", "9999-12-20", n=50)["ok"])
        res = A.calc_dates("add", "2026-09-22", n="abc")
        self.assertFalse(res["ok"])
        self.assertNotIn("dix ans", res["error"].lower())
        self.assertFalse(A.calc_evaluate("100 usd en cad")["ok"])
        self.assertFalse(A.calc_evaluate("1 h 75")["ok"])
        self.assertEqual(A.calc_evaluate("5 min * 3")["raw"], "0.25")
        A.calc_record("tiers = 1/3")
        self.assertEqual(A.calc_evaluate("3 * tiers")["raw"], "1")

    # 15. une société sans taxe québécoise ne voit pas le Québec passer devant
    def test_no_qc_default_when_accounting_has_taxes(self):
        if "account.tax" not in self.env:
            self.skipTest("account absent")
        self.env["account.tax"].search([]).write({"active": False})
        tvh = self.env["account.tax"].create({"name": "13% TVH", "amount": 13, "amount_type": "percent",
                                              "type_tax_use": "sale", "company_id": self.env.company.id})
        profiles = self.A().calc_tax_profiles()
        self.assertEqual(profiles[0]["key"], f"tax:{tvh.id}")
        self.assertFalse(any(p["default"] for p in profiles))

    # 16. le portail n'atteint pas la calculatrice
    def test_portal_is_refused(self):
        P = self.env["bf.calculator.entry"].with_user(self.portal)
        for call in (lambda: P.calc_tax_profiles(), lambda: P.calc_dates("add", "2026-09-22", n=1),
                     lambda: P.calc_evaluate("1 + 1")):
            with self.assertRaises(AccessError):
                call()

    def test_default_target_must_be_readable(self):
        partner = self.env["res.partner"].create({"name": "Illisible"})
        self.env["ir.rule"].create({
            "name": "test : lecture refusée",
            "model_id": self.env.ref("base.model_res_partner").id,
            "domain_force": f"[('id', '!=', {partner.id})]",
            "perm_read": True, "perm_write": False, "perm_create": False, "perm_unlink": False,
        })
        entry_id = self.A().calc_record("1 + 1")["entry"]["id"]
        wizard = self.env["bf.calculator.post"].with_user(self.alice).with_context(
            bf_calc_res_model="res.partner", bf_calc_res_id=partner.id,
            default_entry_ids=[(6, 0, [entry_id])]).create({})
        self.assertFalse(wizard.target_reference)
