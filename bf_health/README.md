# Healthy Fox for Odoo 18

A personal health tracking module for Odoo 18 that centralizes medications, vitals, lab results, screenings, conditions, symptom logs, and substance use into a single dashboard with proactive reminders via Odoo activities and cron jobs.

## Features

### Medication Management
- **Prescription Registry**: Track all active, paused, and discontinued medications with dosage, form, frequency, prescriber, pharmacy, and DIN
- **Kanban by State**: Visual board grouping medications by active/paused/discontinued status with color-coded cards
- **Renewal Tracking**: Configurable renewal dates with lead-day warnings and remaining refills count
- **Automatic Renewal Reminders**: Daily cron creates Odoo activities when renewal dates approach (configurable lead days, default 14)
- **Activity Integration**: Prescription renewal activities appear in the activity inbox; since 18.0.2.5.0 the note carries the drug name only (dosage and pharmacy stay on the record)
- **Chatter**: Private thread on each medication for internal notes, attachments, and history (no "Send message", see Privacy measures)

### Daily Medication Logging
- **Per-Dose Tracking**: Log each dose taken (or skipped) per medication, per time slot (morning, noon, evening, night, as needed)
- **Automatic Log Creation**: Daily cron at 06:00 pre-creates `taken=False` entries for all active medications based on their frequency
- **Frequency-Aware Scheduling**: Respects daily, twice daily, three daily, weekly (by start day), biweekly (14-day cycle), and monthly (same day) patterns
- **Inline Editing**: Editable list view grouped by date for fast data entry
- **Compliance Graph**: Bar chart showing daily medication compliance over time

### Vital Signs & Intake Tracking
- **Multi-Type Vitals**: Weight (kg), blood pressure systolic/diastolic (mmHg), heart rate (bpm), blood sugar (mmol/L), temperature (°C), SpO2 (%), water (mL), calories (kcal)
- **Automatic Units**: Unit field auto-computed from vital type
- **Trend Graphs**: Line chart view for tracking vital sign trends over time
- **Pre-Built Report Views**: Dedicated filtered views for weight trend and blood pressure tracking
- **Inline Editing**: Editable bottom list for quick data entry
- **Period Indexing**: Stored year/month computed fields for efficient aggregation

### Lab Tests & Results
- **Requisition Workflow**: Track lab tests through requisition → scheduled → performed → results received
- **PDF Attachment Support**: Attach lab result PDFs as internal notes in the chatter
- **Recurring Test Reminders**: Set next due dates with configurable lead days (default 30); daily cron creates activities when due
- **Kanban by State**: Visual board grouped by test status
- **Calendar View**: See performed dates on a monthly calendar
- **Activity Integration**: Lab test reminder activities with a neutral summary; the note carries the test name only (doctor and lab stay on the record)
- **Chatter**: Private thread for internal notes and result attachments (no "Send message")

### Health Screenings
- **Recurring Exam Tracking**: Physical, dental, vision, dermatology, blood test, and custom screening types
- **Flexible Frequencies**: Quarterly, biannually, yearly, every 2 years, every 5 years, or custom (N months)
- **Automatic Next Due Calculation**: Stored computed field based on last date + frequency
- **State Machine**: Automatically computed state — up to date (green), due soon (yellow), overdue (red)
- **Proactive Reminders**: Daily cron creates activities when screenings approach their due date (configurable lead days, default 30)
- **Pre-Populated Data**: 5 common screenings created on install (annual physical, dental cleaning, eye exam, dermatology, blood work)
- **Calendar View**: See all upcoming screenings on a calendar by next due date
- **Kanban by Type**: Visual board grouped by screening type with color-coded state badges
- **Activity Integration**: Screening reminder activities with a neutral summary; the note carries the exam name only (clinic and dates stay on the record)
- **Chatter**: Private thread on each screening record, internal notes only

### Conditions & Symptom Logs
- **Condition Registry**: Track acute, chronic, and recurring conditions with onset/resolved dates and active/resolved/managed states
- **Inline Symptom Logging**: Log symptoms directly on condition forms with severity (1-5), body area, duration, and notes
- **Standalone Symptom View**: Dedicated list and graph views for all symptoms across conditions
- **Symptom Graph**: Bar chart grouped by body area for pattern identification
- **Kanban by State**: Visual board grouping conditions by active/managed/resolved
- **Chatter**: Private thread on each condition, internal notes only

### Substance Use Tracking
- **Multi-Substance Support**: Alcohol (drinks), cannabis (joints, gummies), tobacco (cigarettes), caffeine (cups, mg)
- **Flexible Units**: Drinks, mg, cigarettes, cups, joints, gummies, or other
- **Context Field**: Track where/why (social, stress, etc.)
- **Monthly Graph**: Bar chart showing consumption by substance type per month
- **Pivot Table**: Dedicated pivot view for monthly substance use analysis
- **Period Indexing**: Stored year/month for efficient aggregation

### Reduction Plans
- **Phased Step Tracking**: Define structured reduction plans with steps organized across phases (e.g., Stabilisation, Réduction, Sevrage, Arrêt, Zéro)
- **Dependencies**: Steps can depend on other steps, modeling prerequisite relationships
- **State Machine**: Each step progresses through À faire → En cours → Fait (or Ignoré)
- **Proactive Reminders**: Daily cron creates activities 2 days before each step's start date
- **Multi-User Support**: Each user has their own independent copy and progression (isolated via `create_uid` record rules)
- **Privacy by Design**: All tracking messages forced to internal notes, no automatic followers, no email notifications from activity types
- **Chatter**: Full private message thread on each step for notes and progress tracking

### Mood Journal (18.0.2.5.0)
- **Seconds to log**: five levels (very bad to very good), activities to tick, an optional note.
  One more page in the Quick entry wizard, and a phone page (see below)
- **Activities are the person's own**: ten starters seeded at the first visit (sport, outdoors,
  family, friends, work, rest, reading, screens, creating, meditation), editable
- **Daily reminder at the person's hour, in the person's time zone**, without streaks: a cron
  every 15 minutes posts one activity on the person's journal, in their name, assigned to them,
  with a neutral text ("Healthy Fox : votre saisie du jour"). No e-mail (`mail_activity_quick_update`).
  Yesterday's reminder is deleted at each pass and an entry deletes today's: nothing is ever
  "overdue", nothing piles up
- **History, export and doctor's report always free**: list, calendar, graph and pivot views;
  CSV export of every entry (`/healthy-fox/humeur/export.csv`); a PDF for a health-care provider
  (average, daily curve, distribution, weekly averages, correlations if enabled, notes only if
  ticked). The PDF is never stored as an attachment
- **Correlations, off by default** (Quebec Law 25, art. 8.1: profiling): once the
  person turns them on, mood is compared with sleep (`health.vital` `sleep_hours`, Pearson r from
  7 paired days, and 7 h or more versus less), medication adherence (days without a missed dose
  versus days with one), symptoms (days with versus without, and r with severity) and each
  activity (with versus without, 3 days on each side). Computed on demand through the ORM, under
  the person's rights; each line states its counts, and says so when days are missing
- **Nothing leaves the instance**: no e-mail, no push, no external call

### Phone page (PWA)
- `/healthy-fox/humeur`: five faces, activity chips, optional note, the current week. Installable
  (manifest `/healthy-fox/humeur/manifest.webmanifest`, service worker `/healthy-fox/humeur/sw.js`
  with `Service-Worker-Allowed`), dark by design, accent read from the company brand
  (`report_brand_primary` when present, `#29ABE2` otherwise) with a computed ink colour
- **Offline**: the shell is cached (it carries no personal data); an entry made offline waits in a
  per-user local queue and is sent when the network is back, `client_uuid` preventing duplicates
- JSON routes run under the person's web session: the "screen" channel of the Gen lock
- Pattern: `bf_bloc_notes` `/notes` (no asset bundle, no OWL)

### Gen lock (honoured by `bf_claude_chat` 18.0.1.36.0 and later, not yet published; optional)
Every Healthy Fox model declares a Gen scope (`_gen_scope`, `models/gen_portees.py`), read by the
generic lock of `bf_claude_chat` from 18.0.1.36.0. Healthy Fox does not depend on Gen. The
published `bf_claude_chat` (18.0.1.35.0) and earlier versions ignore these declarations: with one
of them installed, Gen reads health records like any other record the person can read. The rest of
this section describes the lock as 18.0.1.36.0 applies it.
- `bf_health`: health data (medications, vitals, lab tests, screenings, conditions, symptoms,
  substance use, reduction, workouts, nutrition)
- `bf_health.mood`: the mood journal
- `bf_health.saisie`: entry wizards, which mix both before saving: never offered to Gen
Gen reads a scope only if the **instance** opens it (system parameter
`bf_claude_chat.sensitive_open_scopes`, empty by default) **and** the person consents on screen
(toggles in "Mon journal d'humeur"), or, for a single record, in a conversation launched from that
record. Never writes. The dashboard, which reads health data in raw SQL, refuses itself when the
lock closes the scope to the caller.

### Privacy measures (18.0.2.5.0, from a privacy impact assessment)
- **Internal notes only on health records**: every Healthy Fox model with a chatter inherits
  `bf.health.note.only` (`models/health_note_only.py`, placed before `mail.thread`). A "Send
  message" becomes an internal note, no partner is added as a recipient, and the thread computes no
  recipient at all: no email, no inbox notification, whatever the subtype (activity done included).
  The "Send message" button is hidden on these models (`static/src/xml/chatter_note_only.xml`,
  same list), as a convenience only: the server enforces it.
- **Names and followers stay private (18.0.2.5.1)**: Odoo reads some names with elevated rights
  (the access error shown in debug mode, which any internal user can open, a many2one, a
  reference). A health record's name is computed for the person reading: anyone the record rule
  refuses gets "Fiche santé privée" / "Private health record" (`models/nom_prive.py`). Follower
  lines of health records can only be read by the follower themselves (`models/mail_followers.py`),
  so nobody learns who keeps health records, nor how many.
- **Daily digest**: the published `daily_todo_digest` (18.0.2.3.0) sends each person a daily email
  listing their own activities, Healthy Fox reminders included (record name and neutral summary).
  Its next version leaves out the models that declare a Gen scope. Until then, do not install it
  next to Healthy Fox if those emails must not carry health record names.
- **Reminders in the owner's name**: see Cron Jobs.
- **GPX tracks**: the uploaded file is read when the workout is saved and only the distance,
  elevation gain and duration are kept. `gpx_file` and `gpx_filename` are not stored: no attachment,
  no column. The form says so next to the upload field.
- **Migration to 18.0.2.5.0**: activities posted by the system account on health records and daily
  logs it created are handed to the record's owner (in SQL, so no "activity assigned" email); notes
  of cron reminders are reduced to the record's name (a reminder the person scheduled by hand keeps
  its note); stored GPX files are read for missing values, then deleted, and file names cleared.

### Dependents (18.0.2.6.0)
A parent can follow the health of their child under 14 in their own account; at 14 the records
move to an account of the teen's own.
- **Express consent, month and year only**: "Personnes à charge" asks for a first name, the month
  and year of birth (never the day) and a box the parent ticks after a notice saying what happens
  at 14. Consent date and notice version are stored. Children of 14 or more are refused
- **"Pour" on every health record** (medications and doses, vitals, lab tests, conditions and
  symptoms, screenings, substance use and reduction steps, workouts, meal logs, nutrition goals):
  empty means the record is the person's own. The child's records belong to the parent
  (`create_uid` rule unchanged), so other household members see nothing of them, exactly as for
  the parent's own records. A dose follows its medication; a symptom follows its condition when it
  has one. Only one's own dependent, still followed, can be chosen (constraint, `onchange` and
  `default_get` guards)
- **Dashboard and Quick entry per person**: a "Moi / child" selector appears for parents; the
  dashboard filters every raw SQL query on `dependent_id`. The mood journal stays the person's own:
  no dependent, no sharing, and the parent's correlations ignore the child's records
- **Passage at 14** (first day of the birth month, 14 years later): a daily cron reminds the parent
  (in their name) and Blue Fox (`base.user_admin`, generic text, no name). Blue Fox, the only system
  administrator, sees the first name and birth of children who are 14, never their records;
  it opens the teen's account (internal, health group, not an administrator, not the parent) and
  proposes the passage. The teen alone accepts (express consent, stored) or asks for erasure
- **What the passage does**: `create_uid` of every record is rewritten in SQL to the teen and
  `dependent_id` cleared, so the existing rules and their isolation tests stay untouched. Reminders
  are reassigned, the parent is unsubscribed and the teen subscribed, attachments follow, the foods
  of the meal log are copied into the teen's own catalogue. Odoo always lets the author or creator
  of a message read it and reply to it: the passage therefore also cuts that link on the parent's
  messages in the records' chatter (`author_id` emptied, the parent's name kept in `email_from`)
- **Sharing back, by category, read-only**: after the passage the teen can re-share categories
  with the parent who held the records (`health.share`, toggles on the dependent's form), and
  withdraw in one gesture, which also unsubscribes the parent from those records. Read-only group
  rules: nothing to write, nothing to delete
- **Withdrawal**: the parent (while holding the records) or the teen (while the passage is proposed)
  erases every record of the child, their messages, attachments and reminders, and the dependent
- **Second parent (18.0.2.6.0)**: the parent who holds the records can name a second
  parent (`coparent_id`), who reads and writes the child's records through their own group rules
  while the child is followed. A record the second parent creates for the child is handed to the
  holder (`create_uid` rewritten), so every record of a child has the same owner and the passage at
  14 moves them all; a meal logged with the second parent's own food points to a copy in the
  holder's catalogue. When the second parent steps back or is replaced, they lose the records'
  chatter, their own messages (author link cut, as at the passage), attachments and reminders.
  After the passage, the teen shares back with each parent separately.
- **Merged with 18.0.2.5.1 (2026-10-09)**: the dependent's chatter is "internal notes only" too and
  its name private for anyone who cannot read it; cron reminders keep a neutral summary, the child's
  first name goes in the note; a parent cannot follow a record the teen shares (only the person
  follows their records); activities on a health record go to its holder, the second parent, the
  teen offered the passage, or Blue Fox once the child is 14.

### OWL Dashboard
- **Today's Med Compliance**: Percentage and count (taken/total) with color-coded indicator
- **Active Medications Count**: Quick stat with click-through to kanban view
- **Water & Calorie Intake Today**: Real-time daily totals
- **Latest Vitals Snapshot**: Most recent reading for each vital type with date
- **Overdue Items**: Combined list of overdue screenings and medication renewals
- **Upcoming Screenings**: Next 30 days of due screenings
- **Substance Weekly Summary**: 7-day totals by substance type
- **Quick Navigation**: Buttons to jump to any model view
- **Saisie Rapide Button**: One-click access to the daily log wizard
- **Refresh**: Manual refresh button to reload all dashboard data
- **Person selector** (18.0.2.6.0): "Moi" or a child under 14, for parents only

### Daily Log Wizard (Saisie rapide)
- **One-Form Entry**: Single wizard to log medications taken, vitals, and substance use for a given date
- **Auto-Populated Med Lines**: Onchange loads all active medications with appropriate time slots based on frequency
- **Pre-Checked from Existing**: Detects already-logged entries and pre-checks them
- **Creates Multiple Record Types**: Generates `health.medication.log`, `health.vital`, and `health.substance.log` records in one action
- **Update-or-Create Logic**: Updates existing med log entries if found, creates new ones otherwise
- **For a child** (18.0.2.6.0): "Pour" lists the child's medications only and files every entry
  under the child; mood is refused for a child

## Data Model

### Core Models

| Model | Description | Mixins |
|-------|-------------|--------|
| `health.medication` | Prescription registry with renewal tracking | `mail.thread`, `mail.activity.mixin` |
| `health.medication.log` | Daily per-dose medication compliance log | — |
| `health.vital` | Vital signs and intake measurements | — |
| `health.lab.test` | Lab test requisitions and results | `mail.thread`, `mail.activity.mixin` |
| `health.symptom.log` | Symptom entries linked to conditions | — |
| `health.condition` | Chronic/acute/recurring conditions | `mail.thread` |
| `health.substance.log` | Substance use entries | — |
| `health.reduction.step` | Phased reduction plan steps with dependencies | `mail.thread`, `mail.activity.mixin` |
| `health.screening` | Recurring health screening schedules | `mail.thread`, `mail.activity.mixin` |
| `health.dashboard` | Abstract model (no table): RPC entry point of the OWL dashboard | — |
| `health.daily.log.wizard` | TransientModel for quick daily entry | — |
| `health.daily.log.wizard.med.line` | TransientModel wizard medication lines | — |
| `health.mood.entry` | Mood entry: date, level (1-5), stored score (avg), activities, note | — |
| `health.mood.activity` | The person's activities to tick | — |
| `health.mood.settings` | One per person: reminder, correlations switch, Gen consents | `mail.thread` (reminder only), `mail.activity.mixin` |
| `health.mood.report.wizard` | TransientModel for the doctor's PDF | — |
| `health.dependent` | A child under 14 followed by a parent: first name, birth month and year, consent, passage | `mail.thread`, `mail.activity.mixin` |
| `health.share` | What a teen re-shares with the parent who held their records, per category | None |
| `bf.health.dependent.mixin` | Abstract: the "Pour" field and its guards | None |

### Key Fields on health.medication

| Field | Type | Description |
|-------|------|-------------|
| `name` | Char | Drug name (required) |
| `dosage` | Char | e.g. "50 mg" |
| `form` | Selection | tablet, capsule, liquid, injection, topical, inhaler, patch, other |
| `frequency` | Selection | daily, twice_daily, three_daily, weekly, biweekly, monthly, as_needed |
| `times_per_day` | Integer | Computed from frequency |
| `renewal_date` | Date | Tracked, triggers activity creation |
| `renewal_lead_days` | Integer | Days before renewal to create reminder (default 14) |
| `refills_remaining` | Integer | Remaining prescription refills |
| `state` | Selection | active, paused, discontinued |
| `prescriber` | Char | Prescribing doctor |
| `pharmacy` | Char | Pharmacy name |
| `din` | Char | Drug Identification Number |

### Key Fields on health.screening

| Field | Type | Description |
|-------|------|-------------|
| `name` | Char | Screening name (required) |
| `screening_type` | Selection | physical, dental, vision, dermatology, blood_test, other |
| `frequency` | Selection | quarterly, biannually, yearly, two_years, five_years, custom |
| `frequency_months` | Integer | Custom frequency in months |
| `last_date` | Date | When last performed (tracked) |
| `next_due` | Date | Computed: last_date + frequency (stored, tracked) |
| `lead_days` | Integer | Days before due to create reminder (default 30) |
| `state` | Selection | Computed: up_to_date, due_soon, overdue |
| `provider` | Char | Clinic or provider name |

### Key Fields on health.vital

| Field | Type | Description |
|-------|------|-------------|
| `date` | Datetime | Measurement timestamp (default: now) |
| `vital_type` | Selection | weight, bp_systolic, bp_diastolic, heart_rate, blood_sugar, temperature, spo2, water_ml, calories |
| `value` | Float | Measurement value |
| `unit` | Char | Computed from vital_type (kg, mmHg, bpm, etc.) |
| `year` | Integer | Computed, stored for aggregation |
| `month` | Integer | Computed, stored for aggregation |

## Cron Jobs

| Job | Schedule | Method | Description |
|-----|----------|--------|-------------|
| Vérifier les examens à venir | Daily | `health.screening._cron_check_screenings()` | Creates activities for screenings within lead_days of next_due |
| Vérifier les renouvellements | Daily | `health.medication._cron_check_renewals()` | Creates activities for medications within renewal_lead_days |
| Vérifier les analyses de labo | Daily | `health.lab.test._cron_check_lab_tests()` | Creates activities for lab tests within next_due_lead_days |
| Créer les entrées du jour | Daily | `health.medication._cron_create_daily_med_logs()` | Pre-creates medication log entries with `taken=False` for all active meds |
| Vérifier les étapes de réduction | Daily | `health.reduction.step._cron_check_reduction_steps()` | Creates activities for steps starting within 2 days |
| Rappels du journal d'humeur | Every 15 min | `health.mood.settings._cron_mood_reminders()` | One reminder per person and local day, at their hour, in their time zone; deletes past reminders |
| Passage à 14 ans | Daily | `health.dependent._cron_passage_14_ans()` | Once per dependent, from the first day of the month of their 14th birthday: a reminder to the parent and one to Blue Fox |

Since 18.0.2.5.0 every cron posts its activities **in the name of the record's owner and assigned
to them** (`au_nom_du_proprietaire`), and the daily medication logs are created in their name.
Before, the cron posted them as the system account: activities were assigned to OdooBot and the
daily logs stayed invisible to the person (`create_uid` rule). The notes of renewal, lab test and
screening reminders carry the record's name only. The 18.0.2.5.0 migration hands existing rows back
to their owner (see Privacy measures).

All crons implement duplicate prevention: check for existing open activities of the same type before creating new ones.

## Activity Types

| Activity Type | Icon | Model | Purpose |
|---------------|------|-------|---------|
| Renouvellement de prescription | `fa-pills` | `health.medication` | Prescription renewal reminders |
| Rappel d'examen de santé | `fa-calendar-check-o` | `health.screening` | Screening due reminders |
| Analyses de laboratoire | `fa-flask` | `health.lab.test` | Lab test scheduling reminders |
| Étape de réduction | `fa-leaf` | `health.reduction.step` | Step start date reminders (no email template) |
| Humeur du jour | `fa-smile-o` | `health.mood.settings` | Daily mood reminder (no email template, neutral text) |
| Passage à 14 ans | `fa-child` | `health.dependent` | Passage of a dependent at 14 (no email template) |

## Menu Structure

```
Santé (application, opens dashboard)
├── Humeur
│   ├── Saisies (list, form, calendar, graph, pivot)
│   ├── Mon journal et corrélations (server action, the person's settings)
│   ├── Activités
│   └── Saisir au téléphone (/healthy-fox/humeur)
├── Quotidien
│   ├── Saisie rapide (wizard, with a Humeur page)
│   ├── Médicaments du jour (med log, grouped by date)
│   ├── Signes vitaux (list + graph)
│   ├── Symptômes (list + graph)
│   └── Consommation (list + graph)
├── Réduction
│   ├── Plan de réduction (list + form, grouped by phase)
│   └── Étapes actives (filtered: todo + in_progress)
├── Médicaments
│   ├── Tous les médicaments (list + kanban)
│   └── Renouvellements à venir (filtered)
├── Analyses
│   ├── Toutes les analyses (list + kanban)
│   └── Réquisitions en attente (filtered)
├── Examens
│   ├── Tous les examens (list + kanban)
│   └── Calendrier (calendar by next_due)
├── Conditions
│   ├── Conditions actives (filtered)
│   └── Toutes les conditions
├── Personnes à charge (children under 14, and records to receive at 14)
└── Rapports
    ├── Tableau de bord (OWL)
    ├── Tendance du poids (vital graph, filtered)
    ├── Pression artérielle (vital graph, filtered)
    └── Consommation mensuelle (substance pivot)
```

## Views Per Model

| Model | List | Form | Graph | Kanban | Calendar | Activity | Pivot |
|-------|------|------|-------|--------|----------|----------|-------|
| health.medication | Yes | Full + chatter | — | By state | — | Yes | — |
| health.medication.log | Editable bottom, grouped by date | Minimal | Bar (compliance) | — | — | — | — |
| health.vital | Editable bottom | Minimal | Line (trends) | — | — | — | — |
| health.lab.test | Yes | Full + chatter + attachments | — | By state | By date | Yes | — |
| health.symptom.log | Yes | Standard | Bar (by area) | — | — | — | — |
| health.condition | Yes | Full + chatter | — | By state | — | — | — |
| health.substance.log | Editable bottom | Minimal | Bar (by type/month) | — | — | — | Yes |
| health.reduction.step | Editable bottom, grouped by phase | Full + chatter + statusbar | — | — | — | Yes | — |
| health.screening | Yes | Full + chatter | — | By type | By next_due | Yes | — |

## Pre-Populated Data

The following screening records are created on install (`noupdate=1`):

| Screening | Type | Frequency |
|-----------|------|-----------|
| Examen annuel | Physical | Yearly |
| Nettoyage dentaire | Dental | Biannually |
| Examen de la vue | Vision | Yearly |
| Examen dermatologique | Dermatology | Yearly |
| Bilan sanguin complet | Blood test | Yearly |

## Security

### Groups

| Group | Description |
|-------|-------------|
| `module_category_health` | Module category "Santé" |
| `group_health_user` | Full CRUD access to all health models |

### Record Rules

All models have an `ir.rule` restricting records to `create_uid = user.id`. Each user can only see their own health data.

The mood models (`health.mood.entry`, `.activity`, `.settings`, `.report.wizard`) and, since
18.0.2.5.0, the Quick entry wizard and its lines use **global** rules (no group): they bind
everyone, administrators included, where a group rule can be sidestepped by adding oneself a
group. Odoo 18 does not isolate transient records by itself (measured: B read A's wizard).

⚠️ **Limit**: the superuser is not bound by any rule. A system administrator can become
superuser (`/web/become`), and the hosting operator has the database. The isolation holds
against ordinary household members and non-system managers, not against a system administrator
(on a hosted household instance, nobody in the household should be a system administrator).

Since 18.0.2.6.0 (dependents and second parent):
- `health.dependent`: the parent who holds the records (`create_uid`) and the teen the passage is
  proposed to (`ado_id`); Blue Fox (`base.group_system`) sees those who are 14 (`passage_atteint`,
  a searchable field, so the rule's cached domain never freezes a date). Writes are guarded field
  by field: no one writes the passage's fields directly
- `health.share`: the teen and the parent read it; only the teen creates or removes it
- every health model (and `health.food`) has a read-only group rule for what a teen shares with
  their parent: `[('create_uid.bf_health_partage_ids', 'any', [('parent_id', '=', user.id),
  ('categorie', '=', <category>)])]`
- the second parent (`coparent_id`) has a group rule on each of the 12 models a child's record can
  belong to, `[('dependent_id.coparent_id', '=', user.id), ('dependent_id.state', 'in', ('suivi', 'offert'))]`,
  and reads the foods of the child's meal logs (read-only)

The dashboard model (`health.dashboard`) has no table and no access line: it reads the caller's
own records in SQL (`create_uid` = caller), or a child's records under the parent who holds them once
the caller is checked as that child's parent or second parent, and refuses when the Gen lock closes
the scope.

### Access Control (ir.model.access.csv)

Every model grants read, write, create and delete to `group_health_user`; the record rules above
then restrict each record to the person who created it. `health.dependent` and
`health.share` keep their own guards: the dependent is written field by field and only removed by
its gestures; a share is created or removed by the teen only.

## File Structure

```
bf_health/
├── controllers/
│   ├── __init__.py
│   └── humeur_pwa.py
├── data/
│   ├── health_cron.xml
│   ├── health_mood_data.xml
│   ├── health_screening_data.xml
│   └── mail_activity_type.xml
├── i18n/
│   ├── bf_health.pot
│   └── en_CA.po
├── migrations/
│   └── 18.0.2.5.0/
│       ├── end-migrate.py
│       └── post-migrate.py
├── models/
│   ├── __init__.py
│   ├── gen_portees.py
│   ├── health_condition.py
│   ├── health_dashboard.py
│   ├── health_food.py
│   ├── health_lab_test.py
│   ├── health_meal_log.py
│   ├── health_medication.py
│   ├── health_medication_log.py
│   ├── health_mood.py
│   ├── health_note_only.py
│   ├── health_nutrition_goal.py
│   ├── health_reduction_step.py
│   ├── health_screening.py
│   ├── health_substance_log.py
│   ├── health_symptom_log.py
│   ├── health_vital.py
│   ├── health_workout.py
│   ├── mail_followers.py
│   ├── nom_prive.py
│   └── parent_guard.py
├── report/
│   └── health_mood_report.xml
├── security/
│   ├── health_security.xml
│   └── ir.model.access.csv
├── static/
│   ├── description/
│   │   └── icon.png
│   └── src/
│       ├── humeur/
│       │   ├── humeur.css
│       │   ├── humeur.js
│       │   ├── icon-192.png
│       │   ├── icon-512.png
│       │   ├── icon-maskable-192.png
│       │   └── icon-maskable-512.png
│       ├── js/
│       │   └── health_dashboard.js
│       └── xml/
│           ├── chatter_note_only.xml
│           └── health_dashboard.xml
├── tests/
│   ├── __init__.py
│   ├── test_humeur.py
│   ├── test_humeur_http.py
│   ├── test_isolation_adverse.py
│   ├── test_isolation_menage.py
│   ├── test_mesures_efvp.py
│   └── test_noms_prives.py
├── views/
│   ├── health_condition_views.xml
│   ├── health_daily_log_wizard_views.xml
│   ├── health_dashboard_views.xml
│   ├── health_food_views.xml
│   ├── health_lab_test_views.xml
│   ├── health_meal_log_views.xml
│   ├── health_medication_log_views.xml
│   ├── health_medication_views.xml
│   ├── health_mood_views.xml
│   ├── health_nutrition_goal_views.xml
│   ├── health_reduction_step_views.xml
│   ├── health_screening_views.xml
│   ├── health_substance_log_views.xml
│   ├── health_symptom_log_views.xml
│   ├── health_vital_views.xml
│   ├── health_workout_views.xml
│   └── menu.xml
├── wizard/
│   ├── __init__.py
│   ├── health_daily_log_wizard.py
│   └── nom_prive.py
├── LICENSE
├── README.md
├── __init__.py
└── __manifest__.py
```

## Installation

### Requirements
- Odoo 18.0
- Dependencies: `base`, `mail`, `web`

### Installation Steps

1. Copy `bf_health` into a directory listed in your `addons_path`.
2. Update the apps list, then install **Healthy Fox** (or run
   `odoo -d <database> -i bf_health --stop-after-init`).
3. Add each person who keeps a health journal to the **Utilisateur santé** group
   (Settings > Users). Every record is private to the person who created it,
   administrators included.

### Post-Install

The five pre-populated screening records are created by the system account, so the per-person
rules hide them from everyone. To hand them to one person, reassign their `create_uid` to that
person's user id, or let each person create their own screenings.

## Usage

### Daily Workflow

1. Open the **Santé** app — the dashboard shows today's snapshot
2. Click **Saisie rapide** to open the daily log wizard
3. Check off medications taken, enter weight/water/calories, log any substance use
4. Click **Enregistrer** — all records are created in one action

### Managing Medications

1. Go to **Médicaments > Tous les médicaments**
2. Create a new medication with name, dosage, form, frequency
3. Set the renewal date and pharmacy — the cron will create activities as the date approaches
4. View the kanban board to see medications by state (active/paused/discontinued)

### Tracking Lab Tests

1. Go to **Analyses > Toutes les analyses**
2. Create a new lab test with name, type, and ordering doctor
3. Advance the state as it progresses: requisition → scheduled → performed → results received
4. Attach result PDFs via the chatter
5. Set `next_due_date` for recurring tests — the cron will create reminders

### Monitoring Screenings

1. Go to **Examens > Tous les examens**
2. Edit the pre-populated screenings to set `last_date` when you complete one
3. `next_due` is automatically computed based on frequency
4. The state badge shows up to date (green), due soon (yellow), or overdue (red)
5. Use the calendar view to see all upcoming screening dates

### Reviewing Trends

- **Tendance du poids**: Graph view pre-filtered to weight vitals
- **Pression artérielle**: Graph view pre-filtered to BP systolic/diastolic
- **Consommation mensuelle**: Pivot table of substance use by month and type

## Design Patterns

This module follows established patterns from other Blue Fox custom modules:

| Pattern | Source Module | Usage in bf_health |
|---------|-------------|-------------------|
| Recurring schedule + activity creation | `hosting_management` | Screening, medication renewal, and lab test cron jobs with duplicate prevention |
| Activity duplicate prevention | `hosting_management` | `activity_ids.filtered(lambda a: a.activity_type_id == type)` check before creating |
| Virtual dashboard model | `personal_budget` | `health.dashboard` with `_auto=False`, `@api.model` methods, raw SQL aggregation |
| OWL dashboard component | `personal_budget` | `useService("orm")`, `useState`, `registry.category("actions").add()` |
| Date-based log with computed period | `personal_budget` | `health.vital` and `health.substance.log` with stored year/month fields |
| Cron XML data pattern | `hosting_management` | `noupdate=1`, model_id ref, state=code |

## Changelog

### Version 18.0.2.5.1 (2026-10-09)
- **Security**: a health record shows a neutral name to anyone the record rule refuses (access
  errors in debug mode, many2one and reference fields read with elevated rights).
- **Security**: follower lines of health records are readable by the follower only, at search and
  at direct access.
- New tests (`test_noms_prives.py`).

### Version 18.0.2.5.0 (2026-10-02)
- **Mood journal**: `health.mood.entry`, `.activity`, `.settings`, `.report.wizard`. Five levels,
  activities to tick, an optional note; a Mood page in the Quick entry wizard.
- **Phone page** `/healthy-fox/humeur` (PWA, offline, local queue without duplicates).
- **Daily reminder** at the person's hour and in their time zone, posted in their name, no e-mail,
  no streaks, nothing overdue. **PDF report** for a health-care provider and **CSV export**, always
  available.
- **Correlations off by default** (Quebec Law 25, art. 8.1), turned on by the person: sleep,
  medication, symptoms, activities.
- **Gen lock**: every Healthy Fox record declares a scope (`_gen_scope`), honoured by
  `bf_claude_chat` when installed. Two gates closed by default (instance, then person). The
  dashboard, which reads in raw SQL, refuses when the scope is closed to the caller.
- **Privacy measures**: internal notes only on health records, reminders posted in the owner's
  name with a neutral summary, GPX files no longer stored. A migration hands existing reminders and
  daily logs to their owner and discards stored GPX files after reading their values.
- **Security**: global rules on the mood journal and on the entry wizard; the parent-record guard
  also covers Many2many fields.
- New tests (`test_humeur.py`, `test_humeur_http.py`, `test_mesures_efvp.py`).
  `i18n/bf_health.pot` and `i18n/en_CA.po` (the source strings are French).

### Version 18.0.2.4.1 (2026-09-23)
- **Security**: hardened isolation between internal users of the same database (context defaults
  in every form).

### Version 18.0.2.4.0 (2026-09-23)
- **Security**: the parent-record guard also applies to `default_get` (logs and entry wizard).

### Version 18.0.2.3.0 (2026-09-23)
- **Security**: entry wizard lines and `onchange()` only resolve parent records the person can read.
  Common guard: `models/parent_guard.py`.

### Version 18.0.2.2.0 (2026-09-22)
- **Security**: a log (medication, meal, symptom) can only be attached to a parent record the
  person can read.
- **Security**: the "Healthy Fox" root menu is restricted to `group_health_user`.

### Version 18.0.1.1.0 (2026-03-29)
- New `health.reduction.step` model for phased reduction plans with dependencies
- Daily cron for step reminders (2-day lookahead)
- New activity type "Étape de réduction" (fa-leaf, no email template)
- Privacy-hardened: `_track_subtype` forces internal notes, `_message_auto_subscribe_followers` returns empty
- New menu section: Réduction (Plan de réduction, Étapes actives)
- Record rule + ACL for the new model

### Version 18.0.1.0.0 (2026-03-19)
- Initial release
- 7 stored models + 1 virtual dashboard + 1 wizard (2 transient models)
- 4 daily cron jobs (screening check, medication renewal, lab test reminder, daily med log creation)
- 3 activity types (prescription renewal, screening reminder, lab test)
- OWL dashboard with KPIs, vitals snapshot, compliance, overdue items, substance summary
- Daily log wizard for one-form entry of meds, vitals, and substance use
- 5 pre-populated screening records
- Full menu structure with 17 menu items across 6 sections
- Security: dedicated group + record rules restricting to own records
- Views: list, form, graph, kanban, calendar, activity, and pivot across all models

## Future Ideas

- BMI auto-calc (height config + weight vitals)
- Vaccination records with booster schedules
- Data import from Apple Health CSV
- Medication interaction warnings

## License

LGPL-3

Copyright (c) 2026 Les services de consultation Blue Fox, Inc.

---

*Developed with AI assistance (Claude) for code generation and implementation.*
