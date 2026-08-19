# bf_cx_fundraising: donor experience survey

Auto-installs when both `bf_cx` and `bf_fundraising_core` are installed.
A product feature for non-profits using the fundraising suite (not for
Symbifox itself). When a donation is validated, sends the designated
program's survey (`bf_cx.donor_program_id`, empty = disabled) to the
donor. Once per donation, with the solicitation guardrails applied. A
donor may give often, so the program's minimum pacing is the main
protection here (90 days recommended).

## License

AGPL-3, inherited rather than chosen: this module sits on top of Odoo Community
Association donation code that is itself AGPL-3.

**Practical limitation on redistribution.** Its dependency `bf_cx` is BUSL-1.1,
and the coupling is structural rather than a convenience: the settings form
extends `bf_cx.res_config_settings_view_form`, the donor gate reads the
`bf_cx.donor_program_id` parameter, and the tests build on
`bf_cx.tests.common.CxBridgeCase`. None of that can be resolved at runtime, so
the dependency cannot be made optional without removing the feature.

The AGPL-3 text applies to this module's own source, but you cannot exercise
the redistribution it grants without also obtaining terms for `bf_cx`. If that
is what you need, [talk to us](https://symbifox.com).
