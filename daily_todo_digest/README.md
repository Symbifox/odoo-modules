# Daily To-Do Digest

An Odoo 18 module that automatically emails a daily digest containing the
user's activities and tasks, the local weather and an inspirational quote.

## Features

### Digest content

| Section | Description |
|---------|-------------|
| **Weather** | Current temperature with emoji, min/max, precipitation for the configured city (default: Montreal) |
| **Overdue activities** | `mail.activity` records with a past due date |
| **Today's activities** | `mail.activity` records due today |
| **Overdue tasks** | `project.task` records with a past deadline |
| **Today's tasks** | `project.task` records due today |
| **7-day outlook** | A clickable visual grid of the next 7 days with colour-coded counters (green/amber/red) |
| **Hidden tasks** | A summary of tasks with `display_in_project=False` (clickable link) |
| **Inspirational quote** | A random quote from 120 artists, poets and thinkers |

### Technical characteristics

- **Time zone**: automatic UTC → America/Montreal conversion for date comparisons
- **Visibility filter**: excludes tasks with `display_in_project=False` from the detailed listing
- **Symbifox branding**: HTML template using the brand's colours and typefaces
- **Clickable links**: every task/activity carries a direct link to the Odoo record
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
dreamers, organised by theme:

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
| `include_weather` | Boolean | Include weather |
| `weather_city` | Char | City name |
| `weather_latitude` | Float | Latitude |
| `weather_longitude` | Float | Longitude |
| `include_quote` | Boolean | Include a quote |
| `company_id` | Many2one | Company (optional) |
| `last_sent` | Datetime | Last send |

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
🌄 Your day | Thursday, 5 February 2026
```

### HTML structure
- Header with the Symbifox logo and a title
- Cyan accent bar (#29ABE2)
- Content sections with styled tables
- Footer with contact details
- Two-tone accent bars at the bottom

### Symbifox colours

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

## Author

**Symbifox** - [symbifox.com](https://symbifox.com)

## Licence

LGPL-3
