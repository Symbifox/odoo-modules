"""Lot 2 (2026-09-22) : valeur résiduelle, variables, mémoire, épingles,
arrondi, devises (Banque du Canada), dates et fériés du Québec."""

import json
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import BaseCase, TransactionCase, new_test_user, tagged

from ..lib import dates as caldates
from ..lib.expression import CalcError, evaluate

NBSP = "\u00a0"
RATES = {"USD": Decimal("1.4064"), "EUR": Decimal("1.6096"), "CAD": Decimal(1)}


def fake_rate(src, dst):
    return RATES[src] / RATES[dst], "2026-09-22"


@tagged("post_install", "-at_install")
class TestExpressionLot2(BaseCase):

    def ev(self, text, **kw):
        kw.setdefault("rate", fake_rate)
        kw.setdefault("currencies", set(RATES))
        return evaluate(text, NBSP, **kw)

    def test_conversion(self):
        res = self.ev("100 USD en CAD")
        self.assertEqual(res.value, Decimal("140.64"))
        self.assertEqual(res.currency, "$")
        self.assertEqual(res.conversions[0][:2], ("USD", "CAD"))
        # Un nombre nu n'est pas converti.
        self.assertEqual(self.ev("3 * 100 USD en CAD").value, Decimal("421.92"))
        self.assertEqual(self.ev("140,64 $ en USD").value.quantize(Decimal("0.01")), 100)
        self.assertEqual(self.ev("50 € + 20 US$ en $").value, Decimal("108.608"))

    def test_conversion_refused(self):
        with self.assertRaises(CalcError) as cm:
            self.ev("100 USD en XYZ")
        self.assertEqual(cm.exception.code, "no_rate")
        with self.assertRaises(CalcError) as cm:
            evaluate("100 USD en CAD", NBSP, rate=None, currencies=set(RATES))
        self.assertEqual(cm.exception.code, "no_rate")

    def test_currency_code_alone(self):
        res = self.ev("100 USD")
        self.assertEqual((res.value, res.currency), (100, "USD"))

    def test_variables(self):
        variables = {"taux": 125, "semaines": 4}
        self.assertEqual(self.ev("6 h * taux", variables=variables).value, 750)
        self.assertFalse(self.ev("6 h * taux", variables=variables).duration)
        # En position de descriptif, le mot reste un descriptif.
        self.assertEqual(self.ev("6 semaines * 750", variables=variables).value, 4500)
        res = self.ev("taux = 100 + 25")
        self.assertEqual((res.assign, res.value), ("taux", 125))

    def test_variable_errors(self):
        for text, code in (("x = 3", "bad_variable"), ("USD = 3", "bad_variable"),
                           ("6 h * taux", "unknown_variable")):
            with self.assertRaises(CalcError) as cm:
                self.ev(text)
            self.assertEqual(cm.exception.code, code, text)

    def test_quebec_holidays(self):
        h = caldates.quebec_holidays(2026)
        self.assertEqual(len(h), 8)
        self.assertIn(date(2026, 4, 3), h)       # Vendredi saint
        self.assertIn(date(2026, 5, 18), h)      # lundi avant le 25 mai
        self.assertIn(date(2026, 9, 7), h)       # 1er lundi de septembre
        self.assertIn(date(2026, 10, 12), h)     # 2e lundi d'octobre
        self.assertNotIn(date(2026, 12, 26), h)  # pas un férié CNESST
        h29 = caldates.quebec_holidays(2029)
        self.assertIn(date(2029, 6, 25), h29)    # 24 juin un dimanche
        self.assertIn(date(2029, 7, 2), h29)     # 1er juillet un dimanche
        self.assertIn(date(2027, 3, 29), caldates.quebec_holidays(2027, "monday"))
        self.assertNotIn(date(2027, 3, 26), caldates.quebec_holidays(2027, "monday"))

    def test_business_days(self):
        n, skipped = caldates.business_days_between(date(2026, 9, 22), date(2026, 10, 22))
        self.assertEqual(n, 21)
        self.assertEqual([d for d, _n in skipped], [date(2026, 10, 12)])
        end, skipped = caldates.add_business_days(date(2026, 12, 23), 3)
        self.assertEqual(end, date(2026, 12, 29))
        self.assertEqual(caldates.add_business_days(date(2026, 9, 22), -10)[0], date(2026, 9, 8))
        with self.assertRaises(ValueError):
            caldates.add_business_days(date(2026, 1, 1), 5000)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


VALET = {"observations": [
    {"d": "2026-09-21", "FXUSDCAD": {"v": "1.4000"}, "FXEURCAD": {"v": "1.6000"}},
    {"d": "2026-09-22", "FXUSDCAD": {"v": "1.4064"}, "FXEURCAD": {"v": ""}},
]}


@tagged("post_install", "-at_install")
class TestCalculatorLot2(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.alice = new_test_user(cls.env, "lot2_alice", groups="base.group_user", lang="fr_CA")
        cls.bob = new_test_user(cls.env, "lot2_bob", groups="base.group_user", lang="fr_CA")
        cls.env["ir.config_parameter"].sudo().set_param("bf_calculator.boc_rates", "")

    def setUp(self):
        super().setUp()
        # Le répit après une panne est gardé par processus : il passerait d'un essai à l'autre.
        from ..models import bf_calculator_rates
        bf_calculator_rates._last_failure.clear()

    def A(self):
        return self.env["bf.calculator.entry"].with_user(self.alice).with_context(lang="fr_CA")

    def B(self):
        return self.env["bf.calculator.entry"].with_user(self.bob).with_context(lang="fr_CA")

    def test_residual_value(self):
        self.assertEqual(self.A().calc_record("6 (semaines) * 750 $")["residual"], f"4{NBSP}500 $")
        self.assertEqual(self.A().calc_record("1 h 45 + 2 h 30")["residual"], "4,25 h")
        self.assertEqual(self.A().calc_record("12,5 * 2")["residual"], "25")
        # La valeur résiduelle se relit : on continue le calcul dessus.
        self.assertEqual(self.A().calc_evaluate(f"4{NBSP}500 $ * 2")["result_text"],
                         f"9{NBSP}000,00{NBSP}$")
        self.assertEqual(self.A().calc_evaluate(f"1{NBSP}234{NBSP}567,5 * 2")["raw"], "2469135")
        self.assertEqual(self.A().calc_evaluate("4,25 h + 1 h")["residual"], "5,25 h")

    def test_variables_are_private(self):
        res = self.A().calc_record("taux = 125")
        self.assertEqual([v["name"] for v in res["variables"]], ["taux"])
        self.assertEqual(self.A().calc_evaluate("6 h * taux")["result_text"], "750")
        self.assertFalse(self.B().calc_evaluate("6 h * taux")["ok"])
        self.assertFalse(self.B().calc_variables())
        Var = self.env["bf.calculator.variable"]
        mine = Var.search([("name", "=", "taux"), ("user_id", "=", self.alice.id)])
        with self.assertRaises(AccessError):
            Var.with_user(self.bob).browse(mine.id).read(["value"])
        self.A().calc_record("taux = 150")
        self.assertEqual(len(Var.search([("name", "=", "taux"), ("user_id", "=", self.alice.id)])), 1)
        self.assertEqual(self.A().calc_delete_variable("taux"), [])

    def test_memory(self):
        self.A().calc_memory("add", "100")
        self.A().calc_memory("add", "50.5")
        res = self.A().calc_memory("sub", "0.5")
        self.assertEqual([(v["name"], v["value"]) for v in res["variables"]], [("M", 150.0)])
        self.assertEqual(self.A().calc_evaluate("M * 2")["result_text"], "300")
        self.assertEqual(self.A().calc_memory("clear")["variables"], [])
        self.assertFalse(self.A().calc_memory("add", "pas un nombre")["ok"])

    def test_rounding(self):
        res = self.A().calc_evaluate("19,98 $ * 1", rounding="0.05")
        self.assertEqual(res["result_text"], f"20,00{NBSP}$")
        self.assertIn("0,05", res["note"])
        self.assertEqual(self.A().calc_evaluate("19,97 * 1", rounding="0.05")["raw"], "19.95")
        self.assertEqual(self.A().calc_evaluate("2,5", rounding="1")["raw"], "3")
        self.assertEqual(self.A().calc_evaluate("2,5", rounding="bidon")["raw"], "2.5")
        entry = self.A().calc_record("19,98 $", rounding="0.05")["entry"]
        self.assertIn("0,05", entry["note"])

    def test_pins(self):
        first = self.A().calc_record("1 + 1")["entry"]["id"]
        self.A().calc_record("2 + 2")
        self.A().browse(first).calc_toggle_pin()
        history = self.A().calc_history()
        self.assertEqual(history[0]["id"], first)
        self.assertTrue(history[0]["pinned"])
        self.A().calc_clear_history()
        self.assertEqual([e["id"] for e in self.A().calc_history()], [first])
        self.env.cr.execute("UPDATE bf_calculator_entry SET create_date = %s WHERE id = %s",
                            (fields.Datetime.now() - timedelta(days=500), first))
        self.env["bf.calculator.entry"].invalidate_model(["create_date"])
        self.env["bf.calculator.entry"]._cron_purge_history()
        self.assertTrue(self.env["bf.calculator.entry"].browse(first).exists())

    def test_pin_someone_elses_entry_does_nothing(self):
        mine = self.A().calc_record("1 + 1")["entry"]["id"]
        try:
            self.B().browse(mine).calc_toggle_pin()
        except AccessError:
            pass
        self.assertFalse(self.env["bf.calculator.entry"].browse(mine).pinned)

    def test_rates_latest_per_series_and_cache(self):
        get = "odoo.addons.bf_calculator.models.bf_calculator_rates.requests.get"
        with patch(get, return_value=FakeResponse(VALET)) as mocked:
            res = self.A().calc_record("100 USD en CAD")
            self.assertEqual(mocked.call_count, 1)
            self.A().calc_evaluate("100 EUR en CAD")
            self.assertEqual(mocked.call_count, 1)  # le relevé est gardé
        self.assertEqual(res["entry"]["result_text"], f"140,64{NBSP}$")
        self.assertIn("1 USD = 1,4064 CAD", res["entry"]["note"])
        self.assertIn("2026-09-22", res["entry"]["note"])
        # EUR vide le 22 : la valeur du 21 sert, avec SA date.
        eur = self.A().calc_evaluate("100 EUR en CAD")
        self.assertEqual(eur["result_text"], f"160,00{NBSP}$")
        self.assertIn("2026-09-21", eur["note"])
        entry = self.env["bf.calculator.entry"].browse(res["entry"]["id"])
        self.assertIn("Banque du Canada", entry.with_context(lang="fr_CA")._chatter_line())

    def test_rates_outage_uses_last_reading(self):
        Param = self.env["ir.config_parameter"].sudo()
        stale = fields.Datetime.to_string(fields.Datetime.now() - timedelta(days=2))
        Param.set_param("bf_calculator.boc_rates", json.dumps(
            {"date": "2026-09-20", "rates": {"USD": "1.40"}, "dates": {"USD": "2026-09-20"},
             "fetched": stale}))
        get = "odoo.addons.bf_calculator.models.bf_calculator_rates.requests.get"
        with patch(get, side_effect=OSError("réseau")):
            res = self.A().calc_evaluate("100 USD en CAD")
        self.assertEqual(res["result_text"], f"140,00{NBSP}$")
        self.assertIn("2026-09-20", res["note"])

    def test_no_rate_ever(self):
        get = "odoo.addons.bf_calculator.models.bf_calculator_rates.requests.get"
        with patch(get, side_effect=OSError("réseau")):
            res = self.A().calc_evaluate("100 USD en CAD")
        self.assertFalse(res["ok"])
        # Le message doit dire qu'il manque un TAUX, pas « écrivez en majuscules »
        # (l'exemple « 100 USD » de ce message-là contient aussi « USD »).
        self.assertIn("taux", res["error"])
        self.assertIn("USD", res["error"])

    def test_no_rate_lookup_without_currency(self):
        get = "odoo.addons.bf_calculator.models.bf_calculator_rates.requests.get"
        with patch(get) as mocked:
            self.A().calc_evaluate("6 (semaines) * 750 $")
        mocked.assert_not_called()

    def test_dates(self):
        res = self.A().calc_dates("between", "2026-09-22", "2026-10-22")
        self.assertEqual([r["value"] for r in res["rows"]], ["30", "21"])
        self.assertEqual(len(res["holidays"]), 1)
        self.assertIn("Action de grâce", res["holidays"][0])
        res = self.A().calc_dates("add", "2026-12-23", n=3, business=True, record=True)
        self.assertIn("29 décembre 2026", res["result_text"])
        self.assertEqual(self.env["bf.calculator.entry"].browse(res["entry"]["id"]).mode, "date")
        self.assertIn("22 octobre 2026", self.A().calc_dates("add", "2026-09-22", n=30)["result_text"])
        self.assertFalse(self.A().calc_dates("add", "", n=3)["ok"])
        self.assertFalse(self.A().calc_dates("add", "2026-01-01", n=5000, business=True)["ok"])

    def test_easter_parameter(self):
        self.env["ir.config_parameter"].sudo().set_param("bf_calculator.easter_holiday", "monday")
        res = self.A().calc_dates("between", "2027-03-25", "2027-03-30")
        self.assertIn("Lundi de Pâques", " ".join(res["holidays"]))
