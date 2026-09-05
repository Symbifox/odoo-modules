# AI Bridge — `bf_ai_bridge`

The **only** transport to the `claude-chatbot-bridge` service, and the **only**
system parameter saying where to reach its Unix socket.

## Why this module exists

Five in-house modules each carried their own copy of the same hand-written HTTP
frame: roughly 50 lines duplicated five times. Two divergences were asleep in
those copies:

- one module defaulted to the socket path as seen from the **host**, which does
  not exist inside the container. Hidden for as long as the system parameter
  happened to be set.
- another read a **different** parameter, one that existed in no database at
  all. It worked only because its hardcoded default happened to match the real
  setting.

The scenario that hurts: the day the socket moves, you change the setting
visible in Settings and the second module keeps the old path without a word.
Whatever it powers stops working, and nothing says why.

## What the module exposes

Abstract model `bf.ai.bridge`, for any caller holding an environment:

| Call | Returns |
|---|---|
| `env["bf.ai.bridge"].call(endpoint, payload, timeout=100, headers=None)` | the decoded JSON response |
| `env["bf.ai.bridge"].stream(endpoint, payload, timeout)` | a byte generator, as it arrives |
| `env["bf.ai.bridge"].socket_path()` | the configured path |
| `env["bf.ai.bridge"].available()` | whether the socket exists |
| `env["bf.ai.bridge"].check_available()` | raises a `UserError` naming the parameter to fix |

Environment-free functions, `bf_ai_bridge.tools.transport.post` and `.stream`,
serve callers whose cursor is already closed: a chat stream consumed after the
response has returned, or a detached worker thread. Those capture
`socket_path()` **before** they leave.

Exceptions are not wrapped. `socket.timeout`, `ConnectionRefusedError`,
`FileNotFoundError` and `ValueError` reach the caller as they are, exactly as
they did before the transport was unified.

## The socket parameter

`bf_ai_bridge.socket`, default `/run/claude-bridge/bridge.sock`, which is the
path **as seen from inside the Odoo container**. The host keeps the socket
elsewhere and bind-mounts it there.

Set it under **Settings → Gen → Bridge Socket Path** when the assistant module
is installed, or directly in system parameters when it is not.

On install, the value of an older per-module parameter is carried over when the
new one is not set, so a tenant that had pointed the socket elsewhere does not
lose it.

⚠️ The old keys are **removed** by a migration belonging to the assistant
module. On a tenant where the assistant is **not** installed, that migration
never runs: remove the stale key by hand after the switch, or it stays there
reading as though it still drove something.

## The tenant parameter

`bf_ai_bridge.tenant`, **no default**, read through
`env["bf.ai.bridge"].tenant()`. It says which system is calling the bridge, and
the module that owns the connection owns that answer too.

The absence of a default is the point. A hardcoded default is right for
whoever wrote it and wrong everywhere else, without saying so: a module
installed on a second system would announce the first, and the bridge would
serve it another customer's data. A call that fails beats a call that succeeds
against the wrong account, so `tenant()` raises a `UserError` naming the
parameter to set rather than guess.

An older per-module key is read **at runtime**, not only carried over on
install: this module installs *before* the one that used to hold the value, and
an installation order must not decide whether a call goes out under the right
tenant. The 18.0.1.1.0 migration writes the value under the new name on a
system that already has the module, so Settings shows one truth instead of two
keys saying the same thing.

## Why a leaf module rather than a model inside the assistant

Two measured reasons, not theoretical ones.

1. **The assistant is not installed everywhere.** On tenants where the chat
   module is absent, other modules are still installed and still call the
   bridge. A hard dependency on the chat module would install a visible
   application nobody asked for.
2. **Licences do not stack.** The callers sit under different licences, and one
   of them ships without a `LICENSE` file of its own. This module is LGPL-3,
   carries its `LICENSE`, and depends only on `base`: the weakest dependency
   that still renders the service.

## Tests

```
odoo -d <database> -u bf_ai_bridge --test-enable --test-tags /bf_ai_bridge
```

18 tests: the HTTP frame against a real throwaway `AF_UNIX` socket (JSON round
trip, chunked encoding, error status, missing socket), refusal of header
injection (CR/LF in both name and value), the stream yielded chunk by chunk
whatever the network fragmentation, and the carry-over of the older parameters.
