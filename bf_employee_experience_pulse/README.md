# Employee Experience: Pulse (`bf_employee_experience_pulse`)

An anonymous mood survey that cannot tell who answered what — because of how it
is built, not because of a setting.

## The problem

The retention indicators in `bf_employee_experience` are all computed after the
fact. A turnover rate counts departures once they have happened, and it measures
best when the damage is done.

This module adds the half that moves *before*: what people are living through
while they are still here.

## Why it does not use Odoo's Survey module

The word "anonymous" appears exactly **once** in the whole of Odoo 18's `survey`
module, and it is a display label in the live-session leaderboard:

```
survey/views/survey_templates_user_input_session.xml:222:  <span t-else="">Anonymous</span>
```

There is nothing to tick. And the native module does worse than offer nothing:

```python
# survey/models/survey_survey.py, _create_answer()
if user and not user._is_public():
    answer_vals['partner_id'] = user.partner_id.id
    answer_vals['email'] = user.email
    answer_vals['nickname'] = user.name
```

Its public controller passes the session straight in. A survey configured as
"anyone with the link, no login required" therefore **still records the name** as
soon as the person clicking has an Odoo session open — and an employee has one
open all day. The access setting protects visitors, not staff.

A test in this module (`test_contre_exemple_le_sondage_natif_nomme_le_repondant`)
checks that this is still Odoo's behaviour. If it ever goes green, Odoo has fixed
it and the case for this module is worth reopening.

## How this one works

### Two registers, and nothing between them

| Model | Knows | Does not know |
|---|---|---|
| `bf.ex.pulse.invitation` | who must answer, who has answered | what, or when |
| `bf.ex.pulse.answer` | what | who, or when |

No column of the answer register leads to a person. A test checks this by walking
the **declared fields**, not by re-reading the file, so a field added later by a
bridge falls into the same net.

### No automatic columns

The three collection models carry `_log_access = False`, which removes
`create_uid`, `create_date`, `write_uid` and `write_date`. Without that, the
"has answered" flag would carry a microsecond timestamp and the two registers
would join on time. A test reads `information_schema.columns`: a field removed
from Python can survive as a SQL column.

### Batched, shuffled hand-off

An answer written straight into the final register would be anonymous by its
columns and talkative by its `id` — identifiers climb in insertion order. Anyone
watching the invitation register twice learns who answered between the two
readings, and the answers that arrived in that window are theirs.

Answers therefore wait in a holding area, and a scheduled pass moves them across
**in a batch and in random order**, discarding the token. Nothing moves while the
batch holds fewer respondents than the written-comment threshold.

### Thresholds that actually hold

The holding area and the answer register appear in **no access-control line at
all**, so they are reachable only in `sudo`. The single door is the aggregate,
and the aggregate is what applies the thresholds.

That is the difference between a threshold and a display filter: opening the
register to HR would make the thresholds decorative, and a two-person team's
comments would be readable straight from the database.

| Threshold | Default | Why |
|---|---:|---|
| Numeric score | 3 respondents | Market practice. Below three, an average reads as one person's answer. |
| Written comments | 5 respondents | A verbatim is recognisable by its voice; a number is not. |

Below the threshold the screen says "not enough answers". Never zero, never an
approximation.

### A rolling window

Scores aggregate the waves of the last 90 days by default. That is what lets a
team of six clear the threshold over a quarter instead of never over a week.

⚠️ A score belongs to the **window**, not to the wave: two waves opened on the
same day share their figures, and the aggregate is keyed accordingly.

## What the module assumes

It knows who has *not* answered, and follow-ups target those people. Without
that, a reminder pesters the ones who already played along. What it does not
know, and cannot know, is what any individual answered.

The invitation register is readable by HR in read-only: nobody can flip the
"has answered" flag by hand.

## What ships

Ten metrics, twenty questions, one of them on the eNPS scale. The data is
`noupdate="1"`, so a customer who renames a metric does not have it overwritten
on the next upgrade.

Relationship with the manager, relationship with peers, feedback, recognition,
growth, alignment, workload and wellbeing, autonomy, satisfaction, pride and
recommendation.

The "workload and wellbeing" metric is the one that touches psychosocial risk.
⚠️ Note for Québec deployments: the *Règlement sur les mécanismes de prévention
et de participation en établissement* does **not** require a survey. It requires
that psychosocial risks be identified and analysed. A recurring anonymous pulse
documents part of that, with a dated trail. Do not sell it as more than that.

## Branding

The invitation email and the public page read their colours from the company
record rather than from hard-coded values, so a tenant that changes its brand
changes its pages without a code edit.

⚠️ The raw accent colour never carries text. White on a typical accent blue
renders at **2.62:1**, well under the 4.5 that WCAG AA requires, so anything
bearing text uses a darkened variant computed at render time. The raw accent is
kept for rules, borders and selected states.

## What it does not do

* No points, no badges, no write to any gamification engine. A named reward would
  be the survey's attendance sheet: an XP ledger carries the user and a
  second-precision timestamp. A test checks that no field and no dependency leads
  there.
* No segmentation below the threshold. Splitting a fifteen-person company by
  department produces an empty table — which is the correct behaviour and a bad
  idea anyway.
* No route back to an individual, not even for a worrying comment. That is the
  price of anonymity, and it is why this module does not replace a named
  reporting channel.

## Tests

59 tests. Fresh install from an empty database, and the journey played over HTTP
by a **signed-in** user — which is the only place the native module's trap shows
itself.

```
odoo -c <config> -d <database> -i bf_employee_experience_pulse \
     --test-enable --test-tags /bf_employee_experience_pulse --stop-after-init
```

⚠️ The test server must mount **its own** configuration file. A command-line
`--addons-path` does not win for Python imports: `odoo.addons.__path__` is built
from the config file, so the module would load from a neighbouring tree while its
data files came from the right one. A mutation pass on a hybrid tree measures
nothing.

## Licence

BUSL-1.1. Each version converts to LGPL-3.0-or-later on its Change Date, four
years after publication. See `LICENSE`.
