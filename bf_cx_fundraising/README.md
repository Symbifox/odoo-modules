# bf_cx_fundraising: donor experience survey

Auto-installs when both `bf_cx` and `bf_fundraising_core` are installed.
A product feature for non-profits using the fundraising suite (not for
Symbifox itself). When a donation is validated, sends the designated
program's survey (`bf_cx.donor_program_id`, empty = disabled) to the
donor. Once per donation, with the solicitation guardrails applied. A
donor may give often, so the program's minimum pacing is the main
protection here (90 days recommended).
