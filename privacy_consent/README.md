# Consent tracking (Law 25)

[![Odoo Version](https://img.shields.io/badge/Odoo-18.0-purple.svg)](https://www.odoo.com)
[![License: BUSL-1.1](https://img.shields.io/badge/License-BUSL--1.1-blue.svg)](https://mariadb.com/bsl11/)

An Odoo 18 CE module for privacy management under Quebec's **Law 25** on the
protection of personal information: consents, document destruction and
anonymisation.

Since **v18.0.4.0.0** the engine is **multi-framework**: Law 25 is built in by
default, and optional companion modules add **GDPR (EU)**, **UK GDPR**,
**PIPEDA (Canada)** and the **Privacy Act 2020 (New Zealand)**.

---

## Table of contents

- [Overview](#overview)
- [Documentation](#documentation)
- [Features](#features)
  - [Consents](#consent-management)
  - [Document destruction](#document-destruction-and-anonymisation-v1800300)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Technical architecture](#technical-architecture)
- [Security and compliance](#security-and-compliance)
- [Client portal](#client-portal)
- [Automations](#automations)
- [Dependencies](#dependencies)
- [Licence](#licence)
- [Support](#support)

---

## Overview

**Law 25** (formerly Bill 64) modernises Quebec's legal framework for the
protection of personal information. It places new obligations on
organisations, notably to:

- Obtain **manifest, free, informed and specific consent** for each purpose
- Present consent requests in **clear and simple language**
- Document and **trace** every consent obtained
- Allow consent to be **withdrawn** at any time
- Manage **expiries** and renewals

This module provides:

**Consents**: a unified register linking the data subject, the purpose, the
context and the evidence, with a tamper-evident history and automations.

**Destruction and anonymisation** (v18.0.3.0.0): a retention calendar per
document type, classification of personal information, an immutable destruction
register (s. 3.2 of the Private Sector Act), batch destruction campaigns,
anonymisation assessments against the 3 criteria of Regulation A-2.1, r. 0.1,
and the right to erasure (s. 28.1).

**Multiple regulatory frameworks** (v18.0.4.0.0): a `privacy.framework` record
carries the statutory facts (supervisory authority, officer title, age of
consent, incident reporting deadlines, data subject rights, legal citations).
Each company picks its default framework, overridable per record (consent,
notice, retention calendar, assessment). Emails and certificates adapt
automatically to the applicable framework. **Law 25** is built in; **GDPR**,
**UK GDPR**, **PIPEDA** and the **Privacy Act 2020 (NZ)** are added through
`privacy_framework_*` companion modules (see [Dependencies](#dependencies)).

---

## Documentation

### User manual

A **complete user manual** in French is available for end users:

📖 **[User manual (French)](doc/MANUEL_UTILISATEUR.md)**

The manual covers:

- **Navigation**: menu structure and access by role
- **Dashboard**: key indicators and quick actions
- **Operations**: managing consents, pending requests, destructions
- **Configuration**: purposes, notices, preferences, retention policies, email sequences, DocuSeal
- **Client portal**: access and features for clients
- **Integrations**: contacts, projects, marketing
- **Life cycle**: a complete state diagram with transitions
- **Evidence and traceability**: evidence types and forensic data
- **Automations**: cron jobs and automatic activities
- **Roles and permissions**: access levels (User, Manager, Officer)
- **Legal compliance**: Law 25, GDPR
- **Glossary**: definitions of key terms

---

## Features

### Purpose management

- Purposes defined with a unique code and a plain-language description
- The required consent type is configurable (express opt-in or implied)
- A configurable default validity period
- Scope by channel (email, SMS, phone, video, in person)
- Scope by context (project, marketing, meeting, CRM)

**Purposes included by default (18):**

*General purposes (consulting, hosting, marketing):*

| Code | Purpose | Express opt-in | Default validity |
|------|---------|----------------|------------------|
| `marketing` | Marketing communications and newsletters | Yes | 730 days |
| `recording` | One-off meeting recording | Yes | 365 days |
| `recording_audio` | Audio recording | Yes | 365 days |
| `transcription` | Transcription of communications | Yes | 365 days |
| `training` | Internal training | Yes | 365 days |
| `reference` | Use as a client reference | No | 1095 days (3 years) |
| `logo` | Use of the logo | No | 1095 days (3 years) |
| `case_study` | Case study | Yes | 1825 days (5 years) |
| `service` | Service communications | Consent not required | Unlimited |
| `third_party` | Sharing with third parties | No | 365 days |
| `agent` | Installing a software agent (CASL) | Yes | Unlimited (for the duration of the engagement) |
| `sensibles` | High-risk processing and sensitive data | Yes | 365 days |

*Sector-specific purposes — childcare centres:*

| Code | Purpose | Express opt-in | Default validity |
|------|---------|----------------|------------------|
| `medicaments` | Administering medication | Yes | 365 days |
| `sorties` | Educational outings and excursions | Yes | 365 days |
| `baignades` | Swimming and water activities | Yes | 365 days |
| `transport` | Transporting children | Yes | 365 days |
| `depart` | People authorised to collect the child | No | 365 days |
| `surveillance` | Video surveillance of the premises | Consent not required | Unlimited (recordings kept 30 days) |

> Purposes are **extensible**: every organisation can add, change or deactivate
> them for its own sector. The last six are sector templates for childcare
> centres.

### Notice management

- Bilingual notice templates (French/English)
- **Automatic versioning** with a SHA256 hash
- Full traceability: which version was presented, and when
- A version already in use cannot be modified (immutability)

### Recording consents

- A complete workflow: Draft → Pending → Granted/Refused → Withdrawn/Expired
- A link to the **contact** (the data subject)
- An optional link to a **representative** (for minors under 14)
- A contextual link to a **project** or another record
- A traceable collection method (portal, email, signature, verbal, import)
- Timestamping of every state change
- **Built-in chatter** for the complete history

### Evidence management

- Attachments (signed PDF, screenshot, document)
- Verbal confirmation notes
- Technical metadata (IP address, user agent) for the portal
- Traceability: who collected it, when and how

### Contact preferences

- Granular management per channel:
  - Service email / marketing email
  - Phone / SMS
- A **Do not contact** flag (a global kill switch)
- Preferred language and preferred contact hours
- A categorised opt-out reason
- A change history through the chatter

### Document destruction and anonymisation (v18.0.3.0.0)

#### Retention calendar

- Retention rules per document type (contracts, invoices, HR files, projects, and so on)
- A mandatory legal basis for each rule (e.g. art. 2925 C.C.Q.)
- Active and semi-active retention periods in years
- A configurable final disposition: destroy, anonymise, archive permanently, transfer
- Built-in annual review with date tracking

**Rules included by default:**

| Code | Document type | Retention | Legal basis |
|------|---------------|-----------|-------------|
| `CTR-001` | Contracts | 6 years | Art. 2925 C.C.Q. |
| `FIN-001` | Invoices and tax documents | 7 years (6+1) | Tax Administration Act |
| `RH-001` | Employee files | 5 years (3+2) | Labour standards |
| `PRJ-001` | Project files | 5 years | Art. 2925 C.C.Q. |
| `COR-001` | Correspondence | 3 years | Practice |
| `MED-001` | Medical documents | 7 years | Health records regulation (OHS Act) |
| `SEC-001` | Credentials and passwords | 0 years | Security |
| `CST-001` | Consent registers | 3 years | Law 25 |

#### Document classification

- Classifies any Odoo record with its personal information categories
- 10 categories of personal information (identification, medical, financial, biometric, and so on)
- 4 sensitivity levels (public, internal, confidential, highly confidential)
- Direct and indirect identifiers
- A retention expiry date computed automatically
- **A model allowlist**: only models that can hold personal information may be classified (security)

#### Destruction register (immutable)

- A destruction register compliant with section 3.2 of the Private Sector Act
- **Total immutability**: no modification (except notes) and no deletion
- **SHA-256 chain**: each entry embeds the previous entry's hash — altering or deleting any link breaks the chain and becomes detectable
- **An integrity verification cron**: walks the register in order and reports any broken hash (v18.0.3.1)
- Automatic numbering (REG-YYYY-NNNNN)
- Links to destruction requests and campaigns
- Double protection: Python (`write()`/`unlink()` overrides) plus ORM record rules

#### Batch destruction campaigns

- A complete workflow: Draft → Scan → Review → Approval → Execution → Done
- Automatic scanning for documents past their retention under the calendar
- Per-line execution with individual error handling (no global abort)
- Automatic creation of destruction register entries for each destroyed document
- The option to skip individual documents

#### Anonymisation assessments (Regulation A-2.1, r. 0.1)

- Assessment against the 3 criteria of the anonymisation regulation (May 2024):
  1. **Individualisation**: can a person be singled out?
  2. **Correlation**: can data sets be linked?
  3. **Inference**: can personal information be deduced?
- Overall risk computed automatically (the highest of the 3)
- Automatic determination of whether the data is effectively anonymous
- Scheduled periodic reassessments with an automatic alert
- A reassessment chain (parent/child)

#### Right to erasure (s. 28.1)

- A server action from the contact record: "Request erasure of the data"
- Automatic creation of a destruction request covering all of the contact's classifications
- Secure destruction of credentials (cryptographic overwrite)
- An activity created for manually deleting the Nextcloud folder

### Contacts integration

- A new **"Privacy (Law 25)"** tab on the contact record
- **Visual badges**: Marketing ✓/✗, Recording ✓/✗, Reference ✓/✗
- A list of active consents
- Quick action buttons:
  - Request a consent
  - View/edit the preferences

### Projects integration

- A new **"Consents"** tab on the project record
- An overall status indicator (None / Pending / Partial / Complete)
- A list of consents linked to the project
- A button to request consents from the project's contacts

### Client portal (preference centre)

- A **"My privacy preferences"** page at `/my/privacy/preferences`
- Self-service management of communication preferences
- Consent history
- Responses to pending consent requests
- A bilingual French/English interface

---

## Installation

### Prerequisites

- Odoo 18.0 Community Edition
- Dependent modules: `base`, `mail`, `project`, `portal`, `bluefox_branding`

### Procedure

1. **Copy the module** into your `addons` directory:
   ```bash
   cp -r privacy_consent /path/to/odoo/addons/
   ```

2. **Restart Odoo**:
   ```bash
   ./odoo-bin -c odoo.conf -u base
   ```

3. **Install the module**:
   - Go to *Apps*
   - Click *Update Apps List*
   - Search for "Consent tracking" or "privacy_consent"
   - Click *Install*

---

## Configuration

### 1. Configure the purposes

Go to **Privacy > Configuration > Purposes**

For each purpose, define:
- **Code**: a unique technical identifier
- **Name**: the displayed label
- **Plain-language summary**: the text presented to data subjects
- **Consent required**: yes/no
- **Express opt-in required**: for sensitive purposes
- **Default validity**: number of days (0 = unlimited)

### 2. Create the consent notices

Go to **Privacy > Configuration > Notices**

For each notice:
1. Associate a purpose
2. Write the content in French and English
3. Click **"Create a new version"**

### 3. Configure the security groups

| Group | Access |
|-------|--------|
| **Privacy User** | Read consents, classifications, register |
| **Privacy Manager** | CRUD on consents, classifications, campaigns. Can create destruction requests |
| **Privacy Officer** | Full administration. Can approve and execute destructions and assessments |

Users are assigned to groups through *Settings > Users*.

---

## Usage

### Requesting a consent

**From a contact:**
1. Open the contact record
2. Go to the "Privacy (Law 25)" tab
3. Click **"Request a consent"**
4. Select the purpose and the notice
5. Choose whether an email should be sent
6. Confirm

**From the Privacy menu:**
1. Go to **Privacy > Operations > Consents**
2. Click **Create**
3. Fill in the information and save
4. Click **"Send the request"**

### Granting a consent

- From the **client portal**: the contact responds directly
- From the **backend**: a user can click "Grant"
- The expiry date is computed automatically

### Withdrawing a consent

1. Open the granted consent
2. Click **"Withdraw"**
3. Select the withdrawal reason
4. Optionally, update the contact preferences
5. Confirm

### Reviewing the history

Every consent has a **chatter** showing:
- Status changes
- Emails sent
- Notes added
- Date changes

---

## Technical architecture

### Data models

**Consents:**
```
privacy.purpose               # Consent purposes
privacy.notice                # Notice templates
privacy.notice.version        # Immutable versions with a SHA256 hash
privacy.consent               # Consent records (mail.thread)
privacy.consent.evidence      # Evidence and attachments
privacy.contact.preference    # Communication preferences
privacy.consent.group         # Consent groups
privacy.dashboard             # Dashboard with KPIs (transient)
```

**Destruction and anonymisation:**
```
privacy.retention.policy             # Retention policies (per consent)
privacy.retention.calendar           # Retention calendar (per document type)
privacy.document.classification      # Document classification (personal information)
privacy.destruction.request          # Destruction requests (mail.thread)
privacy.destruction.register         # Immutable destruction register
privacy.destruction.campaign         # Batch destruction campaigns (mail.thread)
privacy.destruction.campaign.line    # Campaign lines
privacy.anonymization.assessment     # Anonymisation assessments (mail.thread)
```

**Electronic signatures:**
```
privacy.docuseal.config        # DocuSeal configuration
privacy.docuseal.template      # DocuSeal templates
privacy.libresign.config       # LibreSign configuration
privacy.libresign.template     # LibreSign templates
```

**Extensions:**
```
res.partner              # Privacy tab + badges + counters
project.project          # Consents tab + overall status
```

### File structure

```
privacy_consent/
├── __init__.py
├── __manifest__.py
├── controllers/
│   ├── portal.py
│   ├── docuseal_webhook.py
│   └── libresign_webhook.py
├── data/
│   ├── mail_template.xml
│   ├── mail_template_sequence.xml
│   ├── mail_activity_type.xml
│   ├── privacy_cron.xml
│   ├── privacy_retention_cron.xml
│   ├── privacy_retention_calendar_data.xml
│   ├── privacy_destruction_register_cron.xml
│   ├── privacy_purpose_data.xml
│   └── privacy_notice_data.xml
├── doc/
│   └── MANUEL_UTILISATEUR.md
├── models/
│   ├── mail_blacklist.py
│   ├── privacy_consent.py
│   ├── privacy_consent_evidence.py
│   ├── privacy_consent_group.py
│   ├── privacy_contact_preference.py
│   ├── privacy_notice.py
│   ├── privacy_notice_version.py
│   ├── privacy_purpose.py
│   ├── privacy_dashboard.py
│   ├── privacy_retention.py
│   ├── privacy_retention_calendar.py
│   ├── privacy_document_classification.py
│   ├── privacy_destruction.py
│   ├── privacy_destruction_register.py
│   ├── privacy_destruction_campaign.py
│   ├── privacy_anonymization_assessment.py
│   ├── privacy_email_sequence.py
│   ├── privacy_docuseal_config.py
│   ├── privacy_docuseal_interface.py
│   ├── privacy_docuseal_template.py
│   ├── privacy_libresign_config.py
│   ├── privacy_libresign_interface.py
│   ├── privacy_libresign_template.py
│   ├── project_project.py
│   └── res_partner.py
├── report/
│   ├── privacy_destruction_certificate.xml
│   └── privacy_consent_certificate.xml
├── security/
│   ├── ir.model.access.csv          # 55 ACLs
│   └── privacy_security.xml         # 3 groups, 14 record rules
├── views/
│   ├── menu_views.xml
│   ├── portal_templates.xml
│   ├── privacy_consent_views.xml
│   ├── privacy_consent_group_views.xml
│   ├── privacy_dashboard_views.xml
│   ├── privacy_destruction_views.xml
│   ├── privacy_destruction_register_views.xml
│   ├── privacy_destruction_campaign_views.xml
│   ├── privacy_anonymization_assessment_views.xml
│   ├── privacy_retention_views.xml
│   ├── privacy_retention_calendar_views.xml
│   ├── privacy_document_classification_views.xml
│   ├── privacy_email_sequence_views.xml
│   ├── privacy_docuseal_views.xml
│   ├── privacy_libresign_views.xml
│   ├── privacy_evidence_views.xml
│   ├── privacy_notice_views.xml
│   ├── privacy_preference_views.xml
│   ├── privacy_purpose_views.xml
│   ├── project_views.xml
│   └── res_partner_views.xml
├── tests/
│   ├── test_privacy_dashboard.py
│   ├── test_privacy_docuseal.py
│   ├── test_privacy_email.py
│   ├── test_privacy_portal.py
│   └── test_privacy_retention.py
└── wizards/
    ├── privacy_consent_request_wizard.py
    ├── privacy_consent_request_wizard_views.xml
    ├── privacy_consent_withdraw_wizard.py
    ├── privacy_consent_withdraw_wizard_views.xml
    ├── privacy_docuseal_send_wizard.py
    └── privacy_libresign_send_wizard.py
```

---

## Security and compliance

### Audit trail

- All the main models inherit `mail.thread`
- Critical fields (`status`, `expires_at`, and so on) carry `tracking=True`
- Every change is logged in the chatter

### Notice integrity

- Notice versions are **immutable** once used
- A **SHA256** hash is generated automatically
- The content of a version linked to consents cannot be modified

### Data isolation

- Per-**company** security rules (multi-company) on every model
- **Portal** users see only their own data
- Graduated access by group (User < Manager < Officer)

### Security hardening (v18.0.3.0.0)

- **Immutable register**: double protection, Python plus ORM rules (no unlink)
- **A model allowlist** for document classification
- **Access rights verified** before any `sudo()` operation in destructions
- **Python group checks** on every sensitive action (approve, execute)
- **State transition constraints** on anonymisation assessments
- **Validation of destruction methods** (unexpected values rejected)
- **Secured cron approval**: only requests with a policy are auto-approved
- **Per-company isolation** for destruction requests and classifications

### Law 25 compliance

| Requirement | Implementation |
|-------------|----------------|
| Manifest consent (s. 14) | An explicit workflow with timestamping |
| Clear language (s. 14) | A mandatory "plain-language summary" field |
| Per purpose (s. 14) | One purpose = one consent record |
| Evidence (s. 14) | The `privacy.consent.evidence` model with attachments |
| Withdrawal (s. 14) | A wizard with a reason and propagation to the preferences |
| Expiry | A daily cron plus alerts 30 days ahead |
| Minors under 14 (s. 14) | A "given by" field for the legal representative |
| Destruction (s. 23) | Destruction requests plus an immutable register |
| Governance (s. 3.2) | A retention calendar plus a destruction register |
| Right to erasure (s. 28.1) | A server action from the contact record |
| Anonymisation (Reg. A-2.1) | Assessment against the 3 criteria with periodic reassessment |

---

## Client portal

### Available URLs

| URL | Description |
|-----|-------------|
| `/my/privacy/preferences` | Preference centre |
| `/my/privacy/consents` | Consent history |
| `/my/privacy/consent/<id>` | A consent's detail |
| `/my/privacy/consent/<id>/respond` | Responding to a request |

### Features

- **Preference management**: turning communications on/off per channel
- **A "Do not contact" button**: global opt-out
- **History**: seeing every past and present consent
- **Responding to requests**: granting or refusing directly

---

## Automations

### Scheduled jobs (cron)

| Job | Frequency | Action |
|-----|-----------|--------|
| Expiry check | Daily | Creates an activity 30 days before expiry |
| Marking expiries | Daily | Moves the status to "Expired" |
| Auto-expiry of pending requests | Daily | Expires consents left unanswered |
| Processing email sequences | Daily | Sends the scheduled reminders and renewals |
| Creating destruction requests | Daily | Creates requests according to the retention policies |
| Processing scheduled destructions | Daily | Approves and executes the due requests |
| Reassessment check | Daily | Flags anonymisation assessments that are due |
| Register integrity check | Daily | Recomputes the SHA-256 chain and alerts on alteration |

### Email templates

- **Consent request**: sent on a new request
- **Expiry warning**: available for manual or automated sending
- **Automated sequences**: configurable email sequences (reminders, renewals)

---

## Dependencies

| Module | Use |
|--------|-----|
| `base` | The `res.partner` model, base infrastructure |
| `mail` | Chatter, activities, email templates |
| `project` | Extending the `project.project` model |
| `portal` | Client portal controller and templates |
| `bluefox_branding` | Branded email and report templates |

| Python dependency | Use |
|-------------------|-----|
| `cryptography` | Encrypting passwords and API keys |
| `dateutil` | Computing reassessment dates (relativedelta) |

### Optional companion modules — regulatory frameworks (v18.0.4.0.0)

Law 25 (Quebec) is built into the main module, and **no companion module is
required** for default use. To serve other jurisdictions, install one or more of
the following data-only modules (they depend only on `privacy_consent`):

| Module | Framework added | Authority |
|--------|-----------------|-----------|
| `privacy_framework_gdpr` | GDPR (European Union) | national supervisory authority / EDPB |
| `privacy_framework_uk` | UK GDPR / Data Protection Act 2018 | ICO |
| `privacy_framework_pipeda` | PIPEDA (federal Canada) | OPC |
| `privacy_framework_nz` | Privacy Act 2020 (New Zealand) | OPC NZ |

---

## Licence

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations.
- **Requires a written agreement**: providing the module as a product or
  service to third parties, whether hosted, managed or resold.
- **Change Date**: on 2029-07-20, this version converts automatically to
  **LGPL-3.0-or-later**.

## Disclaimer

This module is provided as-is, without warranty of any kind. Use at your own
risk. Les services de consultation Blue Fox, Inc. assumes no liability for any damages arising from the use of
this software.

---

## Support

To report a problem or suggest an improvement, contact the technical team or
open a ticket in the repository.

---

## Version history

| Version | Date | Description |
|---------|------|-------------|
| 18.0.4.1.0 | 2026-07 | **Branded** consent portal: the portal templates consume the `--brand-primary` / `--brand-dark` variables from `report_brand_*` instead of hardcoded colours, falling back to the defaults |
| 18.0.4.0.0 | 2026-06 | **Multi-framework** engine: a `privacy.framework` model (plus legal bases and data subject rights), a per-company default framework overridable per record, parameterised emails and certificates. Law 25 built in, plus GDPR / UK GDPR / PIPEDA / Privacy Act 2020 (NZ) companion modules. Law 25 is **unchanged** (identical rendering, destruction register integrity chain preserved) |
| 18.0.3.1.0 | 2026-04 | SHA-256 chained destruction register (each entry embeds the previous one's hash) plus an integrity verification cron (the 8th scheduled job) |
| 18.0.3.1.4 | 2026-06 | Documentation and metadata synchronisation (licence/LICENSE). See the git history for details. |
| 18.0.3.0.1 | 2026-04-11 | QA fixes: 17 `action_*` methods with no XML-RPC return, a missing `secure_wipe` selection, register ACL (notes editable by the Officer), the PDF certificate redirected to the QWeb report, README codes corrected |
| 18.0.3.0.0 | 2026-04 | Document destruction and anonymisation: retention calendar, document classification, immutable destruction register, batch destruction campaigns, anonymisation assessments (Reg. A-2.1), right to erasure, full security audit |
| 18.0.2.0.0 | 2026-02 | Added the complete user manual, DocuSeal integration, retention policies, destruction certificates |
| 18.0.1.0.0 | 2026-01 | Initial release |

