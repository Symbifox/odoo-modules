# Symbifox École: school foundation (`bf_school_core`)

The foundation of the Symbifox school suite, built for Québec primary and secondary
schools that want to leave their current family portal.

## Why

A school portal lives or dies on one question: which adult is told what about which
child. Families are not "a parent and a child". They are separated parents who both
receive notices while only one pays, a stepparent who may pick up but signs nothing, a
grandparent on the emergency list. This module records that once, as data, so every
other module (announcements, forms, billing) asks the same question and gets the same
answer.

## Features

- Schools, school years (one current year per school), levels from pre-K 4 to
  Secondary V, groups with their teachers. A group may span several levels.
- Students are contacts (`res.partner`): birth date, permanent code (four letters then
  eight digits, normalised and unique), enrolments in groups. Leaving a group keeps
  the enrolment as history. The Students menu opens a student form without the
  company fields (VAT, sales and purchases).
- Guardian links with explicit roles: parental authority, receives notices, signs,
  pays, may pick up, emergency contact, lives with. An adult is linked once per
  student; roles are ticked, not duplicated.
- A signer must hold parental authority or tutorship.
- A grandparent or "other" adult starts without parental authority, signature or
  notices: the school ticks what it decides. Health, conduct, declaring an absence and
  ordering meals are for the holders of parental authority (or the payer, for meals),
  not for every adult who receives notices.
- The links with parental authority are mirrored into `legal_guardian_ids` of
  `privacy_consent`: consents and signatures reach the right adults without the
  consent module knowing about schools.
- `is_minor_child` is computed from the birth date and the age set by the company's
  privacy framework (14 under Law 25). A daily cron flips it on the birthday.

## Security

- **Staff** read schools, groups, students and families, and change nothing.
- **Administration** creates and edits everything, and invites families.
- Portal users have no access right on any school model. The family portal reads
  through the adult's own links (see `bf_school_portal`).
- A student's birth date, age and permanent code, and the staff's notes on any contact, are
  for internal users only: a student with their own portal account does not read them on
  their card, over RPC either. The portal pages read what they show in sudo.
- Multi-company rules on every model.

## What has not been confirmed

- Staff read every student and family of the school, not only those of their groups.
  This is deliberate for now (supervision, substitutes, office); a school that wants
  it narrower needs a teacher-scoped read rule.
- The icon is provisional.
