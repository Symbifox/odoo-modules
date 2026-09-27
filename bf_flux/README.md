# RSS Feeds (`bf_flux`)

RSS and Atom feeds, polled, de-duplicated and sorted by rules, then delivered
by department or by project, in Discuss and by email.

## The problem

A feed only keeps its latest items, and there is no public archive to catch up
from: whatever leaves the window between two polls is lost. And a keyword list
alone is not enough to sort a general feed. The terms that matter also show up
in quarterly earnings calls from issuers in the right industry.

## What it does

- **Each source is polled at its own pace.** Rate limiting (429), a temporary
  refusal (403, 408, 425), a 5xx and timeouts are transient and retried soon;
  any other 4xx, and content that is not a feed, is permanent. After six
  consecutive failures the source posts a warning and schedules an activity,
  once: "retried next time" must not last for weeks unnoticed.
- **Only `http(s)` links enter**, and full-text fetching refuses any address
  that resolves to a private, loopback, link-local or reserved network,
  redirects included: an item's link comes from outside.
- **An item enters once**, by its stable identifier (`dc:identifier`, then
  `guid`/`id`, then the normalised link), never by title. The same release
  received through two feeds is one item attached to both sources.
- **The working language wins.** When an item exists in two languages under the
  same identifier, the source's preferred language replaces the other, even when
  it arrives later.
- **Full text on demand**, for items a list has retained only. "Text language"
  records the language of what was actually fetched, not of what was requested.
- **A list sorts by rules, in order:** exclusions discard; one industry term is
  enough; a specialised issuer is enough on its own; a dual-line issuer is only
  noted when an industry term is already present. The reason is written on each
  retained item. Plain lines are matched case- and accent-insensitively; lines
  starting with `re:` are regular expressions, matched as written.
- **Subscriptions follow departments and projects.** Members are computed:
  employees of the departments (sub-departments included) and internal followers
  of the projects. Anyone can unsubscribe from a list without leaving their team.
- **Delivery in Discuss and by email.** One channel per list, kept in line with
  its members, and a daily or weekly email digest, or none.
- **Post an item to a project's thread** in one step.
- **A reader, "To read"**: one card per item (image, source, date, title,
  summary). Read state is per person. Opening an item — in Odoo, from Discuss
  or from the digest, all of which go through `/flux/lire/<id>`, which marks it
  read then redirects to the article — takes it off the list, as does "Mark as
  read" on one card or on a selection. The digest leaves out what the person
  already read.
- **Named subscribers**, besides departments and projects.

## What it does not do

- It does not judge relevance beyond rules. AI scoring belongs in a bridge
  module, so this one installs without a language model.
- It does not publish feeds: the Odoo blog already does.

## Extension points

- `bf.flux.retenue._flux_juger()`: judge what passed the rules (AI bridge).
- `bf.flux.element._flux_nettoyer_texte()`: strip a site's page template.
