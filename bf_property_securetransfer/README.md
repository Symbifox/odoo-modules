# Co-ownership: Secure hand-over of documents (`bf_property_securetransfer`)

The bridge between the three document regimes of a fraction's sale and the
secure transfer platform (`bf_securetransfer`). The three regimes *are* a
transmission of documents to a person outside the syndicate, and two of them
carry a fifteen-day deadline. That is exactly what a secure transfer does.

An auto-installing bridge, on the `bf_cx` pattern: it installs itself when both
sides are present, and the co-ownership suite works perfectly without it.

## What it does

| Regime | Model | Recipient | What leaves |
|---|---|---|---|
| Art. 1068.1 CCQ, syndicate's attestation | `bf.property.attestation` | the requester (the selling co-owner) | the printed attestation, produced at hand-over |
| Art. 1069 para. 2 CCQ, statement of common expenses due | `bf.property.charge.statement` | the requester | the printed statement, produced at hand-over |
| Art. 1068.2 CCQ, documents to the promisee-buyer | `bf.property.disclosure` | the requester | the files the syndicate attached to the record |

The three regimes share one abstract model, `bf.property.secure.delivery`. Each
form gets a "Hand over by secure transfer" button and a block showing when the
document left, the transfer, how many times it was downloaded and, where one
exists, the copy that was handed over. The organisation form gets the code
channel setting (see below).

`action_secure_send` builds the working copies, opens a transfer through the
platform's send wizard, attaches the transfer the wizard returns to the record,
and writes a line in the thread. It does not change the record's state: each
regime has its own passage to "delivered", with its own conditions and notices,
and duplicating it would make two truths. The bridge carries; the state machine
stays at home.

## Sending is not delivering

A secure link expires. If it expires without a single person opening it, the
syndicate has sent and the recipient has received nothing. The module says so
(`secure_unread`, computed from the transfer's state and download count) rather
than counting a hand-over that never happened.

This matters most on the charge statement. Under art. 1069 para. 2 CCQ the
deadline runs **against the syndicate**: past fifteen days the purchaser is no
longer liable for the common expenses owed on the fraction, and the claim has
to be pursued against a vendor who is usually gone. On the charge statement the
warning is red, not amber, for that reason.

## What is frozen, and what is not

The send wizard deletes its own copy after the transfer, and it is right to: it
must not leave a third party's bytes in its store, nor in the nightly backups.
But the syndicate has to be able to say what it handed over, and an attestation
regenerated six months later would not say the same thing: it is dated to its
hand-over and reflects that day's figures. So a document the module produces is
copied onto the record (`secure_frozen_attachment_id`) once the transfer has
left.

**Regimes that hand over documents *attached* by the syndicate freeze nothing.**
The attachment already is the record's copy; duplicating it would double the
document list without adding anything a syndicate could prove. One class
attribute, `_secure_freezes_copy`, carries that distinction.

A send that fails deletes the working copies it created: files attached to no
record would never be seen again and would stay in the backups.

## Only what was attached to the record, never the thread

For art. 1068.2 the bridge hands over the files attached to the record itself,
and excludes every attachment carried by a message in the record's thread. An
email filed on the record, the unredacted original a co-owner sent, a file from
an internal exchange: they all carry the same model and record id as a
deliberate attachment. Taking them all would hand the promisee-buyer exactly
what the privacy review had just set aside. With nothing attached, the bridge
refuses and says so.

## What the bridge refuses

- **Art. 1068.2, before the privacy review.** The promisee-buyer's
  authorisation does not cover the personal information of the *other*
  co-owners, and once a document has left through a secure link it cannot be
  redacted. The bridge refuses while the request is still `requested`.
- **Art. 1069 para. 2, before the statement is issued.** Notice to the owner is
  a condition of the authorisation, not a courtesy. The model already checks it
  when the statement is issued; the bridge refuses a statement still
  `requested` rather than re-checking the notice its own way.
- **A cancelled attestation, statement or disclosure.**
- **A recipient with no email address.** The link would have nowhere to go.
- **A sender with no email address.** The sending receipt would have nowhere to
  go.
- **A second hand-over of the same document.**
- **No secure transfer brand configured.** The link would have no domain to
  live on.
- **Anyone who is not the syndicate.** See below.

## The guard comes before the send, not after

`action_secure_send` is a public method, and every public method is callable
over RPC by anyone with access to the model. The authority guard
(`_ensure_organisation_decides`, from `bf.property.organisation.authority`,
which the three regimes already carry from their own modules) is therefore the
**first** line of the method, before any read and any send.

The reason is not theoretical. An authority probe found the same shape on the
text-message bridge: a resident could call the urgent-notice method over RPC,
a text left for the provider, and only then did the `AccessError` on the final
write roll the transaction back. The rights had saved the database, not the
telephone network, and since the sent date stayed empty, the call could be
replayed without limit. A file uploaded to a third party and a link already
sent are not recalled by a rollback either.

## Which brand, and how long

The link is dressed by the platform's default brand, or failing that the first
active brand that is not tied to a fixed recipient. A syndicate that wants its
own domain on the links will say so, and that choice would then belong on the
syndicate's record; picking silently would be worse.

The link stays available 30 days when the brand offers that duration, and
otherwise for the longest duration the brand offers. The duration is a
preference, not a requirement: imposing 30 days on a brand whose offer stops
earlier made every hand-over fail after the manager had prepared everything.

## The code and the link do not have to travel together

Left to the platform defaults, the bridge would send the code by email with no
password. The link and the code would land in the same mailbox, which makes the
hand-over single-factor in practice. That matters here, because art. 1068.2
documents can carry the personal information of *other* co-owners, who gave no
authorisation and will not be asked for one.

The channel is therefore a per-organisation setting (`secure_otp_channel`:
email, or text to the recipient's mobile), and its help text says what each
choice costs instead of letting a default speak for it. By text, the mobile has
to be on the recipient's contact record; the help text warns that the platform
otherwise falls back to email.

**The number consented to for text notices is not used here.** The tempting
move is to take the one `bf_property_sms` holds when it exists. Art. 1070
para. 1 CCQ puts that number in the register because the person consented to
being reached *for those notices*; using it for something else is exactly the
drift that consent bounds. And in any case the recipient of a hand-over (a
purchaser, a notary) has no consent record at all.

## Security

The co-ownership manager group implies the secure transfer platform's **user**
group, not its administrator group: the manager holds the button and the right
to use it, and the platform's configuration stays with the administrator. The
authority guard above decides who may hand over a document on the syndicate's
behalf.

## Dependencies

- `bf_property_records`
- `bf_property_finance` (the charge statement of art. 1069 para. 2 CCQ lives
  there)
- `bf_securetransfer`

## Tests

18 tests. What they measure is deliberately not "the method raises" but
"nothing left the building": the send wizard is mocked and the assertions are
on whether it was called, on the attachment count, and on the state left on the
record. They cover, among others, a resident trying to hand a document to a
third party, a thread attachment that must never reach the buyer, the retention
that follows the brand's offer, and the manager holding the platform as a user.

The freeze test runs with `force_report_rendering` in the context. Under
`--test-enable` Odoo renders HTML instead of PDF to avoid paying for
wkhtmltopdf on every test; without that context the test would have asserted
"a document is frozen" without ever looking at the document that ships.

## Licence

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations, which include administering immovables that you own or
  that you are constituted to administer, and letting a person acting on your
  behalf use your instance for that purpose. A syndicate, a housing cooperative
  or a non-profit housing organisation running the module for its own immovable
  is covered, and so is the bookkeeper or the manager it hires who works inside
  its instance.
- **Requires a written agreement**: administering immovables for the account of
  others, and providing the module as a product or service to third parties,
  whether hosted, managed or resold.
- **Change Date**: on 2030-08-23, this version converts automatically to
  **LGPL-3.0-or-later**.

## Acknowledgements

Created and maintained by Les services de consultation Blue Fox, Inc. AI coding
assistants were used as productivity tools during development.
