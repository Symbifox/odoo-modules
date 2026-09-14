# NFC tags: session attendance (`bf_nfc_event`)

A tag on the training room door. When arriving, everyone brings their phone
close: their attendance is recorded at the session taking place in that room,
with the time. No sheet going around, no list copied afterwards.

- **Tag on the venue** (the event's address contact): the session in progress at
  that place. If two are running, the screen asks which one.
- **Tag on the session**: that session, only while it takes place.

The person's registration becomes *Attended*. Without a registration, one is
created: this records who came, not only who had registered. With the training
register (`bf_training_session`), attendance is written there automatically.

## What it refuses

- **Attendance on behalf of a generic account.** A signed tag acts for a
  designated account: attendance goes through the app or a session.
- **Attendance outside the session**: from one hour before the start to thirty
  minutes after the end, not earlier, not later.

Attendance is written with elevated rights: in Odoo, registrations are open to
the registration desk and event managers only, and recording one's own
attendance is not a desk privilege.

Installs itself when both `bf_nfc` and `event` are present.

## License

Business Source License 1.1, see [LICENSE](LICENSE).
