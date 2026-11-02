# Daily To-Do Digest

An Odoo 18 module that automatically emails a daily digest containing the
user's activities and tasks, the local weather and an inspirational quote.

## Features

### Digest content

| Section | Description |
|---------|-------------|
| **Weather** | Current temperature with emoji, min/max, precipitation for the configured city (default: Montreal) |
| **Today's calendar** | `calendar.event` records of the recipient's day that they attend (declined and cancelled events left out), with times in their time zone and in their secondary calendar time zone |
| **Overdue activities** | `mail.activity` records with a past due date |
| **Today's activities** | `mail.activity` records due today. Reminders created by a scheduler fold into **one line per model**, with their count and a link to the list |
| **Overdue tasks** | `project.task` records with a past deadline |
| **Today's tasks** | `project.task` records due today. Tasks waiting on a client or a colleague leave the list and are counted in one line, unless `include_waiting_tasks` is on |
| **Upcoming tasks** | Tasks due in the next 1 to 7 days, when `include_upcoming_tasks` is on |
| **Watched subjects** | What the RSS lists that watch a name retained since the last digest, read from the RSS module **at runtime** (no manifest dependency), and only for lists that carry watched subjects |
| **7-day outlook** | A clickable visual grid of the next 7 days with colour-coded counters (green/amber/red) |
| **Hidden tasks** | A summary of tasks with `display_in_project=False` (clickable link) |
| **Inspirational quote** | A random quote from 120 artists, poets and thinkers |

### Technical characteristics

- **Time zone**: every date is read in the **recipient's** own time zone (`res.users.tz`), including the day boundaries, the delivery hour and the "already sent today" guard — so one template delivers at the local morning hour for recipients in different zones
- **Visibility filter**: excludes tasks with `display_in_project=False` from the detailed listing
- **Symbifox branding**: HTML template using the brand's colours and typefaces
- **Clickable links**: every task and activity carries a direct link to its Odoo record. A link to a **filtered list** (a folded line, the waiting tasks, the hidden tasks, a day of the grid) points at a window action whose domain lives server-side and is evaluated with the identity of whoever clicks: Odoo 18 reads no domain from a URL, so a `&domain=` in an email filters nothing
- **Per-recipient preferences**: each person sets their own delivery hour, their own rhythm (daily, weekdays, chosen days) and each section to yes, no or "whatever the template says", from their own user form — no administrator needed
- **Templates target groups**: a template can aim at `res.groups` instead of naming people. When several templates cover the same person, the first by sequence wins and that person receives exactly **one** digest
- **Configurable cron**: checks hourly, sends at the configured hour
- **Email preheader**: a quick preview in mail clients (e.g. "3 overdue | 5 today | ☀️ -8°C")
- **Weather emoji**: visual icons matching the conditions (☀️🌧️❄️⛈️ and so on)

## Installation

1. Copy the module into the `addons` directory
2. Update the module list in Odoo
3. Install "Daily To-Do Digest"

```bash
# Update and install
docker exec <container> odoo -d <database> -i daily_todo_digest --stop-after-init
```

## Configuration

### Where

**Settings → Technical → Daily digest → Configuration**

### Available settings

| Field | Description | Default |
|-------|-------------|---------|
| Name | Digest name | "My daily digest" |
| Send hour | Hour of sending (0-23, America/Montreal time zone) | 4 |
| Recipients | Users who will receive the digest | - |
| Company | Optional company filter (not currently used) | Current company |

### Toggleable widgets

| Widget | Description |
|--------|-------------|
| Calendar events | Include the day's calendar events |
| Overdue activities | Include past activities |
| Today's activities | Include today's activities |
| Overdue tasks | Include past tasks |
| Today's tasks | Include today's tasks |
| Weather | Include the local weather |
| Inspirational quote | Include a random quote |

### Weather configuration

| Field | Description | Default |
|-------|-------------|---------|
| Weather city | Displayed city name | Montréal |
| Latitude | Latitude coordinate | 45.5017 |
| Longitude | Longitude coordinate | -73.5673 |

**Common coordinates:**
- Montreal: 45.5017, -73.5673
- Quebec City: 46.8139, -71.2080
- Toronto: 43.6532, -79.3832
- Ottawa: 45.4215, -75.6972

## Quotes

The module ships **120 quotes** from artists, revolutionaries, poets and
dreamers, in English and in French: each recipient reads the version of their
own language. They are organised by theme:

- **Mutualism and anarchism**: Proudhon, Kropotkin, Emma Goldman, Bakunin
- **Poets and writers**: Rimbaud, Hugo, Neruda, García Lorca, Camus, Beauvoir, Galeano
- **Artists**: Frida Kahlo, Picasso, Oscar Wilde, Van Gogh
- **Civil rights**: Martin Luther King Jr., Nelson Mandela, Gandhi, Audre Lorde
- **Feminists**: Maya Angelou, bell hooks, Virginia Woolf
- **Thinkers**: Einstein, Seneca, Socrates, Aristotle
- **Contemporary activists**: Greta Thunberg, Paulo Freire, Aaron Swartz
- **Decolonial thinkers**: Frantz Fanon, Aimé Césaire
- **Proverbs from around the world**: African, Chinese, Japanese, Indigenous American, Persian

### Managing quotes

**Settings → Technical → Daily digest → Quotes**

Quotes can be added, edited or deactivated through the interface.

## Module structure

```
daily_todo_digest/
├── __init__.py
├── __manifest__.py
├── README.md
├── models/
│   ├── __init__.py
│   ├── daily_digest.py          # Main model and sending logic
│   └── inspirational_quote.py   # Quote model
├── data/
│   ├── daily_digest_cron.xml    # Scheduled job (cron)
│   └── inspirational_quotes.xml # 120 preloaded quotes
├── security/
│   └── ir.model.access.csv      # Access rights
└── views/
    └── daily_digest_views.xml   # Views and menus
```

## Models

### `daily.digest.config`

Daily digest configuration.

| Field | Type | Description |
|-------|------|-------------|
| `name` | Char | Digest name |
| `active` | Boolean | Active/Inactive |
| `user_ids` | Many2many | Recipients |
| `send_hour` | Integer | Send hour (0-23) |
| `include_overdue_activities` | Boolean | Include overdue activities |
| `include_today_activities` | Boolean | Include today's activities |
| `include_overdue_tasks` | Boolean | Include overdue tasks |
| `include_today_tasks` | Boolean | Include today's tasks |
| `include_calendar_events` | Boolean | Include the recipient's calendar events of the day (default: on) |
| `include_weather` | Boolean | Include weather |
| `weather_city` | Char | City name |
| `weather_latitude` | Float | Latitude |
| `weather_longitude` | Float | Longitude |
| `include_quote` | Boolean | Include a quote |
| `company_id` | Many2one | Company (optional): a template bound to a company does not reach users of the others |
| `sequence` | Integer | Order that decides which template serves a person covered by several |
| `group_ids` | Many2many | Recipient groups: every member receives the digest without being named |
| `recipient_user_ids` | Many2many | Named recipients plus every member of the recipient groups (computed, searchable) |
| `frequency` | Selection | Daily, weekdays, or the days each person picked |
| `activity_fold_model_ids` | Many2many | Models whose reminders fold into one line |
| `include_waiting_tasks` | Boolean | Keep tasks waiting on someone else in the day's list (default: off) |
| `include_upcoming_tasks` | Boolean | Include the upcoming tasks section |
| `include_week_preview` | Boolean | Include the 7-day grid |
| `include_flux_subjects` | Boolean | Include the watched subjects section |
| `last_sent` | Datetime | Last send (any recipient) |

### `res.users` (per-recipient preferences)

Each person reads and writes these on their **own** profile; an administrator
cannot be in the way, and nobody can set them on somebody else's.

| Field | Type | Description |
|-------|------|-------------|
| `digest_send_hour` | Selection | Own delivery hour, empty = the template's |
| `digest_frequency` | Selection | Own rhythm, empty = the template's |
| `digest_monday` … `digest_sunday` | Boolean | The days picked, when the rhythm is "chosen days" |
| `digest_opt_<section>` | Selection | Yes / No / empty for each of the eleven sections; empty means the template decides |
| `digest_weather_city` | Char | Own city for the weather |

### `daily.digest.send.log`

One row per (template, recipient), carrying the last send. The "already sent
today" guard asks whether **this person** received their digest today, through
any template — so changing who a template covers mid-morning does not send a
second one.

### `daily.digest.quote`

Inspirational quotes.

| Field | Type | Description |
|-------|------|-------------|
| `quote` | Text | Quote text |
| `author` | Char | Author |
| `active` | Boolean | Active/Inactive |

## Weather API

The module uses the **Open-Meteo** API (free, no API key required).

- **URL**: `https://api.open-meteo.com/v1/forecast`
- **Data retrieved**: current temperature, min/max, precipitation, precipitation probability, weather code
- **Time zone**: America/Montreal

## Email format

### Subject
```
🌄 Your day | Thursday, February 5, 2026
```

The whole email is written in **the recipient's language** (texts, dates,
weather, quote), whoever or whatever triggers the sending.

### HTML structure
- Header with the logo of the recipient's company and a title
- Accent bar in the company's brand colour
- Content sections with styled tables
- Footer with the company's name and email address
- Two-tone accent bars at the bottom

### Colours

The header, background and accent follow the company's brand colours
(`report_brand_dark` / `report_brand_primary`, from `bf_onboarding_base`) when
they are set; otherwise the defaults below apply.

| Element | Colour |
|---------|--------|
| Outer background | #2E3132 |
| Header | #22303B |
| Accent | #29ABE2 |
| Light text | #E6EDF3 |
| Grey text | #6B7280 |
| Red (overdue) | #dc3545 |
| Green (success) | #198754 |

### Typeface
`'Lexend', 'Segoe UI', Arial, sans-serif`

## Manual sending

### Through the interface
- **Send now**: sends the digest to every configured recipient
- **Test (me only)**: sends a test to the signed-in user only

### Through the Odoo shell
```python
config = env['daily.digest.config'].search([('name', '=', 'My digest')], limit=1)
config._send_digest()
env.cr.commit()
```

## Dependencies

- `base`
- `mail`
- `project`
- `bf_meeting`

### Python libraries
- `pytz` (ships with Odoo)
- `requests` (ships with Odoo)

## Changelog

### 18.0.3.0.0
- **Less noise.** Reminders created by a scheduler fold into one line per model, with their count and a link to the list: measured on a real digest, half the activities of the day were identical reminder titles repeated. Tasks waiting on a client or a colleague leave the day's list and are counted in one line — nothing can be done about them today. Both are switchable (`activity_fold_model_ids`, `include_waiting_tasks`).
- **Per-recipient preferences.** Delivery hour, rhythm (daily, weekdays, chosen days) and each of the eleven sections, set by each person on their own user form. A section has three states: the person said yes, the person said no, or the person said nothing and the template decides. A recipient who turns every section off receives nothing.
- **Templates that target groups.** A template can aim at `res.groups`; every member receives the digest without being named. When several templates cover the same person, the first by `sequence` wins, so that person receives exactly one digest.
- **Watched subjects.** A section listing what the RSS lists that watch a name retained since the last digest, read from the RSS module at runtime — no manifest dependency, and nothing happens when that module is absent.
- **Deadlines are shown in the recipient's time zone.** They were computed in the server's zone: a recipient reading their digest on the other side of the world saw the wrong date on most of their tasks.
- **The "upcoming tasks" section is rendered.** It was gathered for several versions and never shown, and its toggle reached no form. The upgrade leaves it off, since nobody had ever seen it.
- **Texts are escaped.** A task or activity title carrying `<` or `&` could break the email's HTML.
- **List links work.** The four links to a filtered list carried their filter in the URL, which Odoo 18 never reads: they opened the home screen, or the unfiltered list. Their domains now live in window actions, evaluated with the identity of whoever clicks — so a link shows that person's records, not the scheduled job's.
- **A failing recipient no longer costs the others their digest.** Each recipient is sent inside its own savepoint, so one SQL error no longer aborts the cursor for everyone after them.
- A delivery hour that **does not exist** on the day the clocks move forward is sent at the next hour that does, instead of being skipped in silence.

### 18.0.2.3.0
- **Today's calendar.** A new section, placed before what is due, lists the events of the recipient's local day. An event belongs to someone through their **attendance**, not its organizer: events synchronised from Nextcloud are organized by the system user and would otherwise be missed. Declined events are left out, and so are meetings marked cancelled when `bf_calendar_invite` is installed. Times read in the recipient's time zone, with a second line in their secondary calendar time zone when they set one; all-day and multi-day events are handled, and a web address in the call or location field becomes a **Join** link. The preheader counts the events. Toggle: `include_calendar_events`. The section is skipped when the Calendar app is not installed.
- **Readable accent colour.** Table headers and count badges now use a darkened variant of the brand accent, computed to reach WCAG AA (4.5:1) under white text and on the light badge background. A light brand blue behind white text read at about 2.6:1; the plain accent stays for bars and links.

### 18.0.2.2.0
- **The email is written in each recipient's language.** It was assembled in hard-coded French for everybody: day and month names, weather, section titles. Texts now go through the catalogue, dates are formatted by the recipient's locale, and the scheduled job renders each recipient in their own language.
- **The header and footer carry the recipient company's logo, name and email address**, instead of a fixed logo and signature.
- **Quotes are translatable** and ship in English and French. Upgrading switches a shipped quote to English only where it is still the shipped text; a quote edited by hand keeps its wording in every language.
- English source strings, French in `i18n/fr_CA.po`.

## Author

**Symbifox** - [symbifox.com](https://symbifox.com)

## Licence

LGPL-3
