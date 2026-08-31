# Editorial Workshop (`bf_editorial`)

An editorial calendar, measured cadence and publication gates for the Odoo
Community blog.

## The problem

Odoo Community knows whether a post is published. It knows nothing else: not
the pillar it belongs to, not the cadence of the stream, not what blocks what,
not whether the translation has caught up. Social Marketing, which would cover
part of this, is Enterprise only.

The reflex is to keep the calendar somewhere else: a file, a spreadsheet, a
Notion board. And that calendar drifts, because it **retypes by hand facts the
database already holds**: last publication date, visit counts, word counts,
publication state.

## The principle

**Store decisions only, derive everything else.**

A decision: an article's pillar, the target cadence, the languages required, a
publication dependency, a fact-check verdict.

Derived, and therefore never typed: word count, days since last publication,
ratio per pillar, language completeness, version drift, the state of the
pre-flight gate.

## What it does

- **Editorial entries**, attached to a post or standing alone, carrying the
  angle, the promise, the audience, the target keyword, sources, checked claims
  and the list of outstanding human steps.
- **Language slots**: one version per language with its own state, word count
  and slug frozen at publication.
- **Next-article proposal**: cadence, ratio, dependencies and readiness are all
  evaluated against the database, and every point in the ranking is explained.
- **Deterministic checks**, with no AI involved: em dashes, banned house
  phrases in French and English, empty headings, `th` without a scope, images
  without alt text, low-contrast inline colours, forgotten drafting markers,
  word floor, structural consistency between language slots.
- **Pre-flight gate**: publishing is refused, with reasons, whether the action
  is manual or scheduled.
- **Checklist templates**, applied when an entry is created and re-appliable on
  demand, so a new piece starts with the checks its kind always needs.
- **A timeline date** computed from the entry, giving the editorial calendar one
  field to sort and group on rather than several competing dates.
- **Version drift**: an article documenting a module reports that the module has
  moved since the last fact-check.
- **Signed waivers**: when a finding is acceptable on this particular article,
  the editor says so in writing and the refusal yields — on that ground, on
  that ground only, and until the text moves again.
- **Mechanical repairs**: the two findings that call for no judgement at all —
  an empty heading left by the editor, a table header without a scope — are
  fixed by one button, across every language.

## What it does not do

It holds state and runs mechanical checks. It does not judge an angle, does not
decide a topic is dead, and does not replace a human read.

It does not write. It touches post content only for the two mechanical repairs
above, which are enumerated and admit no judgement. Anything that needs one — an
em dash to replace, a sentence to recast, a missing visual — stays a human's
job.

## Things worth knowing

### Translated content is not rewritten through the ORM

A post's `content` field is translated with `html_translate` and stored as
jsonb, one key per language. An ORM write in a foreign language context
**overwrites the source slot**. This module always reads with an explicit
language context and never writes into post content.

### The `en_US` slot is ignored by design

On an instance whose source language is French, `en_US` acts as the source
slot: the website never serves its content and the editor rewrites it on every
save. The module does not check it and never reports it as a defect.

### A human edit invalidates the QA

The website editor can propagate a string from one language into another
language's slot. Any write to a post's content or title resets the QA state of
its entries to "to run": a stale green is worse than an unknown state.

### The scheduled-publishing cron ships disabled

Publishing that fires on its own is an operational decision, not an
installation default. Enable it from Scheduled Actions once the calendars are
configured.

### The native visit counter includes bots

`blog.post.visits` increments on every page render, crawlers included. The
module surfaces it as "Visits (raw)" so nobody mistakes it for a readership
figure.

## Installation

Depends on `mail`, `website_blog`, `utm`, `link_tracker`, `project`.

After installing, define at least one calendar, flag the tag categories that
act as pillars, and give each one a target share.

## Licence

BUSL-1.1. Internal use is free; providing a product or service to third parties
from this module requires a written agreement.
