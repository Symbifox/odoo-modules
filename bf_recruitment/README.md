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
| `group_hr_recruitment_interviewer` | Reads scorecards, sees the sessions it sits on, writes **its own** rating |
| `group_hr_recruitment_user` | Sees everything, creates scorecards and sessions, prints books |
| `group_hr_recruitment_manager` | Deletes a scorecard that was never published |

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
