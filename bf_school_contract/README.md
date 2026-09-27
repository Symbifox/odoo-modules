# Symbifox École: contract for educational services (`bf_school_contract`)

The contract a Québec private school signs with the family, under the Act respecting
private education (E-9.1, s. 66-76), a law of public order (s. 76): its caps cannot
be bargained away, so they are enforced as constraints, not as warnings.

## Features

- Detailed price, as the regulation requires it: admission or registration fee,
  educational services, each accessory service with its price, total (the total
  includes the admission fee, s. 66).
- Caps (Regulation E-9.1, r. 3):
  - eligibility fee at most **50 $** (s. 11);
  - admission or registration fee at most **200 $ or 1/10 of the total price**,
    whichever is lower (s. 12);
  - at least **two instalments** (Act s. 70);
  - cancellation indemnity or penalty at most **500 $ or 1/10 of the total price,
    minus the admission fee** (Act s. 72 and 73; r. 3, s. 13).
- Instalment schedule: nothing is due before the services begin but the admission
  fee (s. 70).
- The contract document prints every mention of Regulation E-9.1, r. 1, s. 20: the
  school as on its permit, the language of instruction, the dates, the detailed
  price, section 14 of the regulation when the school provided a security, the full
  text of sections 70 to 75 of the Act, the sentence "L'établissement s'engage à ne
  pas céder ou vendre le présent contrat" followed by the signature space, and the
  face-uncovered clause of s. 68.1 (L.Q. 2025, c. 29), required on pain of nullity.
- Sent for signature through `bf_sign` to every guardian who signs (roles of
  `bf_school_core`); the contract turns to **Signed** when the request is completed.
- Termination by the client (s. 71): the amount the school may keep (s. 72 before
  the services, s. 73 after: the months provided plus the penalty) and the refund
  due within ten days (s. 74) are computed.

## Language

The document is written in French and is not translated: a contract of adhesion is
given in French first (Charter of the French language, s. 55). The texts of the Act
and the regulation are reproduced word for word from LégisQuébec (E-9.1 up to date on
2026-06-10, E-9.1 r. 1 on 2026-05-01), with straight apostrophes. The date on which the
client expressly asked for an English version can be recorded; no English document is
produced yet.

## What has not been confirmed

- Whether the school's daycare and extracurricular services belong to the contract
  (Act s. 66 mentions accessory services) or fall under the Consumer Protection Act
  needs a legal opinion; this module treats the accessory services listed on the
  contract as part of it.
- Section 70 sets the due dates "approximately at the beginning of each half" of the
  services. Spreading the balance over more instalments (monthly, as schools do) is
  more favourable to the client, but no source read settles whether it complies.
- No payment collection: the schedule is printed, invoices come in a later release.
- The document has not been reviewed by a lawyer.
