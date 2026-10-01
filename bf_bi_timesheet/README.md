# BF BI - Professional services (`bf_bi_timesheet`)

A ready-made dashboard for [`bf_bi`](../bf_bi), with the named measures it is built on.

Professional services dashboard: hours, revenue, labour cost and margin

## Installation

Nothing to do: the module installs itself as soon as `bf_bi` and `hr_timesheet` and `account` are both present (`auto_install`).

## What it ships

- The **Professional services** dashboard, in the *BI* section of the Dashboards app. Its visibility is set by this module and restored on every update; to adapt it or share it differently, use *Duplicate and edit*: your copy is yours, and an update of this module never touches it.
- Its named measures, which any dashboard can reuse with `=BF.MEASURE("code")`. They are shipped once and are not overwritten by updates, so you can adjust them:

| Code | Measure |
|---|---|
| `hours` | Hours entered on projects. |
| `revenue` | Posted customer invoices minus credit notes, before taxes. |
| `timesheet_amount` | Cost of the hours as recorded on timesheets (negative). |
| `labour_cost` | Cost of the hours worked, from employees' hourly cost. |
| `margin` | Revenue minus labour cost. |
| `margin_rate` | Margin as a share of revenue. |

Every figure is computed for the person looking, under their own access rights, and follows the dashboard's *Period* and *Customer* filters. Tiles compare the period with the previous one of the same length (a year to date against the same dates last year), and show the change of a rate or a score in points; a measure with no date, such as a current count, shows no comparison.

## Translations

Source labels are in English; the dashboard's texts are translated when it is read (`i18n/fr_CA.po` ships French).

## License

Business Source License 1.1 (see `LICENSE`); each version becomes LGPL-3 on its Change Date.
