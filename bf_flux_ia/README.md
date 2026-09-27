# RSS Feeds: AI sorting (`bf_flux_ia`)

A second sort, after a list's rules and only on what they retained, so the cost
follows the retained volume, not the feed's. Uses `bf_llm` (feature `triage`).

- One plain-language brief per list: who reads it, what helps them.
- A 0–100 score and a one-line reason per item. Below the list's threshold the
  item is set aside, still visible under "Set aside by judgement" with its reason.
- Feed content is data, never an instruction. Only scores for the ids of the
  submitted batch are accepted.
- An outage blocks nothing: unjudged items are retried; past the list's delay
  they are delivered on the strength of the rules, and the reason says so.
