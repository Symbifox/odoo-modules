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
  - [Streaks](#streaks)
  - [Rewards](#rewards)
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
- **Streak tracking** — consecutive days of activity with configurable reset threshold
- **Real-time popups** — animated achievement and level-up overlays via Odoo `bus.bus`
- **Systray widget** — persistent XP, streak, and progress bar in the top navigation
- **Full audit trail** — every XP gain and loss is recorded with source references
- **Manager tools** — grant badges manually, configure rules, manage rewards and claims
- **Retroactive backfill** — on install/upgrade, automatically awards XP for recent work (last 96 hours)

---

## Requirements

| Dependency | Purpose |
|---|---|
| `base` | Core framework |
| `mail` | Messaging, activity tracking, chatter XP integration |
| `bus` | Real-time browser notifications for popups |
| `hr_timesheet` | Timesheet XP integration |
| `project` | Task completion XP integration |
| `web` | OWL component framework |
| `project_knowledge_matrix` | Document/knowledge item XP integration |
| `hosting_management` | Maintenance completion XP integration |
| `helpdesk_mgmt` | Helpdesk ticket resolution XP integration |
| `bf_onboarding_base` | Shared onboarding-panel helpers (guided setup step) |
| `bf_meeting` | Meeting record/agenda/decision XP integration |
| `bf_email_management` | Email (`bf.email`) XP integration |

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
| Jours avant reset du streak | 2 | Days of inactivity before streak resets to 0 |

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

### Streaks

A streak counts consecutive days where a user earns XP. The streak increments when XP is awarded
on a new day, provided the gap since the last activity is within the configured reset threshold
(default: 2 days).

A daily cron job resets streaks for users who have been inactive beyond the threshold.

The streak counter is visible in the systray widget and on the dashboard.

### Rewards

Managers can create rewards that users redeem by spending XP:

**Reward categories:** Conge (time off), Avantage (perk), Reconnaissance, Personnalise

**Workflow:** Pending -> Approved -> Consumed (or Refused with XP refund)

**Safeguards:**
- XP balance check before claiming
- Optional stock limits (0 = unlimited)
- Optional per-user claim limits
- Full XP refund on refusal

---

## Integrations

Fox Quest hooks into existing modules via model inheritance. All hooks are wrapped in
try/except blocks with warning-level logging to never disrupt core business operations.
A master `gamification_enabled` config parameter acts as a kill switch.

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

Overrides `write()`. When `last_performed_date` is updated (maintenance marked done):

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
| Profile | Read all (leaderboard), write own | Full access |
| XP Transaction | Read own | Full access |
| Reward Claim | Read own, create | Full access |

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
| Fox Quest : mise a jour des streaks | Daily | `_cron_update_streaks()` | Reset streaks for inactive users |
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
|   +-- res_config_settings_views.xml
|-- security/
|   |-- gamification_security.xml       # Groups + record rules
|   +-- ir.model.access.csv            # CRUD rights
|-- data/
|   |-- gamification_level_data.xml     # 10 fox-themed levels
|   |-- gamification_badge_category_data.xml  # 6 categories
|   |-- gamification_badge_data.xml     # 15 badges
|   |-- gamification_xp_rule_data.xml   # 11 rules
|   +-- gamification_cron.xml          # 2 scheduled jobs
|-- migrations/
|   +-- 18.0.2.0.0/
|       +-- post-migrate.py            # Fox theming + new sources + backfill
+-- static/
    |-- description/
    |   +-- icon.png                    # Module icon
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

This module is released under the **GNU LGPL-3** license.

```
This module is licensed under the GNU Lesser General Public License v3.0 (LGPL-3). See [LICENSE](LICENSE) for the full text.
```

---

## Acknowledgements

Built with Odoo 18 Community Edition and the OWL framework.
