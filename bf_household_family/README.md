# Household Family (`bf_household_family`)

The family side of a Symbifox Personal household: who is in it and who manages the
accounts, the children, the family's papers and the emergency cards. Labels in English, French (Canada) in `i18n/fr_CA.po`. LGPL-3.

Depends on `bf_household_base` (the household group, the ten-account cap and the refusal
of administrators). On Symbifox Personal, the hosted household instance installs it with
everyone's apps through a household profile that is not part of this repository. Two
bridges in this repository install by themselves when their other module is there:
`bf_household_family_health` (Healthy Fox) and `bf_household_family_celebrations`
(Celebrations). The export of one's papers when leaving comes from a third bridge, for the
personal records transfer, which ships with the hosted service only.

## Members and roles

- **Household manager** (group): invites a member (the invitation goes to their own
  address; they choose their password), resends the password link, removes a teen or an
  account never used, names or removes another manager. Every gesture is a guarded button
  of the **Members** list, run as superuser without the caller's `default_*` context keys.
- A manager **never** sets a password, changes an email, touches two-factor
  authentication or removes an adult who has used their account: each would hand over the
  account, so its private records, or cut a person off from their own records. Odoo's core
  already refuses that a member edits another internal user's contact.
- **An adult leaves by themselves** (« Leave the household », after exporting their
  records when the transfer bridge is there), or **Blue Fox removes them on request**,
  with a notice and a delay (30 days by default) during which they keep their access.
- Roles: adult, or teen (14 to 17, birth month and year only, readable by access-rights
  managers only). A teen becomes an adult the month of their 18th birthday (daily cron).
  A manager is an adult.
- Every departure is recorded (`bf.household.departure`, Blue Fox only), with the date by
  which the departed account's private records must be erased by Blue Fox's procedure.

## Children

- One record per child, with one or two named parents. The creator is the primary parent;
  the primary parent names the second parent; the second parent can step back. « Swap
  parents » hands the child over (the Healthy Fox bridge follows).
- The whole household reads the first name and the birthday. Only the parents write.
- A contact is created for the child (shared, first name only): invite them to a calendar
  event, celebrate their birthday.
- Birth: year and month always, the day only if a parent gives it.
- When a parent leaves, a child they held with a second parent goes to the second parent.

## Family papers

- Passports, health cards, licences, certificates, permits, insurance policies,
  vaccination records: type, number, issuer, dates, photos and scans, notes.
- **Private**: the holder, or the child's parents, read and write; members it is shared with
  read only. No exception for administrators (same rule for everyone).
- **No social insurance number**: there is no field for it, and a nine-digit number that
  passes the Luhn check is refused.
- **Reminders** arrive as private to-dos (a task without project is visible to its
  assignees only), six months before a passport or permanent-resident card expires, 90 days
  for a visa, 60 for a health card or a licence, 30 otherwise. The to-do names the type and
  the person, never the number.
- No chatter, no activity, no follower on a paper; its name reads « Private paper » for
  anyone who cannot open it.

## Emergency cards

- Who to call, blood type, allergies, medications, conditions, doctor, notes. No paper
  number, ever.
- The household reads every card; the person (or the child's parents) keeps it.
- **Print** (PDF) and **Share with a babysitter**: a link with a 32-byte random token, of
  which only the SHA-256 digest is stored. It lasts 4 hours to 7 days (24 hours by default),
  can be withdrawn, and logs every opening. The page shows only the card, is served with
  `noindex`, `no-store`, `no-referrer`, and links nowhere into the instance. An unknown,
  expired or withdrawn link gets the same 404 page.
- With the Healthy Fox bridge, the person can tick « Add the medications and conditions
  from Healthy Fox ».

## License

LGPL-3. Les services de consultation Blue Fox, Inc.
