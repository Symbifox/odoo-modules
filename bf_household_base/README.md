# Household Base (`bf_household_base`)

The small common ground of a Symbifox Personal household instance: who belongs to the
household, how many accounts it may have, and the one thing a household account can never
be. Labels in English, French (Canada) in `i18n/fr_CA.po`. LGPL-3.

It depends on `base` only. `bf_household_family` (members, children, family papers,
emergency cards) builds on it. On Symbifox Personal, a household profile that is not part of
this repository extends the group with the personal and shared apps of the hosted instance.

## What it does

- **Household user** group: an internal account of a person of the household. It implies
  nothing but internal access; each household module extends it with the apps it opens.
- **Never an administrator**: a household user can hold neither Settings
  (`base.group_system`) nor Access rights (`base.group_erp_manager`). A system administrator
  can become the superuser and read every private app of the household; access-rights
  managers can give themselves any group. The two groups that read every message of the
  household are refused too when their modules are installed
  (`bf_email_management.group_email_admin`, `bf_sms_archive.group_sms_manager`). The check
  reads the groups stored on the account (with the groups they imply), never the
  `has_group` cache, so an account that already holds a refused group can still be repaired.
  It runs on `res.users` (users screen, invitation, import, RPC) and again when the groups
  screen writes a group's users or implied groups. A module whose group sees a whole private
  class through a group rule adds it to the list by overriding
  `res.users._household_forbidden_groups()`.
- **At most ten accounts**: a write that brings an eleventh active internal household account
  into the count is refused, whatever the path, reactivation and the groups screen included.
  A household already above the cap (after the cap was lowered) stays manageable: removing a
  group, naming a manager or archiving an account still works. Portal users, archived
  accounts and accounts outside the group (the instance administrator, for one) do not
  count. Two invitations sent at the same moment cannot both pass: the check writes the
  group's row, so the second transaction fails; from the web client or RPC, Odoo retries it
  and it counts the first (from a cron or a script, it simply fails). The system
  parameter `bf_household_base.max_users` changes the cap for one instance (a number;
  anything else keeps 10 and logs a warning).

## Upgrading an existing household

Before this module, the category and the group lived in the household profile, a module of
the hosted service
(`bf_household_profile.module_category_household`, `bf_household_profile.group_household_user`).
On installation, a `pre_init_hook` renames those identifiers to this module before its data
loads: the existing group keeps its members, rules and implied groups, and no second group
appears. The former cap parameter `bf_household_profile.max_users` is carried over the same way.
If both identifiers already exist, the installation stops rather than guess. The group's
comment keeps its English text only, so this module's catalogue gives it its French.

## Technical

| Item | Kind | Purpose |
|---|---|---|
| `group_household_user` | `res.groups` | The household person; implies `base.group_user` |
| `module_category_household` | `ir.module.category` | "Household" category of the groups |
| `res.users._household_forbidden_groups()` | method | xmlids a household user may never hold (extensible) |
| `res.users._household_max_users()` | method | The cap, from `bf_household_base.max_users` (default 10, at least 1) |
| `res.users._household_seats_taken()` | method | Active internal accounts of the group |
| `res.users._household_check_all()` | method | Guards replayed by the groups screen (extensible) |
| `res.groups.write()` / `create()` | override | Replays both guards when `users` or `implied_ids` change |

Tests: `tests/test_household_base.py` (both guards, run as administrator: they are
model-level checks and hold for the superuser too).
