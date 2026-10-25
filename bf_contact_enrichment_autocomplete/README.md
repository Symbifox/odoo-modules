# Contact enrichment: Odoo Autocomplete (`bf_contact_enrichment_autocomplete`)

A small bridge between Odoo's company autocomplete (`partner_autocomplete`) and
the company completion by Gen of `bf_contact_enrichment`.

## What it does

Odoo's autocomplete finds a company by name for free (the suggestions come from
a business directory). What follows the click is paid: the enrichment of the
suggestion costs an IAP credit. Without credit, Odoo merges what the suggestion
already holds (name, city, country) into the record and shows a "Not enough
credits" message with a link to buy more.

With this module installed, that message changes for the people who will
really see Gen at work: it says that **Gen will complete the company with an
online search after you save it**. The suggestion still enters the record as
before; it is the creation of the company that puts it in Gen's queue
(`bf_contact_enrichment`, which also asks for confirmation unless the user
chose to let Gen fill in the empty fields).

Only the message is replaced, not the behaviour:

- it applies to `enrich_by_duns` and `enrich_by_gst` answering "Insufficient
  Credit", and to nothing else;
- it applies only to a member of the "Contact enrichment" group whose
  preference (*My profile ▸ Companies completed by Gen*) is not "Do nothing on
  creation". Everyone else keeps Odoo's invitation to buy credit, which stays
  true for them;
- it changes nothing when the company has credit.

## Installing

Install it by hand where both `bf_contact_enrichment` and `partner_autocomplete`
are present. It is not `auto_install`: a module that installs itself would
install in the middle of another module's upgrade.

## Dependencies

`bf_contact_enrichment`, `partner_autocomplete` (Odoo, LGPL).

## Changelog

- **18.0.1.1.0** — The source is now written in English; French lives in
  `i18n/fr_CA.po`.
- **18.0.1.0.0** — First version.

## License

Business Source License 1.1, see `LICENSE`.
