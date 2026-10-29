# Preferred Names and Pronouns: Link Page Bridge (`bf_preferred_name_linkpage`)

Pronouns and the preferred name on a person's link page.

A person's link page (`bf_linkpage`) is public: it is the page a QR code or a
shared link leads to. It used to be created with the contact's name and keep it
forever, because the periodic pass of `bf_linkpage` only refreshes the links of
the template. Renaming a person left the former name at the top of their page,
in the browser tab title and in the sharing preview.

This bridge makes the page follow what `bf_preferred_name` knows about the
person.

## Pronouns under the name

Once the person has given pronouns, they appear under their name on the public
page. A **Show my pronouns** box on the page form, checked by default, removes
them from the public page without removing them from the contact.

Pronouns appear only on a page whose user is the person themself, the person
who can uncheck the box. They do not appear on a page another user made in the
person's name, nor on the page of a contact or employee without a user account
(the portal has no access to link pages). Nothing appears for someone who gave
nothing, nor on an organization page.

## The title follows the preferred name

On every change of the contact name (contact form, **Change preferred name**,
portal, preferences, API) and on contact merge, the title of the person's pages
follows, archived pages included, **without a trace in the page's chatter**.
The title is tracked there, and the former name would have stayed. A title
written by hand, anything other than the contact name, is never overwritten.

Renaming an employee in the Employees app does not rename their contact: Odoo
does not copy the employee name to the work contact. Change preferred name
renames both.

## Addresses, by choice

Three public addresses often carry a person's name: their link page
(`/l/first-last`), their secure drop page (`/to/…`, the brand they own in
`bf_securetransfer`) and their appointment types (`/appointment/…`, from
`bf_appointment`). Change preferred name offers to change them with a box,
**Also change the addresses that carry the name**, which is **never checked by
default**: links already sent and printed QR codes stop working. The box shows
only when such addresses exist.

Once checked, it lists one line per address that **contains the former name**,
with the proposed address (the new name in place of the old one:
`perso-dominique` becomes `perso-sacha`), editable, and a **Change** toggle per
line.

- The toggle starts on when the address carries at least two words of the
  former name, or the only word of a one-word name.
- It starts off when the address carries a single word of a longer name, which
  may also be a common word (`paris-seminar`).
- Particles and single letters never trigger a proposal
  (`tour-de-la-maison`).
- An address that names nobody (`flexible-meeting`) is not proposed, nor an
  appointment type shared with another person or with a resource that has no
  user account.
- Only addresses the user is allowed to edit are listed. The server computes
  the proposals again when the dialog is applied: a line added or altered
  through the API changes nothing.

The old address then leads to a "not found" page, **with no redirection**,
because a redirection would publicly link the former name to the person.
Addresses change without a trace in the chatter. Also following:

- **short links** (`/r/…`) of this instance that led to a changed address are
  pointed to the new one (the short code does not carry the name);
- a **link page** that named a renamed appointment type follows it;
- the **title of the secure drop page** and the name of its recipient are
  copied from the new name, whether the box is checked or not, when the
  `bf_securetransfer` setting "Create a drop page per employee" is on.

Only the name the contact is **leaving** is searched for in the addresses. An
address that carries an even older name (an earlier renaming made without
changing the address) is not proposed. Change it by hand.

## Retired addresses

A link page address that a page left, through the action or by hand, or the
address of a deleted page, is **reserved**: no other person's page can take it,
and the address generator skips it. A printed QR code never leads to someone
else. The same person can take it back (a page created again for them, for
example).

The reserve keeps only a **fingerprint** of the address (HMAC-SHA256 with a key
of its own, created once: rotating the database secret does not empty it),
never the address in clear, which often carries a former name. The refusal
only says that the address is not available.

Residual risk: someone who tries a name as the address of their page learns
that it is not available while `/l/<name>` does not answer, and so that someone
left it.

An administrator lifts a reservation (a corrected typo, for example) in
**Settings > Technical > Retired link page addresses** by deleting its line.
The list never shows the address.

Secure drop and appointment addresses are not reserved: their modules do not go
through this bridge.

## History

For the **Legal names** group, the **Erase the name-change history** option
also erases, in the page's chatter, what keeps the name there: the traces of
its title, address, contact and user, and the notifications sent to users
(assignments, activities), which carry the title. The title copied into the
page's other messages and activities is reset to the current name. Notes and
other traces stay.

## Requirements

- Odoo 18.0
- `bf_preferred_name` (Symbifox)
- `bf_linkpage` (Symbifox)
- Optional: `bf_securetransfer` and `bf_appointment` (Symbifox), whose
  addresses the action can then change; `link_tracker` (Odoo), whose short
  links then follow.

Installs itself when `bf_preferred_name` and `bf_linkpage` are present.

## Changelog

- **18.0.1.1.0**: Change preferred name proposes, line by line, every public
  address that carries the former name (link page, secure drop page,
  appointment types), and a link page address that a page left is reserved
  (kept as a fingerprint, never in clear).
- **18.0.1.0.0**: First release.

## Licence

Business Source License 1.1 (`LICENSE`). Each version converts to
LGPL-3.0-or-later on its Change Date: 2030-10-29 for this version.
