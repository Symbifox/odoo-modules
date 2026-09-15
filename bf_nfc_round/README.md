# NFC tags: rounds (`bf_nfc_round`)

A security patrol, a maintenance round, a building inspection: one tag at each
checkpoint. Tap them while passing; the phone says "checkpoint 3 of 7, next:
server room".

- **Proof of passage**: who, at which checkpoint, at what time. A checkpoint
  tapped without a network (basement, plant room) keeps the phone's time.
- **Order**: an "in order" round flags a checkpoint tapped too early.
- **Alerts**: a round started and not finished within its duration, or a
  scheduled round nobody did within its window, creates an activity for the
  person in charge. One alert per miss, never one per watcher run.

## Design rules

- **The schedule is read in the time zone of the person in charge**, not the
  server's (UTC) nor the tapper's: "the 10 pm patrol" is 10 pm where it happens.
- **The open round is found per person and per tap time**: two guards doing the
  same round at the same time make two runs, and a checkpoint tapped the next
  day starts a new round instead of completing the old one.
- A signed tag does not do the round: passages carry the person who taps.

## Setup

*Tags → Rounds*: create the round, its checkpoints in order, the person in charge
and, if needed, a schedule (every day or weekdays, start time, maximum
duration), then *Create the checkpoint tags*: one "Round checkpoint" tag per
checkpoint, skipping those that already have one. Engrave them from *My tags* in
the app. A watcher runs every 15 minutes.

The *A round and its checkpoints* template recipe creates the round, its
checkpoints and their tags from a list of lines. With `bf_nfc_inspection`
installed, a checkpoint can also carry a checklist (see `bf_nfc_round_inspection`).

## License

Business Source License 1.1, see [LICENSE](LICENSE).
