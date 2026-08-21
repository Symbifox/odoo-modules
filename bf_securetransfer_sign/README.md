# Secure Transfer: confidentiality agreement (`bf_securetransfer_sign`)

A **bridge** module between [`bf_securetransfer`](../bf_securetransfer/README.md)
and [`bf_sign`](../bf_sign/README.md). It adds one gate between the one-time code
and a transfer's content: signing a **confidentiality agreement** (NDA).

Its natural home is the open-audience mode ("data room"): a link that names
nobody, opened by people you have not vetted in advance. But the option also
applies to a send with named recipients.

- **Version**: `18.0.1.0.1`.
- **Licence**: **BUSL-1.1** — production use allowed for your own internal
  business operations; providing the module as a product or service to third
  parties (hosted, managed or resold) requires a written agreement. Converts to
  **LGPL-3.0-or-later** on **2029-07-20**. See [`LICENSE`](LICENSE).
- **Dependencies**: `bf_securetransfer`, `bf_sign`.

---

## The journey

1. The visitor opens the link and proves an identity — email or mobile — with a
   one-time code, exactly as the base module already requires.
2. Before the content is rendered, the bridge looks up **that visitor's** own
   agreement and, if it is not signed, sends them to `/s/<token>/nda`.
3. They read the agreement, tick the consent line and sign it **in the name they
   just confirmed**.
4. The signed document and its certificate are filed on the transfer, and the
   content opens.

## Four properties, each held by a test

### 1. The agreement's state is **read**, never received

The gate reads the signature state on every request instead of waiting to be
told about it. That is not a stylistic choice: `bf_sign` only calls its source
hook on a model that carries a discussion thread, and an audience row does not.
A bridge that waited to be notified would have left the door open with nobody
able to see it.

### 2. The **direct file link** is gated like the page

`_extra_access_gate()` is called on the download page **and** on
`/s/<token>/dl/<file_id>`. Wiring it to the page alone would let the direct link
through, and the direct link is precisely the one that circulates.

### 3. One agreement **per person**

Each visitor signs their own request, under the identity they confirmed. A
document signed "by somebody" is worth nothing.

### 4. No **second code**

The identity is already proven by the transfer's one-time code, and no
invitation email is sent: the visitor is already in their browser. Fifty people
would otherwise mean fifty emails.

## ⚠ A mobile identity cannot sign

A signature requires an email address. When an agreement is required, the send
wizard refuses to also offer the SMS channel, and the runtime limits drop that
channel, rather than letting mobile visitors walk into an agreement they cannot
sign.

Since 18.0.1.19.1 of the base module, **requiring an agreement also requires the
recipient code**. An anonymous signature proves nothing, and a gate with no
identity to talk to used to produce an infinite redirect.

## ⚠ Why a separate module

A typed field pointing at `bf.sign.request` inside `bf_securetransfer` would
have made `bf_sign` a **hard** dependency of the base module — invisible until
the first fresh install, and a licence mismatch besides. The base module exposes
only an extension point; this bridge is what fills it.

## Configuration

On the brand (Secure Transfer → Configuration → Brands):

- **Require a confidentiality agreement** — the default for new transfers of
  this brand.
- **Agreement (PDF)** — the document every visitor must sign. Requiring an
  agreement without uploading one is refused: it would lock every visitor behind
  a door with no key.
- **Field template** — optional. Without one the signature is still valid and
  certified, it is simply not drawn on the pages.
- **Consent text** — optional; empty falls back to the `bf_sign` default.

Each send may then keep or drop the requirement from the wizard.

## Routes

| Route | Type | Role |
|---|---|---|
| `GET /s/<token>/nda` | http | The page presenting the agreement. Replays **every** upstream gate (password, code, visitor not blocked): without them there is nothing to show, not even the agreement's title. |
| `POST /s/<token>/nda/sign` | http | Creates this visitor's request if needed and takes them to it. **POST** on purpose: creating a signature request is a side effect, and does not belong behind a GET a browser may replay or prefetch. |

## Tests

`test_nda_gate.py` covers the gate on both surfaces (page and file route), the
per-visitor agreement, the refusal of a mobile identity, and the invariant that
a gate never returns the URL of the page that calls it.
