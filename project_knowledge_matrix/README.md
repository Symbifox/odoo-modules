# Project Knowledge Matrix

A comprehensive Odoo 18 module for knowledge management, document control, and project implementation tracking. Designed for organizations needing systematic document versioning, client documentation distribution, internal policy compliance, and structured project information gathering.

*Developed and maintained by [Les services de consultation Blue Fox, Inc.](https://symbifox.com) This module stores encrypted credentials — see [SECURITY.md](SECURITY.md) for the trust model.*

## Overview

Project Knowledge Matrix helps project managers, documentation teams, and compliance officers:

- **Manage documents** with full version control and distribution tracking
- **Track client documentation** ensuring clients have current versions
- **Enforce internal compliance** with policy acknowledgment tracking
- **Gather project information** in a timely and organized fashion
- **Track decisions** organized by implementation phase and section
- **Monitor deadlines** with expiration alerts and review reminders
- **Store credentials securely** with encrypted passwords and key file support
- **Visualize KPIs** through a comprehensive dashboard filterable by project
- **Corporate governance** with resolutions, directors, officers, and compliance calendar
- **Generate branded PDFs** for corporate resolutions ready for signing
- **Print knowledge matrix reports** as branded Symbifox PDFs with KPIs, progress bar, and items grouped by section
- **Email matrix reports** manually or on a configurable schedule (weekly, biweekly, monthly, custom interval)
- **Capture knowledge from chatter** with one-click message-to-matrix-item creation
- **RACI stakeholder summary** with pivot view across all projects
- **Inline matrix editing** directly in the project form

The module integrates seamlessly with Odoo's Project app and provides a complete knowledge management solution.

## Features

### KPI Dashboard

The module opens with a comprehensive dashboard showing:

- **Document Overview**: Active, draft, archived, internal, and client document counts
- **Review & Expiration Tracking**: Overdue reviews, expired documents, and upcoming expirations (0-30, 30-60, 60-90 days)
- **Client Documentation Health**: Distribution counts, acknowledgment rates, outdated documents
- **Internal Compliance**: Employee acknowledgment rates, overdue procedures
- **Content Quality**: Documents without versions, stale content
- **Knowledge Matrices**: Completion rates, blocked items, in-progress items
- **Credentials**: Active, expiring, expired, and revoked credentials
- **Distribution Activity**: Monthly trends and overdue acknowledgments

All dashboard metrics are clickable, navigating directly to filtered views.

**Project Filtering**: A dropdown selector allows filtering all dashboard metrics by project. When filtered, corporate governance metrics (which are company-wide, not project-specific) are hidden. The dashboard can also be opened pre-filtered from a project's smart button.

### Document Management

- **Document Types**: 22 pre-configured types including client documentation (Manual, Contract, Guide, Specification, Release Notes, Training), internal policies (Policy, Procedure, HR Policy, IT Procedure, Safety, Handbook, Onboarding, Confidential), and Loi 25 compliance (Privacy Policy, Form, Template, Registry, Annex, Assessment, Letter, Other)
- **Version Control**: Full version history with changelog, change types (Major, Minor, Editorial), and release tracking
- **Internal vs Client Documents**: Separate workflows for policies/procedures and client-facing documentation
- **Expiration Tracking**: Set expiration dates with automatic status updates and reminders at 90, 60, 30, and 7 days
- **Review Scheduling**: Periodic review dates with overdue tracking

### Distribution & Acknowledgment

**Optional, and off by default.** Turn it on under Settings > Knowledge Base >
"Distribution et accusés de réception". While off, the three distribution menus,
the two distribution passes of the daily maintenance job, three dashboard blocks
and two blocks of the biweekly report are hidden. Existing distributions and
their acknowledgments stay in the database untouched: an acknowledgment is
evidence that a document was made known to someone on a given date, so the
switch hides the feature, it never deletes anything.

The switch governs visibility and computation, not access rights: the access
rules on distributions are unchanged, so a user who already had them keeps them
and can still reach a record by direct URL. Use the Document groups if you need
to restrict who may read distributions.

- **Client Distribution**: Track which document versions each client has received
- **Internal Distribution**: Distribute policies to employees and track compliance
- **Acknowledgment Tracking**: Record when recipients confirm receipt
- **Outdated Alerts**: Automatic flagging when clients have outdated document versions
- **Email Notifications**: Automated reminders for pending acknowledgments and outdated documents

### Knowledge Matrices

- **Knowledge Matrices**: Container objects linked to projects or used as reusable templates
- **Knowledge Items**: Individual decision/requirement records with full tracking
- **Sections**: 10 universal categories covering the full implementation lifecycle
- **Progress Tracking**: Automatic calculation of completion percentage per matrix

### Chatter Knowledge Capture

Capture knowledge directly from task conversations:

- **Message Action Button**: Lightbulb icon (`fa-lightbulb-o`) on every message in project task chatters
- **Pre-filled Form**: Clicking creates a new knowledge item with project, matrix, author, message body (as blockquote), linked task, and auto-generated MSG-prefixed ID
- **Auto-assignment**: Current user is assigned, first active project matrix is selected
- **Condition**: Only visible to internal users on project.task threads

### RACI Stakeholder Summary

Centralized view of who has which role on how many items:

- **SQL Pivot View**: Aggregated from 4 sources — Responsible (assigned_user_id), Accountable (decision_maker_id), Consulted (M2M), Informed (M2M)
- **Default Pivot**: Partner as rows, RACI role as columns, item count as measure
- **Project Smart Button**: Open RACI filtered for a specific project
- **Menu Access**: "Résumé RACI" in main module menu
- **Role Badges**: Color-coded R (green), A (blue), C (orange), I (gray)

### Inline Matrix Tab

Edit matrix items directly in the project form:

- **Notebook Tab**: "Matrice" tab after Description in the project form
- **Grouped List**: Items grouped by section with inline bottom-editing
- **Visual Decorations**: Red (overdue), yellow (pending with deadline), green (done), gray (N/A), blue (in progress)
- **Auto-Matrix Assignment**: Items created inline are automatically assigned to the project's first active matrix
- **Empty State**: Placeholder with "Create a matrix" button when no matrix exists

### Task Integration

- **Create Task Button**: One-click task creation from any knowledge item
- **Auto-populated Tasks**: Task name, description, assignee, and deadline copied from item
- **Linked Tasks View**: See all tasks associated with an item
- **Project Integration**: Tasks appear in project Gantt, Kanban, and list views

### Planning & Scheduling

- **Priority Levels**: Low, Medium, High, Urgent (with star widget)
- **Deadlines**: Set when information is needed by
- **Implementation Phases**: Discovery, Requirements, Build, Testing, Go-Live
- **Overdue Alerts**: Visual indicators for past-due items

### Dependencies

- **Blocked By**: Link items that must be completed first
- **Is Blocked Indicator**: Visual cue when waiting on other items
- **Blocking View**: See what items depend on this one

### Collaboration

- **Chatter Integration**: Full message thread and activity scheduling
- **Field Tracking**: Automatic logging of status, assignment, and deadline changes
- **Assignments**: Assign items to team members

### User Experience

- **Smart Buttons**: Task count and attachment count on item forms
- **Color-Coded Lists**: Visual status and overdue indicators
- **Inline Editing**: Edit items directly in list view
- **Kanban View**: Drag-and-drop workflow with priority and deadline display
- **Rich Filters**: Overdue, Due This Week, High Priority, Blocked, By Phase

### Corporate Governance

Full corporate governance module for Quebec LSAQ-compliant companies:

- **Resolutions**: Board and shareholder resolutions with full lifecycle (Draft → Proposed → Adopted/Rejected/Superseded)
- **Director Register**: Track active and former directors with appointment/end dates, domicile (LSAQ Canadian residency), and linked resolutions
- **Officer Register**: Track corporate officers (President, VP, Secretary, Treasurer, DG) with appointment history
- **Compliance Calendar**: Annual compliance events (REQ annual declaration, AGM, director elections, auditor appointment, financial approval) with automatic deadline reminders
- **Minute Book**: Filtered document view for minute book sections, integrated with the document management system
- **Signatories**: Name who signs a resolution and in what capacity, per resolution. Without them the PDF falls back to the board in office on the meeting date, so a reprinted document names the directors of the day rather than today's
- **PDF Generation**: Branded resolution PDFs with dark banner header, Symbifox corporate styling, signature blocks, and "Livre des minutes" footer — ready for printing and signing

### Knowledge Matrix PDF Report

Generate professional branded PDF reports directly from any knowledge matrix:

- **One-click printing**: Blue "Imprimer PDF" button in the matrix form header (not buried in the gear menu)
- **Symbifox branding**: Dark banner with company logo, accent blue bar, and Lexend typography via `bf_lexend` (optional: the stylesheet link degrades gracefully when the module is absent)
- **KPI summary**: Four metric cards (Total, Completed, In Progress, Overdue) with a visual progress bar
- **Section grouping**: Items organized by section (sorted by sequence), each with a compact 6-column table (ID, Element, Status, Priority, Deadline, Assigned)
- **Color-coded badges**: Green for done/accepted, blue for in_progress, grey for pending, red for overdue; priority badges for urgent (red) and high (orange)
- **Smart file naming**: Downloaded as `Matrice_[name]_[date].pdf`
- **Paper format**: US Letter portrait with optimized margins (15/10/7/7mm) for wkhtmltopdf rendering
- **UTF-8 safe**: Uses `<div class="article">` wrapper pattern ensuring proper charset encoding (no mojibake)

### Matrix Report Emailing

Send branded PDF reports by email — manually or on a configurable schedule:

- **Send Wizard**: "Envoyer rapport" button on matrix form opens a dialog with pre-filled recipients, subject, body (with progress stats), PDF preview, and send action
- **Branded Email**: Symbifox email wrapper with logo, "Matrice de connaissances" header, a footer carrying the company name, contact info, and privacy links
- **Configurable Recipients**: Set default recipients per matrix via the "Envoi de rapport" tab
- **Flexible Scheduling**: Four frequency options — Weekly (pick day of week), Biweekly (same day, even ISO weeks), Monthly (pick day 1-28), Custom interval (N days)
- **Daily Cron**: Runs at 08:00 EST, checks all active non-template matrices with `auto_send=True` and sends reports to matrices that are due
- **Audit Trail**: Each send updates `last_report_date`, posts a chatter note with recipient names, and attaches the PDF
- **PDF Preview**: Preview the PDF directly from the wizard before sending

- **Automated Cron**: Daily compliance deadline check creates activities for Corporate Governance Managers when events are due within 30 days

#### Resolution Types

| Type | Description |
|------|-------------|
| Board Resolution | Standard board resolution |
| Shareholder Resolution | Standard shareholder resolution |
| Written Board Resolution | Written resolution in lieu of meeting |
| Written Shareholder Resolution | Written shareholder resolution |

#### Subject Categories

Officer Appointment, Director Election, Dividend Declaration, Share Issuance, Bylaw Amendment, Contract Approval, Bank Authorization, Auditor Appointment, Fiscal Year, Financial Approval, Dissolution, Other

### Automatic Follow-up Activities

Daily cron job creates Odoo activities to keep matrix items on track:

- **Approaching deadline** (≤7 days): Activity assigned to item owner with deadline date
- **Overdue items**: Urgent activity when deadline has passed and item is still pending/in_progress
- **Stale items** (>30 days no update): Reminder activity for items stuck in_progress with no recent writes
- **Deduplication**: Each pass checks for existing activities of the same type to avoid duplicates
- **Auto-close**: When an item's state changes, all related follow-up activities are automatically marked done
- **3 custom activity types**: `knowledge_deadline`, `knowledge_overdue`, `knowledge_stale`

### Bulk Operations

Server actions for efficient multi-select operations:

- Assign to Me
- Start (In Progress)
- Mark as Done
- Mark as N/A
- Reset to Pending

### Data Management

- **CSV Import Wizard**: Import matrices from spreadsheet exports
- **Template System**: Create template matrices and copy to new projects
- **Section Management**: Customize sections for your methodology

### Credential Storage

- **Encrypted Storage**: Passwords and API keys encrypted at rest using Fernet symmetric encryption
- **File Support**: Upload SSH keys, certificates (.pem, .key, .p12, .ppk)
- **Type-Based Fields**: 9 pre-configured credential types with configurable field visibility
- **Lifecycle Management**: Track expiration dates, rotation history, and verification status
- **Access Control**: Restricted credentials visible only to managers
- **Password Rotation**: Wizard with audit trail and reason tracking

## Installation

1. Copy the `project_knowledge_matrix` folder to your Odoo addons directory
2. Update the apps list: **Apps** → **Update Apps List**
3. Search for "Project Knowledge Matrix" and click **Install**

### Dependencies

- `project` (Odoo Project Management)
- `mail` (Discuss/Chatter)
- `bf_onboarding_base` (brand fields on `res.company` for PDF reports and email templates)
- `cryptography` (Python package for credential encryption)

## Configuration

### Sections

The module comes with 10 universal implementation sections:

| Code | Section | Purpose |
|------|---------|---------|
| DIS | Discovery & Objectives | Goals, pain points, success criteria, scope |
| PPL | People & Organization | Stakeholders, decision-makers, SMEs, users |
| CUR | Current State | Existing systems, processes, what works |
| REQ | Requirements | Functional/non-functional needs, priorities |
| TEC | Technical Environment | Infrastructure, integrations, constraints |
| DAT | Data & Migration | Data sources, quality, mapping, cutover |
| SEC | Security & Compliance | Access control, audit, regulatory |
| CFG | Configuration & Setup | Settings, workflows, customizations |
| TRN | Training & Adoption | Users, change management, documentation |
| GLV | Go-Live & Support | Cutover, hypercare, ongoing support |

Sections can be customized at: **Knowledge Matrix** → **Configuration** → **Sections**

### Security Groups

| Group | Permissions |
|-------|-------------|
| Knowledge Matrix User | Read/write matrices and items for accessible projects |
| Knowledge Matrix Manager | Full access to all matrices, items, and sections |
| Document User | Read/write documents and distributions for accessible projects/partners |
| Document Manager | Full access to documents, types, delete permissions |
| Distribution & Acknowledgment | Feature switch, granted by the settings checkbox. Grants nothing on its own: it carries the three distribution menus and answers "is the subsystem on?" for the crons, the dashboard and the report |
| Credential User | Read/write credentials for accessible projects |
| Credential Manager | Full access to credentials, view encrypted passwords, manage types |
| Corporate Governance Manager | Full access to resolutions, directors, officers, compliance events |

## Usage

### Creating a Matrix

1. Navigate to **Knowledge Matrix** → **Matrices**
2. Click **Create**
3. Enter a name and select a project (or mark as template)
4. Add items manually or import from CSV

### Working with Items

Items support four states:

- **Pending**: Not yet started (yellow)
- **In Progress**: Currently being worked on (blue)
- **Done**: Completed (green)
- **N/A**: Not applicable to this project (gray)

#### Planning Fields

- **Priority**: Set urgency level (stars in list/kanban)
- **Deadline**: When this information is needed
- **Phase**: Which implementation phase this belongs to

### Creating Tasks from Items

1. Open a knowledge item
2. Click **Create Task** in the header
3. A project task is created with:
   - Name: `[SEC] A1: Define MFA requirements`
   - Description: Info provider, required inputs, deliverable
   - Assignee and deadline from the item
4. The item automatically moves to "In Progress"
5. Task appears in the project's task views

### Managing Dependencies

1. Open an item that depends on other information
2. Go to **Tasks & Dependencies** tab
3. Add items to **Blocked By** field
4. The item shows "Blocked" indicator until dependencies are done

### Filtering Items

Use pre-configured filters to focus on what matters:

- **Overdue**: Items past their deadline
- **Due This Week**: Coming up soon
- **High Priority**: Urgent and high priority items
- **Blocked**: Waiting on other items
- **By Phase**: Filter by implementation stage

### Importing from CSV

1. Go to **Configuration** → **Import from CSV**
2. Select the target matrix
3. Upload your CSV file
4. Configure delimiter and header rows to skip
5. Click **Import**

**Expected CSV format** (semicolon-delimited):

```
decision_id;section;element_decision;questionnaire_location;phase_coverage;info_provider;required_inputs;deliverable;notes
DIS1;Discovery;Top 3 Objectives;Q1;S1;Sponsor;3 objectives + KPIs;Project Charter;Defines success criteria
```

### Using Templates

1. Create a matrix with **Is Template** checked
2. Add all standard items for your methodology
3. When starting a new project, duplicate the template
4. Uncheck **Is Template** and link to the project

## Data Model

### Document Models

#### project.document

| Field | Type | Description |
|-------|------|-------------|
| name | Char | Document name |
| code | Char | Unique reference code |
| type_id | Many2one | Document type |
| project_id | Many2one | Linked project (optional) |
| is_internal | Boolean | Internal policy/procedure flag |
| state | Selection | draft / active / archived |
| expiration_date | Date | When document expires |
| review_date | Date | When document needs review |
| version_ids | One2many | Document versions |
| distribution_ids | One2many | Distribution records |

#### project.document.version

| Field | Type | Description |
|-------|------|-------------|
| document_id | Many2one | Parent document |
| version_number | Char | Version string (e.g., "1.0", "2.1") |
| state | Selection | draft / released / superseded / withdrawn |
| change_type | Selection | major / minor / editorial |
| changelog | Html | What changed in this version |
| attachment_id | Many2one | Attached file |
| release_date | Date | When version was released |
| effective_date | Date | When version becomes effective |

#### project.document.distribution

| Field | Type | Description |
|-------|------|-------------|
| version_id | Many2one | Distributed version |
| recipient_type | Selection | partner / employee |
| partner_id | Many2one | Client recipient |
| user_id | Many2one | Employee recipient |
| state | Selection | pending / acknowledged / superseded / recalled |
| distribution_date | Date | When distributed |
| acknowledged_date | Datetime | When acknowledged |
| is_outdated | Boolean | Newer version exists (computed) |

### Knowledge Models

#### project.knowledge.matrix

| Field | Type | Description |
|-------|------|-------------|
| name | Char | Matrix name |
| project_id | Many2one | Linked project |
| is_template | Boolean | Reusable template flag |
| item_ids | One2many | Knowledge items |
| progress | Float | Completion percentage (computed) |
| recipient_ids | Many2many | Default report email recipients |
| auto_send | Boolean | Enable automatic report sending |
| send_frequency | Selection | weekly / biweekly / monthly / custom_days |
| send_day_of_week | Selection | Day of week for weekly/biweekly (0=Monday) |
| send_day_of_month | Integer | Day of month for monthly (1-28) |
| send_interval_days | Integer | Custom interval in days |
| last_report_date | Datetime | Last report sent timestamp |

#### project.knowledge.item

| Field | Type | Description |
|-------|------|-------------|
| decision_id | Char | Unique identifier (e.g., DIS1, SEC3) |
| name | Char | Decision element description |
| section_id | Many2one | Category section |
| state | Selection | pending / in_progress / done / na |
| priority | Selection | 0 (Low) to 3 (Urgent) |
| deadline | Date | When information is needed |
| phase | Selection | Implementation phase |
| assigned_user_id | Many2one | Responsible person |
| task_ids | Many2many | Linked project tasks |
| blocked_by_ids | Many2many | Dependency items |

#### project.knowledge.section

| Field | Type | Description |
|-------|------|-------------|
| code | Char | Short code (DIS, SEC, etc.) |
| name | Char | Display name |
| description | Text | Section purpose |
| sequence | Integer | Sort order |
| color | Integer | Kanban color index |

### RACI Model

#### knowledge.raci.stakeholder (SQL View)

| Field | Type | Description |
|-------|------|-------------|
| partner_id | Many2one | Stakeholder (res.partner) |
| partner_name | Char | Partner name |
| project_id | Many2one | Project |
| project_name | Char | Project name |
| role | Selection | R (Responsible) / A (Accountable) / C (Consulted) / I (Informed) |
| item_count | Integer | Number of items with this role |

### Corporate Governance Models

#### corporate.resolution

| Field | Type | Description |
|-------|------|-------------|
| name | Char | Resolution title |
| sequence | Char | Auto-generated reference (RES-YYYY-NNN) |
| resolution_type | Selection | board / shareholder / written_board / written_shareholder |
| meeting_type | Selection | regular / special / agm / written |
| subject_category | Selection | 12 categories (dividend, director election, etc.) |
| status | Selection | draft / proposed / adopted / rejected / superseded |
| meeting_date | Date | Date of meeting or written resolution |
| whereas_text | Html | ATTENDU QUE preamble clauses |
| resolved_text | Html | IL EST RESOLU QUE body |
| mover_id | Many2one | Mover (res.partner) |
| seconder_id | Many2one | Seconder (res.partner) |
| vote_for / vote_against / vote_abstain | Integer | Vote counts |
| unanimously_adopted | Boolean | Unanimous adoption flag |
| effective_date | Date | When resolution takes effect |
| company_id | Many2one | Company |

#### corporate.resolution.signatory

| Field | Type | Description |
|-------|------|-------------|
| resolution_id | Many2one | Parent resolution |
| sequence | Integer | Print order in the signature block |
| partner_id | Many2one | Signatory (res.partner) |
| capacity | Selection | Capacity in which the person signs, or `other` |
| capacity_custom | Char | Free-text capacity, required when `capacity` is `other` |
| capacity_label | Char | Computed label actually printed under the name |
| purpose | Char | Why this person signs (attestation, disclosure of interest, …) |

#### corporate.director

| Field | Type | Description |
|-------|------|-------------|
| partner_id | Many2one | Director (res.partner) |
| appointment_date | Date | Date of appointment |
| end_date | Date | Date of departure |
| end_reason | Selection | resignation / removal / term_expired / other |
| appointment_resolution_id | Many2one | Linked appointment resolution |
| end_resolution_id | Many2one | Linked end resolution |
| domicile | Char | Domicile (LSAQ Canadian residency) |
| is_active | Boolean | Computed from end_date |
| company_id | Many2one | Company |

#### corporate.officer

| Field | Type | Description |
|-------|------|-------------|
| partner_id | Many2one | Officer (res.partner) |
| title | Selection | president / vice_president / secretary / treasurer / director_general / other |
| title_custom | Char | Custom title when title=other |
| appointment_date | Date | Date of appointment |
| end_date | Date | Date of departure |
| appointment_resolution_id | Many2one | Linked appointment resolution |
| is_active | Boolean | Computed from end_date |
| company_id | Many2one | Company |

#### corporate.compliance.event

| Field | Type | Description |
|-------|------|-------------|
| name | Char | Event name |
| event_type | Selection | annual_declaration / agm / director_election / auditor_appointment / financial_approval / bylaw_review / other |
| fiscal_year | Char | Fiscal year reference |
| due_date | Date | Compliance deadline |
| completed_date | Date | When completed |
| status | Selection | Computed: upcoming / due_soon / overdue / completed |
| resolution_id | Many2one | Linked resolution |
| filing_reference | Char | REQ confirmation number |
| reminder_sent | Boolean | Cron reminder flag |
| company_id | Many2one | Company |

## API Examples

### Create a matrix programmatically

```python
matrix = env['project.knowledge.matrix'].create({
    'name': 'Odoo Implementation Checklist',
    'project_id': project.id,
})
```

### Add items with planning fields

```python
env['project.knowledge.item'].create({
    'matrix_id': matrix.id,
    'decision_id': 'DIS1',
    'section_id': env.ref('project_knowledge_matrix.section_dis').id,
    'name': 'Define project objectives',
    'info_provider': 'Executive Sponsor',
    'deliverable': 'Project Charter',
    'priority': '2',  # High
    'phase': '1_discovery',
    'deadline': '2026-02-01',
})
```

### Create a task from an item

```python
item = env['project.knowledge.item'].browse(item_id)
item.action_create_task()  # Creates linked task and opens it
```

### Check blocked status

```python
blocked_items = env['project.knowledge.item'].search([
    ('is_blocked', '=', True),
    ('state', 'not in', ['done', 'na'])
])
```

## Customization

### Adding Custom Sections

```xml
<record id="section_custom" model="project.knowledge.section">
    <field name="code">CUS</field>
    <field name="name">Custom Section</field>
    <field name="description">Your custom category</field>
    <field name="sequence">200</field>
    <field name="color">5</field>
</record>
```

### Extending the Item Model

```python
class KnowledgeItemExtended(models.Model):
    _inherit = 'project.knowledge.item'

    client_contact_id = fields.Many2one(
        'res.partner',
        string='Client Contact',
        help='Client-side contact for this item'
    )
```

## Technical Notes

### Odoo 18 Compatibility

This module follows Odoo 18 best practices:

- Uses `mail.thread` and `mail.activity.mixin` for collaboration
- Implements `tracking=True` for field change logging
- Uses `@api.depends` for computed stored fields
- Follows naming conventions (`_id` suffix for Many2one, `_ids` for x2many)

### Performance

- Indexed fields on commonly filtered columns (decision_id, state, section_id, phase, priority)
- Stored computed fields for progress statistics and task counts
- Efficient SQL constraints for uniqueness

## Changelog

### 18.0.11.5.0

- **Distribution is now optional and ships off**: a feature switch under Settings > Knowledge Base hides the three distribution menus, skips the two distribution passes of the daily maintenance job, and drops three dashboard blocks and two blocks of the biweekly email report. Nothing is deleted: distributions and acknowledgments keep their rows, and one checkbox brings the whole subsystem back. The review-and-expiration pass is deliberately not tied to the switch, since it reads no distribution.
- **The switch is a group, not a system parameter**: the group is what hides the menus, so the crons, the dashboard and the report all read that same group rather than a parameter that could drift away from it. One source of truth, tested from both sides.
- **Dashboard blocks are absent, not zeroed**: a block rendered as zero reads as a fact ("no overdue acknowledgment"). A missing key says what is true, which is that nothing is being counted, and the template uses it as its display condition.
- **Migration detaches the three menus**: a `<menuitem groups="...">` compiles to `Command.link` only, so changing the attribute adds the new group and never removes the old one. On an existing database the menus would have kept "Document User" and stayed visible with the feature off. A fresh install carries no such residue, which is why the unit tests alone could not have caught it.
- **Biweekly report template refreshed**: it lives in a `noupdate="1"` file, so a pre-migration deletes it and lets the data load recreate it with its new display guards; the post-migration puts the language slots back in step.

### 18.0.11.4.0

- **Fixed credential counters that always read zero**: the dashboard advertised "Expiring soon: 0, Expired: 0" whatever the dates in the database. The counters looked for credentials in the `active` state whose expiry had passed or was near, but it is the daily job that moves those credentials OUT of `active`, into `expiring` and `expired`. The dashboard contradicted the module's own bookkeeping, and the only population it could count was the one the cron had not processed yet. The defect lived in four places, not one: the dashboard counter, its two client-side drill-downs, the three figures of the biweekly email report, and that report's two drill-downs. All four now read the status.
- **Dashboard folded into grouped queries**: `get_dashboard_data` goes from 47 `search_count` calls to 28. Counters that partition one table by a key are a single `_read_group`, and the total becomes the sum of the cells rather than one more question. Anything that reads a date keeps its own query, since a date range is not a grouping key.
- **Drill-down tests**: one test executes each drill-down domain and compares the count it returns to the figure of the counter it accompanies. A drill-down domain is written by hand, far from its counter, and nothing stopped the two from diverging. Another checks that a document drill-down filters only on states `project.document` actually defines.

### 18.0.11.3.1

- **Resolution signatories**: a resolution can now name who signs it and in what capacity. The PDF template used to print "Administrateur" under every name and to pull those names from the director register whatever the resolution type, so a shareholders' resolution whose operative part expressly excludes any director vote contradicted itself on paper.
- **Date-bounded fallback**: with no signatories, the block falls back to the board in office **on the meeting date** rather than today, so a resolution reprinted years later does not name a director elected after the fact.
- **The mover fallback names without asserting**: when only a mover is known, the block prints the name and no capacity rather than guessing one.

### 18.0.11.2.0

- **Eight scheduled jobs become six**: the three passes that create activities on documents (pending acknowledgments, review and expiration reminders, outdated client documentation) had one cron each, two of which fired on the same minute. They now run under a single daily job, "Documents : entretien quotidien". Each pass keeps its own savepoint: three crons meant three transactions, so one failing pass left the other two to do their work, and a naive merge would have lost that quietly. The biweekly email report keeps its own job — its cadence and its failure mode have nothing to do with an activity pass. A post-migration removes the three merged jobs, which `noupdate="1"` would otherwise leave running alongside the new one, duplicating every activity.
- **Fixed expiration alerts that never fired**: review reminders and expiration alerts shared one flag per threshold. The pass handled reviews first and flagged the document; the expiration search then excluded it on that same flag. A document carrying both a review date and an expiration date therefore never got its expiration alert — and carrying both is the common case, not the exception. Expiration alerts now have their own four flags, and marking a document reviewed no longer re-arms them: its expiration date has not moved.
- **One flag write per threshold** instead of one per document.
- **Scheduled-job inventory test**: every cron the module declares must point at a method that exists. A cron whose method was renamed fails silently, once a day, and nothing surfaces it.

### 18.0.11.1.0

- **Test suite**: the module ships 60 tests covering the distribution counters, the credential vault, corporate governance and the matrices, plus boundary checks that every model of the module has access rights, that no relational field points at `hr.*`, and that the manifest names no missing file. Two of them count the SQL calls behind the document counters, so a return to a per-record `search()` fails the suite even though the values stay correct.
- **Fixed "Supersede" on a knowledge item**: `action_supersede` copied the record without changing `decision_id`, which is unique per matrix, so the button raised a database error on every call. The successor now takes the first free number of the same prefix (superseding `A1` in a matrix that runs to `A3` yields `A4`).
- **Fixed a stale recipient name**: `recipient_name` on a distribution is a stored computed field whose dependencies ignored the linked record's name, so a contact renamed after the fact kept its old name on screen, in reports and in acknowledgment reminders. A post-migration replays the existing rows.
- **Fixed a cached credential secret**: the "Restricted" mask is decided from the current user's groups, but neither decrypted field declared it. Within a single transaction, a secret read by a manager came back in clear for the next read, whoever made it. `depends_context` now set on `password` and `api_key`.
- **Dashboard model declaration**: `knowledge.dashboard` was a `Model` with `_auto = False`, which made Odoo log "has no table" at ERROR level on every registry load and then try to recreate the table. It is now an `AbstractModel`, which says the same thing without misleading the registry.

### 18.0.11.0.0

Slimming release. 298 net lines removed, two dead models dropped.

- **Software catalogue removed**: `document.software` and `document.software.version` are gone. They carried no records and no attached documents; version tracking belongs to a dedicated hosting module.
- **`hr` dependency removed**: two typed `Many2one` fields to `hr.department` made `hr` a hard dependency for a field almost never filled. A fresh install now pulls six fewer modules. The pre-migration logs any value it is about to drop.
- **Two N+1 computations rewritten**: `_compute_distribution_count` and `_compute_distribution_stats` ran one `search()` per record, on fields shown in the default list and kanban views. They are now a single `_read_group`, with the `@api.depends` that were missing. On a two-hundred-document list, the counters go from adding roughly 300 ms to adding about 10.

### 18.0.10.3.0

Catch-up release covering everything since 18.0.9.12.3. The intermediate states were never pushed, so this is a single commit; see git history for the per-file breakdown.

- **Clickable dashboard report**: every figure in the documentation report email now links to the Odoo list that produced it. Each of the 35 drill-down actions carries a domain that mirrors its counter exactly, so the list can never contradict the number you clicked.
- **Fixed a silently wrong metric**: "documents without a version" filtered on `version_count`, a non-stored computed field. Odoo 18 drops such a leaf from the domain **without raising**, so the counter returned the active-document count instead. It now filters on `version_ids`.
- **Fixed the 60-90 day review tile**: it carried `t-att-t-attf-style`, which is not a QWeb directive, so the figure rendered with no styling at all; `{{ }}` also does not interpolate inside a `t-att-` expression.
- **Document bodies** (18.0.10.0.0): author document content directly in Odoo — typed sections per document type, publish/unpublish tracking, content hashing that survives a no-op editor round-trip, per-version frozen section snapshots, and a branded PDF.
- **Dependency change**: `bluefox_branding` → `bf_onboarding_base`. The brand fields (`report_brand_*`) live on `res.company` in the latter; the white-label panel is optional, and without it documents simply use the instance's default colours.

### 18.0.9.12.3

- Documentation and metadata sync (license/LICENSE; README dependency list and branding reference corrected). See git history for intermediate changes.

### 18.0.9.7.0

- **Matrix Report Emailing**: Send branded PDF matrix reports by email via wizard or configurable automatic schedule
- **Send Wizard**: "Envoyer rapport" button on matrix form with pre-filled recipients, subject, body (progress stats), PDF preview, and send action
- **Flexible Scheduling**: Four frequency options — weekly (pick day), biweekly (even ISO weeks), monthly (pick day 1-28), custom interval (N days)
- **Daily Cron**: Checks all auto_send matrices and sends reports to those that are due
- **Branded Email**: Symbifox email wrapper with a "Matrice de connaissances" header and a footer carrying the company name
- **New Fields on Matrix**: `recipient_ids`, `auto_send`, `send_frequency`, `send_day_of_week`, `send_day_of_month`, `send_interval_days`, `last_report_date`
- **New Tab**: "Envoi de rapport" notebook page on matrix form with recipient configuration and scheduling controls

### 18.0.9.6.0

- **Knowledge Matrix PDF Report**: Branded Symbifox PDF report with dark banner, company logo, KPI cards (Total, Completed, In Progress, Overdue), visual progress bar, and items grouped by section in compact 6-column tables with color-coded status/priority badges
- **Dedicated Print Button**: Blue "Imprimer PDF" button in matrix form header (outside gear menu) for one-click report generation
- **Custom Paper Format**: US Letter portrait with 15/10/7/7mm margins and 90 DPI for optimal wkhtmltopdf rendering
- **Smart File Naming**: PDF downloaded as `Matrice_[name]_[date].pdf`

### 18.0.9.5.0

- **Automatic Follow-up Activities**: Daily cron creates activities for approaching deadlines (≤7 days), overdue items, and stale items (>30 days without update)
- **3 Custom Activity Types**: `knowledge_deadline`, `knowledge_overdue`, `knowledge_stale` with distinct icons and labels
- **Auto-close on State Change**: When an item's state changes, all related follow-up activities are automatically marked done
- **Deduplication**: Each cron pass checks for existing activities to avoid duplicates

### 18.0.9.4.0

- **Chatter Knowledge Capture**: Lightbulb button on task chatter messages creates pre-filled knowledge items with auto-generated MSG-prefixed IDs, blockquote attribution, linked task, and auto-assigned matrix
- **Dashboard Project Filter**: Dropdown selector filters all 9 KPI metric sections by project; corporate governance section hidden when filtered; accessible via project smart button
- **RACI Stakeholder Summary**: SQL pivot view aggregating Responsible/Accountable/Consulted/Informed roles from 4 data sources (assigned_user, decision_maker, M2M consulted, M2M informed); accessible from main menu and project smart button
- **Inline Matrix Tab**: New "Matrice" notebook tab on project form with grouped inline-editable list, color-coded decorations, auto-matrix assignment on item creation, and empty-state placeholder
- **New Smart Buttons**: "Tableau de bord" and "RACI" buttons on project form for direct navigation
- **New Models**: `knowledge.raci.stakeholder` (SQL view), `project.task` extension

### 18.0.9.1.0

- **Corporate Governance**: Full corporate governance module with resolutions, directors, officers, and compliance calendar
- **Resolution PDF Generation**: Branded QWeb-PDF reports with Symbifox dark banner, signature blocks, and "Livre des minutes" footer
- **Director Register**: Track active/former directors with LSAQ domicile, appointment/end dates, linked resolutions
- **Officer Register**: Track corporate officers (President, VP, Secretary, Treasurer, DG) with appointment history
- **Compliance Calendar**: Annual compliance events with daily cron deadline monitoring and activity scheduling
- **Minute Book Integration**: Filtered document view for minute book sections
- **Pre-configured Compliance Events**: 5 default events (REQ annual declaration, AGM, director elections, auditor appointment, financial approval)
- **Security**: Corporate Governance Manager group with full CRUD access; Document Users get read-only access
- **Custom Paper Format**: Resolution-specific US Letter format with optimized margins for wkhtmltopdf rendering

### 18.0.8.1.0

- **Dashboard Error Handling**: Fixed OWL error when dashboard data fails to load; added proper error state with retry button
- **Email Report Styling**: Fixed conditional coloring in bi-weekly email reports (red/orange colors only appear when metrics > 0)
- **Document Types**: Expanded from 9 to 22 pre-configured types including Loi 25/compliance categories (Privacy Policy, Form, Template, Registry, Annex, Assessment, Letter)
- **Version Tracking**: Added YYYY.MM version numbering scheme based on document dates

### 18.0.8.0.0

- **Bi-weekly Dashboard Report**: Automated email report every 2 weeks with full KPI metrics
- **Manual Report Trigger**: Button in Settings to send dashboard report on-demand
- **Symbifox Styled Emails**: Professional HTML email templates with company branding
- **Recipient Configuration**: Configure report recipients through system parameters

### 18.0.7.0.0

- **KPI Dashboard**: Comprehensive dashboard as module startup page with clickable metrics
- **Conditional Indicators**: Green checkmarks when metrics are healthy, warning colors only when attention needed
- **Quick Actions**: Direct navigation to documents needing attention, outdated distributions, pending acknowledgments

### 18.0.6.0.0

- **French Canadian Translations**: Complete fr_CA translations for all menu items, fields, and UI elements
- **Document Expiration Tracking**: Automatic activity reminders at 90, 60, 30, and 7 days before expiration
- **Software Version Management**: Track software versions with support status (Supported, Extended, End of Life, Beta)
- **Review Date Tracking**: Separate review dates from expiration dates for periodic document reviews

### 18.0.5.0.0

- **Document Management**: Full document lifecycle with version control
- **9 Document Types**: Pre-configured types (Manual, Policy, Procedure, Contract, Guide, Specification, Release Notes, Training, Other)
- **Distribution Tracking**: Track which clients/employees have which document versions
- **Acknowledgment System**: Record receipt confirmation with dates and signatures
- **Outdated Detection**: Automatic flagging when newer versions exist
- **Software Catalog**: Document software products and their versions
- **Email Notifications**: Automated reminders and outdated document alerts
- **Partner Integration**: Smart button showing client's document distribution count
- **Internal Compliance**: Track employee policy acknowledgments

### 18.0.3.0.0

- **Credential Storage**: Secure storage for project credentials with Fernet encryption
- **9 Credential Types**: Pre-configured types (Email, AD, App, Database, Server, API, VPN, Cloud, Other)
- **Password Rotation**: Wizard with audit trail and reason tracking
- **Key File Support**: Upload SSH keys and certificates
- **Expiration Tracking**: Automatic state updates via daily cron job
- **Project Integration**: Smart button showing credential count
- **Security Groups**: Credential User and Credential Manager roles
- **Restricted Access**: Hide passwords from non-managers

### 18.0.2.0.0

- **Task Integration**: Create project tasks directly from knowledge items
- **Planning Fields**: Priority, deadline, and implementation phase
- **Dependencies**: Block items until prerequisites are complete
- **Universal Sections**: 10 generic sections for any implementation type
- **Enhanced Filters**: Overdue, due this week, high priority, blocked, by phase
- **Visual Indicators**: Overdue highlighting, blocked status, priority stars

### 18.0.1.0.0

- Initial release
- Core models: Matrix, Item, Section
- Full chatter integration
- CSV import wizard
- Bulk server actions
- Project smart button integration

## Contributing

Contributions are welcome! Please ensure:

1. Code follows Odoo 18 coding guidelines
2. New features include appropriate tests
3. Documentation is updated

## License

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations.
- **Requires a written agreement**: providing the module as a product or
  service to third parties, whether hosted, managed or resold.
- **Change Date**: on 2030-08-12, this version converts automatically to
  **LGPL-3.0-or-later**.

## Credits

**Author**: Les services de consultation Blue Fox, Inc.
**Website**: [symbifox.com](https://symbifox.com)

## Support

For bug reports and feature requests, please contact Symbifox Inc or open an issue on the project repository.
