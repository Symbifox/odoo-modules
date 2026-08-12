# Hour bank (`bf_hour_bank`)

An Odoo 18 module for automated tracking of client hour banks.

## Features

- **Per-client configuration**: included projects, company/partner/product filters, report recipients
- **Automatic balance calculation**: debits (timesheets) + credits (invoices) + manual adjustments
- **Manual adjustments**: credits or debits outside invoices and timesheets (rebilling, corrections, journal entries)
- **PDF report**: Symbifox branded, with banner, entry table (credits in green), per-project summary and monthly summary
- **Excel report**: 4 sheets (Timesheets, Per-project summary, Monthly summary, To be invoiced)
- **Email delivery**: a wizard with the PDF and Excel attached, in a Symbifox branded email
- **Preview**: PDF preview directly from the sending wizard
- **Automatic sending**: configurable cron (weekly, fortnightly, monthly) sending a branded report to the recipients
- **Notification thresholds**: proactive email alerts when consumption crosses a threshold (unbilled hours, % of an allocated budget, or balance below a floor) with the XLSX attached
- **Client portal**: a `/my/hour-banks` page for portal users, with self-service PDF/Excel download

## Dependencies

- `project`, `account`, `hr_timesheet`, `mail`, `portal`
- `bluefox_branding` (report header and brand palette)
- `bf_onboarding_base` (guided welcome panel)
- `openpyxl` (Python, for Excel generation)

## Calculation logic

```
Debits      = timesheets on the configured projects (account.analytic.line)
Credits     = posted customer invoice lines (account.move.line)
              filtered by company + partner + product (all optional)
Adjustments = manual entries (positive = credit, negative = debit)

Balance = Σ credits + Σ adjustments - Σ debits
```

Entries are shown newest first in the PDF, the Excel file and the portal.

### Billing filters

Three independent levels of filtering are available in the Billing tab:

#### Company filter

| Mode | Behaviour |
|------|-----------|
| **All companies** (default) | No filtering by company |
| **Include only** | Only invoices from the selected companies count |
| **Exclude** | Every invoice except those from the selected companies |

#### Billing partner filter

An optional field to target specific partners instead of the automatic
`commercial_partner_id`. Useful when one commercial client has several billing
contacts (for example, an internal reorganisation). Left empty, the default
behaviour (every invoice of the commercial partner) is kept.

#### Product filter

| Mode | Behaviour |
|------|-----------|
| **All lines** (default) | Every invoice line of the client is included |
| **Include only** | Only lines carrying the listed products count |
| **Exclude** | Every line except those carrying the listed products |

### Report columns

| Column | Description |
|--------|-------------|
| Date | Date of the operation |
| Hours | Hours (negative = debit, positive = credit) |
| Running balance | Cumulative balance at that date |
| Description | Timesheet line name or invoice number |
| Project | Project name, or "Billed hours" / "Adjustment" |
| Task | Task name (timesheets only) |

## Structure

```
bf_hour_bank/
├── models/
│   ├── hour_bank_client.py            # Configuration + report generation + thresholds
│   ├── hour_bank_adjustment.py        # Manual adjustments
│   ├── hour_bank_threshold_line.py    # Threshold configuration
│   └── hour_bank_threshold_event.py   # Append-only alert history
├── wizard/
│   └── hour_bank_send_wizard.py       # Email delivery (branded)
├── controllers/
│   └── portal.py                      # Client portal (/my/hour-banks)
├── report/
│   ├── hour_bank_paperformat.xml      # US Letter paper format
│   └── hour_bank_report_templates.xml # QWeb PDF template
├── views/
│   ├── hour_bank_client_views.xml     # Form, list, search
│   ├── hour_bank_threshold_views.xml  # Views for hour.bank.threshold.event
│   ├── hour_bank_portal_templates.xml # Portal pages
│   └── menu_views.xml                 # Application root menu
├── data/
│   ├── hour_bank_mail_template.xml
│   └── hour_bank_cron.xml             # Crons: periodic reports + thresholds
├── security/
│   ├── hour_bank_security.xml         # Access rules (internal + portal)
│   └── ir.model.access.csv
├── tests/
│   └── test_thresholds.py             # 13 unit tests on the thresholds
└── i18n/
    └── fr_CA.po
```

## Access

### Backend (internal)
- **Hour bank > Clients** (a dedicated application, with its own icon)
- Read: `project.group_project_user`
- Full management: `project.group_project_manager`

### Client portal
- **My account > Hour bank** (`/my/hour-banks`)
- Access: `base.group_portal` (restricted to the client's `commercial_partner_id`)
- Available routes:
  - `/my/hour-banks` — list of hour banks
  - `/my/hour-banks/<id>` — detail with table, balance, per-project summary
  - `/my/hour-banks/<id>/pdf` — PDF download
  - `/my/hour-banks/<id>/xlsx` — Excel download

## Automatic sending (cron)

The `Hour bank: automatic report sending` cron runs daily at 08:00 and sends
reports according to the configured frequency:

| Frequency | Trigger |
|-----------|---------|
| Weekly | Every Monday |
| Fortnightly | Mondays of even ISO weeks |
| Monthly | 1st of the month |

## Notification thresholds

A **Thresholds** tab on the bank record. Three mutually exclusive modes,
disabled by default.

### Modes

| Mode | Measure | Rearming |
|------|---------|----------|
| **Unbilled hours** | Sum of debits since the client's last posted invoice | A new posted invoice |
| **% of allocated budget** | `unbilled hours / allocated budget × 100` | A change to `Allocated budget (h)` on the bank |
| **Remaining balance below a floor** | Current cumulative balance | The balance rising back above `floor + 0.5h` (hysteresis) |

The floor may be **negative**, which is the useful form on a postpaid account:
there the client buys nothing up front, hours pile up as a debt and each invoice
lifts the balance back towards zero. A floor of `-12` alerts once the debt passes
twelve hours, and rearms on the next invoice. Prefer it over **Unbilled hours**
in that setup: unbilled counts from the last credit entry only, so any hours an
invoice did not cover are dropped from the measure and the alert lands late by
that residual. Zero is rejected, so that a threshold line saved without a value
cannot silently alert on the first empty balance. Below zero the line reads
"Dette de 12.0h" (debt of) rather than "balance below -12.0h", which is also the
wording the client sees in the alert subject.

### Configuration

1. Choose the **Threshold mode**
2. For `% of budget`: fill in **Allocated budget (h)**
3. Add one or more **Thresholds to watch** lines (the `value` field — hours or
   percentage depending on the mode)
4. **Alert recipients**: leave empty to reuse the periodic report's recipients,
   or set a distinct Many2many for specific alert recipients
5. **Notify internal followers** (on by default): also posts a `message_notify`
   on the chatter so the bank's internal followers are warned

### Triggering

- The **`Hour bank: check notification thresholds`** cron, daily at 08:30 UTC
- A manual **"Check thresholds now"** button in the form header
- For each active line: if the current measure reaches the threshold AND the
  line is armed (a period key different from the last trigger), a branded email
  is sent with the XLSX attached

### Audit

Every trigger creates an append-only `hour.bank.threshold.event` record with a
snapshot of the mode, the threshold value, the measured value, the period key,
the recipients, the XLSX attachment and a link to the chatter message. An
**"Alerts sent"** smart button on the form opens the filtered list.

### Behaviour when switching thresholds on

If you enable thresholds on a bank that is already past several of them,
**every crossed threshold fires on the next check**. The cascade is logically
correct but can be noisy. To avoid it when configuring mid-engagement: add the
thresholds with `active=False` first, or plan to absorb the initial batch in the
next day's cron.

### Effect on install/update

Installing or updating the module **creates no reminder, no activity and no
email** on existing banks: the default `threshold_mode` is `disabled`, and the
cron already filters on `threshold_mode != 'disabled'`. To switch the feature
on, the operator has to configure each bank manually.

## Installation

```bash
docker exec <container> odoo -d <db> -i bf_hour_bank --stop-after-init --no-http
```
