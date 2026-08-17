/** @odoo-module **/

// Shared SSE streaming client for GenFox.
//
// Instead of one blocking JSON-RPC round trip (a frozen spinner that returns
// nothing on timeout), this streams the assistant answer token by token, plus
// tool activity and thinking progress — the same experience as the console.
//
// The server route (/claude-chat/stream) relays the bridge's Server-Sent
// Events. We POST via fetch (EventSource is GET-only) with a custom header the
// server requires, which same-origin XHR can set but a cross-site form cannot —
// CSRF-equivalent protection on top of auth=user.

const STREAM_URL = "/claude-chat/stream";

/**
 * Open a streaming chat request and dispatch each parsed event to `onEvent`.
 *
 * @param {Object}   opts
 * @param {Object}   opts.body     JSON payload {session_id, message, context?}
 * @param {AbortSignal} opts.signal AbortController signal (Stop button)
 * @param {Function} opts.onEvent  (eventName, dataObject) => void
 */
export async function streamChat({ body, signal, onEvent }) {
    const resp = await fetch(STREAM_URL, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "X-Claude-Stream": "1",
        },
        body: JSON.stringify(body),
        signal,
    });
    if (!resp.ok || !resp.body) {
        throw new Error(`stream HTTP ${resp.status}`);
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    let event = "message";

    for (;;) {
        const { done, value } = await reader.read();
        if (done) {
            break;
        }
        buf += decoder.decode(value, { stream: true });

        let nl;
        while ((nl = buf.indexOf("\n")) >= 0) {
            const line = buf.slice(0, nl).replace(/\r$/, "");
            buf = buf.slice(nl + 1);

            if (line === "") {
                event = "message"; // blank line terminates an SSE event
                continue;
            }
            if (line[0] === ":") {
                continue; // keep-alive comment
            }
            if (line.startsWith("event:")) {
                event = line.slice(6).trim();
                continue;
            }
            if (line.startsWith("data:")) {
                let data;
                try {
                    data = JSON.parse(line.slice(5).trim());
                } catch {
                    data = {};
                }
                onEvent(event, data);
            }
        }
    }
}

/**
 * Turn a raw tool identifier (e.g. "mcp__tentaclaude-pme__odoo_get_task" or a
 * built-in like "Read") into a short, human label for the activity chip.
 */
export function prettyToolName(name) {
    if (!name) {
        return "";
    }
    return name
        .replace(/^mcp__[a-z0-9-]+__/i, "") // drop the MCP server prefix
        .replace(/^odoo_/, "")
        .replace(/_/g, " ")
        .trim();
}
