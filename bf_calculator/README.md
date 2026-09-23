# Symbifox Calculator (`bf_calculator`)

A calculator in Odoo's top bar that behaves like a real one, keeps each
person's history, reads the words you type between the numbers, and posts a
calculation, labels included, as an internal note on the record you are
looking at.

## What it does

- **Top-bar icon**, or **Alt+Shift+C** from anywhere. The panel is a
  calculator screen (the calculation small, the result large) with a keypad for
  phones and the mobile PWA.
- **The result stays in the field, like a real calculator.** After Enter or
  `=`, the result (`4 500 $`) is left in the input, selected and in large type.
  An operator continues from it (`* 2` gives `9 000,00 $`); a digit replaces
  it. If you already typed the next step while the server was answering, your
  typing is not overwritten.
- **Labels inside the calculation.** `6 (weeks) * 750 (dollars) =` gives 4,500
  and keeps the words. A parenthesis that contains no digit is a label;
  otherwise it groups. Bare words work too: `6 weeks * 750 $`.
- **French and English number formats.** `12,5`, `1 250,50` and `1,250.50` are
  all read the way a person means them, following the user's language.
- **Desk-calculator percentages**: `200 + 10 %` is 220, `200 * 10 %` is 20.
- **Durations**: `1 h 45 + 2 h 30` is 4.25 h (4 h 15); `1 h 45 * 120 $` is
  210 $.
- **Variables and memory**, per person: `rate = 125`, then `6 h * rate`. A
  word is a variable only in operand position, so in `6 weeks`, "weeks" stays a
  label. MC, MR, M+ and M− work on the variable `M`.
- **Rounding**: exact, to the cent, to 5 ¢ (Canadian cash rounding) or to the
  unit. The rounding applied is noted with the calculation.
- **Currencies**: `100 USD en CAD`, `50 € + 20 US$ en $`, at the **Bank of
  Canada** daily rates (Valet API: free, no key). Only amounts that carry a
  currency are converted; a bare multiplier is not. Rates are cached for six
  hours; if the Bank does not answer, the last reading is used and dated, and a
  failed attempt is not retried for fifteen minutes.
- **Tabs**:
  - *Taxes*: sales taxes read from the accounting (group taxes such as
    GST+QST or GST+PST, and stand-alone ones such as HST), with the parent
    company's taxes visible from a branch. **Reverse tax (total → before
    taxes) is the default direction.** Each tax is rounded to the cent, as on
    an invoice, and the before-tax amount offered is the one that, invoiced
    again, gives back exactly the total typed. When no amount can, the panel
    says so instead of showing inconsistent taxes. Without any sales tax in
    the accounting, Québec's rates are used.
  - *Hours*: `1 h 45` ↔ `1.75`.
  - *%*: margin and markup, change, discount, share of a total.
  - *Dates*: calendar and business days between two dates; a date plus N days,
    business days or not, forward or backward. Holidays are the eight of
    Québec's Act respecting labour standards (CNESST), with Good Friday or
    Easter Monday set by `bf_calculator.easter_holiday` (`friday` by default).
    They are **not** court deadlines, and the panel says so.
  - *Column*: paste a column from a spreadsheet or an email to get the sum,
    average, count, minimum and maximum. `(125.00)` counts as negative and
    headers are skipped.
- **Outputs**: copy the result, copy the calculation with its result, insert
  the result into the last number field you clicked in a form, or **post it to
  a chatter**.
- **History** per person, searchable, with pins (pinned lines lead the list and
  are never purged), ↑ / ↓ to recall previous calculations, and a full list view
  where several calculations can be posted in one note.

## Posting to a chatter

The wizard proposes the record that is open, or any other record through the
picker of `bf_chatter_target` (name, number, `task:123` shortcut or pasted
URL). The note is an **internal note** posted **as the person**, never with
elevated rights, so the target's own posting rights apply. It carries the
comment, the title, the calculation with its labels, the result, and the note
(exchange rate and its date, rounding). Titles and labels are escaped. **No
email is sent.**

## Privacy and access

- A **global** record rule keeps each person's history and variables private,
  administrators included. What is posted to a chatter follows the rights of
  the record that receives it.
- The fields that tie a line to a person or a posted message cannot be written
  through RPC: only the module sets them.
- The calculator is for internal users; portal users are refused.
- History older than `bf_calculator.history_days` days (system parameter, 90 by
  default, 0 = never) is purged daily, pinned lines excepted.

## Design

One evaluator, in Python (`lib/expression.py`): tokenizing, then recursive
descent over `Decimal`, with no `eval`. Length, parenthesis depth, exponent,
literal size and result size are bounded. The panel calls it over RPC, with a
180 ms debounce on the live preview, and discards stale answers.

## Requirements

`web`, `mail`, `bf_chatter_target`. `account` is optional (tax rates).

## Translations

French (`fr_CA`) is complete.

## Changelog

- **18.0.1.2.0**: first public release.
