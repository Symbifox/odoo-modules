# Symbifox Employee Photo Age (`bf_employee_photo_age`)

Know how old an employee's photo is, and remind them to update it.

## What it does

- **Photo taken on**: the date the current photo was put on file, in the
  employee form's *Settings* tab, with its age in months. A drawn avatar (any
  SVG) is not a photo and leaves the date empty. On install, photos already
  on file are dated from their attachment.
- **Two filters** in the employee list: *Photo to update* and *No photo on
  file*.
- **A weekly reminder**: an employee whose photo is older than the set number
  of months (24 by default, 0 turns it off) gets a to-do on their own contact,
  which every internal user can open (the employee file is for HR only).
  The reminder says the photo is due, not how old it is: the contact is
  readable by every internal user, while the date and age stay HR's. Never
  two open reminders for the same person, and it closes by itself when a new
  photo is on file.
- **The right instructions.** Odoo only copies a picture changed in the user
  preferences to an employee file that already has one when employees may
  edit their own data. The reminder says so: preferences when self-editing is
  on, the HR contact otherwise.

## Requirements

- Odoo 18.0, `hr`, `mail`.

## Translations

French (Canada) in `i18n/fr_CA.po`. Source strings are English.

## Changelog

### 18.0.1.0.1

- First public release.
