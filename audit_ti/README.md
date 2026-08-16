# Audit TI - Loi 25

Odoo 18 module for managing IT compliance audits under Quebec's **Loi 25** (Act respecting the protection of personal information in the private sector).

## Features

- **14 audit elements**: structured evaluation grid covering Loi 25 requirements
- **Client × supplier matrix**: per-supplier evaluation for each element, with statuses (adequate, partial, declared, to validate, inadequate, N/A)
- **OWL dashboard**: real-time KPIs, per-client progress (stacked bars: adequate, partial, inadequate, to validate / declared / N/A) with a 100% badge for fully evaluated clients, per-supplier progress, status distribution, open watchpoints
- **Delivery workflow**: In progress / Delivered states with tracking (date, user) and automatic filtering on the dashboard and list view
- **Watchpoints**: track risks by priority (high, medium, low) with assignment
- **PDF report**: professional progress report with element coverage, supplier matrix, watchpoints, and a progress bar
- **Bulk printing**: server action to generate PDF reports for multiple clients from the list view
- **Supplier aliases**: automatic matching of supplier names (variants, abbreviations)
- **Chatter**: full history of state changes and notes per client

## Models

| Model | Description |
|---|---|
| `audit.element` | The 14 Loi 25 audit elements |
| `audit.supplier` | Evaluated IT suppliers |
| `audit.supplier.alias` | Supplier name aliases / variants |
| `audit.client` | Audited clients (with In progress / Delivered state) |
| `audit.client.supplier` | Client-supplier relationship with roles |
| `audit.assessment` | Evaluations (client × supplier × element) |
| `audit.watchpoint` | Watchpoints |
| `audit.dashboard` | OWL dashboard (`_auto=False` view) |

## Dependencies

- `base`, `mail`, `project`

## Installation

```bash
odoo -i audit_ti -d <database> --stop-after-init
```

## License

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations.
- **Requires a written agreement**: providing the module as a product or
  service to third parties, whether hosted, managed or resold.
- **Change Date**: on 2030-08-12, this version converts automatically to
  **LGPL-3.0-or-later**.

## Disclaimer

This module is provided as-is, without warranty of any kind. Use at your own risk. Les services de consultation Blue Fox, Inc. assumes no liability for any damages arising from the use of this software.

---

<sub>Authored and maintained by Les services de consultation Blue Fox, Inc. AI coding assistants were used as productivity tools during development.</sub>
