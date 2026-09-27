# RSS Feeds: AI sorting (`bf_flux_ia`)

A second sort, after a list's rules and only on what they retained, so the cost
follows the retained volume, not the feed's. Reaches the model through
`bf_llm` (feature `triage`) or, without it, through `bf_ai_bridge` (endpoint
`/flux-juger`); neither is a dependency.

- One plain-language brief per list: who reads it, what helps them.
- A 0–100 score and a one-line reason per item. Below the list's threshold the
  item is set aside, still visible under "Set aside by judgement" with its reason.
- Feed content is data, never an instruction. Only scores for the ids of the
  submitted batch are accepted.
- An outage blocks nothing: unjudged items are retried; past the list's delay
  they are delivered on the strength of the rules, and the reason says so.
- **Namesakes set aside.** On a list of watched topics, the model reads each
  topic's description and scores 0 what carries the name without being about it.
- **An alert when it cannot wait.** A list can ask the model whether an item
  cannot wait for the digest. If so, an email goes out right away to the named
  people, behind four guards kept in code: recent publication (48 h by
  default), one email per event even when five sources tell it, a daily cap in
  the first recipient's time zone, named recipients. Each held-back alert keeps
  its reason on the item. The alert's Message-ID starts with `bf-flux-alerte-`,
  so an inbox watcher can recognise it.
- Extension point: `bf.flux.retenue._flux_apres_alerte()`, another channel after
  the email (a night push, for instance).
