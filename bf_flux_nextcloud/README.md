# RSS Feeds: Nextcloud corpus (`bf_flux_nextcloud`)

Deposits what an RSS list retains into a Nextcloud folder, as Markdown, for a
knowledge base read by an agent. It uses the WebDAV connection already set up
for document synchronisation (`bf_document_nextcloud_sync`).

- One folder per source, one file per month, one block per item: title,
  issuer, date, language, subjects, why it was retained, link, then the full
  text (or the feed summary, with the reason the page could not be read).
- A month that grows adds follow-up files and never renames the first one.
  Deposits never delete: a renamed file would leave the old one beside the new,
  with the same content twice in front of the agent.
- An index keeps the count, the list's own introduction and the file list.
- A ledger keeps the SHA-1 of each deposited file, so nothing is uploaded
  twice. A failed upload stays out of the ledger and is retried next time.
