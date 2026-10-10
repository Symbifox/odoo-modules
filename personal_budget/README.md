# Personal Budget (`personal_budget`)

A household budget for Odoo 18, independent from Odoo accounting: it only depends on `base` and
`web`. Labels are written in English; French (Canada) comes from `i18n/fr_CA.po`. LGPL-3. No seed
data: each person creates their own categories.

## What it does

- **Transactions** (income and expenses), quick entry, CSV and OFX/QFX import, bulk category
  assignment.
- **Budget plans** per category: a yearly amount (spread over 12 months) or month by month.
- **Recurring expenses and subscriptions**: amount, frequency (weekly to yearly), first payment
  date, optional end, expense category, "my share". See below.
- **Loans** with lines and running balance, **cheques**, **freelance invoices** (40 % tax
  reserve), a **sharing ledger** (who owes what, with a running balance).
- **Dashboard**: actual versus planned per category and month, year to date, comparison with the
  previous year, subscriptions, loans, invoices, recent transactions. A selector picks the budget
  shown.

## Several people in one database

A household instance holds several people. Every record belongs to a **budget**
(`personal.budget.book`):

| Who | Sees and edits the content | Manages the budget (name, sharing, deletion) |
|---|---|---|
| The owner | yes | yes |
| The people the owner shared it with (`member_ids`) | yes | no |
| Anyone else in the database, administrators included | **no** | no |

- Each person gets an unshared personal budget ("My budget") on first use.
- A couple or household budget is created in *Configuration › Budgets and sharing*, then shared
  explicitly. Nothing else is shared.
- Record rules are **global**: they bind administrators too. Code running as superuser
  (`sudo()`, scheduled actions) is not bound by them, and a **system administrator can lift the
  rules themselves** (record rules, server actions, scheduled actions). The isolation holds
  between the people of the household, not against whoever administers the database: on a
  hosted household instance, nobody in the household should be a system administrator.
- Transactions, plans and recurring expenses follow the budget of their category; loan lines
  follow their loan. A record cannot be attached to, or moved into, a budget that is not shared
  with the person doing it: the target is checked before anything is written, and the move is
  checked again afterwards.
- Names read with elevated rights (a many2one on one's own record, an access error in debug mode,
  a constraint message) are computed for the person reading: someone who cannot see a budget gets
  "Private budget" or "Private budget entry", never its name.
- The import and bulk-assignment wizards are private to the person who opened them.
- The dashboard reads the database in SQL: every query filters on a budget whose access has been
  checked.

To see the budget, a person must belong to the *Budget › Budget user* group.

## Recurring expenses

- Due dates are computed from the first one, never step by step: a subscription on the 31st comes
  back on February 28, then on March 31.
- **Forecast**: a category with no plan for the year uses the due dates of its recurring expenses
  as its plan. A plan entered by hand wins.
- **Commitments**: the dashboard shows the monthly equivalent, what is left to pay this year (due
  dates not entered yet, overdue ones included) and the due dates of the next 30 days.
- **Entry**: "Record the payment" creates the transaction of the next due date; "Record due
  payments" catches up on past ones. With "Record automatically" ticked, the daily scheduled action
  does it. Created transactions stay in the budget of their category.

## Import

CSV (transactions, sharing ledger, cheques, loan lines, invoices, budget plans) and OFX/QFX bank
statements. Each line is imported on its own: a line that fails is reported and leaves nothing
behind, the others go through. An OFX transaction already imported (account + FITID) is matched,
not duplicated, and a transaction entered by hand is reconciled with it when the amount agrees and
the dates are at most three days apart.

## Languages

Source labels are in English (fields, selections, views, menus, OWL templates, JS `_t()`, Python
`_()`, constraint messages, import reports, OFX reader messages). French comes from
`i18n/fr_CA.po`.

Names created automatically are **data**, written in the person's language at creation: the
personal budget ("My budget") and the OFX import categories ("To assign", "Other income"). A
person who changes language will see new automatic categories created in the new language.

Known limits: a msgid has a single translation, so "May" reads "mai" everywhere in French; the
dashboard amounts keep the "1 234,56 $" format in every language.

## Changelog

### 18.0.2.1.1 — security

- The import and bulk-assignment wizards get global rules: each person sees only their own.
- A record can no longer be attached to a budget, category, loan, contributor or recurring expense
  the person cannot read, whatever the path (values, context defaults, user defaults, onchange):
  the check runs before anything is written, with the same refusal for a missing target.
- The import refuses a budget or a loan the person cannot read before reading the file, imports
  each line under its own savepoint, and no longer turns an access error into a line error.
- Budgets and their records show a neutral name to anyone who cannot see them.
- Constraint messages no longer name the record they refer to.
- Migration from 18.0.1.x: records without a known author go to the system account's budget,
  invisible to everyone as before, instead of the administrator's.

### 18.0.2.1.0 — English source

- All source strings are in English; `fr_CA.po` carries the French. Import reports and OFX reader
  messages are now translatable.
- The plan's `month_name` is no longer stored: it follows the reader's language.
- Migration `18.0.2.1.0/post-migrate`: rewrites the English source of `noupdate` records (group,
  module category, scheduled action), then reloads the module catalogue for each active language
  other than `en_US`.

### 18.0.2.0.0 — household instance

- New `personal.budget.book` model: ownership and explicit sharing.
- Isolation per budget on the 11 data models (global rules, `noupdate="0"`), instead of the
  per-`create_uid` isolation of 18.0.1.5.1.
- New `personal.budget.recurring` model (recurring expenses and subscriptions), daily scheduled
  action, effect on forecasts and the dashboard.
- The sharing ledger is renamed `personal.budget.share.line`.
- Dashboard: budget selector, subscriptions card.
- Uniqueness per budget: category name, account + FITID (a joint account can be imported into two
  budgets without clashing).
- Fixes: "my share" follows a change of the gross amount; the invoice summary no longer counts
  zero invoices when "Copy" was never written; the sharing-ledger balance is computed per budget;
  the analysis is isolated per budget.
- Declares the dependency on `web`, so the dashboard assets load after Odoo's JS module loader.

#### Migration from 18.0.1.x

Automatic on `-u`:

1. *pre-migrate*: renames the table, sequence, constraints, indexes and external identifiers of the
   former sharing ledger; lifts the `noupdate` lock on the record rules so the new ones apply.
2. *post-migrate*: creates a personal budget for each person who has data, files each record by its
   author (`create_uid`), propagates the budget to transactions, plans and loan lines, sets then
   checks `NOT NULL`, and recomputes the sharing-ledger balances. Records without a known author go
   to the system account's budget, invisible to everyone. A transaction entered by one person in a
   category created by another follows its category's budget: it is logged, never changed
   silently.

"My share" amounts already stored are NOT recomputed by the upgrade.

### 18.0.1.5.1 and earlier

Original single-user budget; 18.0.1.5.1 ships without seed data and isolates records by
`create_uid`.
