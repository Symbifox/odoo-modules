# Hosting — System updates (`bf_hosting_patch`)

Tracks the update state of every machine in a fleet: pending packages, kernel,
pending reboot, the auto-updater's *mode*, disk headroom and end of support —
then applies updates on command, from a queue the machines poll.

A satellite of `hosting_management`. It lives on `hosting.endpoint`, which
already carries the fleet inventory.

## The rule everything else follows

> **A missing report is an alert, never a silence.**

A machine that stops talking flips to `stale` after 48 hours and surfaces just
like a machine two hundred packages behind. A package count is therefore never
shown without the date of the report that produced it: a "0 packages" from three
weeks ago must not read like a "0 packages" from today.

The rule comes from three mechanisms that all reported "everything is fine"
while measuring nothing: `unattended-upgrades` writing "No packages found that
can be upgraded" with eight packages waiting outside its allowed origins,
`dnf5-automatic` stopping at "Packages downloaded", and a Docker version
collector returning "0 checked" inside a `WARNING` nobody reads.

The same idea runs through the code. When a package manager fails — a broken
repository, a held `dpkg` lock — the agent reports **"count unknown"** rather
than an empty list that would read as zero. A machine must not go green by
failing to measure.

## The shape of the link: the machine talks, the centre listens

Odoo has **no** path to the machines, and wants none. A `symbifox-hostd` agent
reports locally and POSTs with a revocable bearer token.

What that solves: NAT (a containerised Odoo often cannot even reach its own host
by its public name), the roaming laptop that is only reachable while it is
awake, and the attack surface — a compromised Odoo does not hold an SSH key to
the whole fleet.

The token authenticates **a machine**, never a person. It can only file a report
against the record it names: **the agent cannot create a fleet record**, only
fill one a human created.

## Machine, or installation?

A dual-boot laptop has **one** `/etc/machine-id` per operating system, so the
machine-id identifies the *installation*, not the machine. The model splits in
two:

- `hosting.endpoint` describes the **machine** — serial, warranty, purchase,
  licence seats, and the DMI board UUID read once at enrolment (it is root-only,
  and it never changes).
- `bf.patch.system` describes an **installation** — its machine-id, kernel,
  package manager, pending packages, end of support, agent and token.

A machine's state is the **worst** of its systems. One whose Linux side is up to
date and whose Windows side is silent is not "up to date": it is silent. That is
the only aggregate that hides nothing.

## The seven readings that pay off daily

| Field | Why this one |
|---|---|
| `machine_id` | `/etc/machine-id`. The hostname lies, this does not. A uniqueness constraint stops duplicates re-forming and makes enrolment idempotent. |
| `auto_update_mode` | The **mode**, never the presence. A machine can have an auto-updater configured and still apply nothing; a boolean would answer "yes" and be misleading. |
| `reboot_pending_since` | A boolean sorts nothing, a date does. Read, never inferred: `/var/run/reboot-required` on Debian and Ubuntu, `dnf needs-restarting -r` on Fedora, a kernel comparison on Arch. |
| `kernel_running` / `kernel_installed` | Two distinct fields. The gap between them is what reveals a reboot owed for days. |
| `disk_root_pct` / `disk_boot_pct` | A `/` at 92 % is the most ordinary way to fail an update halfway. The percentage follows `df`'s convention, so it matches what a human sees in a terminal. |
| `os_support_end` | **Read from the machine**, never guessed: `SUPPORT_END` in `os-release` on Fedora, `distro-info` on Ubuntu. A rolling release has no date, and that absence is the information. |
| `hosted_service_count` | The blast radius. Rebooting a machine carrying seventy services and one carrying two are not the same gesture. |

Plus `pending_delta`: "3 more than yesterday" reads better than "153".

## Applying updates on command

Odoo pushes nothing. It drops an order into `bf.patch.job`, and the agent picks
it up on its next poll — so a machine that is switched off does not miss the
order, it takes it on waking.

**Three consents, and all three are required:**

1. An identified human creates the order in Odoo (`requested_by`).
2. The server refuses to hand it over unless the machine's last report says
   `apply_allowed`.
3. The agent refuses to run it unless `/etc/symbifox/apply-allowed` exists on the
   machine, whatever the server answered.

Only the third really matters: it is **local**, placed by hand, and revoked
without going through Odoo. A compromised Odoo cannot brick a laptop. The first
two exist so an operational mistake is caught before it reaches a machine.

An order carries a scope (`security`, `all`, `named`), an optional package list,
a reboot policy (`never`, `if_required`, `always`) and an optional start window.
The server stops offering an unclaimed order after 7 days; the agent refuses to
run one older than 24 hours — a machine woken after three weeks must not apply a
decision taken in another world.

**Refusals are explicit rather than reinterpreted.** On Arch, a `security` scope
is refused because the repositories carry no security channel: quietly applying
"everything" instead would be doing something other than what was asked. A
`named` scope is refused there too, since partial upgrades break dependencies.
Doing nothing and saying so beats doing something else in silence.

## Installing the agent on a machine

1. Create (or open) the machine's record in **Hosting → Fleet**.
2. **Updates** tab → *Generate an enrolment code*. It is good for 30 minutes and
   for **one** system — a dual-boot laptop needs two, one per side.
3. On the machine, as root:

```sh
./agent/install.sh https://odoo.example.com THE-CODE
```

The installer drops `symbifox-hostd`, its unit and timer, exchanges the code for
a token, and files a first report. Remote application stays closed until
`/etc/symbifox/apply-allowed` exists; create it by hand, then enable
`symbifox-hostd-poll.timer`.

Requirements: `python3` and nothing else. The agent uses only the standard
library, listens on no port, and does not run continuously.

## What the report does not do

Reporting is **strictly read-only**: `apt list --upgradable`, `dnf check-update`,
`checkupdates`, `uname -r`, a `df`, reading `/etc/os-release`. None of these
change the machine, and none needs privileges.

⚠️ The reporting unit is sandboxed (`ProtectSystem=full`), the applying unit is
**not** — installing packages writes to `/usr` and `/var`, and a stricter
sandbox would make `dpkg` fail in a way that looks like an unreachable
repository. What holds the line there is the consent file, not the sandbox.

## Cadence

| Gesture | Cadence |
|---|---|
| Report | daily, plus on boot, spread over 15 minutes |
| Poll for orders | every 15 minutes (only where consent exists) |
| Flip to `stale` | 48 h without a report (cron every 4 h) |
| Purge reports | 90 days |
| Expire unclaimed orders | 7 days |

## API

```
POST /symbifox/patch/v1/enrol    {code, machine_id}    -> {token}
POST /symbifox/patch/v1/report   Bearer <token>        -> files a report
GET  /symbifox/patch/v1/ping     Bearer <token>        -> the link is alive
POST /symbifox/patch/v1/poll     Bearer <token>        -> "anything for me?"
POST /symbifox/patch/v1/result   Bearer <token>        -> outcome of an order
```

Any value outside the controller's field table is ignored silently: the agent may
run ahead of the server without breaking it. An unknown selection value falls
back to "unknown", which is the truth, rather than raising. An order's result can
only be filed by the system that owns it, and a terminal state is never reopened.

## Tests

```sh
odoo -d <db> -u bf_hosting_patch --test-enable --test-tags=/bf_hosting_patch \
     --stop-after-init --db-filter='^<db>$' --http-port=8169
```

71 tests, weighted towards the unhappy paths: missing token, revoked token,
unreadable body, oversized body, invented selection value, duplicate machine-id,
the flip to `stale` as time passes with nothing written, an order handed to the
wrong machine, an order without local consent, and a terminal state an agent
tries to rewrite.

## Licence

LGPL-3.0-or-later. See `LICENSE`.
