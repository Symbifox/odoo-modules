# Privacy Framework — GDPR (EU)

[![Odoo Version](https://img.shields.io/badge/Odoo-18.0-purple.svg)](https://www.odoo.com)
[![License: BUSL-1.1](https://img.shields.io/badge/License-BUSL--1.1-blue.svg)](https://mariadb.com/bsl11/)

A **data-only** module for [`privacy_consent`](../privacy_consent). It adds the **GDPR** regulatory framework (Regulation (EU) 2016/679) so consents, emails and certificates can render under EU law.

## What this pack adds

- A `privacy.framework` record "GDPR": national supervisory authority / EDPB, **Data Protection Officer (DPO)**, consent age **16**, breach notification within **72 h** (Art. 33) plus notification to data subjects on high risk (Art. 34), DPIA (Art. 35).
- The **6 lawful bases** of Art. 6(1): consent, contract, legal obligation, vital interests, public task, legitimate interests.
- The **data-subject rights**: access, rectification, erasure, restriction, portability, objection, complaint.

## Usage

After installing, set this framework as the company default (**Privacy → Configuration → Regulatory frameworks**) or per record (consent, notice, retention calendar). No further configuration required.

## Dependencies

`privacy_consent`. Data-only module: no models, views or code added.

## License

BUSL-1.1 — Les services de consultation Blue Fox, Inc. Bascule en LGPL-3.0-or-later le 2029-07-20. Voir [`LICENSE`](LICENSE).
