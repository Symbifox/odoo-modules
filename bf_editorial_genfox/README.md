# Editorial Workshop: Gen (`bf_editorial_genfox`)

`bf_editorial` computes and refuses; it does not judge and it does not rewrite.
This module lends it judgement without taking away its reserve: **Gen proposes,
a human applies.**

## Three gestures

**Suggest the next article**, from the root of the workshop. The deterministic
ranking says what is furthest along; Gen says whether the angle still holds,
whether the pillar that is behind has only one candidate, and what it would do.

**Review an entry** — the half that regular expressions cannot reach:
repetitions, link text, drift from the declared angle, faithfulness of the
translation, house style.

**Expand and align**: a proposed text, filed beside the article and never put
in its place.

## What it does not do

**It never writes into a post by itself.** Applying a proposal is a second,
manual, logged gesture, guarded by a SHA-256 fingerprint of the text the
proposal was computed against. If the article moved in between — someone
edited it in the editor, a translation landed — applying is refused rather
than silently overwriting the newer version.

**It does not replace the deterministic QA.** It completes it. A Gen review
does not turn the pre-flight gate green; only the checks that can be verified
mechanically do that.

## Dependency on Gen

The buttons only appear if the assistant's bridge socket answers
(`bf.ai.bridge.available()`). Without it the module installs and stays quiet —
no broken button, no error on a screen that cannot do anything about it.

## Things worth knowing

A proposal is a record, not a message. It carries what was asked, what came
back, against which version, and who applied it. Months later the question
"why does this paragraph read like that" has an answer that does not depend on
anybody's memory.

A per-day ceiling is set through a configuration parameter, so a loop in a
periodic job cannot spend an afternoon's worth of calls in a minute.

## Installation

Depends on `bf_editorial`, `bf_ai_bridge`.

## Licence

BUSL-1.1. Internal use is free; providing a product or service to third parties
from this module requires a written agreement.
