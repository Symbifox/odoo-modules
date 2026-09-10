# Pourboire (`bf_expense_tip`)

A restaurant receipt carries two amounts of different natures. The meal is a
taxable supply: GST and QST are computed on it and printed on the slip. The
tip is given freely and carries no tax at all — which is precisely why it sits
*below* the total, not inside it.

Odoo knows only one amount per expense, and treats it as tax-inclusive. Leave
the tip in there and it joins the tax base.

## The number

A CA$26.30 receipt with a CA$3.10 tip, under the grouped
`14.975% GST+QST` purchase tax:

| | Base | Tax |
|---|---|---|
| One expense of 26.30 | 22.88 | **3.42** |
| What the restaurant actually collected | 20.18 | **3.02** |

Forty cents of input tax credits claimed in excess, on one meal. Not a
rounding artefact — a wrong return. (Odoo rounds each child tax separately,
so the naive `26.30 − 26.30/1.14975 = 3.43` is not what it computes.)

## What Odoo 18 already gives you

The **Split Expense** button (`hr.expense.split.wizard`). It produces the
correct figures, and it costs nothing. But it *copies* the expense into as
many records as lines, it vanishes the moment the expense is attached to a
report (`invisible="sheet_id or product_has_cost"`), and it asks you to retype
the category, the taxes and the description of every piece. For the business
meal — the most common and most repetitive case — that is half a dozen
gestures per receipt.

## What this module does

One **Tip** field on the expense, next to the total.

`total_amount_currency` keeps its original meaning: what the person actually
paid, so the reimbursement does not move. Only the tax base changes, to
`total − tip`. `untaxed_amount_currency` stays "total excluding tax" and
therefore holds the meal net *plus* the tip — the identity
`untaxed + tax == total`, which `hr.expense.sheet._compute_amount` already
assumes, still holds, and the tip lands on the side that carries no tax.

At posting time the entry carries **two expense lines** instead of one: the
meal with its taxes, the tip with none. Both payment modes are covered — the
vendor bill when the employee fronts the money, the payment entry when the
expense sits on a company card.

The tip goes to the same account as the meal, because it *is* part of
entertainment expenses. Point it elsewhere from Accounting settings if you
want it isolated for analysis.

## Where the tip is removed from the base, and where it must not be

`hr.expense._prepare_base_line_for_taxes_computation` is called from five
places in core, and they do not all pass the same thing —
`_compute_total_amount` uses it to *recompute the total from itself*, where
subtracting the tip would corrupt the total. The subtraction is therefore
gated behind the `bf_tip_hors_assiette` context flag, set only by the callers
that want it.

`_prepare_payments_vals` needs one extra step: core rewrites the base line's
`balance` as `total_amount - total_tax_line_balance` after the fact, from the
*full* total, so the tip is subtracted there too. `amount_currency` already
comes out right — the tax machinery worked on `total − tip` thanks to the
context — and correcting it a second time would remove the tip twice.

## What it does not do

* **The 50% restriction.** Revenu Québec caps ITCs and QST input tax refunds
  on meal and entertainment expenses at half, either through a year-end
  adjustment or by claiming only half each period. This module fixes the
  base; it does not pick the claiming method, which belongs to the company's
  tax file.
* **Reading the receipt.** The field is shaped to be filled by automatic
  extraction, but this module reads no image.
* **Fixed-cost categories.** A category carrying a standard price (mileage)
  computes its own total; the field is hidden there, as Odoo's own split
  button already is.

## Upgrade checkpoint

`_prepare_payments_vals` reaches into the line commands core produced. If a
future Odoo changes how that method assembles its lines, `test_ecriture`
fails loudly rather than quietly posting a wrong entry. Run it before shipping
a version bump.

## Tests

31 tests, `--test-tags '/bf_expense_tip'`. Four of them exist only to prove
the module is invisible when the tip is zero: the amounts, the entry and both
payment modes must match core exactly.
