# NFC tags: readings and registers (`bf_nfc_inspection`)

A timestamped pass proves someone came by. It does not prove what they saw. An
inspection register asks for a result per item: is the extinguisher accessible,
its pressure in the normal range, its seals intact? And when something is wrong,
what was done about it, and when.

- **Checklists**: the items to check, each answered conforming / not conforming, a
  measured value with its range, a choice, or text.
- **One reading per tap**: the phone shows the checklist, it is filled in on site,
  with or without a network (the app keeps the checklist of a tag it has read
  once).
- **Issues**: an activity for the person in charge, and a dated correction to
  record on the reading.
- **Register**: a PDF with the columns of a paper register (date, item checked,
  checked by, result, correction and its date).
- **Watcher**: a tag whose checklist is monthly and that was not read this month
  creates an activity, once per missed period.

## Design rules

- **A reading is never rewritten.** Its results are read-only for everyone,
  managers included. What gets added is the correction, which is exactly what a
  register must carry.
- **The checklist is a field of the tag**, never a parameter.
- **Through a signed tag, the checklist asks for a name first**: the person tapping
  has no account, and a register says who checked.
- **An app that never received the checklist cannot save an empty reading**: the
  reading is refused with the reason instead of being written as conforming.
- Each result line copies the item's label, unit and range at the time of the
  reading: editing a checklist later does not change past readings.

## Shipped checklists

Starting points, to adapt to the actual equipment and validate against the rule
that applies (`noupdate`): portable extinguishers (monthly), emergency lighting
(monthly), battery exit signs (monthly), fire doors (daily), fire alarm panel
(daily), first aid kit and defibrillator (monthly), outdoor playground (daily
visual, monthly detailed; flagged *to validate*: built from a manufacturer's
summary of CSA Z614, not from the standard itself), office closing round.

Templates shipped here: first aid kit and defibrillator, fire alarm panel, office
closing round.

## License

Business Source License 1.1, see [LICENSE](LICENSE).
