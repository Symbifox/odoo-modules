# Privacy Framework — PIPEDA (Canada)

[![Odoo Version](https://img.shields.io/badge/Odoo-18.0-purple.svg)](https://www.odoo.com)
[![License: BUSL-1.1](https://img.shields.io/badge/License-BUSL--1.1-blue.svg)](https://mariadb.com/bsl11/)

A **data-only** module for [`privacy_consent`](../privacy_consent). It adds Canada's federal **PIPEDA** framework (for Canadian clients outside Quebec).

## What this pack adds

- A `privacy.framework` record "PIPEDA": **Office of the Privacy Commissioner of Canada (OPC)**, privacy officer, breach notification on a **real risk of significant harm**.
- Processing bases grounded in the 10 fair-information principles: consent, legal exception (s. 7), business transaction (s. 7.2).
- The **rights**: access, challenge accuracy / correction (Principle 4.9), complaint to the OPC.

## Usage

After installing, set this framework as the company default (**Privacy → Configuration → Regulatory frameworks**) or per record.

## Dependencies

`privacy_consent`. Data-only module.

## License

BUSL-1.1 — Les services de consultation Blue Fox, Inc. Bascule en LGPL-3.0-or-later le 2029-07-20. Voir [`LICENSE`](LICENSE).
