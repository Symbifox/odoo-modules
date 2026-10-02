"""Taxable-benefit classification of shift-related events. Pure Python.

The module only *captures* the events and says, for each jurisdiction,
whether the event looks taxable, not taxable, or needs a tax specialist.
The payroll service computes the deductions and fills the RL-1 and T4 boxes.

Sources (checked September 2026, legal and tax review):
- Québec, overtime meal or transport home (LI art. 37.0.3): not taxable
  when the overtime is requested by the employer, is planned to last at
  least 2 consecutive hours, happens fewer than 3 times a week, and the
  value is reasonable. A meal or a taxi *provided* by the employer needs no
  receipt; a *reimbursed* one is paid on receipts. The taxi also needs no
  public transit, or the employee's safety at risk.
- CRA, overtime meals and allowances: not taxable up to $23, at least
  2 hours of overtime, fewer than 3 times a week; no receipts required.
- CRA, taxi home: travel between home and a regular place of work is
  personal; none of the CRA's published exceptions covers a taxi after
  overtime. Taxable by default; the employer may record a justified
  exemption.
- Parking: taxable in both, apart from the exceptions (value cannot be
  determined, or unassigned spaces, at most 2 for 3 employees).
- Distinctive uniform and protective clothing: not taxable.
- Subsidised meals, Québec (letter 15-028003-001) and CRA alike: not
  taxable when the employee pays a reasonable charge, the cost of the food
  and its preparation; otherwise the benefit is that cost less the price
  paid. When a third party runs the service, the cost is the full price of
  the meal, so the benefit is the part the employer pays.
"""

from dataclasses import dataclass, field

TAXABLE = "taxable"
NOT_TAXABLE = "not_taxable"
CHECK = "check"

PROVIDED = "provided"        # the employer provides the meal or the taxi
REIMBURSED = "reimbursed"    # the employee pays, the employer reimburses


@dataclass
class Verdict:
    quebec: str
    federal: str
    reasons: list = field(default_factory=list)


def subsidized_value(cost, price_paid):
    """Value of a subsidised meal: cost of the food and its preparation less
    the price the employee paid, never below 0."""
    return max(0.0, round((cost or 0.0) - (price_paid or 0.0), 2))


def classify(kind, amount=0.0, overtime_hours=0.0, employer_requested=False,
             times_this_week=1, has_receipt=False, qc_reasonable=0.0,
             cra_meal_limit=23.0, no_transit_or_safety=False,
             parking_exception=False, provision=REIMBURSED,
             meal_cost=0.0, meal_price_paid=0.0, federal_exemption=False):
    """Classify one event.

    ``times_this_week`` counts this event too. ``overtime_hours`` is the
    planned length of the consecutive overtime (LI art. 37.0.3 reads the
    overtime as planned, not as finally worked). ``provision`` says whether
    the meal or taxi is provided by the employer or reimbursed on receipts.
    ``federal_exemption``: the employer recorded why a taxi is not taxable
    at the CRA.
    """
    reasons = []
    if kind in ("overtime_meal", "taxi"):
        if not employer_requested:
            reasons.append("not_requested")
        if overtime_hours < 2.0 - 1e-9:
            reasons.append("under_two_hours")
        if times_this_week >= 3:
            reasons.append("three_times")
        # Receipts are asked only of a reimbursement, not of what the
        # employer provides.
        if provision != PROVIDED and not has_receipt:
            reasons.append("no_receipt")

    if kind == "overtime_meal":
        if qc_reasonable and amount > qc_reasonable:
            reasons.append("qc_amount")
        qc_ok = not reasons
        fed_ok = (overtime_hours >= 2.0 - 1e-9 and times_this_week < 3
                  and amount <= cra_meal_limit)
        if amount > cra_meal_limit:
            reasons.append("cra_amount")
        return Verdict(NOT_TAXABLE if qc_ok else TAXABLE,
                       NOT_TAXABLE if fed_ok else TAXABLE, reasons)

    if kind == "taxi":
        if not no_transit_or_safety:
            reasons.append("transit_available")
        quebec = NOT_TAXABLE if not reasons else TAXABLE
        if federal_exemption:
            reasons.append("federal_exemption")
            return Verdict(quebec, NOT_TAXABLE, reasons)
        reasons.append("cra_taxi")
        return Verdict(quebec, TAXABLE, reasons)

    if kind == "parking":
        if parking_exception:
            return Verdict(NOT_TAXABLE, NOT_TAXABLE, ["parking_exception"])
        return Verdict(TAXABLE, TAXABLE, [])

    if kind == "uniform":
        return Verdict(NOT_TAXABLE, NOT_TAXABLE, [])

    if kind == "subsidized_meal":
        if not meal_cost:
            return Verdict(CHECK, CHECK, ["meal_cost_missing"])
        if subsidized_value(meal_cost, meal_price_paid) <= 0.0:
            return Verdict(NOT_TAXABLE, NOT_TAXABLE, ["price_covers_cost"])
        return Verdict(TAXABLE, TAXABLE, ["price_below_cost"])

    return Verdict(CHECK, CHECK, [])
