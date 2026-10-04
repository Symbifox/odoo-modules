# Symbifox École: students on the portal (`bf_school_student_portal`)

Students sign in to the family portal with their school directory account.

## Features

- Each student directory account (Authentik, `bf_school_device`) gets a **portal user**
  tied to the student's card. It has no password of its own: the student signs in through
  the school's directory, with the same user name and password as on the school's
  computers.
- The portal user **follows the account**: archived when the student is no longer enrolled
  this year (even if the directory cannot be reached), active again when they are. A daily
  job and every synchronisation keep it in line.
- On the portal, a student sees their own page (work, homework, courses), not the parents'
  view. Signing in through the school's directory lands them there, not on the website's
  home page (a portal link followed while signed out still leads to that link).

## Security

- **Signing in through the school's directory never creates a user**, whatever the
  website's sign-up setting: Odoo's OAuth would otherwise open a portal user for any
  directory account, staff included. Only a student directory account gives one.
- The provider must be the school's own directory (same host as the directory address): a
  user id from another Authentik would be someone else. It is set by an administrator only,
  and checked again whenever the provider's addresses or the directory address change.
- When the school signs its students in through its directory, that is the only door: a
  password an administrator gave a student does not sign them in.
- The portal user is set by the system only, at creation too, and the synchronisation only
  ever writes a portal user tied to the student's own card. It refuses a card that already
  has a user or that is a guardian's (an adult made a "student" would act for their
  children); one refused account does not stop the others.
- A student does not edit their contact card (only their language and time zone), by the
  portal, the eLearning profile or RPC, does not reset a password (it would give a password
  outside the directory, sent to the card's address), and does not delete their account.
- A student under 14 (`is_minor_child`) neither sees nor answers their own consents and
  privacy preferences: the parental authority does (Law 25, s. 4.1). From 14, the student is
  the one who consents.

## Setup

In the school's Authentik, an OpenID provider and application for the portal: subject mode
**Based on the User's ID**, grant type **authorization code**, a **signing key** (the ID token
is checked against the provider's keys), a redirect URI per host serving Odoo
(`https://<host>/auth_oauth/signin`), and an authentication flow without MFA for the students.

🔴 Authentik 2026.8 no longer accepts the flow Odoo's own OAuth module uses
(`response_type=token`: `unsupported_response_type`). Install the OCA module **auth_oidc**
(server-auth) and set the provider's flow to **OpenID Connect (authorization code flow)**,
with its token endpoint, its keys (`/application/o/<slug>/jwks/`) and the token map
`sub:user_id`. Then set the school's **Student sign-in** field. Every address of the
provider must be on the school's directory host.

## What has been checked

Against a real Authentik 2026.8.3: a student signs in with their directory account and lands
on their own portal page; a staff account of the same directory is refused, with no user
created; once the student leaves, their portal user is archived and the same sign-in is
refused.

## What has not been confirmed

- Odoo's own OAuth (token flow) does not check the token's audience: a token issued by the
  same directory to another application could also open the portal. With OCA auth_oidc's
  code flow, the code is issued to this application only and the ID token is checked against
  its client id as audience.
