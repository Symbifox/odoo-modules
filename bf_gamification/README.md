# Fox Quest — Gamification for Odoo 18

**Fox Quest** (`bf_gamification`) is a fox-themed gamification module for Odoo 18 Community that
motivates teams through XP, levels, badges, streaks, and redeemable rewards. It integrates with
timesheets, project tasks, knowledge documentation, hosting management, chatter messages, helpdesk
tickets, and scheduled activities to reward daily work with a progression system inspired by RPGs.

Built for [Les services de consultation Blue Fox, Inc.](https://symbifox.com)

---

## Table of Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [How It Works](#how-it-works)
  - [XP System](#xp-system)
  - [Levels](#levels)
  - [Badges](#badges)
  - [Streaks (constance)](#streaks-constance)
  - [On time](#on-time)
  - [Caught up](#caught-up)
  - [Rewards](#rewards)
  - [Constance time off](#constance-time-off)
  - [Known limits](#known-limits)
- [Integrations](#integrations)
- [User Interface](#user-interface)
  - [Dashboard](#dashboard)
  - [Systray Widget](#systray-widget)
  - [Achievement Popup](#achievement-popup)
  - [Level-Up Popup](#level-up-popup)
  - [Menus](#menus)
- [Security](#security)
- [Models Reference](#models-reference)
- [OWL Components](#owl-components)
- [Scheduled Jobs](#scheduled-jobs)
- [File Structure](#file-structure)
- [Changelog](#changelog)
- [License](#license)
- [Acknowledgements](#acknowledgements)

---

## Features

- **10-tier fox-themed level system** — Renardeau through Kitsune (0 to 20,000 XP)
- **Configurable XP rules** — award XP for timesheets, tasks, documents, hosting, messages, helpdesk tickets, activities, and streaks
- **Badge system** — 15 default badges across 6 fox-themed categories with automatic, threshold, and manual conditions
- **Reward store** — users redeem XP for perks, time off, or recognition
- **Leaderboard** — real-time ranking by total XP with avatars and level titles
- **Streak tracking** — consecutive *working days* of activity, in each person's time zone; weekends, public holidays and declared absences pause it instead of breaking it
- **On-time rewards** — meeting reports sent within 24 h, agendas sent 24 h ahead, activities and tasks done by their due date
- **Caught-up fox** — when one of your work lists empties, a fox congratulates you (Odoo's rainbow-man effect, the one that ends a bank reconciliation)
- **Constance time off** — optional, off by default: after a set number of working days of constance, a time-off request goes to a manager for approval; each employee chooses to take part
- **Service accounts out of the game** — integration and portal accounts earn nothing and stay off the leaderboard
- **Real-time popups** — animated achievement and level-up overlays via Odoo `bus.bus`
- **Systray widget** — persistent XP, streak, and progress bar in the top navigation
- **Full audit trail** — every XP gain and loss is recorded with source references
- **Manager tools** — grant badges manually, configure rules, manage rewards and claims
- **Retroactive backfill** — on install/upgrade, automatically awards XP for recent work (last 96 hours)
- **Free colors** — badge categories and levels take any hex color (`bf_color`) instead of Odoo's twelve

---

## Requirements

| Dependency | Purpose |
|---|---|
| `base` | Core framework |
| `bf_color` | Free colors on badge categories and levels |
| `mail` | Messaging, activity tracking, chatter XP integration |
| `bus` | Real-time browser notifications for popups |
| `hr_timesheet` | Timesheet XP integration |
| `project` | Task completion XP integration |
| `web` | OWL component framework |
| `project_knowledge_matrix` | Document/knowledge item XP integration |
| `bf_corporate_governance` | Holds `corporate.resolution`, which this module inherits |
| `hosting_management` | Maintenance completion XP integration |
| `helpdesk_mgmt` | Helpdesk ticket resolution XP integration |
| `bf_onboarding_base` | Shared onboarding-panel helpers (guided setup step) |
| `bf_meeting` | Meeting record/agenda/decision XP integration |
| `bf_email_management` | Email (`bf.email`) XP integration |

Optional, through bridges that install themselves when both sides are present:

| Bridge | With | Purpose |
|---|---|---|
| `bf_gamification_contact_absence` | `bf_contact_absence` | A declared absence changed afterwards (declared late, corrected, archived, removed) recomputes the streak |
| `bf_gamification_hr_holidays` | `hr_holidays` | An approved constance time-off request becomes a time-off allocation |

Odoo 18 Community Edition.

---

## Installation

```bash
# From the Odoo container or CLI
odoo -d <database> -i bf_gamification --stop-after-init --no-http
```

Then restart Odoo to load the web assets (OWL components, SCSS).

On first install, the module creates:
- 10 fox-themed levels (Renardeau through Kitsune)
- 6 badge categories (La Chasse, Le Terrier, La Meute, La Taniere, Les Etoiles, Le Glapissement)
- 15 default badges
- 11 default XP rules
- 2 scheduled jobs (streak reset + badge check)
- Security groups for all internal users
- Retroactive XP backfill for the last 96 hours of work

---

## Configuration

Navigate to **Settings > Fox Quest** (visible to Fox Quest managers).

| Setting | Default | Description |
|---|---|---|
| Activer Fox Quest | On | Master toggle — disables all XP awarding when off |
| Afficher le classement | On | Show/hide the leaderboard |
| Popups d'achievement | On | Show animated overlays on badge/level-up events |
| Sons | On | Enable audio effects (reserved for future use) |
| Confettis | On | Enable confetti visual effects |
| Jours ouvrables avant reset du streak | 2 | Working days without activity that break the streak (2 = one missed working day is tolerated) |

All settings are stored as `ir.config_parameter` keys prefixed with `bf_gamification.*`.

---

## How It Works

### XP System

XP (experience points) is the core currency. Every tracked action creates an `xp.transaction`
record with the amount, source, description, and a reference to the originating object.

XP rules are fully configurable via **Fox Quest > Configuration > Regles XP**:

| Rule | Source | Trigger | XP | Condition |
|---|---|---|---|---|
| Heure de feuille de temps | Timesheet | Create | 1/h | Per hour logged |
| Journee productive | Timesheet | Daily | 5 | >= 6h billable in a day |
| Streak quotidien | Streak | Daily | 10 | Per consecutive active day |
| Tache completee | Task | Complete | 5 | Task moved to done stage |
| Tache avant deadline | Task | Complete | 10 | Completed on or before deadline |
| Document cree | Document | Create | 3 | New document created |
| Revision document | Document | Write | 2 | Meaningful document update |
| Maintenance completee | Hosting | Complete | 2 | Scheduled maintenance marked done |
| Message poste | Message | Create | 1 | Message or internal note posted in chatter |
| Ticket resolu | Helpdesk | Complete | 3 | Helpdesk ticket moved to resolved stage |
| Activite completee | Activity | Complete | 1 | Scheduled activity marked as done |

Rules can be enabled/disabled, and XP amounts adjusted without code changes.

### Levels

10 fox-themed levels with increasing XP thresholds:

| # | Level | Min XP | Title | CSS Class |
|---|---|---|---|---|
| 1 | Renardeau | 0 | Louveteau | `level-bronze` |
| 2 | Jeune Renard | 100 | Eclaireur | `level-bronze` |
| 3 | Renard Roux | 300 | Pisteur | `level-silver` |
| 4 | Renard Argente | 750 | Chasseur | `level-silver` |
| 5 | Renard Arctique | 1,500 | Gardien | `level-gold` |
| 6 | Renard Dore | 3,000 | Alpha | `level-gold` |
| 7 | Renard Mystique | 5,000 | Sage | `level-platinum` |
| 8 | Esprit Renard | 8,000 | Oracle | `level-platinum` |
| 9 | Renard Celeste | 12,000 | Legende | `level-diamond` |
| 10 | Kitsune | 20,000 | Kitsune | `level-diamond` |

Levels are editable. Add, remove, or adjust thresholds via
**Fox Quest > Configuration > Niveaux**.

When a user crosses a level threshold, a level-up event is pushed through `bus.bus` and an
animated popup appears in their browser.

### Badges

Badges are visual achievements organized into fox-themed categories:

**Categories:** La Chasse (productivity), Le Terrier (knowledge), La Meute (teamwork), La Taniere (hosting), Les Etoiles (milestones), Le Glapissement (communication)

**Rarity tiers:** Common, Uncommon, Rare, Epic, Legendary — each with distinct visual styling.

**Default badges (15):**

| Badge | Category | XP | Rarity | Condition |
|---|---|---|---|---|
| Premieres Pattes | La Chasse | 10 | Common | First timesheet entry |
| Queue de Feu | La Chasse | 25 | Uncommon | 7-day streak |
| Renard Infatigable | La Chasse | 100 | Epic | 30-day streak (hidden) |
| Renard Lettre | Le Terrier | 15 | Common | First document created |
| Sage du Terrier | Le Terrier | 50 | Rare | 10 documents created |
| Renard Aventurier | La Meute | 10 | Common | First task completed |
| Renard Implacable | La Meute | 75 | Rare | 50 tasks completed |
| Depanneur | La Meute | 10 | Common | First ticket resolved |
| Renard Serviable | La Meute | 50 | Rare | 25 tickets resolved |
| Gardien de la Taniere | La Taniere | 10 | Common | First maintenance done |
| Premier Glapissement | Le Glapissement | 10 | Common | First message posted |
| Renard Bavard | Le Glapissement | 25 | Uncommon | 50 messages posted |
| Renard d'Argent | Les Etoiles | 25 | Uncommon | Reach 500 XP |
| Renard Endurant | Les Etoiles | 50 | Uncommon | 100h of timesheets |
| Renard d'Or | Les Etoiles | 100 | Epic | Reach 5,000 XP (hidden) |

**Condition types:**

| Type | Behavior |
|---|---|
| Manual | Granted by a manager via the wizard |
| Automatic | Awarded when a record count threshold is met on a monitored model |
| Threshold | Awarded when the user's total XP reaches a value |

Automatic and threshold badges are checked on every XP award and by a scheduled job every 6 hours.

**Popup effects** (configurable per badge): `confetti`, `fireworks`, `glow`, `shake`, or `none`.

### Streaks (constance)

A streak counts consecutive **working days** on which a person earns XP, in that person's own time
zone. A working day is a day worked in the person's resource calendar (their employee record, else
their company's, else Monday to Friday) that is neither a public holiday of that calendar, nor a time
off on their resource (validated `hr_holidays` leaves land there), nor a declared absence on their
contact (`bf_contact_absence`, when installed).

A non-working day pauses the streak: activity on a Saturday neither increases nor breaks it. The reset
setting keeps its meaning: with the default of 2, one missed working day is tolerated. The
"Streak quotidien" rule pays its XP once on each working day the streak continues.

Only the person's own actions count: a day counts when it carries XP earned during the person's own
request (a click, or a call made under their own account: a script or an assistant calling Odoo over
XML-RPC as that person is a request like any other and counts as theirs), never from a scheduled job,
a colleague or a service account acting on their work (a colleague closing their task, or a robot
reviewing their meeting report, still credits them, but makes no day of constance), nor from a support
session seeing Odoo as that person (`bf_impersonate`): the XP is still credited, but the support's
actions make no day of constance. History from before 3.0 counts as such. The date of an XP entry
is always the moment it is recorded: no context default can backdate it, and no context default
fills what Fox Quest creates (XP entries, badges, requests, allocations) in the caller's place.

The streak is derived from the XP history rather than incremented: it is recomputed on the first
action of each day, by a daily cron job, and whenever a time off changes or (with the bridge
`bf_gamification_contact_absence`) a declared absence is created, corrected, archived or deleted. An
absence declared afterwards (sick on Monday and Tuesday, declared on Wednesday) or a time off
validated late repairs the streak instead of leaving it broken. The daily job recomputes each person
on their own: a failure on one never cancels the others.

The streak counter is visible in the systray widget and on the dashboard.

### On time

| Rule | Condition |
|---|---|
| CR envoyé sous 24 h | Meeting report sent at most 24 h after the meeting ends |
| OdJ envoyé 24 h avant | Agenda sent at least 24 h before the meeting starts |
| Activité faite à l'échéance | Your own activity, planned the day before or earlier, done by its due day |
| Tâche avant deadline | Task closed (folded stage or *done* state) by its deadline day |

The XP of a sent report or agenda goes to the person who sends it, or to the organizer when a service
account sends it. A task pays its completion XP once, even if reopened and closed again.

### Caught up

Five work lists, each in the person's own day:

| List | Empty when |
|---|---|
| Activities | no activity of yours is overdue or due today |
| Inbox | your `bf_email` inbox is empty (Inbox Zero) |
| Tasks | none of your open tasks is past its deadline day |
| Reports | every report of a *planned* meeting you organized (linked to a calendar event, ended within 30 days) is sent; unplanned calls do not count |
| Agendas | every agenda of yours for a meeting in the next 48 hours is sent |

When **your own action** removes an item from one of your lists and the list becomes empty, Fox Quest
celebrates once per day and per list: a fox appears in Odoo's rainbow-man effect and a small XP bonus
is recorded. When all five are empty at once, a bigger celebration, once a day. Nothing fires from a
cron, a sorting rule or a service account acting on its own, nor from a support session seeing Odoo
as you (`bf_impersonate`), nor from the mailbox engines run from your own click (« Synchroniser
maintenant », re-applying rules). A call made under your own account (a script or an assistant over
XML-RPC) is a request like any other: it counts as your action. For tasks, only closing an overdue
task counts (not postponing it or leaving it); a task in a folded stage is closed. A celebration that
fails never breaks the action that triggered it. The effect follows Fox Quest's popup setting and
Odoo's "Show effects" setting (when effects are off, Odoo shows a plain notification).

### Rewards

Managers can create rewards that users redeem by spending XP:

**Reward categories:** Conge (time off), Avantage (perk), Reconnaissance, Personnalise

**Workflow:** Pending -> Approved -> Consumed (or Refused with XP refund)

**Earned XP and balance are two numbers.** *Total XP* is everything a
person has earned: it sets the level, the rank and the XP-threshold badges.
*XP balance* is what is left to spend. A claim draws on the balance only, so
buying a reward never costs a level.

**Safeguards:**
- XP balance check before claiming
- Optional stock limits (0 = unlimited)
- Optional per-user claim limits
- Full refund to the balance on refusal

With `bf_avatar` installed, `bf_avatar_gamification` adds six packs of
decorative avatar parts as ordinary rewards, approved as soon as they are
claimed.

### Constance time off

Off by default (**Settings > Fox Quest > Constance**). The company sets the step (working days of
constance, default 60), the days granted per step (default 1) and a yearly cap (default 5). Odoo
keeps no 0 in a setting: 0 or an empty field restores the default (so 0 does not mean "no cap"; to
not limit, enter a large number).

Each employee then chooses to take part from their dashboard, after reading what is counted. Nobody
else can turn it on for them (not even a Fox Quest manager), nor can the support seeing Odoo as them
(`bf_impersonate`: turning it on or off is refused under impersonation, as is approving or refusing a
request). Colleagues cannot see who takes part or their progress; Fox Quest managers can, and so can
the person who decides on the requests. From that day, each working day of constance counts towards
the step; a broken streak starts the count again, and a repaired one (absence declared afterwards)
gets it back. A step already reached is never counted twice. At the step, a "Journée de congé
(constance)" request opens as *pending* with an activity for the person's manager (the manager on
their employee record if they manage Fox Quest, else a Fox Quest manager, else an administrator other
than the person; with nobody, the request waits and says so). Only the person and the approver follow
the request; the request, its note and the approver's activity are signed by the system, not by
whoever triggered the recount.

**Nothing is granted without a person deciding**, and nobody approves their own request: once
created, a request cannot change person, reward, number of days or approver (set by *Approve* only),
approving or refusing reads its current state, and a constance request cannot be deleted. The
constance reward sells no avatar parts (a manager cannot add any: they would approve every request
without anyone deciding). Lowering the step opens one request per recount, never a burst.
Withdrawing stops the count; requests already open stay for the manager to decide. Resetting all
progress keeps constance time-off requests.

With `hr_holidays` installed, the bridge `bf_gamification_hr_holidays` turns an approved request into
a time-off allocation.

### Known limits

- A call made under your own account (a script or an assistant over XML-RPC) counts as your action:
  it can count a day of constance and show the fox.
- Any internal user can declare an absence (`bf_contact_absence`) on any contact, which pauses that
  person's constance.
- The fox ignores a do-not-disturb mode, as badges do.
- Turning constance time off off and back on: the count kept going while it was off, so requests
  open right away (one per recount, within the yearly cap).
- Service accounts are excluded on upgrade for `__system__` and a short list of known integration
  logins; any other service or integration account is ticked "Hors Fox Quest" by hand on the user.

---

## Integrations

Fox Quest hooks into existing modules via model inheritance. All hooks go through one helper
(`fq_savepoint.tolerated`) that logs and swallows an error so as never to disrupt the business
operation, except a database concurrency error (serialization, lock, deadlock), which goes up so that
Odoo replays the request. A master `gamification_enabled` config parameter acts as a kill switch.

### Timesheets (`account.analytic.line`)

Overrides `create()`. When a timesheet line is created with a project and positive hours:

1. **Hourly XP**: awards `unit_amount * rule.xp_amount` (default: 1 XP per hour)
2. **Daily bonus**: if total hours for the day >= `min_value` (default: 6h), awards a one-time
   daily bonus (default: 5 XP), with duplicate detection

### Tasks (`project.task`)

Overrides `write()`. When a task's stage changes to a folded (done) stage:

1. **Completion XP**: awards XP (default: 5) to the first assigned user
2. **Early completion bonus**: if completed on or before deadline, awards bonus XP (default: 10)

### Knowledge (`project.document`)

Overrides `create()` and `write()`:

- **Document creation**: awards XP (default: 3) to the creator
- **Document revision**: awards XP (default: 2) on meaningful field changes
  (`name`, `content`, `state`, `attachment_ids`, `version_ids`)

### Hosting (`hosting.maintenance.schedule`)

Overrides `write()`. When `last_performed` is updated (maintenance marked done):

- Awards XP (default: 2) to the current user

### Messages (`mail.message`)

Overrides `create()`. When a user posts a comment or internal note in the chatter:

- Awards XP (default: 1) per message
- Only triggers for `message_type='comment'` with a non-empty body (system notifications are excluded)

### Helpdesk (`helpdesk.ticket`)

Overrides `write()`. When a ticket's stage changes to a folded (resolved) stage:

- Awards XP (default: 3) to the assigned user or ticket creator

### Activities (`mail.activity`)

Overrides `_action_done()`. When a scheduled activity is marked as done:

- Awards XP (default: 1) to the activity's assigned user
- Captures user info before the activity is unlinked by Odoo

---

## User Interface

### Dashboard

The main dashboard (`ir.actions.client` tag `bf_gamification_dashboard`) provides a single-page
overview built as an OWL component:

- **Profile card** — avatar, name, level badge, title, rank, total XP, progress bar
- **Stats row** — current streak, badge count, weekly XP, monthly XP
- **Leaderboard** — top 10 players with trophy icons for positions 1-3
- **Recent badges** — last 6 earned badges with rarity-colored icons
- **Activity feed** — last 8 XP transactions with source icons and dates
- **Global stats** — total players, total XP awarded, total badges unlocked

### Systray Widget

A compact widget in the top navigation bar (sequence 80), always visible:

- Fire icon + streak count (when streak > 0)
- Star icon + total XP
- Mini progress bar (40px wide) showing progress to next level
- Click opens the dashboard

### Achievement Popup

A centered overlay (z-index 10000) triggered via `bus.bus` when a badge is earned:

- Semi-transparent backdrop
- Rarity-colored top border (gray/green/blue/red/gold)
- Trophy icon, badge name, description, XP reward
- Custom popup message (if configured)
- Optional CSS effects: glow animation, screen shake
- "Genial !" dismiss button
- Auto-closes after 8 seconds

### Level-Up Popup

A full-screen overlay (z-index 10001) triggered on level transitions:

- Dark gradient background matching the new level's tier
- Bouncing gold star animation
- Old level -> New level transition with animated arrow
- New level title in gold
- Total XP badge
- "Continuer" dismiss button

### Menus

```
Fox Quest (root, sequence 65)
+-- Tableau de bord          (OWL dashboard)
+-- Mon profil               (current user's profile form)
+-- Classement               (leaderboard list, sorted by XP)
+-- Badges
|   +-- Tous les badges      (kanban with images and rarity)
|   +-- Mes badges           (filtered to current user)
|   +-- Historique           (all badge awards)
+-- Recompenses
|   +-- Catalogue            (kanban with images and XP cost)
|   +-- Mes reclamations     (current user's claims)
|   +-- Toutes reclamations  (manager only)
+-- Activite
|   +-- Journal XP           (transaction list with filters)
+-- Configuration            (manager only)
    +-- Niveaux              (editable list)
    +-- Categories de badges (editable list)
    +-- Regles XP            (editable list)
    +-- Gerer les recompenses
    +-- Attribuer un badge   (wizard dialog)
```

---

## Security

### Groups

| Group | Scope | Members |
|---|---|---|
| `group_gamification_user` | Read profiles, badges, rewards. Create claims. View own XP. | All internal users (auto-assigned via `base.group_user`) |
| `group_gamification_manager` | Full CRUD on all models. Grant badges. Manage configuration. | Assigned manually |

### Record Rules

| Model | User | Manager |
|---|---|---|
| Profile | Read all (leaderboard) | Full access (time-off participation and progress: Fox Quest only; no context default can set them) |
| XP Transaction | Read own | All but the "À jour" entries of others; created entries are dated now and never "from a gesture"; person, date, source, amount and reference cannot be rewritten; nothing is deleted |
| Reward Claim | Read own, create (always *pending*, whatever the caller's context says) | Full access (constance requests cannot be deleted, nor decided under impersonation) |
| Caught-up day | Own only | Own only |

---

## Models Reference

| Model | Description | Table |
|---|---|---|
| `bf.gamification.profile` | Player profile (XP, level, streak, badges) | Yes |
| `bf.gamification.level` | Level definitions (thresholds, titles) | Yes |
| `bf.gamification.badge` | Badge definitions (conditions, effects) | Yes |
| `bf.gamification.badge.category` | Badge categories | Yes |
| `bf.gamification.user.badge` | Earned badge records | Yes |
| `bf.gamification.xp.transaction` | XP audit log | Yes |
| `bf.gamification.xp.rule` | Configurable XP rules | Yes |
| `bf.gamification.reward` | Redeemable rewards | Yes |
| `bf.gamification.reward.claim` | Reward claims (workflow) | Yes |
| `bf.gamification.dashboard` | Dashboard data provider | No (`_auto=False`) |
| `bf.gamification.grant.badge.wizard` | Badge granting wizard | TransientModel |

### Key Methods

**`bf.gamification.profile._award_xp(amount, source, description, reference=None)`**

Central method for all XP awards. Creates the transaction, recomputes level, updates streak,
checks for level-up (triggers bus notification), and evaluates automatic badges.

**`bf.gamification.profile._grant_badge(badge, granted_by=None, note=None)`**

Grants a badge to the user, creates an XP bonus transaction for the badge reward, and sends
a bus notification for the achievement popup.

**`bf.gamification.profile._backfill_recent_xp(hours=96)`**

Retroactively awards XP for recent work. Scans timesheets, completed tasks, documents, messages,
and resolved helpdesk tickets within the specified window. Deduplicates against existing
transactions to avoid double-counting. Runs automatically on fresh install (via `post_init_hook`)
and on upgrade (via migration).

**`bf.gamification.dashboard.get_dashboard_data()`**

Single RPC call returning all dashboard data: profile, leaderboard (top 10), recent badges,
XP history (30 days), global stats, and available rewards.

---

## OWL Components

| Component | Registry | Template | Description |
|---|---|---|---|
| `GamificationDashboard` | `actions` (tag `bf_gamification_dashboard`) | `bf_gamification.Dashboard` | Main dashboard |
| `GamificationSystray` | `systray` (seq 80) | `bf_gamification.Systray` | Top bar widget |
| `AchievementPopup` | `main_components` | `bf_gamification.AchievementPopup` | Badge earned overlay |
| `LevelUpPopup` | `main_components` | `bf_gamification.LevelUpPopup` | Level-up overlay |
| `gamificationBusService` | `services` | — | Bus event listener |

### Bus Events

| Channel | Payload | Trigger |
|---|---|---|
| `bf_gamification/badge_earned` | badge name, description, XP, rarity, effect, sound, message | Badge granted |
| `bf_gamification/level_up` | old level, new level (name, title, CSS class), total XP | Level threshold crossed |
| `bf_gamification/xp_gained` | amount, description | XP awarded (reserved) |

---

## Scheduled Jobs

| Job | Interval | Method | Purpose |
|---|---|---|---|
| Fox Quest : mise a jour des streaks | Daily | `_cron_update_streaks()` | Recompute streaks and time-off progress from the XP history |
| Fox Quest : verification des badges | Every 6 hours | `_cron_check_badges()` | Evaluate automatic/threshold badges for all users |

---

## File Structure

```
bf_gamification/
|-- __init__.py                        # post_init_hook for retroactive XP backfill
|-- __manifest__.py
|-- README.md
|-- models/
|   |-- __init__.py
|   |-- gamification_profile.py         # Player profile, XP engine, streak, badges, backfill
|   |-- gamification_level.py           # Level definitions
|   |-- gamification_badge.py           # Badge definitions
|   |-- gamification_badge_category.py  # Badge categories
|   |-- gamification_user_badge.py      # Earned badge records
|   |-- gamification_xp_transaction.py  # XP audit log
|   |-- gamification_xp_rule.py         # Configurable XP rules
|   |-- gamification_reward.py          # Rewards + claims
|   |-- gamification_dashboard.py       # Dashboard data (_auto=False)
|   |-- gamification_constance.py       # Caught-up fox, constance time off
|   |-- fq_days.py                      # The person's day and working days
|   |-- fq_savepoint.py                 # Savepoint that never breaks the triggering action
|   |-- res_users.py                    # "Hors Fox Quest" flag for service accounts
|   |-- resource_calendar_leaves.py     # Holidays and time off recompute the streak
|   |-- res_config_settings.py          # Settings section
|   |-- account_analytic_line.py        # Timesheet XP hook
|   |-- project_task.py                 # Task completion XP hook
|   |-- project_document.py             # Document XP hook
|   |-- hosting_maintenance.py          # Maintenance XP hook
|   |-- mail_message.py                 # Chatter message/note XP hook
|   |-- mail_activity.py                # Activity completion XP hook
|   +-- helpdesk_ticket.py              # Helpdesk ticket resolution XP hook
|-- wizard/
|   |-- __init__.py
|   |-- grant_badge_wizard.py           # Manual badge granting
|   +-- grant_badge_wizard_views.xml
|-- views/
|   |-- menu_views.xml                  # Full menu tree
|   |-- gamification_profile_views.xml  # Profile form + leaderboard list
|   |-- gamification_level_views.xml
|   |-- gamification_badge_views.xml    # Kanban + form
|   |-- gamification_badge_category_views.xml
|   |-- gamification_user_badge_views.xml
|   |-- gamification_reward_views.xml   # Kanban catalog
|   |-- gamification_reward_claim_views.xml  # Workflow
|   |-- gamification_xp_rule_views.xml  # Editable list
|   |-- gamification_xp_transaction_views.xml
|   |-- gamification_dashboard_views.xml
|   |-- res_config_settings_views.xml
|   +-- res_users_views.xml             # "Hors Fox Quest" on the user form
|-- security/
|   |-- gamification_security.xml       # Groups + record rules
|   +-- ir.model.access.csv            # CRUD rights
|-- data/
|   |-- gamification_level_data.xml     # 10 fox-themed levels
|   |-- gamification_badge_category_data.xml  # 6 categories
|   |-- gamification_badge_data.xml     # 15 badges
|   |-- gamification_xp_rule_data.xml   # 11 rules
|   |-- gamification_constance_data.xml # On-time rules, caught-up and constance rewards
|   +-- gamification_cron.xml          # 2 scheduled jobs
|-- migrations/
|   |-- 18.0.2.0.0/
|   |   +-- post-migrate.py            # Fox theming + new sources + backfill
|   +-- 18.0.3.0.0/
|       +-- post-migrate.py            # Service accounts out, streaks recomputed in working days
|-- tests/                              # XP balance, reset, free colors, constance
+-- static/
    |-- description/
    |   +-- icon.png                    # Module icon
    |-- img/
    |   +-- renard_a_jour.svg           # The caught-up fox
    +-- src/
        |-- js/
        |   |-- gamification_bus_service.js
        |   |-- gamification_dashboard.js
        |   |-- gamification_systray.js
        |   |-- achievement_popup.js
        |   +-- levelup_popup.js
        |-- xml/
        |   |-- gamification_dashboard.xml
        |   |-- gamification_systray.xml
        |   |-- achievement_popup.xml
        |   +-- levelup_popup.xml
        +-- scss/
            |-- gamification.scss
            |-- achievement_popup.scss
            +-- levelup_popup.scss
```

---

## Changelog

### v3.0.1 — Readable claim name

- A reward claim is named after its reward and its person (for example "Journée de congé (constance) – Sam"). The activity email sent to the approver, the "Document" column of the activity and the reply-to address used to show the model's technical name. The activity summary no longer repeats the person's name.

### v3.0.0 — Constance, on time, caught up

- Streaks count working days in the person's time zone; weekends, public holidays and declared
  absences pause them. They are derived from the XP history, so an absence or a time off recorded
  afterwards repairs them; existing streaks are recomputed from history on upgrade.
- The "Streak quotidien" rule now pays (no code read it). "Tâche avant deadline" now pays: it
  compared a date to a datetime and failed silently. Tasks closed through the *done* state count too,
  and a task pays once.
- Service accounts ("Hors Fox Quest" on the user, set on upgrade for `__system__` and known
  integration accounts) and portal users earn nothing and leave the leaderboard. Report and agenda XP
  goes to the person who sends, not to whoever created the record.
- New on-time rules, the caught-up fox, and constance time off (off by default, employee opt-in,
  approval by a person). New bridges `bf_gamification_contact_absence` and
  `bf_gamification_hr_holidays`.
- Only the person's own action, in a request, counts: not a scheduled job, a colleague, a service
  account, the mailbox engines (ingestion, rules, sync) or a support session seeing Odoo as them
  (`bf_impersonate`), which also cannot take part in constance time off for them nor approve or refuse
  a request.
- The XP journal sets the date and the "from a gesture" flag itself; managers can no longer backdate,
  rewrite or delete XP entries, nor read others' caught-up entries. Nothing Fox Quest creates takes
  defaults from the caller's context (`default_*`): a claim is always *pending* and unapproved, a
  profile never starts with time-off participation, a request is dated now and counted against the
  yearly cap, a badge is dated now.
- A constance request cannot be deleted, its approver cannot be rewritten, one opens per recount, and
  the request, its note and the approver's activity are signed by the system. With no Fox Quest
  manager, an administrator decides. The progress reset keeps constance time-off requests. The
  constance reward takes no avatar parts.
- Concurrency errors go up to be replayed instead of being swallowed; the daily streak job isolates
  each person. The mailbox engine wrappers pass any signature through. Settings help no longer says
  "0 = no cap" (0 restores the default). Dead code removed.

### v2.7.1

- Levels and badge categories show their *Couleur affichée* (displayed color),
  which opens "My color", a shade only you see. Badge categories get a form of
  their own: without the right to edit, a click opened Odoo's generated form,
  which had neither.

### v2.7.0

- Badge categories and levels take a free color from `bf_color`: any hex
  instead of Odoo's twelve. Odoo's color
  index follows the closest shade, so every screen that reads it still shows
  a near color.

### v2.6.2

- Resetting everyone's progress runs raw SQL that no access rule sees: it
  now requires an administrator, also when the method is reached through
  RPC rather than the settings page.

### v2.6.1

- XP earned and XP balance are separated. `total_xp` now sums every
  transaction except rewards and still drives the level, the rank and the
  threshold badges; the new stored `xp_balance` (earned minus rewards
  claimed) is what a claim checks and draws on. Before, a claim was taken out
  of `total_xp`, so buying a reward could lower a person's level. The upgrade
  recomputes both numbers for every profile.
- The dashboard shows the balance under the rank; the profile form and the
  leaderboard list show it too.
- A user can no longer set the state, the approver, the processing date or
  the price of their own claim, nor claim for someone else: those values are
  ignored unless the caller is a Fox Quest manager.
- Users no longer have write access to profiles, their own included, so the
  stored XP total cannot be edited by its owner. XP credits from everyday
  work run as sudo, which also lets an action by one person credit another.
- First tests for the module (`tests/test_xp_balance.py`).

### v2.5.4

- Avatars on the dashboard and the leaderboard now load through Odoo's
  `/web/image/res.users/<id>/avatar_128` route instead of a
  `data:image/png;base64,` URI. Odoo's default avatar is a generated SVG, so
  the old URI showed a broken image for everyone without an uploaded photo;
  the route serves the right content type, is cached by the browser and
  removes several kilobytes of base64 from each leaderboard row.
- The confirmation text of the "reset all progress" button in Settings is
  reworded.

### v2.5.1

- The maintenance badges watched `last_performed_date`, a field that no longer
  exists under that name. Three badge conditions, the 2.1.0 migration that
  seeds them and the `write()` override now all read `last_performed`; a
  migration repairs the conditions already stored on existing databases.
- `bf_corporate_governance` is declared: `corporate.resolution` left
  `project_knowledge_matrix` at its 18.0.12.0.0, and without the dependency
  Fox Quest loads before the module holding the model it inherits.

### v2.3.0

- Synchronisation documentation et métadonnées (licence/LICENSE). Voir
  l'historique Git pour le détail des changements intermédiaires.

### v2.0.0 (2026-02-22) — Fox Theme + New XP Sources

**Fox Theming:**
- All 10 levels renamed from generic RPG (Bronze/Silver/Gold) to fox progression (Renardeau, Jeune Renard, Renard Roux, ... Kitsune)
- All badge categories renamed: La Chasse, Le Terrier, La Meute, La Taniere, Les Etoiles
- All 11 existing badges renamed with fox-themed names and popup messages
- New badge category: Le Glapissement (communication)

**New XP Sources (3):**
- **Messages** (1 XP): posting comments or internal notes in the chatter (`mail.message`)
- **Helpdesk tickets** (3 XP): resolving a helpdesk ticket (`helpdesk.ticket`)
- **Activities** (1 XP): completing a scheduled activity (`mail.activity`)

**New Badges (4):**
- Premier Glapissement (1st message), Renard Bavard (50 messages)
- Depanneur (1st ticket resolved), Renard Serviable (25 tickets)

**Retroactive Backfill:**
- `_backfill_recent_xp(hours=96)` scans recent work and awards XP retroactively
- Runs on both fresh install (`post_init_hook`) and upgrade (migration)

**Dependencies:**
- Added `helpdesk_mgmt` as a required dependency

### v1.1.0 — Initial Release

- 10-tier level system, 12 badges, 8 XP rules
- Timesheet, task, document, and hosting integrations
- OWL dashboard, systray widget, achievement/level-up popups
- Reward store with claim workflow

---

## License

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations.
- **Requires a written agreement**: providing the module as a product or
  service to third parties, whether hosted, managed or resold.
- **Change Date**: on 2030-10-02, this version converts automatically to
  **LGPL-3.0-or-later**.

---

## Acknowledgements

Built with Odoo 18 Community Edition and the OWL framework.
