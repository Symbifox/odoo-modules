# Irregularities (`bf_irregularity`)

A shared registry for the things that are quietly wrong in a database: a record
that is late, out of process, hazardous, silent, orphaned or simply abnormal.
Each kind of wrong is a **detector** — a named, tunable rule — and every detector
writes its findings into one list that people can acknowledge, mute or resolve.

This module is the base. It ships the registry, the detector model, the hourly
run and four detectors over `mail.activity` and the mail queue. Other modules add
their own detectors through a bridge, instead of each one writing its own
scheduled job and its own notification.

## What it does

- **`bf.irregularity.detector`** — a rule: a code, a family, a severity, an
  optional threshold with its own label, a fallback owner, and whether a new
  finding should schedule a to-do. A detector is just data: it can be disabled,
  retuned or reassigned without touching code.
- **`bf.irregularity`** — one finding: what it is, which record it points at,
  who owns it, when it was first and last seen, and its state (open,
  acknowledged, muted, resolved). A finding is unique per detector and key, so a
  detector that runs every hour updates rather than duplicates.
- **Self-resolving.** A finding whose key is no longer detected resolves itself.
  A detector that only scans a window returns the keys it checked, so findings
  outside that window are left alone rather than wrongly closed.
- **Hourly run** (`Irregularities: run detectors`) over every active detector. A
  detector that raises is caught, its error is kept on the detector, and the
  others still run.
- **Four detectors out of the box**: an activity overdue past its threshold, an
  activity assigned to an archived user, a scheduled message that never left,
  and an outgoing email in failure.

## What it does on a fresh install

The hourly job is **active from the start** — that is the point of the module —
and it only writes to its own registry. Nothing is emailed, and no activity is
scheduled unless you tick **Schedule an activity** on a detector, which is off
on all four shipped detectors. To stop the detection entirely, deactivate the
scheduled action; your change survives upgrades (the job ships `noupdate`).

## Who sees what

- Internal users read and write their **own** findings, by a record rule on
  `owner_id`. Administrators see all of them.
- Detectors are read-only for internal users: only `base.group_system` can
  create, retune or run them. `action_run` checks that write right itself, since
  a public method is reachable over RPC whatever the menu shows.
- A finding names the record it points at, and that name is resolved with
  elevated rights — the registry can therefore name a record its reader could
  not open on their own. A reader only ever sees findings owned by them, and
  opening the record itself goes through the normal access rules.

## Adding your own detector

Declare the rule as data, then add the method that finds it:

```python
class BfIrregularityDetector(models.Model):
    _inherit = "bf.irregularity.detector"

    def _detect_my_rule(self):
        records = self.env["my.model"].sudo().search([...])
        return [
            {"record": rec, "message": _("What is wrong"), "owner": rec.user_id}
            for rec in records
        ]
```

Return a list of findings, or a tuple `(findings, checked_keys)` when you only
scanned a window. `key` defaults to `model,id`.

## Dependencies

`mail`. The French translation is in `i18n/fr_CA.po`.
