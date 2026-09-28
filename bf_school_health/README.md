# Symbifox École: health (`bf_school_health`)

Health alerts, health records and medication at school, on two levels.

## Features

- **Level 1, every staff member**: a short health alert on the student (severe or to
  know), on the student form and list. A replacement teacher sees it too.
- **Level 2, the Health group**: the detailed health record, the individual emergency
  plan, the medication authorisations and the register of doses. The school names who
  belongs to it (nurse, designated office staff); the Administration role does **not**
  imply it (Private Sector Act s. 20, need-to-know).
- **Medication authorisation**: a prescribed medication (Professional Code s. 39.8,
  routes it allows), dosage, validity period, clear conditions for an as-needed
  medication. Sent to the guardians who sign; the text is frozen once sent and its
  SHA-256 fingerprint is kept with who signed, when and from which address. The state
  and the signature move only when a guardian answers or the Health group revokes:
  nobody writes them by hand. A refusal always wins: a guardian's "no" after the other
  guardian's "yes" stops the medication.
- **Register of doses**: nothing is given without a valid authorisation for that
  student on that day. **Epinephrine in an emergency** is the exception: any staff
  member records it (menu Health, Emergency epinephrine) and the family is told at
  once, without waiting for the mail queue. The register always names the person who
  recorded the dose, and a dose recorded is not rewritten: only a note is added.
- **Family portal** (**Health**), for the adults with parental authority: the alerts,
  the authorisations to sign, what is authorised and what was given in the last 60
  days. The detailed record never reaches the portal.

## Before putting it in service

Health information is sensitive (s. 12). A privacy impact assessment is required
(s. 3.3); a French template ships in `doc/EFVP_gabarit_fr.md`.

## What has not been confirmed

- The emergency mail does not claim that emergency services were called: the software
  does not know it. The school's procedure (call 911, second dose) stays on paper and
  in the individual emergency plan.
- No reminder when an authorisation expires; no stock of medication.
- The routes list follows s. 39.8 as read on 2026-09-26; a legal review is still open.
