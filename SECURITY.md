# Security Policy

## Reporting a vulnerability

**Email [hello@symbifox.com](mailto:hello@symbifox.com)** with `SECURITY` in the
subject line. Please do not open a public issue for a security problem.

Tell us what you can: the module and version, what an attacker can do, and how to
reproduce it. A rough report is worth more than no report — we would rather ask you
follow-up questions than never hear about it.

We will acknowledge your report within **3 business days** and tell you what we
think of it within **10 business days**. If we accept it, we will tell you when we
expect to ship a fix and we will let you know when we do.

We are a small company in Québec, Canada. We do not run a bug bounty and we cannot
pay for reports. We will credit you by name in the release notes if you want to be
credited, and we will keep you out of them if you do not.

## What is in scope

Every module in this repository, on the `main` branch, at its current released
version. That includes the two that hold secrets, which are the ones we would most
like you to look at:

| Module | Why it matters |
|---|---|
| `bf_otp` | one-time-password devices, tokens and recovery codes |
| `bf_credentials` | an encrypted credential vault |
| `bf_securetransfer` | password-protected file transfer and expiring links |
| `bf_sign` | electronic signature and document stamping |

Out of scope: anything hosted by us rather than shipped here (that is an
infrastructure report, same address), vulnerabilities in Odoo itself (report those
to [Odoo](https://www.odoo.com/security-report)), vulnerabilities in Odoo Community
Association modules we depend on (report those to
[OCA](https://github.com/OCA/security)), and findings that only apply to a
configuration the module explicitly warns against.

## Disclosure

We ask for **90 days** before public disclosure, or until a fix ships if that comes
first. If we go quiet on you or miss our own dates, publish — a policy that lets us
sit on a report indefinitely is not a policy.

## Supported versions

These modules target **Odoo 18.0 Community Edition**. Only the current version of
each module receives security fixes; there are no long-term-support branches.

A security fix ships the day it is written, on every module already public. This is
the one case that never waits: our publication policy holds new *capabilities* back
in some circumstances, never a security fix.

## Licensing note

This repository ships modules under three regimes (BUSL-1.1, LGPL-3, AGPL-3), and
which one applies is stated in each module's own `LICENSE` file. **None of them
restricts you from reporting a vulnerability, from testing the code you run, or
from discussing a finding publicly after the disclosure window above.**
