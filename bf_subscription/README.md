# Subscriptions (`bf_subscription`)

A single register for paid subscriptions: SaaS, corporate memberships,
infrastructure, certificates, domain names, recurring professional services.

## Features

- Track subscriptions held **in your own name** or **managed on a client's
  behalf**.
- Manual and automatic correlation with vendor bills (`account.move`).
- Rebilling to the client in 3 modes: at cost / with a margin (%) / fixed
  amount.
- Automatic reminders ahead of renewal (Odoo activity), which a **renewal
  decision** puts to rest until the next cycle.
- Dashboard view: monthly-equivalent cost, upcoming renewals, consolidated MRR.
- Smart buttons on the partner record (managed / billed subscriptions).
- Subscription tags take a free color (`bf_color`), shown on the
  subscription's tags in the form and the kanban.

## Report and dashboard

- **Built-in recap**: this base module ships its own `subscription.digest`
  model (*Recap* menu), which generates a PDF subscription report (spend
  summary, upcoming renewals, dormant subscriptions, cost per managed client)
  and can send it periodically. A `subscription.digest` configuration ships by
  default, in "on demand" mode (`auto_send = False`).
- **Dashboard card (MRR)**: the Subscriptions summary card on the Symbifox
  dashboard is **not** provided by this module. It is added by the separate
  `bf_subscription_dashboard` bridge, which auto-installs when
  `bf_subscription` and `bf_dashboard` are both present.
- **Shared layout**: the recap email goes out under the shared mail layout
  (`bf_onboarding_base.bf_mail_layout`, 18.0.2.1.1 or later), like the other
  in-house emails. Without it, the content goes out undressed.
- **Renewal dates stay current**: the stored next renewal date is recomputed
  every day by the renewal-reminder job, and before each recap.
- **Subscriptions paid through expense reports**: see the
  `bf_subscription_expense` bridge.
- **A recap stays in its company**: it can be read, printed and test-sent only
  by users of its company, and only a subscription manager sends it to its
  recipients.

## Dependencies

`base`, `mail`, `account`, `analytic`, `bf_onboarding_base`, `bf_color`.

## Languages (18.0.1.7.0)

Labels and messages are written in English in the source; the French ships in `i18n/fr_CA.po`. Before this version they read in French for every user, including users set to English. Each text is written for its reader. The price-increase alert and the renewal-notice activity are in the language of the subscription's responsible person, or the company's when there is none. The digest is rendered once per recipient language: subject, body, footer and PDF. A rebill invoice's description and note are in the language of the client it is issued to, while the line left on the subscription's chatter is in the language of whoever issued it. The alert and the rebill line keep their link and emphasis in the chatter instead of showing the HTML tags.

## Licence

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations.
- **Requires a written agreement**: providing the module as a product or
  service to third parties, whether hosted, managed or resold.
- **Change Date**: on 2030-10-25, this version converts automatically to
  **LGPL-3.0-or-later**.

## Changelog

### 18.0.1.8.1

- **Multi-company isolation of the recap.** A user of another company could test-send or print a recap, which reads its company's subscriptions with full rights. A record rule now confines each recap to its company.
- **Only a manager sends a recap to its recipients.** A read-only user could trigger the send before failing on the write that follows it.

### 18.0.1.8.0

- **The recap uses the shared mail layout** instead of building its own header and footer.
- **The next renewal date no longer stays in the past.** It is recomputed daily and before each recap, so the "Upcoming renewals" table no longer comes out empty.
- **Margin per managed client counts only rebilled subscriptions**; the others show "—" instead of a negative margin.

### 18.0.1.7.0

- **Source language.** Labels and messages are written in English; the French ships in `i18n/fr_CA.po`. See the Languages section.
- **Each reader's language** for the price-increase alert, the renewal notice, the digest and the rebill invoice.
- **The chatter shows the alert and the rebill line as formatted text**, not as raw HTML tags.

### 18.0.1.6.0

- Subscription tags take a free color from `bf_color`: any hex instead of
  Odoo's twelve, and each user can keep their own for a tag by selecting its
  badge on a subscription.

### 18.0.1.5.2

- Report logo, name and colours read the document's company (`doc.company_id`), not `env.company`.

### 18.0.1.5.0

- Added: a renewal decision. "Renewal confirmed" records the date the *keep it*
  call was made and closes the standing alert; while that decision covers the
  next renewal no new one is raised, and at the following cycle the question
  asks itself again. "Reconsider" reverses it. Until now the only way to stop
  an alert was to answer it, so a decision taken in a meeting left the record
  looking undecided.
- Added: renewal alerts now carry their own activity type, so the confirm
  button closes exactly those and leaves the record's other to-dos alone. A
  migration re-points alerts already standing.

### 18.0.1.4.0

- Fixed: renewal alerts that could never go quiet. The cron raised an activity
  whenever `next_billing_date` minus the notice period fell inside the 30-day
  horizon, and re-armed every 14 days. On a monthly subscription that condition
  is always true, so the activity came back at every run, generally already
  overdue, with no decision left to make; with 30 days' notice a monthly
  subscription never left the window at all. The cron now skips a subscription
  when the notice period plus the alert horizon cover the whole cycle.
  Quarterly and longer cycles are untouched: their window still closes for most
  of the year, which is what makes the alert worth reading when it opens.

### 18.0.1.3.1

- Removed client names from the help and onboarding texts (public-release
  artefact); added the `LICENSE` file.
