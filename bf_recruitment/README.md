# Recruitment: structured interview book (`bf_recruitment`)

Adds to `hr_recruitment` the part Odoo does not cover: the structured evaluation
of a candidate by a panel, against a frozen scorecard.

## The gap

Odoo's recruitment gives you the job, the person, the application, the stages
and the refusal reasons. It stops at the evaluation: `hr.applicant` carries a
single free-text notes field (`applicant_notes`), and `hr_recruitment_survey`
adds **one** questionnaire per job, with no weighting, no round, no panel and no
comparison between candidates.

The interview book is the piece that exists nowhere.

## The design rule

> `hr_recruitment` stays the truth of the pipeline. This module only adds what
> Odoo cannot do.

No field of `hr.applicant` or `hr.candidate` is duplicated. Three fields only
are grafted onto the application: the interview sessions, the reason for the
decision, and who took it.

## Models

| Model | Role |
|---|---|
| `bf.interview.guide` | The scorecard: weighted criteria, scale, round, instructions |
| `bf.interview.criterion` | One criterion, its weight, its question, its knock-out threshold |
| `bf.interview.anchor` | What each score means, in observable behaviour |
| `bf.interview` | One session, held by a panel, against a frozen scorecard |
| `bf.interview.rating` | One score, per person and per criterion |

## The four properties that carry the module

**1. A scorecard freezes once it is in use.** A published scorecard can no
longer be edited, neither it nor its criteria, and never returns to draft. To
make it evolve you draw a **new version**, which is a separate record
(`action_new_version`). A session held last year therefore stays readable
exactly as it was scored.

**2. Other people's scores stay hidden until you submit.** A record rule on
`bf.interview.rating`: until my rating is submitted, I only see my own. That is
what separates a panel from an echo chamber. The condition is expressible in SQL
thanks to the **stored** computed field `bf.interview.submitted_user_ids`.

⚠️ That field computes under `sudo` deliberately. Without it, the person who has
just submitted would recompute the aggregate over the only lines they can see,
erasing everybody else's trace.

**3. Every note is written to be read by the person evaluated.** The form says
so on screen. An application refused after an interview that was **actually
held** requires a written reason and records who decided. Refusing an
application that went through no interview stays frictionless.

**4. The book is computed, not stored.** Two QWeb reports, no extra model: a
candidate's book (`hr.applicant`) and a job's comparison grid (`hr.job`).

## The starter catalogue: 32 scorecard templates

An empty scorecard model is a blank page, and a blank page gets filled with
whatever comes to mind on the day, which is exactly what structured
interviewing exists to prevent. The module therefore ships a catalogue of **32
templates**, as module data:

| Category | Count | What it covers |
|---|---|---|
| Cross-cutting | 5 | Phone screen, values, final interview, remote work, integrity and privileged access |
| Role families | 17 | Sales, customer service, marketing, HR, accounting, administration, purchasing and logistics, retail, software, IT infrastructure, data, project management, people management, executive, production and trades, entry level, training |
| Sectors | 10 | Health and social services, education, food service, construction, non-profit, municipal, transport, food processing, hospitality, professional services |

Each template carries weighted criteria (**193** in total), the question asked
**word for word**, what the interviewer is actually looking for, and **579
anchors** describing in observable behaviour what a 1, a 3 and a 5 are worth.
Twenty-eight criteria are knock-outs (a licence, an availability, a safety
refusal) and they **flag** a session without discarding anyone. Every template's
instructions repeat the four rules that make the practice work: same questions
in the same order, facts rather than impressions, submit before discussing, and
no question touching a protected characteristic.

**A template is not a scorecard.** Nobody is scored on a template. It is used to
drop a **draft scorecard** into the current company, which the organisation then
adapts to its own job before publishing it. The two stay separate for two
reasons: the scorecard list stays the organisation's own, and a module update
can fix a badly worded question in the catalogue without ever touching the
scorecards already drawn from it. A template archived by a tenant stays archived
across updates: `active` is not in the XML definition, so the update does not
rewrite it.

| Model | Role |
|---|---|
| `bf.interview.guide.template` | One catalogue entry, read-only, delivered by the module |
| `bf.interview.guide.template.criterion` | Its criteria, weights and knock-out thresholds |
| `bf.interview.guide.template.anchor` | What each score means, in observable behaviour |

## What the module does not do

- **It ranks nobody automatically.** The weighted score helps a person decide;
  it discards no application. A knock-out criterion below its threshold
  **flags** the session, it refuses nothing. This is a requirement of Quebec's
  Law 25 on decisions based exclusively on automated processing.
- **It carries no retention rule.** That is the job of the `bf_recruitment_privacy`
  bridge. An unsuccessful application is not an employee file, and an
  employee-records schedule does not cover it.
- **It talks to no job board.** Posting statistics are obtained first with
  `hr.recruitment.source` (UTM link and mail alias per job and per source),
  with no API — see `bf_recruitment_source`.

## Groups and rights

No new group. The three groups of `hr_recruitment` already describe the roles:

| Group | What it can do |
|---|---|
| `group_hr_recruitment_interviewer` | Reads scorecards and the catalogue, sees the sessions it sits on, writes **its own** rating |
| `group_hr_recruitment_user` | Sees everything, creates scorecards and sessions, draws scorecards from the catalogue, prints books |
| `group_hr_recruitment_manager` | Deletes a scorecard that was never published, archives a catalogue template |

The catalogue itself is **read-only**, for the manager as well: a local edit
would be silently overwritten by the next module update. Drawing a scorecard
from it is what everybody does instead.

A rating belongs to the person who wrote it: the recruiter can see it, but does
not score in their place. A submitted rating can no longer be rewritten.

## Test evidence

- **Green suite** covering the scorecard freeze, the weighted average across
  raters, the knock-out flag, blind submission, rating ownership, and the
  refusal reason required after an interview was held.
- **Fresh install** from an empty database, without demo data.
- **Views loaded under real accounts** (recruiter and panel member), not under
  uid 1, which holds no group.
- **Both reports rendered.**
- **Declared columns against `information_schema`: 0 missing.**
- **A mutation placed on the blind-submission rule**: replace its domain with
  `[(1, '=', 1)]` and the panel member then sees the other's rating. The rule is
  indeed what hides it, and the test would have failed without it.
- **The catalogue is tested as a deliverable**, not only as data that loads:
  every template would pass `action_publish` (criteria present, all weights
  positive), every criterion carries its question and anchors at 1 and at the
  scale maximum, and every code is unique. A template with a missing anchor
  fails the suite here rather than at a client.
- **Two mutations on the catalogue path**: dropping the deep copy of the anchors
  makes the fidelity test fail, and granting the recruiter write access on the
  catalogue makes the read-only test fail.
- **The upgrade path**: installed at the previous version, then upgraded. A
  template archived by the tenant survives the upgrade, criteria intact.

## Companion modules

| Module | What it adds |
|---|---|
| `bf_recruitment_privacy` | Retention schedule, anonymised aggregate, real destruction |
| `bf_recruitment_mail` | The four candidate-facing emails, rewritten |
| `bf_recruitment_portal` | The candidate's own view of their application |
| `bf_recruitment_expense` | Spend and cost per hire |
| `bf_recruitment_letter` | The job offer as a branded letter |
| `bf_recruitment_sign` | That offer, sent for signature |
| `bf_recruitment_source` | Statistics per job board, with no API |
| `bf_recruitment_source_expense` | What each job board costs |

All of them are `auto_install` bridges: each one installs by itself when both of
its sides are present, and the interview book works perfectly without any of
them.
