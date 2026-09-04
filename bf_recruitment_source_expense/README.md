# Recruitment: what each job board costs (`bf_recruitment_source_expense`)

`auto_install` bridge between `bf_recruitment_expense` and
`bf_recruitment_source`. It is the third side of the triangle: one module knows
what a **job** cost, another knows what each **board** returned, and neither knew
what a board **cost**, because spend stopped at the job.

## What it adds

- `hr.expense.recruitment_source_id`: you do not pay for "a job", you pay a board
  for a posting on a job. Choosing the board fills in the job.
- `hr.recruitment.source.expense_total`, `cost_per_applicant`,
  `cost_per_hire_from_source`.
- `hr.job.attributed_expense_total` and `unattributed_expense_total`.

## The property that makes the bridge

🔴 **A cost per application that counts the whole job lies upward; one that
counts only what is attributed to it lies downward if it stays silent.** The
module takes the second path and does not stay silent: the amount no board
carries comes out in the job's warning, in money, next to the number of
applications nobody explains.

The two gaps are the same fact seen from both ends: what the boards fail to
explain about the intake, and what they fail to explain about the spend.

⚠️ The bridge **extends** the warning list of `bf_recruitment_source`, it does not
replace it. Two tests prove it, and a mutation removing the `super()` call brings
six of them down.

## The guard an `onchange` cannot provide

⚠️ An `onchange` only runs inside a form. An expense created by import, over RPC
or by another module does not trigger it. Without the constraint, an outlay could
be attributed to the **board of one job** and the **job of another**, and both
totals would lie in opposite directions.

## Traps paid for while writing this

🔴 **The framework's translation function takes `source` as its first
parameter.** A placeholder named `%(source)s` with a `source=` keyword raises
`get_text_alias() got multiple values for argument 'source'`, and it raises **far
from the offending line**, when validating a write rather than at import. The
placeholder is called `site`.

⚠️ **`formatLang`, not the monetary QWeb rendering**, to write an amount into a
text field: `ir.qweb.field.monetary.value_to_html` returns HTML with entities,
which would show up as literal characters in the warning.

## What it does not count

⚠️ **Panel time** does not enter a board's cost. It belongs to the job: a job
board does not run interviews. The JOB's cost per hire still counts it — that is
`bf_recruitment_expense`'s business.

⚠️ A **refused** expense is not an outlay, here as there.
