# Symbifox Color (`bf_color`)

Free colors for Odoo records, resolved for each person: your own color, the
company's color, automatic rules, then the record's own color. Other modules
plug into it with one mixin.

## What it does

- **Any color, not just twelve.** Records carry a hex color (`#RRGGBB`). For
  views that only understand Odoo's 0–11 palette (kanban stripes, many2many
  tags, calendar filters), the module computes the **closest palette index**,
  and a **readable text color** (black or white) for the chosen background.
- **Resolution, strongest first:**
  1. your own color for this record;
  2. the company's color for this record (set by an administrator);
  3. the first **automatic rule** that colors the record;
  4. the record's own color, or the hex of its existing Odoo `color` index;
  5. no color.
  Existing `color` indexes are read as they are: installing the module
  rewrites no data.
- **Swatches.** Personal swatches, private to their owner, and swatches shared
  with the company. "Keep in my colors" adds a picked color to your first
  personal swatch.
- **Automatic rules.** Color the records of a model from the value of one of
  their stored fields: a many2one, a many2many, a selection, a text, an integer
  or a boolean. Only models wired to the mixin can be chosen, so a rule never
  saves and then colors nothing. Within a rule, a record takes:
  1. the first **line** of the rule that matches (with several values, such as
     tags, the highest line wins: drag the lines to set the order);
  2. otherwise the **value's own color**, when the value is itself a colored
     record: a doctor's color set once on the employee shows on every agenda
     sorted by employee, and your own color for that doctor follows you;
  3. otherwise a color from the **fallback swatch**.
  "Listed values only" limits a rule to its lines (color the tasks tagged
  *Business development*, leave the others alone).
- **Colors that stay put.** With a fallback swatch, a new value (a new doctor)
  is given the **least used** color of the swatch, written on the value. Nothing
  already given moves when the swatch grows, and two of three doctors do not
  end up sharing a color by chance, as a hash would. "Assign missing colors"
  on a rule does the same for the values already in use. The default palette
  is colour-blind safe (Okabe-Ito, then IBM Carbon's categorical colors).
- **Color picker** in the web client: swatches, Odoo's palette, a free hex
  value, "My color" for everyone, and "Company color" for administrators.
- **Calendars and kanban cards.** A calendar view that loads `color_resolved`
  paints each event in its free color, softened like Odoo's own palette with
  readable text, in the detail popover and the filter legend too; an event
  without one keeps the view's color. A kanban with `highlight_color` that loads
  `color_resolved` paints the card's edge the same way.
- **Contact tags** (`res.partner.category`) are wired here. In a form, clicking
  a tag opens "My color" (and "Company color" for administrators) instead of
  the 12-color list. Bridges wire project tags, tasks and projects
  (`bf_color_project`), CRM tags and opportunities (`bf_color_crm`) and
  employees (`bf_color_hr`).

## For module developers

Inherit `bf.color.mixin` on a model to get:

| Field | Meaning |
|---|---|
| `color_hex` | the record's own color, for everyone |
| `color_resolved` | the color shown to the current user |
| `color_resolved_index` | closest Odoo palette index (0–11) |
| `color_text` | readable text color on `color_resolved` |
| `color_source` | `user`, `company`, `rule`, `record` or empty |

and the methods `bf_color_set_mine`, `bf_color_clear_mine`,
`bf_color_set_company` and `bf_color_clear_company` (the last two for
administrators only). Override `_bf_color_palette()` to read a model's own
palette for its `color` index.

## Access rights

- Personal colors and swatches are visible to their owner only.
- Company colors and shared swatches are read by every internal user and
  changed by administrators only.
- Automatic rules are read by internal users and managed by administrators,
  per company.

## Requirements

Odoo 18 (`base`, `web`). No external Python dependency.

## Licence

LGPL-3. See `LICENSE`.
