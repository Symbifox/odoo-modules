# Persona des contacts (`bf_persona`)

Relationship intelligence layer for `res.partner` in Odoo 18 CE: store per-contact tone, addressing style, payment behavior, KPIs, and outbound c.c. rules, and surface them where they actually help — in the mail composer, on the contact form, and on a dashboard.

## What it does

### Per-contact persona record

A `contact.persona` extends `res.partner` with:

| Field area | Purpose |
|---|---|
| Tone & addressing | `tu` vs. `vous`, language preference, salutation patterns |
| Payer behavior | Average payment delay, dispute history, scheduled email cadence |
| KPIs | Custom numeric/text KPIs per contact (revenue, hours consumed, satisfaction, etc.) |
| C.c. rules | Auto-add c.c.'d recipients when composing to this contact (e.g., always c.c. the accountant) |
| Category | Free-form persona category labels |

### Composer integration

When you open the mail composer on a contact (or any record with `partner_id`), `bf_persona` injects:

- A **hint banner** with the contact's tone preference (so you don't `tu` a `vous` client by mistake)
- **Auto-cc** of any addresses configured in `contact.cc.rule` for that contact

### Background workers

Crons keep persona fields fresh from observed behavior:

| Cron | Cadence | Effect |
|---|---|---|
| `persona_seed` | Monthly | Auto-populate persona records from email signals on contacts that don't have one |
| `payment_delay` | Daily | Recompute average payment delay from `account.move` history |
| `persona_stale` | Weekly | Flag personas not refreshed in N days |
| `persona_activity` | Weekly | Aggregate observed `mail.message` cadence into the persona record |
| `persona_degradation` | Weekly | Detect contacts where the relationship is degrading (no recent contact, payment delay climbing, etc.); optionally emit a ntfy alert |

### Dashboard

A kanban view at **Contacts → Personas → Dashboard** groups contacts by category with their KPIs and last-touch indicators.

## Dependencies

| Module | Why |
|---|---|
| `contacts`, `mail`, `account` | Core Odoo |
| `project_knowledge_matrix` | KPIs link to knowledge items |
| `bf_onboarding_base` | Onboarding panel scaffolding |

## License

GNU LGPL-3. See [`../LICENSE`](../LICENSE) for the full text.
