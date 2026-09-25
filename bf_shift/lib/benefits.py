"""Taxable-benefit classification of shift-related events. Pure Python.

The module only *captures* the events and says, for each jurisdiction,
whether the event looks taxable, not taxable, or needs a tax specialist.
The payroll service computes the deductions and fills the RL-1 and T4 boxes.

Sources (checked 2026-09-24):
- Revenu Québec, employee who works overtime: meal or allowance not
  taxable when the overtime is requested by the employer, lasts at least
  2 hours, happens fewer than 3 times a week, and is paid on receipts,
  for a reasonable amount.
- CRA, overtime meals and allowances: not taxable up to $23, at least
  2 hours of overtime, fewer than 3 times a week; no receipts required.
- Revenu Québec: taxi home late at night is not taxable when the meal
  conditions are met, on receipts, and there is no public transit or the
  employee's safety is at risk. CRA: not confirmed, so "check".
- Parking: taxable in both, apart from the exceptions (value cannot be
  determined, or unassigned spaces, at most 2 for 3 employees).
- Distinctive uniform and protective clothing: not taxable.
- Subsidised meals: not taxable at the CRA at a reasonable price; the
  Revenu Québec treatment is to be confirmed, so "check".
"""

from dataclasses import dataclass, field

TAXABLE = "taxable"
NOT_TAXABLE = "not_taxable"
CHECK = "check"


@dataclass
class Verdict:
    quebec: str
    federal: str
    reasons: list = field(default_factory=list)


def classify(kind, amount=0.0, overtime_hours=0.0, employer_requested=False,
             times_this_week=1, has_receipt=False, qc_reasonable=0.0,
             cra_meal_limit=23.0, no_transit_or_safety=False,
             parking_exception=False):
    """Classify one event. ``times_this_week`` counts this event too."""
    reasons = []
    if kind in ("overtime_meal", "taxi"):
        if not employer_requested:
            reasons.append("not_requested")
        if overtime_hours < 2.0 - 1e-9:
            reasons.append("under_two_hours")
        if times_this_week >= 3:
            reasons.append("three_times")

    if kind == "overtime_meal":
        qc_ok = not reasons and has_receipt and (not qc_reasonable or amount <= qc_reasonable)
        if not has_receipt:
            reasons.append("no_receipt")
        if qc_reasonable and amount > qc_reasonable:
            reasons.append("qc_amount")
        fed_ok = (overtime_hours >= 2.0 - 1e-9 and times_this_week < 3
                  and amount <= cra_meal_limit)
        if amount > cra_meal_limit:
            reasons.append("cra_amount")
        return Verdict(NOT_TAXABLE if qc_ok else TAXABLE,
                       NOT_TAXABLE if fed_ok else TAXABLE, reasons)

    if kind == "taxi":
        if not has_receipt:
            reasons.append("no_receipt")
        if not no_transit_or_safety:
            reasons.append("transit_available")
        return Verdict(NOT_TAXABLE if not reasons else TAXABLE, CHECK, reasons)

    if kind == "parking":
        if parking_exception:
            return Verdict(NOT_TAXABLE, NOT_TAXABLE, ["parking_exception"])
        return Verdict(TAXABLE, TAXABLE, [])

    if kind == "uniform":
        return Verdict(NOT_TAXABLE, NOT_TAXABLE, [])

    if kind == "subsidized_meal":
        return Verdict(CHECK, NOT_TAXABLE, [])

    return Verdict(CHECK, CHECK, [])
