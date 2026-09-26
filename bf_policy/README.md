# Symbifox — Blue Fox OS Policy (`bf_policy`)

Manage a fleet of **Blue Fox OS** workstations from Odoo 18. This module is the
group-policy plane of Blue Fox OS, an immutable Fedora Kinoite desktop image:
one Odoo record per company describes how its machines are installed, who may
install them, how people sign in, and what their session looks like. Machines
fetch that policy as JSON at install time, then keep following it on their own.

Think of it as a GPO for Linux desktops, where the console is the Odoo your
organisation already runs.

## Why

Installing a workstation by hand means retyping the same answers every time:
keyboard, language, timezone, disk encryption, directory login, the apps and
bookmarks people expect. It also means that once a machine is installed, a
change of policy only reaches it if someone re-does the work. Here the answers
live in Odoo, next to the people and companies they apply to, and a machine
asks for them itself.

## What a policy covers

- **Who may provision a machine**: any authenticated user, members of chosen
  groups, or an explicit list of users (menu-driven, nothing hard-coded).
- **Install**: locale, console keymap and desktop (XKB) layout, variant and
  options, timezone (the person's own Odoo timezone wins over the company
  default), hostname pattern (`bf-{username}`), root locked or enabled.
- **Sign-in**: local accounts, or the Linux seat bound to your identity provider
  through an Authentik LDAP outpost (sssd), with offline login and its expiry,
  MFA requirement, auto-lock delay, and an optional local break-glass account.
- **Disk**: btrfs on LUKS, optional TPM2 auto-unlock (the passphrase is always
  kept as a recovery method), and optional **passphrase escrow** (see below).
- **Session**: accent colour, wallpaper, Nextcloud/WebDAV mounts, pinned web
  apps, 12 h / 24 h clock with a second clock when the person works in another
  timezone than the company, and the person's photo on the login screen.
- **Applications**: Flatpak apps to add to, or remove from, the base image,
  picked from a Flathub catalogue that the module can synchronise on demand
  (about 3,300 desktop apps, filtered by default to a short recommended list).
- **Browser**: extensions forced into Brave, per company and per person — from
  the Chrome Web Store, or Symbifox's own extensions served by this module.
- **Services**: the Nextcloud instance and OIDC client used to mint a Nextcloud
  app password during installation, so the first boot does not ask the person
  to sign in a second time.

Per-person overrides sit on top of the company defaults; a removal always wins
over an addition.

## How machines talk to it

| Route | Auth | Purpose |
|---|---|---|
| `GET /api/v1/policy/me` | OIDC bearer (Authentik userinfo) | Merged policy for the person installing |
| `POST /api/v1/policy/enroll` | Same bearer, during installation | Gives the machine an identity of its own and, if enabled, escrows the disk passphrase |
| `GET /api/v1/policy/machine` | Per-machine secret | Lets an installed machine re-fetch its policy on a timer, with nobody present |
| `GET /bf_policy/extensions/update.xml` | Public | Chromium update manifest for the extensions this module serves |

The payload schema is in `static/schema/policy.v2.json`. Several tenants can
share one database: a request is routed to the company whose domain matches the
host it arrives on, and an unknown host is refused rather than served another
tenant's policy.

### Machine identity

At install time, while the operator's bearer token is still valid, the
installer enrols the machine and receives a secret that Odoo returns exactly
once and stores only as a SHA-256 digest. That secret can read that machine's
policy and nothing else — it opens no Odoo session. Revoking a machine (Policy ›
Machines) cuts it off at its next sync and blocks re-enrolment under the same
identity; authorisation is re-checked on every sync, so removing someone from
the authorised group also stops their machines from updating.

### Disk passphrase escrow

When enabled, the installer draws the LUKS passphrase itself and deposits it
during enrolment, instead of a person typing one nobody records.

- It is encrypted with a Fernet key read from the environment
  (`BF_POLICY_ESCROW_KEY`) or `odoo.conf` (`bf_policy_escrow_key`), **never from
  the database**: a stolen dump opens no disk. Losing that key loses the escrow.
- Reading a passphrase back requires a dedicated group, *Blue Fox OS — Peut
  révéler les phrases de passe de disque*, which system administrators do **not**
  get implicitly. Every reveal is counted, timestamped and attributed on the
  machine record.
- It fails closed: with no key configured, the deposit is refused and the
  installer falls back to a typed passphrase. A disk is never sealed with a
  passphrase nobody holds.

The LDAP service-account password uses the same key and is write-only in the
form.

## Authorization model / Security

- **Who may provision.** Each org chooses a mode (*any internal user*, *listed
  groups*, …). Whatever the mode, portal users, archived users and users outside
  the org's company are refused. New orgs default to *groups*, which authorises
  nobody until groups are chosen; existing orgs keep the mode they had. The same
  check is replayed at every machine sync.
- **Identity mapping.** A bearer token is mapped to an Odoo user through **one**
  configurable userinfo claim (*Identity claim*: `email`, `preferred_username`
  or `sub`), matched exactly (case-insensitively) against the user's login and
  nothing else. No match, or more than one, is a refusal. An Odoo browser
  session does not open `/me` or `/enroll`. Optionally, *Require install client
  in token* also checks that the access token was issued to the installer's
  OIDC client (needs JWT access tokens).
- **Enrolment.** A machine already enrolled for another user or another org
  cannot be re-enrolled (HTTP 409): knowing a machine's UUID is not enough to
  take it over, rotate its secret or replace its escrowed passphrase. An
  escrowed passphrase is never overwritten, not even by its owner.
- **Counted reveal.** A disk passphrase can only be read through the *Reveal*
  button on the machine record, which counts, timestamps and attributes the
  read. The reveal screen is visible only to the person who opened it, for five
  minutes; after that, pressing the button again is required (and counted).
- **No RPC on service methods.** Service methods such as the authorisation
  check and the policy builder are private: they cannot be called through
  `/web/dataset/call_kw`.
- **LDAP bind password** is only served to orgs whose sign-in mode is sssd/LDAP,
  and then to every authorised installer and enrolled machine of that org (the
  machine needs it for `sssd.conf`): give that directory service account
  read-only rights.

## Requirements

- Odoo 18.0 (Community or Enterprise); depends only on `base` and `web`.
- Python `cryptography` for the escrow and the LDAP bind password (Odoo already
  ships it).
- An OIDC identity provider exposing a userinfo endpoint and the device
  authorisation grant (built and tested against Authentik), and an LDAP outpost
  if you use directory sign-in.
- Workstations running Blue Fox OS. The OS image and its installer are
  maintained separately and are not part of this repository; this module is the
  server side they talk to.
- Companion module: [`bf_zerotouch_install`](../bf_zerotouch_install) serves the
  kickstart that starts the whole flow from the installer's boot menu.

## Configuration

1. Install the module; the **Policy** menu appears for system administrators.
2. Create one **Org Defaults** record per company: domain, identity-provider
   endpoints, sign-in mode, disk and session defaults.
3. To use passphrase escrow, set `BF_POLICY_ESCROW_KEY` (generate it with
   `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`),
   restart Odoo, tick *Escrow disk passphrase*, and grant the reveal group to
   the few people who should hold it. Back the key up outside the database.
4. Optionally add **User Overrides** for people who need something different.

## Browser extensions shipped with the module

`static/extensions/` carries two signed CRX3 packages, *Symbifox Signets*
(company, team and personal bookmarks) and *Symbifox Tokens* (one-time codes,
companion of `bf_otp`). They are Symbifox's own extensions, built from sources
kept outside this repository. Brave only accepts forced off-store extensions on
Linux, which is why this catalogue targets Blue Fox OS machines.

## Changelog

- **18.0.2.11.0** — Access hardening: portal, archived and out-of-company users
  refused in every provisioning mode and at every machine sync; new orgs default
  to group-based authorisation; bearer mapped to a user by one exact-match
  identity claim (ambiguity = refusal), optional token-audience check; no
  re-enrolment of a machine held by another user or org, and an escrowed
  passphrase is never overwritten; passphrase readable only through the counted
  Reveal button, by its opener, for five minutes; service methods private to
  RPC; LDAP bind password served in sssd mode only.

## Licence

Business Source License 1.1 — see [LICENSE](LICENSE). Each version converts to
LGPL-3.0-or-later on its Change Date (2030-09-26 for this version), or on the
fourth anniversary of its first public distribution, whichever comes first.
