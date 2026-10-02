# BF BI Reports (`bf_bi_report`)

Reports read like a Power BI report, inside **Odoo 18 Community**, on top of
[`bf_bi`](../bf_bi)'s named measures.

> Version 1.0: reading, on-screen design, drill-through pages, page filters,
> rich visuals, company theme and ready-made reports.

## What a report does

- **Slicers at the top.** Period buttons (month, quarter, this year, last
  year) and the customers that have data, as chips. Every visual follows them.
- **Cross-filtering.** Click a bar, a slice or a table row: the whole page
  filters on that customer or that month. The visual you clicked keeps its data
  and dims the other marks, as in Power BI. Esc or the × clears it.
- **Tiles** with the value, a 12-month sparkline, the change against the
  previous period of the same length (1 January to today against the same dates
  last year), and an optional target bar. Rates and scores move in points.
- **Charts and tables**: column, bar, line and donut charts (Chart.js, which
  ships with Odoo), and tables with data bars and colored rates against a target.
- **Gauge**: one value on a half circle, with a target mark and a maximum
  (automatic when left empty).
- **Waterfall**: what explains the change. By customer, it starts from the
  previous period of the same length, adds each customer's change (largest first,
  a lost customer counts as much as a new one, the rest grouped as *Others*) and
  ends on the current period: the bars always add up. By month, quarter or year,
  each bucket adds to the previous one up to the total. Only measures that add
  up (sums, counts, and formulas made of them) can be shown this way; a rate or
  an average is refused with the reason. Left untitled, it reads “Revenue: what
  changed” in each reader's language.
- **Heat map**: the strongest customers by month or by quarter, each cell shaded
  on one hue from light to dark, the value always written in it. Click a month to
  filter the page on that month, a customer to filter on that customer.
- **Theme**: *Symbifox* or *Company colors*. The company's brand colors (Settings ›
  Companies › Document layout) lead the palette, kept recognizable but adjusted
  so marks stay visible; a gray brand color is left to text, and default colors
  too close to a brand color are dropped.
- **Pages** shown as tabs at the bottom.
- **Drill-through.** A page can be a *detail page* for one customer. Right-click
  a customer (a bar, a slice or a table row) and choose *Drill through › Customer
  detail*, or use the *Detail ›* button of a customer table: the detail page
  opens filtered on that customer, with a *Back* button and a list to switch to
  another customer. Opened from its tab, it asks for a customer first.
- **Page filters.** A page can always show one period (say, last year) or a set
  of customers, whatever the slicers say; the slicer bar tells the reader so.

## Designing a report on screen

BI designers switch the report to **Design**:

- **Report theme**: Symbifox or the company's colors, applied at once.
- **Visualizations pane**: add a visual and choose its type (tile, column, bar,
  line, donut, table, gauge, waterfall, heat map).
- **Visual fields**: title, **Axis** (month, quarter, year or customer) and
  **Values** (named measures; a table takes several), a target for tiles and
  tables, the number of customers shown. Drag a field from the **Fields** pane
  into a well, or click it to add it to the selected visual. A warning tells you
  when a measure cannot follow the period or be split by customer.
- **Canvas**: drag a visual to move it, use its corner handle to resize it, on
  a 12-column grid. A visual can stay unfinished; readers see it as such.
- **Pages**: rename, add and remove pages; make a page a customer detail page,
  fix its period, or fix its customers (none chosen: it follows the slicer).
- **Saving** is automatic. If a colleague saved the report meanwhile, nothing is
  overwritten: you reload their version or keep yours. The **History** keeps the
  last 30 versions; restoring one makes a new revision.
- *New report* in the list creates a report with one page and opens the designer.

## Ready-made reports

BI designers click **From a template** in the list of reports. Each template is
offered once the `bf_bi` bridge it reads is installed; the dialog says what is
missing otherwise. The report is created with its name in every installed
language, visible to BI designers until opened to others in its form.

| Template | Needs | Shows |
|---|---|---|
| Management report | the four bridges | the figures of time, customer experience, hosting and hour banks on one page, what changed in revenue, hours by customer and month, customer detail page |
| Time and margin | `bf_bi_timesheet` | hours, revenue, margin and margin rate, by month and customer, what changed in revenue, customer detail page with a margin gauge |
| Customer experience | `bf_bi_cx` | NPS and complaints by month and customer, customer detail page |
| Hosting | `bf_bi_hosting` | services, backups and domains, backup success against a 95 % target, failed backups by customer and month |
| Hour banks | `bf_bi_hour_bank` | hours bought and adjusted, by month and customer, what changed |

A created report is an ordinary report: change it on screen like any other.

## How values are computed

A visual's values are always **named measures** of `bf_bi`, computed for the
person looking, **under their own access rights**, never as superuser. A visual
splits a measure by month, quarter, year or customer by adding one term to the
filter of each base measure, then calls the same computation as a dashboard
cell. A measure the person cannot read shows an error in that visual only.

Customers are ranked by the first measure; the rest is grouped as *Others*.
Date-time fields follow the person's time zone.

## Access

- Readers see the reports opened to one of their groups; a report with no
  group is visible to BI designers only. Pages follow their report.
- BI designers (group from `bf_bi`) create and edit reports.
- Reports belong to a company and follow the active companies.

## Translations

Source labels are in English; French comes from `i18n/fr_CA.po`.

## License

Business Source License 1.1 (see `LICENSE`); each version becomes LGPL-3 on its
Change Date. Depends on `bf_bi` only.

Power BI is a trademark of Microsoft Corporation. This module is not affiliated with or endorsed by Microsoft.
