# Persona des contacts — customer experience (`bf_persona_cx`)

Bridge between `bf_persona` and `bf_cx`. It auto-installs as soon as both are
present, and it has one job: what a contact told you about their experience
should be in front of you when you write to them.

## What it adds

On the persona of a contact (or of their company):

| Field | What it holds |
|---|---|
| Last feedback (12 months) | The most recent NPS or satisfaction answer with its score and comment |
| Open complaints | How many complaints of theirs are still open |
| Do not solicit | Whether the contact is excluded from surveys and testimonials |
| Feedback / complaints tabs | The full history, read-only, on the persona form |

A company persona reads the answers of everyone who works there.

## What it changes elsewhere

- **The composer banner** shows a low score with its comment, in amber, and an
  open complaint in red, next to the recipient's name.
- **The relationship state** of the persona takes them into account: an open
  complaint makes it *degraded*, a detractor or a score under 70% of its scale
  makes it *to watch*, with the reason spelled out. A promoter is context, not a
  warning.
- **The AI context** (`claude_context_summary`) carries the same line, so an
  assistant drafting a message knows what the contact said last.

The persona is refreshed as soon as a feedback or a complaint is created or
changes, not only when the daily measure runs.

## Compatibility

The exclusion flag is read through whichever field the installed `bf_cx` has
(`bf_cx_exclude_effective`, else `bf_cx_exclude`), and stays quiet when it has
neither: the bridge installs on older `bf_cx` releases without breaking the
registry.

## Dependencies

| Module | Why |
|---|---|
| `bf_persona` | The persona records and the composer banner it extends |
| `bf_cx` | Feedback, complaints and the solicitation guardrails |

## License

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations.
- **Requires a written agreement**: providing the module as a product or
  service to third parties, whether hosted, managed or resold.
- **Change Date**: on 2030-09-15, this version converts automatically to
  **LGPL-3.0-or-later**.
