# Audio capture (`bf_capture`)

Record a meeting on a phone, and have it land where the rest of the pipeline is
already automatic. The server composes the filename; the phone only says which
meeting it is.

## Why the filename matters

A transcription pipeline that watches a folder knows nothing about a recording
except what the **filename** says. Routing a recording to the right client
project is usually scored on the meeting name first, matched participants
second, and the transcript itself last and weakest. A file named
`memo-20260921-1903.m4a` therefore cannot be rescued by its content: nothing
downstream ever recovers what the name failed to carry.

So the name is composed on the server, from the calendar event, and the phone
never invents one.

## What the module does

| Surface | Role |
|---|---|
| `bf.capture` | The engine: composes the name, enforces the size limits, deposits over WebDAV, or turns a memo into a note. |
| `/bf_capture/mobile/v1/ping` | Capability probe. Without it, a mobile client does not show the recorder at all. |
| `/bf_capture/mobile/v1/cibles` | The meetings in the current window, **each with the filename that will be used**. |
| `/bf_capture/mobile/v1/rencontre` | Deposits the audio in the watched folder, under a name the pipeline can read. |
| `/bf_capture/mobile/v1/memo` | Transcribes when a transcription service is configured, creates a note, attaches the audio. |
| `/capture` | An installable page (web app manifest + service worker) for voice memos. |

## Two doors, and they do not lead to the same place

A **meeting** goes to the watched folder, where a transcription pipeline turns
it into minutes, tasks and a report. A **memo** becomes a note: it has no
participants, no decisions and no recipient, and turning it into meeting minutes
would manufacture an object that claims more than it knows.

The page at `/capture` serves the **memo only**, with a five-minute cap. A web
page has no foreground service: on iOS it is suspended as soon as it leaves the
screen, and on Android nothing guarantees the tab survives a locked screen. An
hour-long meeting recording would be lost partway through, silently. Meetings
belong in a native client that can hold a microphone foreground service.

## Timestamps are composed in one timezone, on purpose

A pipeline that reads the time out of a filename reads it in **one** timezone,
and looks the calendar up on the **date** part. A phone stamping its own local
time therefore writes a date that can be a day off, and an hour that can be
wrong by a whole working day, whenever the person is travelling. The server
composes the timestamp from the event's start, in the configured business
timezone, and never from the device clock.

## Two files never share a name

A folder watcher typically de-duplicates **by filename**, in memory, and moves
the original into a processed subfolder as soon as it picks it up. A name it has
already seen is ignored until its process restarts, even though the file is no
longer in the folder. The module therefore looks in both places before writing,
and numbers the name if it is taken.

## Settings

`Settings → Audio capture`, or by system parameter:

| Key | Default | What it is |
|---|---|---|
| `bf_capture.folder` | `Transcriptions` | The watched folder, relative to the root of the active Nextcloud configuration's account. |
| `bf_capture.max_bytes` | 128 MiB | Cap for a meeting. One hour of AAC 16 kHz mono weighs about 15 MB. |
| `bf_capture.memo_max_bytes` | 8 MiB | Cap for a memo, aligned with the transcription service's own limit. |

## What the module does not do

It does not transcribe meetings: that is the pipeline's job, where the GPU, the
diarization and the time are. ⚠️ A meeting must **not** be routed through the
dictation path either: a speech-to-text service serialises behind a model lock,
and a meeting in flight makes a six-second dictation wait for the full timeout.
The module sends nothing to anyone, and it does not decide what deserves to be
recorded.

## Guard

The deposit writes to Nextcloud through a service account, **outside the
caller's own rights**. The guard is therefore explicit: an internal user, active,
not a portal account. A portal account holding a device token could otherwise
drop a file into a folder that a robot empties every thirty seconds.

## Requirements

Odoo 18. Depends on `calendar`, `bf_document_nextcloud_sync` (the Nextcloud
WebDAV gateway) and `bf_bloc_notes` (the notepad a memo becomes).

A speech-to-text module is recognised **at run time and not declared**: a memo
can be deposited on an instance that has none, and arrives as audio without a
transcript. The page and the mobile routes both say so rather than promising
text that will not come.

## Licence

Business Source License 1.1 — see `LICENSE`. Each version converts to
LGPL-3.0-or-later on its Change Date.
