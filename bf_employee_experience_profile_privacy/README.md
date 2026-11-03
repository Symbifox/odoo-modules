# Profile types: privacy bridge (`bf_employee_experience_profile_privacy`)

Declares what a profile type collects, asks for express consent, names the
profiling, sets the retention rule, and destroys at departure. Auto-installs
when `bf_employee_experience_profile` and `privacy_consent` are both present.

The satellite knows who declared what. It does not know how long it is allowed
to know, nor what it must say first. This bridge tells it.

## What it declares

* **A purpose with express consent required.** Declaring a profile type is
  voluntary, and nobody is obliged to hand their employer a measure of their
  personality. Section 12 para. 2 requires consent that is manifest, free and
  informed, and given for specific purposes.

* **The profiling, named.** The definition in s. 8.1 covers collecting and
  using personal information "to evaluate certain characteristics of a natural
  person, notably for the purpose of analysing work performance". A profile
  type is inside it, and the direct manager reads the record. The plain
  language summary says so before anything is asked, and says what the profile
  is **not** used for. A test reads that summary and fails if the words are
  gone.

* **A retention rule, RH-EX-3**, with zero active years. The duration is the
  employment relationship, not a clock: the purpose is accomplished the day the
  person leaves, and s. 23 then commands destruction. Writing "3 years" would
  be false precision.

* ⚠️ **Not a medical record**, unlike the allergies rule. A behavioural style
  questionnaire is not a health act and does not belong to the occupational
  health file: the rule carries `hr_file`. Classifying it `medical` would align
  it by mistake with MED-001 and its seven years.

## 🔴 The purge cron ships disabled

It destroys the declarations of people who left more than the grace period ago
(90 days, because a departure sometimes undoes itself in the first weeks). An
irreversible purge must never be a side effect of an installation: somebody has
to decide to switch it on, and to choose the delay.

## ⚠️ No aggregate, and no statistic kept

The benefits bridge keeps an anonymous aggregate before destroying, because the
company needs the measurement and not the names. Not here: the distribution of
personality types across a workforce that has left supports no decision, and
keeping it would turn destroyed data into a statistic held for nothing. A test
asserts no aggregate model exists.

## ⚠️ The destruction register never names the type

Writing it down would keep forever, in an immutable register, the very
information just destroyed. The entry says there was a declaration and how
many; it does not say which. It names the person, because that is the
register's object.

The register entry also refuses to cite an **archived** retention rule:
`env.ref` returns one without complaining, and an entry citing it would claim
the destruction followed a calendar that is no longer in force.

## A campaign cannot anonymise one

Anonymising a profile type makes no sense: what is sensitive is the link
between the person and the type, and there is nothing else to remove. The
campaign line refuses any method other than `delete` or `secure_wipe`, and
destroys nothing when it refuses.

The score lines leave with their declaration, through the Postgres cascade on
`profile_id`. They carry no attachment and no message thread, so nothing is
left orphaned in the filestore.
