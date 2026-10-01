# Symbifox Customer Experience: AI Analysis of Verbatims

Adds AI analysis to the customer-experience feedback records of `bf_cx`. For each
comment a client leaves, the analysis gives:

- a **sentiment**: positive, neutral or negative;
- a **one-sentence summary**;
- up to five **themes**, picked from the themes you already use when one fits.

Nothing is ever sent to the client: results land on the feedback record and in
an internal note on its chatter.

## How it works

The comment (its first 4,000 characters) and the list of known themes go through
`bf_ai_bridge`, the single transport Symbifox modules use to reach the AI bridge
service over a local Unix socket, to the bridge endpoint **`POST /cx/analyze`**.

The socket is local, but the model behind the bridge is a hosted AI model: the
comment text is sent to it for analysis. Enable the module, and the optional
daily job, with that in mind.

That endpoint runs one pass and has **no tools**. A verbatim is text written by a
third party: the process that reads it has nothing it could be talked into
using, so a comment that tries to give the AI instructions has no surface to act
on. The answer comes back as bounded JSON (sentiment, summary, themes), and Odoo
checks its shape again before writing anything.

### Shared theme vocabulary

The list of known themes combines the customer-experience themes of `bf_cx` and,
when `bf_helpdesk` is installed, the helpdesk themes it builds from closed
tickets. The same irritant therefore carries the same name in a survey comment
and in a support ticket, without either module depending on the other. Returned
themes are attached to the feedback; a theme that does not exist yet is created
in `bf_cx`.

## Usage

- **On demand**: the **"Analyser (IA)"** button on a feedback form that has a
  comment. A notification gives the result, and the details go to the chatter.
- **Optional daily analysis**: in the Customer Experience settings, "Analyse IA
  automatique des verbatims" (system parameter `bf_cx.ai_auto_analyze`). **Off
  by default.** When on, a daily job analyses up to 20 recent comments (last 30
  days) that have not been analysed yet. If the bridge is unreachable, the batch
  stops and resumes the next day.

On the feedback list: a "Sentiment (IA)" column, filters "Sentiment négatif (IA)"
and "Non analysés (IA)", and a group-by on sentiment.

### When the bridge is not available

A bridge outage never blocks anything. The record gets an internal note saying
the analysis was not done, and it can be retried later. An answer that cannot be
read is noted the same way and changes no field.

## Requirements

- A running **AI bridge service** whose socket is reachable from the Odoo
  container (system parameter `bf_ai_bridge.socket`, see the `bf_ai_bridge`
  README).
- Optional: `bf_cx.ai_bridge_timeout` sets the Odoo-side timeout, in seconds
  (default 120).

## Dependencies

- `bf_ai_bridge`
- `bf_cx`

The module is not installed automatically: install it once its dependencies are
in place.

## License

LGPL-3 for this module's own source. `bf_ai_bridge` is LGPL-3; `bf_cx` is
published under the Business Source License 1.1 (BUSL-1.1), so a deployment
that includes it is subject to its terms. Check each dependency before
redistributing.

## Changelog

| Version | Change |
|---|---|
| 18.0.1.2.3 | The fallback parser keeps at most five themes, as the model is asked. First public release. |
| 18.0.1.2.2 | Removed the unused dependency on `bf_claude_chat`. |
| 18.0.1.2.1 | Field help, settings and notes no longer claim that no data leaves the server: the comment text is sent to the AI model behind the bridge, and the texts now say so. The description no longer says the module installs itself. |
| 18.0.1.2.0 | Back in service on the dedicated, tool-less bridge endpoint `POST /cx/analyze`. Earlier versions sent the verbatim through the bridge's general chat endpoint, where tools are enabled; that path and its prompt are removed from the code, which closes a prompt-injection surface. The answer is read as structured JSON (the previous text parser stays as a fallback). The theme vocabulary is shared with the helpdesk themes of `bf_helpdesk` when it is installed. |
