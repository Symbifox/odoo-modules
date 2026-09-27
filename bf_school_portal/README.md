# Symbifox École: family portal (`bf_school_portal`)

One portal account per adult, whatever the number of children or schools.

## Features

- **My children** (`/my/school`): each child with their current groups, levels and
  teachers, and the roles the school recorded for this adult (receives notices,
  signs, pays, may pick up). Only the school changes this information.
- A home tile with the number of children.
- Invitations: from a student ("Invite these guardians to the portal") or from the
  whole school ("Invite families to the portal"). Only the adults who receive notices
  are invited. Adults already on the portal, internal users and adults without a
  usable email are counted and skipped; the names to fix are listed.

## Security

The only door from the portal to the school's data is
`res.partner._school_portal_links()`, which starts from the logged-in adult's own
partner. The school models grant no access right to portal users: a portal user
calling them by RPC is refused. Tests cover both the page and the RPC refusal.

## What has not been confirmed

- No installable app (PWA) and no push notification for portal users yet: the
  Symbifox push transport refuses portal users today.
- A student aged 14 or more has no account of their own yet.
