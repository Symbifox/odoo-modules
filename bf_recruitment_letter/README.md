# Recruitment: the job offer (`bf_recruitment_letter`)

`auto_install` bridge between `bf_recruitment` and `bf_letter_writer`. It
produces the one candidate-facing document Odoo cannot produce, and that emails
cannot replace.

## Why the offer, and not a full set of letters

The acknowledgement, the invitation and the refusal are **emails**, written and
proven in `bf_recruitment_mail`. Duplicating them as branded letters would mean
two texts to keep in agreement for one and the same act.

**The offer, on the other hand, exists nowhere.** Measured: `hr_contract` is not
part of the recruitment base, Odoo's own `sign` module is `uninstallable` on
Community, and `letter.document` counts **0** records anywhere. All core carries
is `salary_proposed` and `salary_proposed_extra`, two fields nobody reads outside
the form.

## What the module adds

| | |
|---|---|
| `letter.document.applicant_id` | The letter knows which application it is about |
| **"Draft the offer"** button | Creates the letter, applies the template, opens it |
| **"Job offer"** template | Data, which the customer edits without us |
| Three formatting fields | The amount, the conditions and the date, written the way they read |

## Three traps, and the first is not ours

### 🔴 The framework refuses its own template to anyone who is not an editor

`mail.render.mixin` allows, to anyone without `mail.group_mail_template_editor`,
only **seven** merge expressions: `object.name`, `object.contact_name`,
`object.partner_id`, `object.partner_id.name`, `object.user_id`,
`object.user_id.name`, `object.user_id.signature`. The comparison is made on the
**entire** string, as-is.

No useful offer fits in that. An ordinary recruiter is met with "Only members of
Mail Template Editor group are allowed to edit templates containing sensible
placeholders". ⚠️ **This is not specific to this module**: `bf_letter_writer`
does not override that point, and nobody had hit it because `letter.document`
had never been used.

The two wrong ways out: granting `mail.group_mail_template_editor` to recruiters
amounts to granting them arbitrary QWeb everywhere; rendering under `sudo()`
disarms the guard for everyone. The module **widens the list, on
`letter.document` only**, to a named palette of nine expressions.

⚠️ Consequence for the customer: a template rewritten with a field outside the
palette will raise for an ordinary recruiter. A test reads the template **as it
stands in the database** and fails if a token falls outside.

### 🔴 The fallback belongs in the field, never in the template

Direct consequence: `{{ object.x or "to be confirmed" }}` matches no entry in the
allow-list and makes the rendering raise. Merge fields have to stay **bare
paths**, hence `salary_proposed_display`, `offer_conditions_display` and
`offer_availability_display`, which carry their own fallback. They also make the
template readable for whoever edits it.

### ⚠️ `letter.document.partner_id` is required

A letter is addressed to somebody. Without a guard, an application with no
contact surfaces a raw SQL constraint violation on screen. The module raises with
a sentence saying where to set the contact, and **does not create** the
`res.partner` in its place: manufacturing a holder of personal information behind
the recruiter's back adds one more retention regime.

⚠️ Measured nuance: core already creates that contact by itself as soon as there
is an email address (`hr.candidate._inverse_partner_email` → `find_or_create`).
The guard therefore only bites on a paper CV keyed in by hand.

## 🔴 Privacy: the letter must not leak the salary

`bf_letter_writer` grants `letter.document` read **and write** to
`base.group_user`: every employee sees every letter. Yet core reserves
`salary_proposed` to `hr_recruitment.group_hr_recruitment_user`. Writing the
offer into a letter would therefore expose, in the clear and to everyone, the
amount core protects.

Two record rules fix this, combining with OR: an ordinary employee only sees
letters **without** an application, a recruiter sees everything. ⚠️ A letter
manager who is not a recruiter loses access to offers, deletion right included.
That is intended.

`salary_proposed_display` carries the same restriction as the field it derives
from: without that, it would deliver sideways what core protects.

## The template is never overwritten

⚠️ `noupdate="1"`. An upgrade does not rewrite the text: what a customer has
rewritten belongs to them. **Accepted trade-off**: an improvement on our side
does not reach tenants already installed. And in development, the record and its
`ir_model_data` row have to be deleted for a `-u` to recreate it — otherwise the
database silently keeps the old text.

## Test evidence

A suite including the pair that proves the access rule discriminates: the offer
is out of reach of an ordinary employee while an ordinary letter stays readable
to them, and the test that reads the template from the database to check each of
its tokens against the palette. **Three mutations** placed and removed: without
the palette, seven tests fall; without the access rule, one; without the
conditions guard, one.
