# Hosting Management for Odoo 18

A comprehensive Odoo 18 module for managing hosting services, including version tracking, health monitoring, storage alerts, maintenance automation, and client billing integration.

## Features

### Service Management
- **Complete Service Lifecycle**: Track services from draft to active, suspended, expired, or cancelled states
- **Multi-Environment Support**: Manage production, staging, development, and testing environments with visual indicators
- **Client Association**: Link services to partners with smart buttons for quick access to service counts and alerts
- **Tagging System**: Organize services with customizable color-coded tags
- **Contract Integration**: Link services to the OCA Contract module for automated invoicing
- **Server Assignment**: Associate services with physical or virtual servers for infrastructure tracking

### Software Catalog
- **Centralized Software Registry**: Maintain a catalog of all software you host (Odoo, Nextcloud, Vaultwarden, etc.)
- **Version Tracking**: Track installed versions across all services
- **Support Status**: Mark versions as supported, deprecated, or end-of-life
- **Maintenance Templates**: Define default maintenance tasks per software type

### Automatic Version Checking
- **GitHub Releases**: Automatically check for new versions from GitHub release pages
- **Docker Hub**: Check for new image tags from Docker Hub repositories
- **GitLab Releases**: Support for self-hosted GitLab instances
- **Configurable Regex**: Custom version extraction patterns for non-standard tag formats
- **Scheduled Checks**: Automatic daily version checks via cron job

### Docker Container Version Detection
- **Remote SSH Checking**: Connect to Docker hosts via SSH to inspect running containers
- **Automatic Version Sync**: Extract image versions from running containers and update service records
- **Multi-Host Support**: Configure different Docker hosts for each service

### Health Monitoring
- **HTTP Health Checks**: Periodic health checks for all services with URLs configured
- **Accepted HTTP Codes**: Per-service configurable list of HTTP status codes (e.g. 404) treated as "up" instead of "degraded" — useful for services like Shlink that return 404 on their root URL
- **Rapid Retry Confirmation**: On failure, immediately retries within 10 seconds (configurable count, default 3 checks) to filter transient glitches before alerting
- **Response Time Tracking**: Monitor response times and detect slow services
- **Status Tracking**: Up, degraded, down, and timeout states with visual indicators
- **30-Day Uptime Calculation**: Automatic uptime percentage based on health check history
- **Email Alerts**: Configurable email notifications for service outages and recoveries
- **Push Notifications (ntfy)**: Instant push notifications via self-hosted ntfy server when services go down or recover
- **Maintenance Window**: Suppress alerts (email and push) during scheduled maintenance periods (configurable start/end times and timezone)

### Storage Monitoring
- **Quota Tracking**: Set storage quotas and monitor usage for each service
- **Storage Alerts**: Automatic alerts when usage exceeds configurable thresholds
- **Visual Progress Bars**: See storage usage at a glance in list and form views

### Dashboard
- **KPI Overview**: At-a-glance view of expiring services, updates available, storage alerts, and health issues
- **Domain Overview**: Track total domains, expiring domains, SSL expiring, and domains without auto-renewal
- **Maintenance Tasks Section**: View overdue, due this week, and due this month maintenance at a glance
- **Color-Coded Cards**: Visual indicators that highlight issues needing attention (red for critical, orange for warning, blue for info)
- **Quick Actions**: One-click access to filtered lists of services or tasks needing attention
- **Refresh All**: Button to run all checks (health, versions, Docker) and update all computed fields

### Client Email Templates
- **Branded Communications**: 5 sleek, client-facing email templates with corporate branding
- **Generic Template**: Multipurpose template for any client communication via `ctx.message_body`
- **Monthly Report**: Service summary with KPI grid (services count, uptime, backups, updates) and next maintenance callout
- **Maintenance Notice**: Amber-accented notification with structured details (date, duration, affected services, impact)
- **Intervention Report**: Green-accented post-intervention summary (work done, duration, result, recommendations)
- **Welcome / Onboarding**: Warm welcome with gradient accent, activated services list, contact info, and CTA button
- **Design System**: Light background (`#F8FAFC`), 600px card with `border-radius: 16px`, Lexend typography, variable accent bars, enriched footer with company tagline and full contact details

### Scheduled Digests
- **Email Summaries**: Configurable email digests with service status summaries
- **Flexible Scheduling**: Daily, weekly, or monthly delivery options
- **Customizable Content**: Choose which alerts to include (expiring, updates, storage, health, maintenance)
- **Multiple Recipients**: Send to any number of users

### Tenant Provisioning Reporting
- **External Provisioning Integration**: Receive verbose output and structured metadata from host-side tenant creation scripts (`create_nextcloud_tenant.sh`, `create_odoo_client.sh`) via REST API
- **Auto-Upsert Service Records**: Endpoint creates or updates `hosting.service` records, resolves software/server by code/hostname, and auto-creates a `res.partner` company stub when missing
- **Verbose Log as Attachment**: Full stdout/stderr (with secrets redacted) is attached to the service chatter as a downloadable `.log` file
- **Audit Trail**: Every provisioning event creates an immutable `hosting.audit.log` entry with severity scaled by exit status

### Backup Reporting
- **External Backup Integration**: Receive backup reports from external scripts via REST API
- **Detailed Logging**: Track each backup run with service-level details, file checksums, and verification status
- **Email Notifications**: Automatic email reports with corporate styling when backups complete
- **Status Overview**: Visual indicators for success, partial success, and failed backups
- **File Verification Tracking**: Track SHA256 checksums and verification status for each backup file

### Maintenance Templates

Templates allow you to define standard maintenance tasks that are automatically applied when services are activated.

#### Template Types
- **Software-Specific Templates**: Define maintenance tasks specific to each software type (e.g., Nextcloud app updates, Odoo database maintenance)
- **Default Template**: Fallback tasks applied to all services regardless of software (e.g., backup verification, SSL checks)

#### Pre-Populated Templates
The module includes ready-to-use templates for common software:

| Software | Tasks Included |
|----------|----------------|
| **Nextcloud** | Backup setup, admin warnings check (biweekly), app updates (biweekly), DB optimization (monthly), storage cleanup (quarterly) |
| **Vaultwarden** | Backup setup, container updates (monthly), security log review (monthly) |
| **Odoo** | Backup setup, DB maintenance (weekly), module updates (monthly), security audit (quarterly), performance review (quarterly) |
| **CryptPad** | Backup setup, container updates (biweekly), storage cleanup (monthly) |
| **OnlyOffice** | Container updates (monthly), log cleanup (monthly) |
| **PrivateBin** | Container updates (monthly), storage cleanup (quarterly) |
| **Grist** | Backup setup, container updates (monthly) |
| **Default** | Backup setup (1 day after activation), SSL check (monthly), backup verification (monthly) |

#### Template Task Frequencies
- **Once (On Activation)**: One-time setup tasks with optional activity creation
- **Weekly**: Every 7 days
- **Biweekly**: Every 14 days
- **Monthly**: Every 30 days
- **Quarterly**: Every 3 months
- **Biannually**: Every 6 months
- **Yearly**: Every 12 months

#### Auto-Apply on Activation
When a service is activated:
1. The system finds applicable templates (software-specific + default)
2. Creates maintenance schedules for recurring tasks
3. Creates activities for one-time setup tasks
4. Posts a summary to the service chatter

### Preventive Maintenance Schedules
- **Maintenance Task Tracking**: Schedule recurring maintenance tasks for each service
- **Multiple Maintenance Types**: Security patching, database optimization, backup verification, backup setup, log cleanup, container updates, SSL renewal, storage cleanup, performance review, app updates, admin panel checks, security audits, and custom tasks
- **Flexible Frequencies**: Weekly, biweekly, monthly, quarterly, biannually, or yearly schedules
- **Automatic Due Date Calculation**: Next due date computed from last performed date and frequency
- **Overdue Detection**: Visual indicators and filters for overdue maintenance tasks
- **Assignment Tracking**: Assign maintenance tasks to specific users
- **Service Integration**: View and manage maintenance schedules directly from service forms
- **Digest Integration**: Include overdue and upcoming maintenance in email digests
- **Multiple Views**: List, kanban, and calendar views for maintenance schedules

### Activity Integration
- **Automatic Activity Creation**: Maintenance schedules automatically create Odoo activities
- **Activity Inbox**: Tasks appear in the clock icon (activity inbox) for easy tracking
- **Detailed Activity Notes**: Activities include formatted details with service name, client, type, frequency, and instructions
- **Auto-Recreation**: When a task is marked done, a new activity is created for the next due date
- **Activity Feedback**: Completed activities are marked with completion date

### Expiration Management
- **Automatic Tracking**: Days until expiration calculated and displayed
- **Activity Creation**: Automatic activities created for services expiring within configurable days
- **Auto-Expire**: Optional automatic state change when services expire
- **Visual Warnings**: List and kanban views highlight expiring services

### Domain Name Management
- **Dedicated Domain Model**: Track domain names independently from services (one domain can serve many services)
- **Registrar & DNS Tracking**: Record registrar, DNS provider, registration and expiration dates
- **SSL Certificate Tracking**: Monitor SSL type (Let's Encrypt, commercial, self-signed), issuer, and expiry dates
- **Renewal Reminders**: Automatic activities created for domains and SSL certificates approaching expiration
- **Configurable Thresholds**: Set warning days for domain expiration (default: 60) and SSL expiration (default: 30) in Settings
- **Auto-Expire**: Domains past their expiration date are automatically marked as expired
- **Cost Tracking**: Track annual renewal costs per domain
- **Service Association**: Link domains to services via Many2one relation; stat button shows linked services
- **Dashboard Integration**: Domains section with total, expiring, SSL expiring, and no-auto-renew counts

### Client Portal Integration
- **Smart Buttons on Partners**: See hosting service counts and alerts directly on partner forms
- **Filtered Views**: Quick access to expiring, update-needed, and storage-alert services per client

## Installation

### Requirements
- Odoo 18.0
- Python packages: `requests` (for health checks and version API calls)
- SSH access configured for Docker version checking (optional)

### Dependencies
- `base`
- `mail`
- `contacts`
- `contract` (OCA Contract module)
- `project`
- `account`
- `project_knowledge_matrix`
- `bluefox_branding`

### Installation Steps

1. Clone or copy the module to your Odoo addons directory:
   ```bash
   cp -r hosting_management /path/to/odoo/addons/
   ```

2. Update the addons list:
   ```bash
   ./odoo-bin -c odoo.conf -u base --stop-after-init
   ```

3. Install via Odoo Apps menu or command line:
   ```bash
   ./odoo-bin -c odoo.conf -i hosting_management --stop-after-init
   ```

## Configuration

### Settings UI

The module provides a dedicated settings page accessible via **Hosting > Configuration > Settings**. This is the recommended way to configure the module.

#### Health Check Alerts
| Setting | Default | Description |
|---------|---------|-------------|
| Alert Email | (empty) | Email address for health alert notifications |
| Response Time Threshold | 5000 ms | Response time threshold for slow service warnings |
| Alert Threshold | 3 | Number of rapid-retry checks (within ~10s) that must all fail before a down alert is sent |
| Expiration Warning Days | 90 | Days before expiration to create warning activities |

#### Push Notifications (ntfy)
| Setting | Default | Description |
|---------|---------|-------------|
| Server URL | (empty) | URL of the ntfy server (e.g., `http://push-ntfy:80` for internal Docker, or `https://ntfy.example.com`) |
| Token | (empty) | Bearer token for publishing to the ntfy server |
| Topic | `hosting-alerts` | ntfy topic name for hosting alerts |

When configured, push notifications are sent alongside email alerts:
- **Service down/timeout/degraded**: Urgent priority with rotating light icon (only after rapid-retry confirmation)
- **Service recovered**: Default priority with checkmark icon (only if a down alert was previously sent)
- Slow service warnings do not trigger push notifications (to reduce noise)
- Push notifications are suppressed during the maintenance window, same as emails

#### Maintenance Window
| Setting | Default | Description |
|---------|---------|-------------|
| Enable Maintenance Window | Yes | Toggle to enable/disable alert suppression |
| Start Time | 1:30 AM | Start of daily maintenance window |
| End Time | 2:30 AM | End of daily maintenance window |
| Timezone | America/Toronto | Timezone for the maintenance window schedule |

During the maintenance window:
- Health checks continue to run and data is recorded
- Alert emails (down, recovered, slow) are suppressed
- Suppressed events are logged for audit purposes

### System Parameters (Alternative)

These can also be configured in Settings > Technical > Parameters > System Parameters:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `hosting.expiration_warning_days` | 90 | Days before expiration to create warning activities |
| `hosting.health_alert_email` | (empty) | Email address for health alert notifications |
| `hosting.response_time_threshold_ms` | 5000 | Response time threshold for slow service warnings |
| `hosting.maintenance_window_enabled` | True | Enable/disable maintenance window |
| `hosting.maintenance_start_hour` | 1.5 | Start hour in decimal (1.5 = 1:30 AM) |
| `hosting.maintenance_end_hour` | 2.5 | End hour in decimal (2.5 = 2:30 AM) |
| `hosting.maintenance_timezone` | America/Toronto | Timezone for maintenance window |
| `hosting.ntfy_url` | (empty) | ntfy server URL for push notifications |
| `hosting.ntfy_token` | (empty) | Bearer token for ntfy publishing |
| `hosting.ntfy_topic` | (empty) | ntfy topic name for alerts |
| `hosting.health_alert_threshold` | 3 | Number of consecutive failed checks (within ~10s) required before firing a down alert |
| `hosting.backup_api_token` | (change me) | API token for backup report webhook authentication |
| `hosting.domain_expiration_warning_days` | 60 | Days before domain expiration to create warning activities |
| `hosting.ssl_expiration_warning_days` | 30 | Days before SSL expiration to create warning activities |

### Security Groups

- **Hosting User**: Can view and manage their own assigned services
- **Hosting Manager**: Full access to all hosting management features, including templates

### Docker Version Checking Setup

For automatic Docker container version detection:

1. Ensure SSH access is configured to Docker hosts (key-based authentication recommended)
2. On each service, fill in:
   - **Docker Host**: The hostname or IP (e.g., `server1.example.com`)
   - **Container Name**: The container name (e.g., `nextcloud-client1`)
3. The "Refresh All" button on the dashboard will check all configured containers

## Usage

### Adding a New Software

1. Go to Hosting > Software > Software Catalog
2. Create a new software entry with:
   - Name and short code
   - Version check method (GitHub, Docker Hub, GitLab, or Manual)
   - Repository URL or path
   - Version extraction regex (if needed)

### Creating Maintenance Templates

1. Go to Hosting > Maintenance > Templates
2. Create a new template with:
   - Template name
   - Software (leave empty for default template)
3. Add maintenance task lines with:
   - Task name
   - Maintenance type
   - Frequency
   - Days after activation (for one-time tasks)
   - Create activity checkbox (for one-time tasks)
   - Instructions

### Creating a Hosting Service

1. Go to Hosting > Services > All Services
2. Click Create and fill in:
   - Service name and client
   - Software and installed version
   - Environment type
   - Server URL (for health checks)
   - Storage quota (optional)
   - Expiration date (optional)
3. Click **Activate** to begin tracking
   - Maintenance templates are automatically applied
   - Activities are created for setup tasks
   - Recurring schedules are created

### Applying Templates to Existing Services

For services that were activated before templates were configured:

1. Open the service form
2. Go to the **Maintenance** tab
3. Click **Apply Maintenance Templates**
4. Templates will be applied without duplicating existing schedules

### Using the Dashboard

1. Go to Hosting > Reporting > Dashboard
2. View KPIs organized in sections:
   - **Services Expiring**: 30, 60, 90 day warnings
   - **Alerts**: Updates available, storage alerts, health issues
   - **Domains**: Total, expiring 30 days, SSL expiring, no auto-renew
   - **Maintenance Tasks**: Overdue, due this week, due this month
   - **Service Status**: Active, suspended, expired counts
3. Click any card to see the filtered list
4. Use "Refresh All" to update all checks and computed fields

### Backup Report API

The module provides a REST API endpoint for receiving backup reports from external scripts.

#### API Endpoint

**URL:** `/api/hosting/backup/report/public`
**Method:** `POST`
**Authentication:** Token-based via `X-Backup-Token` header
**Content-Type:** `application/json`

#### Configuration

1. Set the API token in **Settings → Technical → System Parameters**:
   - Key: `hosting.backup_api_token`
   - Value: A secure random token (e.g., generate with `openssl rand -hex 32`)

2. Store the same token on your backup server (e.g., `/etc/hosting/backup-api-token`)

#### Request Payload

```json
{
  "timestamp": "2026-02-03 18:30:00",
  "hostname": "server1.example.com",
  "backup_root": "/mnt/backups",
  "summary": {
    "total": 13,
    "success": 10,
    "failed": 1,
    "skipped": 2
  },
  "results": [
    {
      "service": "Nextcloud",
      "status": "success",
      "duration": "245s",
      "error": "",
      "files": [
        {
          "name": "nextcloud-client1-20260203_OK.zip",
          "size": "1.2G",
          "checksum": "abc123def456...",
          "verified": true
        }
      ]
    },
    {
      "service": "Odoo",
      "status": "failed",
      "duration": "30s",
      "error": "Database connection timeout",
      "files": []
    }
  ]
}
```

#### Example Request

```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -H "X-Backup-Token: YOUR_TOKEN_HERE" \
  -d @backup-report.json \
  https://odoo.example.com/api/hosting/backup/report/public
```

#### Response

```json
{
  "success": true,
  "backup_run_id": 42,
  "backup_run_name": "BKP-00042",
  "message": "Backup report created and email sent"
}
```

#### Automated Backup Script Integration

The companion script `backup-all-services.sh` supports two modes:

1. **Direct Odoo API** (recommended):
   ```bash
   WEBHOOK_MODE=odoo ./backup-all-services.sh
   # Or use the flag:
   ./backup-all-services.sh --odoo-direct
   ```

2. **n8n Webhook** (for additional processing):
   ```bash
   N8N_WEBHOOK_URL=https://n8n.example.com/webhook/backup-report ./backup-all-services.sh
   ```

**Additional flags:**
- `--dry-run` - Show what would run without executing
- `--no-webhook` - Run backups but don't send report to Odoo/n8n
- `--no-downtime` - Skip backups that cause service downtime (e.g., Nextcloud maintenance mode)

The token is loaded from `$BACKUP_TOKEN_FILE` (default `/etc/hosting/backup-api-token`) or the `ODOO_BACKUP_TOKEN` environment variable.

### Tenant Provisioning Report API

The module provides a REST API endpoint that receives verbose output and structured metadata from host-side tenant creation scripts (`create_nextcloud_tenant.sh`, `create_odoo_client.sh`). On every provisioning run — whether successful or not — a `hosting.service` record is created or updated, the full log is attached to its chatter, and an immutable audit log entry is written.

#### API Endpoint

**URL:** `/api/hosting/provision/report`
**Method:** `POST`
**Authentication:** Token-based via `X-Provision-Token` header
**Content-Type:** `application/json`

#### Configuration

1. Set the API token in **Settings → Hosting → Provisioning de tenants (API)** (or directly via `Settings → Technical → System Parameters`):
   - Key: `hosting.provision_api_token`
   - Value: a secure random token (e.g., `openssl rand -hex 32`)
2. Mirror the token on the host running the provisioning scripts:
   - Default path: `~/.config/hosting-report/token` (chmod 600)
   - Override with `ODOO_REPORT_TOKEN_FILE` env var

#### Request Payload

```json
{
  "slug": "acme",
  "client_name": "Acme Inc.",
  "software_code": "NC",
  "software_name": "Nextcloud",
  "hostname": "server1.example.com",
  "server_url": "https://acme.cloud.example.com",
  "domain_name": "acme.cloud.example.com",
  "admin_url": "https://acme.cloud.example.com",
  "docker_container": "acme-nc-app",
  "db_name": "nextcloud",
  "npm_port": 9612,
  "started_at": "2026-04-15 20:40:00",
  "ended_at": "2026-04-15 20:48:32",
  "status": "success",
  "log": "<full stdout+stderr, secrets redacted>"
}
```

`software_code` matches `hosting.software.code`; `software_name` is a fallback if the code is unknown. `hostname` resolves a `hosting.server` by hostname or code. The partner is resolved by `res.partner.ref = slug`, then by name; if neither matches, a company partner stub is auto-created.

#### Response

```json
{
  "success": true,
  "action": "created",
  "service_id": 12,
  "service_code": "HST-XXXX",
  "partner_id": 345
}
```

`action` is either `"created"` or `"updated"`. On token failure the endpoint returns 401; on missing required fields or unknown software, 400; on internal errors, 500.

#### Companion Bash Helper

The shared helper at `~/lib/hosting-report.sh` is sourced by both wizard scripts. It captures stdout/stderr to a temp log via `tee`, redacts common secret patterns (passwords, tokens, secrets, `POSTGRES_PASSWORD`, `MASTER_PASSWORD`, etc.), and POSTs the report on EXIT (covering both success and failure paths). Override the destination via `ODOO_REPORT_URL` or disable entirely with `ODOO_REPORT_DISABLE=1`.

### Setting Up Email Digests

1. Go to Hosting > Configuration > Digests
2. Create a new digest with:
   - Recipients (users who should receive the email)
   - Frequency (daily, weekly, monthly)
   - Content options (which alerts to include)
3. Use "Send Now" for testing

### Managing Maintenance Schedules

1. **Via Service Form**:
   - Open any hosting service
   - Go to the "Maintenance" tab
   - Click "Add Maintenance Schedule" or "Apply Maintenance Templates"

2. **Via Maintenance Menu**:
   - Go to Hosting > Maintenance > All Schedules
   - Create schedules for any service
   - Use filters to find overdue or due-soon tasks

3. **Marking Tasks Complete**:
   - Click the "Done" button on any schedule
   - The activity is marked complete with feedback
   - A new activity is created for the next due date
   - A message is posted to the schedule's chatter

4. **Via Activity Inbox**:
   - Click the clock icon in the top navbar
   - Find maintenance activities with detailed notes
   - Mark as done directly from the activity

5. **Maintenance Types Available**:
   - Security Patching
   - Database Optimization
   - Backup Verification
   - Backup Setup
   - Log Cleanup/Rotation
   - Container Image Update
   - SSL Certificate Renewal
   - Storage Cleanup
   - Performance Review
   - Application Updates
   - Admin Panel Check
   - Security Audit
   - Other (Custom)

## Data Model

### Core Models

| Model | Description |
|-------|-------------|
| `hosting.server` | Physical or virtual server infrastructure |
| `hosting.service` | Main service records with version, storage, and health tracking |
| `hosting.software` | Software catalog with version checking configuration |
| `hosting.software.version` | Version records with support status |
| `hosting.service.tag` | Color-coded tags for service organization |
| `hosting.domain` | Domain name records with expiration and SSL tracking |
| `hosting.update.log` | History of version updates |
| `hosting.health.check` | Health check results with timestamps |
| `hosting.maintenance.schedule` | Scheduled maintenance tasks for services |
| `hosting.maintenance.template` | Maintenance task templates by software type |
| `hosting.maintenance.template.line` | Individual task definitions within templates |
| `hosting.digest` | Email digest configuration |
| `hosting.dashboard` | Transient model for dashboard KPIs |
| `hosting.backup.run` | Backup run records from external scripts |
| `hosting.backup.line` | Individual service backup details within a run |
| `hosting.backup.file` | Backup file records with checksums and verification |

### Key Fields on hosting.service

| Field | Type | Description |
|-------|------|-------------|
| `code` | Char | Auto-generated reference (e.g., HS00001) |
| `partner_id` | Many2one | Client company |
| `software_id` | Many2one | Software being hosted |
| `server_id` | Many2one | Server hosting this service |
| `installed_version_id` | Many2one | Current version |
| `environment` | Selection | production/staging/development/testing |
| `domain_id` | Many2one | Linked domain record |
| `server_url` | Char | URL for health checks |
| `accepted_http_code_ids` | Many2many | HTTP codes (e.g. 404) treated as "up" |
| `docker_host` | Char | Docker host for version checking |
| `docker_container` | Char | Container name for version checking |
| `storage_quota_gb` | Float | Storage limit in GB |
| `storage_used_gb` | Float | Current storage usage |
| `date_expiration` | Date | Service expiration date |
| `state` | Selection | draft/active/suspended/expired/cancelled |
| `maintenance_schedule_ids` | One2many | Related maintenance schedules |

### Key Fields on hosting.maintenance.template

| Field | Type | Description |
|-------|------|-------------|
| `name` | Char | Template name |
| `software_id` | Many2one | Software this template applies to (empty = default) |
| `is_default` | Boolean | Computed: True if no software specified |
| `line_ids` | One2many | Maintenance task definitions |
| `task_count` | Integer | Number of tasks in template |

### Key Fields on hosting.maintenance.schedule

| Field | Type | Description |
|-------|------|-------------|
| `name` | Char | Task name |
| `service_id` | Many2one | Related hosting service |
| `maintenance_type` | Selection | Type of maintenance task |
| `frequency` | Selection | weekly/biweekly/monthly/quarterly/biannually/yearly |
| `template_line_id` | Many2one | Source template line (if from template) |
| `last_performed` | Date | When task was last completed |
| `next_due` | Date | Computed next due date |
| `is_overdue` | Boolean | Computed: True if past due |
| `user_id` | Many2one | Assigned user |
| `instructions` | Text | Task instructions |

## Cron Jobs

The module includes several automated jobs:

| Job | Schedule | Description |
|-----|----------|-------------|
| Check Expirations | Daily | Creates activities for expiring services |
| Check Domain Expirations | Daily | Creates activities for expiring domains and SSL certificates |
| Auto-Expire | Daily | Changes state of expired services |
| Health Check | Every 30 minutes | Checks health of all active services |
| Version Check | Daily | Checks for new software versions |
| Send Digests | Daily | Sends scheduled email digests |

## Menu Structure

```
Hosting
├── Services
│   ├── All Services
│   ├── Active Services
│   ├── Expiring Soon
│   └── Updates Available
├── Maintenance
│   ├── All Schedules
│   ├── Overdue
│   ├── Due This Week
│   └── Templates (Managers only)
├── Infrastructure
│   ├── Servers
│   ├── Domains
│   └── Domains Expiring
├── Software
│   ├── Software Catalog
│   └── Versions
├── Reporting
│   ├── Dashboard
│   ├── Update History
│   ├── Health Checks
│   └── Backup Runs
└── Configuration
    ├── Service Tags
    ├── Email Digests
    └── Settings
```

## Changelog

### Version 18.0.2.46.0
- Documentation and metadata sync (license/LICENSE; manifest license comment corrected to LGPL-3; README dependency list completed). See git history for intermediate changes.

### Version 18.0.2.29.0 (2026-05-04)
- **Version-capture reliability — 4 bugs fixed**
  - **Critical: every cron version check was failing silently.** `_extract_version` used `signal.SIGALRM` to bound regex evaluation, but `signal.signal()` raises `ValueError: signal only works in main thread of the main interpreter` when called from Odoo's threaded HTTP workers. The caller's `try/except (re.error, TimeoutError)` did not catch this, so every check stored an error in `version_check_error` and `latest_version` was frozen to its initial seed. Removed the signal-based timeout (input is admin-controlled and bounded to 500 chars).
  - **Docker Hub semver sort.** `_check_docker_hub_version` was sorting candidate tags by `last_updated` (upload date), which mis-ranked hotfixes on old branches as "latest". Now parses each tag through a new `_parse_version_tuple` helper and sorts tuples in descending order. Same fix applied to GitHub/GitLab tag-fallback paths via a new `_pick_highest_semver_tag` helper.
  - **`version_policy='lts'` now honored.** `_compute_update_available` previously compared every service to `software.latest_version` regardless of policy. New `_compute_update_target()` helper returns the highest `is_lts=True` version when policy=lts (falling back to `software.latest_version` for other policies). `action_update_to_latest` updated to use the same helper. **Operational note**: for the LTS policy to flag updates, at least one `hosting.software.version` record per software must be marked `is_lts=True` manually; this is a per-software data decision (e.g., for Nextcloud LTS branches).
  - **Pre-release tag rejection.** New static helper `_is_floating_or_prerelease` rejects floating tags (`latest`, `main`, `master`, `head`, `nightly`, …) and suffix-based pre-releases (`-rc`, `-beta`, `-alpha`, `-pre`, `-dev`, `-edge`, `-snapshot`). GitHub `releases/latest` also checks the `prerelease` flag for defense in depth.
- **New public method**: `hosting.software.action_check_versions_now()` — accessible via XML-RPC for ad-hoc version-check scripts (the cron `_cron_check_versions` is `@api.model`-private).

### Version 18.0.2.26.0 (2026-04-15)
- **NEW: Tenant Provisioning Report API**
  - REST endpoint `/api/hosting/provision/report` (token-auth via `X-Provision-Token`)
  - Auto-upserts `hosting.service` from host-side script output, resolving software by code (with name fallback), server by hostname, and partner by `ref`/name (auto-creating a company stub when missing)
  - Full verbose log attached to the service chatter as `.log` file; an immutable `hosting.audit.log` entry is written on every call
  - New setting under **Hosting → Provisioning de tenants (API)** to manage the shared token
  - Companion bash helper `~/lib/hosting-report.sh` wired into `create_nextcloud_tenant.sh` and `create_odoo_client.sh` (captures stdout/stderr, redacts secret patterns, POSTs on EXIT for both success and failure)

### Version 18.0.2.23.0 (2026-04-07)
- **Accepted HTTP Status Codes per Service**
  - New `hosting.accepted.http.code` model with 9 pre-seeded codes (400, 401, 403, 404, 405, 500, 502, 503, 504)
  - New `accepted_http_code_ids` Many2many field on `hosting.service` displayed as tags in the form view
  - Health check logic (`_do_health_check`, `_cron_health_check`, `action_run_health_check`) now considers per-service accepted codes as "up" instead of "degraded"
  - Solves false degraded status for services like Shlink that return 404 on their root URL when healthy
  - Tags visible under "URL du serveur" only when a server URL is configured

- **Security Hardening**
  - **SSRF prevention**: `@api.constrains` on `server_url` rejects private IPs (RFC 1918, loopback, link-local), non-HTTP schemes, and `.internal`/`.local` domains
  - **Command injection prevention**: `hostname` field on `hosting.server` now validated with strict regex (`^[a-zA-Z0-9._-]+$`); SSH commands use `shlex.quote()` on both hostname and user
  - **IP address validation**: `@api.constrains` on `ip_address` field ensures valid IP format
  - **Default API token removed**: backup API token no longer seeded with a guessable default; endpoint rejects `CHANGE_ME_TO_SECURE_TOKEN` explicitly
  - **API error messages sanitized**: backup API endpoints no longer leak exception details to callers
  - **SSH host key policy tightened**: changed from `StrictHostKeyChecking=accept-new` to `StrictHostKeyChecking=yes`
  - **ReDoS bound**: `version_regex` evaluation truncates input to 500 chars (the original SIGALRM timeout was removed in 18.0.2.29.0 because it broke in threaded workers; admin-controlled input + length cap is the residual mitigation)
  - **Data sanitization**: removed real hostnames, paths, and credentials from data files and migrations for safe publication

### Version 18.0.2.22.0 (2026-03-17)
- **Health Check Rapid Retry (False Positive Reduction)**
  - On first failure, immediately retries N-1 more times within ~10 seconds (default N=3)
  - Down alert only fires if ALL retry checks also fail, eliminating transient false positives
  - Recovery alert only fires if a down alert was previously sent for that service
  - New `health_alert_active` field on `hosting.service` tracks whether an outage alert is active
  - New system parameter `hosting.health_alert_threshold` (default 3) controls the number of checks
  - Retry delay auto-calculated to spread checks evenly within 10 seconds
  - Extracted `_do_health_check()` static method for single HTTP check execution
  - Only the final confirmed result is recorded as a `hosting.health.check` entry

### Version 18.0.2.21.0 (2026-02-23)
- **NEW: Push Notifications via ntfy**
  - Instant push notifications when services go down or recover, via self-hosted ntfy server
  - New `_send_ntfy_alert()` method in `hosting.service` called from `_cron_health_check()`
  - Urgent priority for down alerts, default priority for recovery notifications
  - New Settings block "Notifications push (ntfy)" with server URL, token (masked), and topic fields
  - System parameters: `hosting.ntfy_url`, `hosting.ntfy_token`, `hosting.ntfy_topic`
  - Respects maintenance window suppression (no push during maintenance)
  - Docker networking: Odoo container connected to `npm_push-server` network for internal communication

- **Activity View for Maintenance Schedules**
  - Added `activity` view type to all maintenance schedule actions
  - Activities now visible in the view switcher alongside list, kanban, and calendar
  - French translations for remaining English UI labels in maintenance schedule views

### Version 18.0.2.20.0 (2026-02-15)
- **NEW: Cloudflare Registrar API Sync**
  - Automatic daily synchronization of domain expiration dates and auto-renew status from Cloudflare Registrar API
  - New Settings block "Intégration Cloudflare Registrar" with email, Global API Key (masked), and Account ID fields
  - Manual "Synchroniser Cloudflare" button with confirmation dialog and success notification
  - Last sync timestamp displayed in Settings (readonly)
  - Daily cron `ir_cron_hosting_sync_cloudflare_domains` calls `_cron_sync_cloudflare_domains()`
  - Updates existing domains by name (`=ilike` match): `date_expiration`, `auto_renew`, `registrar`
  - Creates new domains automatically if returned by Cloudflare but not in Odoo
  - Chatter messages on each updated/created domain with change details
  - Graceful handling: skips silently if credentials not configured, logs API errors
  - Requires Global API Key (API Tokens do not work with Registrar endpoints)

### Version 18.0.2.18.0 (2026-02-13)
- **NEW: Client Email Templates**
  - 5 sleek, client-facing email templates with corporate branding
  - New data file `hosting_client_email_templates.xml` with `noupdate="0"` for easy iteration
  - All templates use `res.partner` as model, data passed via `ctx` dictionary
  - **Template 1 — Generic**: Multipurpose template for any client communication; accepts `email_subject` and `message_body` via ctx
  - **Template 2 — Monthly Report**: KPI grid with 4 metrics (services count, uptime rate, backups OK, updates applied) + next maintenance callout; accepts `report_month`, `services_count`, `uptime_rate`, `backups_ok`, `updates_count`, `next_maintenance`
  - **Template 3 — Maintenance Notice**: Amber accent bar (`#F59E0B`); structured details card (date/time, duration, affected services, expected impact); accepts `maintenance_date`, `maintenance_duration`, `affected_services`, `expected_impact`
  - **Template 4 — Intervention Report**: Green accent bar (`#059669`); success badge + structured sections (work done, duration, result, recommendations); accepts `intervention_summary`, `intervention_duration`, `intervention_result`, `recommendations`
  - **Template 5 — Welcome/Onboarding**: Centered header, gradient tri-color accent bar, activated services card, contact info card, CTA button; accepts `activated_services`, `support_email`, `support_phone`, `portal_url`

- **Design System (Client-Facing)**
  - Light background `#F8FAFC` (vs internal dark `#2E3132`)
  - Card: 600px, `border-radius: 16px`, `box-shadow: 0 4px 24px rgba(0,0,0,0.08)`
  - Header: `#22303B` with Blue Fox logo + contextual title
  - Variable accent bars: blue (generic/monthly), amber (maintenance), green (intervention), gradient (welcome)
  - Typography: Lexend, `font-weight: 300` body, `font-weight: 500-700` headings
  - Enriched footer: company tagline, full contact (email, phone, website), privacy/terms links
  - Double accent bottom bar maintained (Blue Fox signature)
  - All accented characters encoded as HTML entities for email client safety

### Version 18.0.2.17.0 (2026-02-10)
- **NEW: Domain Name Management**
  - New `hosting.domain` model with `mail.thread` and `mail.activity.mixin`
  - Track domain registrar, DNS provider, registration/expiration dates, and annual cost
  - SSL certificate tracking: type (Let's Encrypt/commercial/self-signed), issuer, expiry date
  - States: active, expiring_soon, expired, transferred
  - Unique constraint on domain name (FQDN)
  - One2many relation to `hosting.service` via new `domain_id` field

- **Domain & SSL Expiration Cron**
  - Daily cron checks domain and SSL expirations against configurable thresholds
  - Creates Odoo activities for domains expiring within warning window (default: 60 days)
  - Creates Odoo activities for SSL certificates expiring within warning window (default: 30 days)
  - Auto-transitions domains to "expiring_soon" and "expired" states
  - Duplicate prevention via `domain_activity_created` / `ssl_activity_created` flags

- **Dashboard Integration**
  - New "Domains" card row with 4 clickable cards: Total, Expiring 30d, SSL Expiring, No Auto-Renew

- **Settings**
  - New "Domains & SSL" block in Settings with configurable warning days

- **Data Migration**
  - Post-migration script migrates existing `domain_name` values on services to `hosting.domain` records
  - Existing `domain_name` Char field preserved for backward compatibility

### Version 18.0.2.9.0 (2026-02-03)
- **NEW: Backup Reporting System**
  - New `hosting.backup.run` model for tracking backup executions
  - New `hosting.backup.line` model for per-service backup details
  - New `hosting.backup.file` model for individual backup files with checksums
  - REST API endpoint `/api/hosting/backup/report/public` for receiving reports
  - Token-based authentication via `X-Backup-Token` header
  - Automatic email notifications with Blue Fox corporate template
  - "Backup Runs" menu under Reporting section
  - Visual status indicators (success/partial/failed)
  - File verification tracking with SHA256 checksums

- **Integration with backup-all-services.sh**
  - Script updated to send webhooks to Odoo API
  - Supports direct Odoo API mode (`--odoo-direct`) or n8n webhook
  - Token loaded from secrets file or environment variable

### Version 18.0.2.6.0 (2026-02-01)
- **NEW: Maintenance Templates**
  - New `hosting.maintenance.template` and `hosting.maintenance.template.line` models
  - Pre-populated templates for Nextcloud, Vaultwarden, Odoo, CryptPad, OnlyOffice, PrivateBin, Grist
  - Default template with common tasks (backup setup, SSL check, backup verification)
  - Auto-apply templates when services are activated
  - Manual "Apply Maintenance Templates" button for existing services
  - Templates menu under Maintenance > Templates for managers

- **NEW: Activity Integration**
  - Maintenance schedules automatically create Odoo activities
  - Activities appear in the activity inbox (clock icon)
  - Formatted activity notes with service details, client, type, frequency, and instructions
  - Activities auto-recreate with next due date when tasks are marked done

- **NEW: Dashboard Maintenance Section**
  - New "Maintenance Tasks" row on dashboard
  - Overdue (red), Due This Week (orange), Due This Month (blue) cards
  - Clickable cards open filtered maintenance schedule lists

- **Maintenance Schedule Enhancements**
  - Added weekly and biweekly frequencies
  - Added maintenance types: backup_setup, app_update, admin_check, security_audit
  - Added `template_line_id` field to track template source

### Version 18.0.2.5.0
- Added Server model for infrastructure tracking
- Extended software catalog with additional entries
- Server assignment for services

### Version 18.0.2.4.0
- Added Preventive Maintenance Schedules feature
- New `hosting.maintenance.schedule` model for tracking recurring maintenance tasks
- Support for multiple maintenance types
- Configurable frequencies: monthly, quarterly, biannually, yearly
- Automatic next due date calculation
- Visual overdue indicators
- Calendar view for maintenance planning
- Service form integration
- Digest email integration

### Version 18.0.2.3.0
- Updated email templates with Blue Fox branding

### Version 18.0.2.2.0
- Added maintenance window feature to suppress alerts during scheduled maintenance
- Added Settings UI page for easier configuration
- Configurable maintenance start/end times with timezone support

### Version 18.0.2.0.0
- Added Docker container version checking via SSH
- Added email alerts for health status changes
- Added response time threshold configuration
- Improved dashboard with color-coded cards

### Version 18.0.1.0.0
- Initial release for Odoo 18
- Core service management
- Version tracking with automatic checks
- Health monitoring
- Storage alerts
- Email digests
- Dashboard KPIs

## Contributing

Contributions are welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Submit a pull request with a clear description

## License

This module is licensed under the GNU Lesser General Public License v3.0 (LGPL-3). See [LICENSE](LICENSE) for the full text.
## Support

For support, please contact Blue Fox Inc. or open an issue in the repository.

