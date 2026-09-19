# Symbifox — SIP phone

A WebRTC softphone (JsSIP) that lives in the Odoo web client, backed by an
Asterisk PBX you already run, plus a REST surface so a mobile client with no
SIP stack of its own can still place and receive calls.

Three distinct paths, and it helps to keep them apart:

| Path | Who carries the media | What Odoo does |
|---|---|---|
| Browser softphone | the browser (WebRTC) | serves the SIP credentials and short-lived ICE credentials |
| PBX-dialled call | the PBX | asks the PBX to ring the user, then dial the correspondent |
| Push wake | nothing | tells a sleeping phone to re-register, so the PBX can reach it |

## The browser softphone

- A floating launcher at the bottom right, coloured by SIP state (green means
  registered). Deliberately **not** in the systray: a phone is picked up, not
  hunted for in a bar of icons, and the navbar imposes its own colours on
  everything inside it.
- The panel carries a keypad, quick dial **by name** (contact search), a call
  log, and the live call (mute, hang up).
- **Caller ID**: an incoming call shows the contact's name, resolved
  server-side, with a link to the record.
- **Click to call**: every `tel:` link in the back end dials through the
  softphone.
- A **callback activity** is created automatically on a missed call, per user
  and switchable off.
- Every call is logged through `call.archive.call`, the shared call archive.

Voicemail badge is not implemented: the pinned JsSIP release does not support
the `message-summary` MWI event, which would need a SUBSCRIBE/NOTIFY
subscription or a counter served by Odoo.

## PBX-dialled calls, for a client with no SIP stack

A browser can carry the media itself. A phone application cannot, unless it
embeds a full SIP stack — a different piece of work. In the meantime the
client asks for the call and **the PBX builds both legs**:

1. the PBX rings the user's own device — their SIP extension, or their
   callback number over the trunk (the default on mobile);
2. when they answer, it dials the correspondent through the usual dialplan, so
   the business line's caller ID is what the correspondent sees. That is the
   whole point: call a customer without showing them a personal number, from
   any phone.

| Route | Effect |
| --- | --- |
| `GET /bf_softphone/mobile/v1/ping` | capability probe (public) |
| `GET /bf_softphone/mobile/v1/config` | `{enabled, extension, callback_number, default_ring}` |
| `GET /bf_softphone/mobile/v1/sip` | SIP credentials, so the app can register as an extension |
| `GET /bf_softphone/mobile/v1/contacts` | contact search for the keypad |
| `GET /bf_softphone/mobile/v1/calls` | the call log |
| `GET /bf_softphone/mobile/v1/active` | what is ringing or talking right now |
| `POST /bf_softphone/mobile/v1/call` | `{number, ring?}` → ask for the call |
| `POST /bf_softphone/mobile/v1/dtmf` | `{digits}` → answer a voice menu |
| `POST /bf_softphone/mobile/v1/hangup` | hang up |

Guards: membership of the **Phone user** group is required; numbers must be
North American ten-digit ones (the pattern refuses short codes, and therefore
911 — deliberately, since the DID's E911 address is the office, not wherever
the phone happens to be); throttling at twelve calls per five minutes per
user; and any CR/LF in an AMI value is refused, which is what stops AMI action
injection.

**What is still missing**: the PBX does not report call duration back, so the
log records the *request* (zero duration), not the conversation. An unanswered
call still leaves a trace.

### The AMI link

Settings → Phone → **PBX dialling**: host, port, user, password (encrypted at
rest with the same Fernet key as the SIP secret), trunk name and dialling
context. **Test the link** authenticates and leaves without dialling anything.

⚠️ Asterisk's AMI normally listens on loopback only. With Odoo in a container
you need an address both sides can see: a dedicated Docker network whose
gateway carries the AMI listener, with `permit=` restricted to that subnet.
Never bind AMI to `0.0.0.0` on a host with a public address.

## Push wake — ringing a phone whose app is closed

A mobile application can be a real SIP endpoint, but its registration dies
with its process. Swipe the app away, or reboot the phone, and the AOR has no
contact left for it: `Dial(PJSIP/<extension>)` never reaches it, with no error
— which reads as a temperamental phone.

So the PBX tells Odoo **before** it dials:

| Route | Effect |
| --- | --- |
| `POST /bf_softphone/pbx/v1/wake` | `{token, ext, from?, call_id?}` → pushes `{"type":"call"}` to that extension's devices |

In the dialplan, the wake context sits on a `Local/` leg **in parallel** with
the others, so the browser and the mobile number ring immediately and pay no
wait. It reads the AOR's contacts, calls the route, waits for a **new** contact
to appear, then dials **that one only** — re-dialling the whole extension would
ring the already-ringing browser a second time.

```
[did-business]
 same => n,Set(__REALCID=${CALLERID(num)})   ; before caller ID gets overwritten
 same => n,Dial(PJSIP/${EXT}&Local/${EXT}@wake-sip/n&PJSIP/${CELL}@${TRUNK},${RINGTIME})
```

**Two switches, not one.** `bf_softphone.wake_enabled` (default: on) is
separate from the SMS push switch. Turning off text-message notifications must
not turn off the phone: a text that waits for a wake costs nothing, a call you
cannot pick up is missed for good.

**The shared token** (`bf_softphone.pbx_wake_token`, Settings → Phone → Push
wake) must match the PBX side. Empty, the route refuses everything: a missing
secret does not become a free pass.

⚠️ **What everything else depends on**: the device must carry a UnifiedPush
`push_endpoint`. Without one the route answers `{"sent": 0}` and logs a
warning — and that log is the only place it shows.

**Encrypted, and bounded like the other transports.** A device that has handed
over its WebPush keys receives an encrypted wake (RFC 8291) with a 60-second
TTL: a wake that lands after the caller hung up is worth nothing. The ntfy
publish token only ever travels to the configured ntfy host, redirects are not
followed, and the endpoint is re-checked public at send time.

### A canary probe rather than a watcher

`res.users.softphone_wake_selftest` manufactures its own wake and publishes
to a canary topic instead of the user's endpoints, returning `up` / `degraded`
/ `down`. The module ships **no** `ir.cron` for it: drive it from a scheduler
you provide, an `ir.cron` of your own or an external probe. It exists because
the failure mode here is silent:
the route answers `200 {"ok": true, "sent": 0}` whether or not anything went
out, so a status-code probe stays green through an outage, and real traffic is
too sparse for a watcher to notice. It covers the five ways this can die
quietly — switch read false, user with no extension, no device registered,
ntfy token refused, ntfy unreachable — and it rings nobody, which is what
makes an hourly cadence safe.

## Security posture

- `sip_password` is a `groups="bf_softphone.group_softphone_manager"` field, so
  Odoo strips it from any read by a non-manager. A user only ever obtains their
  own, through `get_softphone_config()` — never a uid passed in by the client,
  never `localStorage`.
- The SIP secret and the AMI secret are **encrypted at rest** (Fernet, key read
  from the environment or `odoo.conf`, never from the database). Without a key
  the module refuses to store a secret rather than storing it in the clear.
- Contact search runs in the caller's own `self.env` (no `sudo()`), throttled at
  thirty per minute: the address book cannot be harvested.
- TURN credentials are minted server-side with a short TTL for an
  authenticated user, rather than being served from a public `turn.json`. The
  provider key itself never leaves the server.
- `/bf_softphone/ws_auth` is meant to be wired as an nginx `auth_request`
  endpoint, so that a SIP WebSocket does not reach Asterisk without a token
  Odoo issued to a group member. The check is stateless (HMAC) and re-confirms
  group membership, so removing someone from the group revokes them
  immediately. ⚠️ **Installing the module does not switch this on** — until
  your reverse proxy calls it, `/ws` stays anonymous:

  ```nginx
  location = /bf_softphone/ws_auth { internal; proxy_pass http://odoo; }
  location /ws { auth_request /bf_softphone/ws_auth; proxy_pass http://asterisk; }
  ```
- Hanging up is scoped: a channel name arriving from a client is refused unless
  it is proven to belong to the caller.

## Configuration

1. Settings → Phone: PBX host, and the TURN key (id + token).
2. Add the user to the **Phone user** group (or **Phone manager**).
3. On the user form, **Phone** tab: SIP extension and password (manager-visible
   only), then tick **Phone enabled**.

Each `sip_extension` must match a real PJSIP endpoint on the PBX. This module
consumes existing extensions; it does not provision the PBX.

## Requirements

- Odoo 18 with `base`, `web`, `mail`
- `bf_sms_archive` **18.0.5.17.0 or later**, which supplies
  `call.archive.call._ingest_one()`,
  `sms.archive.thread.normalize_phone()`, the partner matching, the mobile
  device token and the push transport — all reused here rather than
  duplicated. The phone is a capability of the same mobile session, not a
  second account to open on the device, and the two are upgraded together: an
  older `bf_sms_archive` raises `ImportError` at load.
  ⚠️ **`bf_sms_archive` is published under the Business Source License 1.1,
  not LGPL-3.** Production use is limited to your own internal business
  operations until its Change Date, after which that version converts to
  LGPL-3.0-or-later. This module does not run without it, so that restriction
  reaches any deployment of it — read its `LICENSE` before you build on this.
- An Asterisk PBX with AMI reachable from Odoo, and a TURN service for media
  relay.

## Bundled third-party code

JsSIP is vendored under `static/src/lib/` and is **not** covered by this
module's licence. See [`THIRD-PARTY.md`](THIRD-PARTY.md). It is ~240 KB and is
loaded **lazily** (`loadJS`), only for a group member who opens the panel, so
it never weighs on the back-end bundle for everyone else.

## Licence

LGPL-3. See [`LICENSE`](LICENSE).
