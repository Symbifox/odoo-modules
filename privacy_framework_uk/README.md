# Privacy Framework — UK GDPR (United Kingdom)

[![Odoo Version](https://img.shields.io/badge/Odoo-18.0-purple.svg)](https://www.odoo.com)
[![License: BUSL-1.1](https://img.shields.io/badge/License-BUSL--1.1-blue.svg)](https://mariadb.com/bsl11/)

A **data-only** module for [`privacy_consent`](../privacy_consent). It adds the **UK GDPR / Data Protection Act 2018** framework (the post-Brexit UK variant of the GDPR).

## What this pack adds

- A `privacy.framework` record "UK GDPR": **Information Commissioner's Office (ICO)**, Data Protection Officer (DPO), consent age **13**, breach notification within **72 h**, DPIA (Art. 35 UK GDPR).
- The **6 lawful bases** of Art. 6(1) UK GDPR.
- The **data-subject rights**: access, rectification, erasure, restriction, portability, objection, complaint to the ICO.

## Usage

After installing, set this framework as the company default (**Privacy → Configuration → Regulatory frameworks**) or per record.

## Dependencies

`privacy_consent`. Data-only module.

## License

BUSL-1.1 — Blue Fox Inc. Bascule en LGPL-3.0-or-later le 2029-07-20. Voir [`LICENSE`](LICENSE).
