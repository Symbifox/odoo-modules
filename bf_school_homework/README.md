# Symbifox École: homework and calendar (`bf_school_homework`)

Homework on the family portal and in the parents' own calendar.

## Features

- A teacher posts homework, studies, projects and announced tests for the groups they
  teach, with the date given and the due date (list and calendar views).
- **Family portal** (**Homework**): what is due for each child, sorted by date.
- **Personal calendar address** (iCal) per adult: subscribed in Google Calendar or
  Apple Calendar, the due dates of the children's homework appear there and update by
  themselves. The feed follows RFC 5545 (escaping, lines folded at 75 octets, all-day
  events), is checked with a constant-time secret, and the adult can get a new address,
  which stops the old one.

## Security

A teacher manages the homework of the groups they teach, and moves a homework only to a
group they teach; the office sees all. Who gave a homework is the person who recorded it. Families have no access right; the feed is public
but secret per adult.

## What has not been confirmed

- No attachment on a homework from the portal side (the teacher's file is not served).
- The feed was checked by the tests, not yet subscribed from a real Google or Apple
  calendar.
