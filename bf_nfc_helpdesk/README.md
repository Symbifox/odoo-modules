# NFC tags: report a problem (`bf_nfc_helpdesk`)

A tag on a customer's printer, server, kiosk or machine. Bring the phone close,
write one sentence, and the ticket is created already filled in: the customer,
the equipment, the place, who reported it and when.

- **On a contact**: the ticket is for that customer.
- **On any record with a customer** (equipment, project): the ticket takes that
  customer, and the record's name in its title.
- **In a menu**: `{"texte_fixe": "Out of paper"}` creates the ticket without
  asking anything. Three buttons, "Out of paper", "Paper jam", "Other", on a
  single tag.

Tickets come in through a **Tag** channel, so you can find them and measure what
tags produce. The person who reports follows the ticket and receives its
updates. A signed tag can report on behalf of the account it designates, which
is how a customer without an account reports a problem.

Writing happens with elevated rights after the record was read with the tapper's
rights: reporting a failure is not a helpdesk privilege. The ticket takes the
team's first stage; if that stage carries an email template, `helpdesk_mgmt`
sends it to the customer.

Installs itself when both `bf_nfc` and `helpdesk_mgmt` are present.

## License

Business Source License 1.1, see [LICENSE](LICENSE).
