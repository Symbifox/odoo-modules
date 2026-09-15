# NFC tags: Gen skills (`bf_nfc_gen`)

Installed automatically when an instance has both the tags and Gen. A tag on the
meeting table asks Gen to prepare the agenda of the meeting it points to; another
one, at the door, to refine the minutes.

## What the module refuses

- **Launching a skill the bridge does not publish.** The list of skills that can be
  launched by a tag comes from the bridge (`/nfc-skills`); the instance
  administrator only ticks the ones they allow. The bridge revalidates every
  request against its own list, because nothing authenticates who calls it.
- **Launching without saying so.** The first tap asks "Launch?". A call to Gen
  cannot be undone: it leaves after the choice, after the transaction commits, and
  the thread checks the request still exists before calling.
- **Launching on behalf of a generic account.** A signed tag acts for a designated
  account: an agent does not start from there.
- **Launching twice.** While a request for the same skill on the same record is in
  progress, a new tap says where it stands.
- **Being open to everyone by default.** The gesture is reserved to tag managers
  until an administrator unticks it.

The phone does not wait: the request has its own state, and the person gets an
activity when Gen has finished or failed.

## Bridge side

`/nfc-skill` keeps the safeguards the older editorial passes lack: MCP servers
limited to the tenant, a turn ceiling, at most two concurrent tag passes, and a
pass that fails is logged too. Only an integer reaches the prompt.

## License

Business Source License 1.1, see [LICENSE](LICENSE).
