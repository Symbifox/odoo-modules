# NFC tags: rounds with readings (`bf_nfc_round_inspection`)

Bridge between rounds and readings, installed automatically when both are. A round
checkpoint can carry a checklist: the tap shows the checklist, and saving it
records the pass AND writes the reading. The monthly extinguisher round becomes a
register, checkpoint by checkpoint, with no second tag.

## Design rules

- **The question comes before the pass.** The core rolls back everything a gesture
  touched when it asks a question: recording the pass first would have it undone,
  then recorded again. Ask first, write after.
- A checkpoint without a checklist taps exactly as before.
- The missed-reading watcher covers these checkpoints too.

## Shipped templates

Extinguishers (monthly), emergency lighting (monthly), exit signs (monthly), fire
doors (daily round), playground visual inspection (weekdays) and detailed
inspection (monthly). The Quebec Safety Code's Building chapter exempts some small
buildings (ss. 340 and 341): check whether it applies to yours.

## License

Business Source License 1.1, see [LICENSE](LICENSE).
