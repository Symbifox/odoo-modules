"""The arithmetic of the gradebook, with no database in it.

Kept apart on purpose: every figure on a report card goes through these few functions, so
they are small enough to read against the Education Regulation (I-13.3, r. 8) and easy to
test one by one.

What the regulation fixes, and where it is applied:

- results are percentages (s. 30.2);
- a competency or component result is a judgment on the evaluations of its stage; the
  discipline result is computed "from the weighting of the competencies set in the
  evaluation framework" (s. 30.2, third paragraph);
- the final result of a competency or component weighs 20 % for the first stage, 20 % for
  the second and 60 % for the third (s. 30.2, second paragraph). The weights are data
  (`bf.school.term.weight`), never constants here;
- the result on a ministerial exam is worth 20 % of the student's final result (s. 30.3).
  The share is data too (`bf.school.course.exam_weight`);
- the success threshold is 60 % for every subject (s. 28.1).

Choices the regulation leaves to the school, all documented in the README:

- every level is rounded half up to a whole percentage before it feeds the next one, so
  that a parent can recompute the report card from the figures printed on it;
- a stage or a component with no evaluation does not count and the others are
  re-weighted (the regulation asks for detailed results only for what was evaluated, s. 30.1).
"""
from fractions import Fraction

#: Success threshold of every subject (Education Regulation s. 28.1). School-wide data in the
#: models; this is only the fallback of the pure functions.
SUCCESS_THRESHOLD = 60


def _exact(value):
    """A number as an exact fraction: no binary floating point between a grade and a report card."""
    return value if isinstance(value, Fraction) else Fraction(str(value))


def round_percent(value):
    """Whole percentage, half up: 74.5 gives 75, never banker's rounding. Exact for a Fraction."""
    if value is None:
        return None
    return int((_exact(value) + Fraction(1, 2)) // 1)


def weighted_mean(pairs):
    """Weighted mean of (value, weight) pairs, exact; None when nothing counts.

    A pair counts only with a value and a positive weight: an evaluation not done, or a
    component with no weight yet, takes no part and shifts nothing.
    """
    total = Fraction(0)
    weights = Fraction(0)
    for value, weight in pairs:
        if value is None or weight is None or _exact(weight) <= 0:
            continue
        total += _exact(value) * _exact(weight)
        weights += _exact(weight)
    if not weights:
        return None
    return total / weights


def score_percent(score, max_score):
    """One evaluation as a percentage of its maximum (exact)."""
    if not max_score or _exact(max_score) <= 0:
        return None
    return _exact(score) * 100 / _exact(max_score)


def component_result(evaluations):
    """Result of a competency or component for a stage.

    `evaluations`: (score, max_score, weight) of the evaluations that were graded.
    """
    return round_percent(weighted_mean(
        (score_percent(score, max_score), weight) for score, max_score, weight in evaluations))


def discipline_result(components):
    """Discipline result from the rounded results of its components.

    `components`: (result, weight) with the weight set by the evaluation framework. A
    component with no result is left out and the others are re-weighted.
    """
    return round_percent(weighted_mean(components))


def final_result(stages):
    """Final result of a competency, a component or a subject from its stage results.

    `stages`: (result, weight) for stages 1 to 3 (20/20/60 under the regulation).
    """
    return round_percent(weighted_mean(stages))


def with_exam(school_result, exam_result, exam_weight):
    """Final discipline result once a ministerial exam counts (s. 30.3).

    Without an exam result the school's own result stands. Without a school result nothing
    can be computed: an exam alone is not a final result.
    """
    if school_result is None:
        return None
    if exam_result is None:
        return school_result
    share = _exact(exam_weight) / 100
    return round_percent(_exact(school_result) * (1 - share) + _exact(exam_result) * share)


def group_average(results):
    """Average of the group, as printed on the report card (whole percentage)."""
    return round_percent(weighted_mean((r, 1) for r in results))


def passed(result, threshold=SUCCESS_THRESHOLD):
    """True when the result reaches the threshold; None when there is no result."""
    if result is None:
        return None
    return result >= threshold


def fifth_rank(position, count):
    """Rank in fifths (1 = top fifth) of the student at `position` (0 = best) in a group of `count`.

    The Ministry asks for it with the results of a course (`RANG_CINQM`). The group is cut
    into five parts of nearly equal size; the extra students go to the first parts.
    """
    if count <= 0 or position < 0 or position >= count:
        return None
    base, extra = divmod(count, 5)
    start = 0
    for rank in range(1, 6):
        size = base + (1 if rank <= extra else 0)
        if position < start + size:
            return rank
        start += size
    return 5


def fifth_ranks(results):
    """Rank in fifths of each student: `results` maps a student to a result (None = no result).

    Students with the same result share the rank of the best-placed of them, so a tie never
    puts one of two equal students in a lower fifth than the other. A student with no result
    has no rank.
    """
    ranked = sorted((r, k) for k, r in results.items() if r is not None)
    ranked.sort(key=lambda item: -item[0])
    count = len(ranked)
    ranks = {}
    first_position = {}
    for position, (result, student) in enumerate(ranked):
        first_position.setdefault(result, position)
        ranks[student] = fifth_rank(first_position[result], count)
    return ranks
