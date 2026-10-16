from odoo.tests import BaseCase, tagged

from odoo.addons.bf_school_gradebook.models import engine


@tagged("post_install", "-at_install")
class TestEngine(BaseCase):
    """The arithmetic alone, against hand-computed figures."""

    def test_rounding_is_half_up_not_bankers(self):
        self.assertEqual(engine.round_percent(74.5), 75, "banker's rounding would give 74")
        self.assertEqual(engine.round_percent(76.5), 77, "banker's rounding would give 76")
        self.assertEqual(engine.round_percent(74.49), 74)
        self.assertIsNone(engine.round_percent(None))

    def test_weighted_mean_skips_what_does_not_count(self):
        self.assertEqual(engine.weighted_mean([(80, 1), (60, 3)]), 65.0)
        self.assertEqual(engine.weighted_mean([(80, 1), (None, 5), (60, 3)]), 65.0, "no result: no weight")
        self.assertEqual(engine.weighted_mean([(80, 0), (60, 3)]), 60.0, "a zero weight takes no part")
        self.assertEqual(engine.weighted_mean([(80, -5), (60, 3)]), 60.0, "a negative weight takes no part either")
        self.assertIsNone(engine.weighted_mean([(80, -5)]))
        self.assertIsNone(engine.weighted_mean([(None, 1)]))
        self.assertIsNone(engine.weighted_mean([]))

    def test_component_result_converts_each_evaluation_to_a_percentage(self):
        # 15/20 = 75 % with weight 1, 40/50 = 80 % with weight 3: (75 + 240) / 4 = 78.75 -> 79
        self.assertEqual(engine.component_result([(15, 20, 1), (40, 50, 3)]), 79)
        self.assertIsNone(engine.component_result([]))

    def test_discipline_result_re_weights_the_evaluated_components(self):
        self.assertEqual(engine.discipline_result([(80, 40), (60, 40), (70, 20)]), 70)
        self.assertEqual(engine.discipline_result([(80, 40), (None, 40), (60, 20)]), 73,
                         "a component not evaluated yet leaves the others re-weighted: (3200 + 1200) / 60")
        self.assertIsNone(engine.discipline_result([(80, 0)]), "weights not entered: no result rather than a guess")

    def test_final_result_is_20_20_60(self):
        self.assertEqual(engine.final_result([(70, 20), (80, 20), (90, 60)]), 84)
        self.assertEqual(engine.final_result([(70, 20), (None, 20), (80, 60)]), 78, "(1400 + 4800) / 80 = 77.5")
        self.assertIsNone(engine.final_result([(None, 20), (None, 20), (None, 60)]))

    def test_exam_is_worth_its_share_of_the_final_result(self):
        self.assertEqual(engine.with_exam(70, 50, 20), 66)
        self.assertEqual(engine.with_exam(70, None, 20), 70, "no exam result: the school's result stands")
        self.assertIsNone(engine.with_exam(None, 90, 20), "an exam alone is not a final result")
        self.assertEqual(engine.with_exam(70, 50, 0), 70)

    def test_group_average_and_threshold(self):
        self.assertEqual(engine.group_average([70, 80, 81]), 77)
        self.assertIsNone(engine.group_average([]))
        self.assertTrue(engine.passed(60))
        self.assertFalse(engine.passed(59))
        self.assertIsNone(engine.passed(None))

    def test_fifth_rank_cuts_a_group_into_five_nearly_equal_parts(self):
        self.assertEqual([engine.fifth_rank(p, 12) for p in range(12)], [1, 1, 1, 2, 2, 2, 3, 3, 4, 4, 5, 5])
        self.assertEqual([engine.fifth_rank(p, 5) for p in range(5)], [1, 2, 3, 4, 5])
        self.assertEqual([engine.fifth_rank(p, 3) for p in range(3)], [1, 2, 3])
        self.assertIsNone(engine.fifth_rank(0, 0))

    def test_equal_results_share_the_best_rank(self):
        ranks = engine.fifth_ranks({"a": 90, "b": 90, "c": 80, "d": 70, "e": 60, "f": None})
        self.assertEqual(ranks, {"a": 1, "b": 1, "c": 3, "d": 4, "e": 5})

    def test_arithmetic_is_exact_so_a_half_point_never_falls_the_wrong_way(self):
        # 9/24 (weight 1), 14/18 (weight 3), 4/15 (weight 1): exactly 59.5, which is 60 and a pass
        self.assertEqual(engine.component_result([(9, 24, 1), (14, 18, 3), (4, 15, 1)]), 60)
        # 2/12 (weight 1) and 7/9 (weight 3): exactly 62.5
        self.assertEqual(engine.component_result([(2, 12, 1), (7, 9, 3)]), 63)
        self.assertEqual(engine.round_percent(0.5), 1)
        self.assertEqual(engine.round_percent(2.5), 3)
