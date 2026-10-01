# BF BI (`bf_bi`)

Design and edit dashboards on screen in **Odoo 18 Community**. Odoo's Dashboards
app ships a reader only; `bf_bi` adds the designer, named measures computed under
each reader's own access rights, and external sources. The reader stays Odoo's
own: filters, drill-down, mobile display.

## Features

- **Editor.** Odoo's spreadsheet engine (o-spreadsheet) in edit mode. It saves
  itself after 1.5 s of inactivity. If someone else saved in the meantime,
  nothing is overwritten: you choose between reloading their version and
  replacing it (theirs stays in the history).
- **Add to a dashboard.** A button in every pivot and graph view: build the view
  (measures, groupings, filters) and send it to a dashboard as a pivot table, a
  chart or an indicator. Everything lands on the first sheet, the only one the
  reader displays, in three bands: figures (charts, tiles), fixed values, then
  variable blocks (pivot tables, source tables). A block's height depends on who
  is looking, so each gets a reserved area of 50 rows and its formula is capped
  (`=PIVOT(id, 46)`, `=BF.SOURCE(…, 49)`): it can never spill onto the next one.
- **Filters created for you.** Community has no screen to create dashboard
  filters. The first insertion creates *Period* (current year by default) and
  *Customer*, and every later one is linked to them (the grouping's date field,
  otherwise a usual date field; the partner field when there is one).
- **Indicators.** A tile with one big number compared with the previous period.
  A tile built from a pivot view compares with the whole previous period; a
  tile built from a named measure compares with the previous period of the
  **same length** (1 January to today against 1 January to the same date last
  year), so a year to date is not compared with a whole year. Rates and scores
  can show their change in points rather than in percent.
- **Filters that follow the reader.** A filter such as *My timesheets* keeps its
  variable (`uid`) and shows each person their own lines.
- **Access.** A *BI Designer* group. A new dashboard is visible to designers only
  until it is opened to other groups. Figures drawn from Odoo are re-read under
  the rights of each person who opens the dashboard.
- **Versions.** The last 30, readable only by designers who can see the dashboard;
  *Restore* makes a new revision out of an old one.
- **Shipped dashboards.** Dashboards provided by a module are rewritten on every
  update; *Duplicate and edit* gives you a copy that stays yours.
- **Public link.** Odoo's *Share* button (a public snapshot) is reserved to the
  *Share a dashboard by public link* group.

## Named measures

*Dashboards › Named measures* (designers). A measure is either:

- an **aggregate**: a model, a field (or the record count), sum, average, min,
  max or count, a filter (which may use `uid`), and the date and customer fields
  the dashboard's filters apply to; or
- a **formula**: codes of other measures with `+ - * /` and parentheses
  (`revenue - costs`, `billable / hours`). Nothing else is accepted, cycles are
  refused, and a division by zero gives *no value* rather than an error (a margin
  rate over a period with no revenue).

Use them with `=BF.MEASURE("code")`, or *Insert a measure* in the editor (tick
one or more, as tiles or as values in cells). `=BF.MEASURE("code", -1)` gives
the previous period of the same length. A value is **always computed for the
person looking, under their own rights** (never as superuser), and follows the
*Period* and *Customer* filters. Filters coming from the browser can only
narrow the result, and are validated term by term.

## External sources (Grist)

A **data connection** (*Dashboards › Configuration › Data connections*,
administrators) describes a source: a Grist document and its API key.

- **Closed by default**: until a group or a person is allowed, nobody sees its
  data.
- In a dashboard, `=BF.SOURCE("code", "Table")` (or *Insert a source* in the
  editor) spills the table, header included. Dates stay dates, lists become
  text, Grist's internal columns are left out, and a row cap is reported.
- Every read is made **on behalf of the person looking**: the server checks
  that they are allowed on the connection before calling Grist. Without the
  right, the cell shows an error, never the figures. An unknown code and a
  refused one give the same message.
- The API key is visible to administrators only, and is never sent to the
  people reading dashboards; Grist's error bodies are not passed on.
- 60-second cache per table, cleared as soon as the connection is changed.

## Ready-made dashboards

Template modules add a dashboard and its named measures to the *BI* section,
and install themselves when `bf_bi` and the module they read are both present
(`auto_install`):

| Module | Dashboard | Requires |
|---|---|---|
| [`bf_bi_timesheet`](../bf_bi_timesheet) | Professional services | `hr_timesheet`, `account` |
| [`bf_bi_hour_bank`](../bf_bi_hour_bank) | Hour banks | `bf_hour_bank` |
| [`bf_bi_hosting`](../bf_bi_hosting) | Hosting | `hosting_management` |
| [`bf_bi_cx`](../bf_bi_cx) | Customer experience | `bf_cx` |

Their measures are shipped once and never overwritten, so they can be adjusted.

## Where to find it

- *Dashboards* › the pencil next to a dashboard (designers);
- *Dashboards › Design*;
- any pivot or graph view › *Add to a dashboard*.

## Translations

Source labels are in English; French comes from `i18n/fr_CA.po`. The texts
`bf_bi` places in a dashboard (the *Period* and *Customer* filters, tile
legends) follow the language of the person reading, on every dashboard.

## Dependencies and license

Depends on `spreadsheet_dashboard` (Odoo, LGPL-3) only. No third-party code is
copied into this module. Business Source License 1.1 (see `LICENSE`): each
version becomes LGPL-3 on its Change Date.
