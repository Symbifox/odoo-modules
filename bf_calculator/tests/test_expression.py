from decimal import Decimal

from odoo.tests import BaseCase, tagged

from ..lib.expression import CalcError, evaluate, parse_column, parse_number

NBSP = "\u00a0"


@tagged("post_install", "-at_install")
class TestExpression(BaseCase):

    def value(self, text, sep=NBSP):
        return evaluate(text, sep).value

    def error(self, text, sep=NBSP):
        with self.assertRaises(CalcError) as cm:
            evaluate(text, sep)
        return cm.exception.code

    def test_labels_in_parentheses(self):
        res = evaluate("6 (semaines) * 750 (dollars) =", NBSP)
        self.assertEqual(res.value, 4500)
        self.assertEqual(res.display(), "6 semaines × 750 dollars")

    def test_bare_labels_currency_and_title(self):
        res = evaluate("Budget stagiaire : 6 semaines * 750 $", NBSP)
        self.assertEqual(res.value, 4500)
        self.assertEqual(res.title, "Budget stagiaire")
        self.assertEqual(res.currency, "$")
        self.assertEqual(res.display(), "6 semaines × 750 $")

    def test_grouping_parenthesis_is_not_a_label(self):
        self.assertEqual(self.value("(6 + 2) (semaines) * 750"), 6000)
        self.assertEqual(self.value("(6 semaines + 2 semaines) * 750"), 6000)

    def test_french_numbers(self):
        self.assertEqual(self.value("12,5*2"), 25)
        self.assertEqual(self.value("1 250,50 + 0,50"), Decimal("1251"))
        self.assertEqual(self.value(f"1{NBSP}250,50 * 2"), Decimal("2501"))
        self.assertEqual(self.value("12.5 * 2"), 25)
        self.assertEqual(self.value("0,1 + 0,2"), Decimal("0.3"))

    def test_english_numbers(self):
        self.assertEqual(self.value("1,250 * 2", ","), 2500)
        self.assertEqual(self.value("1,250.50 * 2", ","), Decimal("2501"))
        self.assertEqual(self.value("12,5 * 2", ","), 25)
        self.assertEqual(parse_number("1.250.000", ","), 1250000)

    def test_precedence_and_power(self):
        self.assertEqual(self.value("2 + 3 * 4"), 14)
        self.assertEqual(self.value("(2 + 3) * 4"), 20)
        self.assertEqual(self.value("-2^2"), -4)
        self.assertEqual(self.value("2^-1"), Decimal("0.5"))
        self.assertEqual(self.value("2 ** 3 ** 2"), 512)
        self.assertEqual(self.value("6 x 750"), 4500)
        self.assertEqual(self.value("=12*3.5"), 42)

    def test_percent_like_a_desk_calculator(self):
        self.assertEqual(self.value("200 + 10 %"), 220)
        self.assertEqual(self.value("200 - 10 %"), 180)
        self.assertEqual(self.value("200 * 10 %"), 20)
        self.assertEqual(self.value("10 %"), Decimal("0.1"))

    def test_durations(self):
        res = evaluate("1 h 45 + 2 h 30", NBSP)
        self.assertEqual(res.value, Decimal("4.25"))
        self.assertTrue(res.duration)
        self.assertEqual(self.value("1:45 + 30 min"), Decimal("2.25"))
        self.assertEqual(self.value("2 heures * 3"), 6)
        # Une durée multipliée par un taux horaire donne un montant, pas une durée.
        res = evaluate("1h45 * 120 $", NBSP)
        self.assertEqual(res.value, 210)
        self.assertFalse(res.duration)
        self.assertEqual(res.currency, "$")

    def test_hour_unit_needs_word_boundary(self):
        # « hommes » n'est pas une durée.
        res = evaluate("10 hommes * 3", NBSP)
        self.assertEqual(res.value, 30)
        self.assertFalse(res.duration)

    def test_two_currencies_give_none(self):
        self.assertEqual(evaluate("10 $ + 5 €", NBSP).currency, "")

    def test_errors(self):
        self.assertEqual(self.error(""), "empty")
        self.assertEqual(self.error("1 +"), "missing_operand")
        self.assertEqual(self.error("2 (3)"), "missing_operator")
        self.assertEqual(self.error("((1)"), "unbalanced")
        self.assertEqual(self.error("1)"), "unbalanced")
        self.assertEqual(self.error("5 / 0"), "division_by_zero")
        self.assertEqual(self.error("1 = 2"), "unexpected")
        self.assertEqual(self.error("1 # 2"), "unexpected")
        self.assertEqual(self.error("1,2,3"), "bad_number")

    def test_guards(self):
        self.assertEqual(self.error("9^9^9"), "too_large")
        self.assertEqual(self.error("10^31"), "too_large")
        self.assertEqual(self.error("(" * 41 + "1" + ")" * 41), "too_deep")
        self.assertEqual(self.error("1+" * 300 + "1"), "too_long")
        self.assertEqual(self.error("(-8)^0,5"), "invalid_power")

    def test_column(self):
        values, skipped = parse_column(
            "Montant\n1 250,00 $\n(125,00)\n-5\nTotal\n12,5\t3", NBSP)
        self.assertEqual(values, [Decimal("1250"), Decimal("-125"), Decimal("-5"),
                                  Decimal("12.5"), Decimal("3")])
        self.assertEqual(skipped, 2)
