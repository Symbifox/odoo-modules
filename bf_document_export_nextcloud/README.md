# Policies & Procedures Export: Nextcloud

Bridge between `bf_document_export` and `bf_document_nextcloud_sync`; it installs on its
own when both are present. It does two things: it **reads** the body of documents kept on
Nextcloud, and it **deposits** the exported tree in a Nextcloud folder.

## Depositing the published copy

On a template, the **Nextcloud** tab names the configuration and the deposit folder. Every
full export of the template is then deposited there, whether it was run on demand or by a
publication.

* **Only what changed is written.** A rendered document is compared by a fingerprint of
  what it shows, export date aside: two renders of the same version are not identical byte
  for byte, yet they are not written again. The indexes (master list, index, manifest,
  readme) are rewritten on each deposit. Staff with a sync client therefore download only
  what actually changed.
* **A file changed on Nextcloud is never overwritten**, even while the deposit runs: each
  write names the version of the file it replaces, and Nextcloud refuses it if the file
  changed meanwhile. The file is left untouched and reported in the delivery log.
* **A file already there but not written by the export** is adopted when its content is
  identical, and left untouched otherwise.
* **Nothing is deleted.** A document that leaves the registry, or changes name, is moved
  to the archive folder under its name followed by "withdrawn on YYYY-MM-DD". A document
  whose body could not be read this time keeps its files as they were: that is no
  withdrawal.
* **The deposited manifest describes the files on Nextcloud**: a document left as it was
  keeps the fingerprint of the copy deposited earlier.
* **Only the template's "Export as" person deposits**, and a template that deposits must
  name one. An export read with narrower rights would otherwise move to the archives every
  document that person cannot see; it still produces its ZIP, but is not deposited.
* **A selection of documents is not deposited**: it would replace the published copy with
  part of the registry.
* **The deposit folder must be apart from the sources.** It may not overlap a source
  folder, and nothing is ever written or moved outside it.

Sharing the folder read-only with your staff is done in Nextcloud.

## Reading source files

A registry document may keep its body in a Nextcloud file (ODT, DOCX, PDF). At export
time, this bridge reads the file as it is today and files it under the document's stable
name, with its original extension.

* **Only through the template's source Nextcloud, and under its source folders**, both
  set by a manager (one folder per line, never the root). A configuration's service
  account can read its whole Nextcloud: the template bounds what an export reads, whoever
  linked the file. A document linked to another configuration, or whose file lies
  elsewhere, is left out and reported; without a source Nextcloud and folders, nothing is
  read. A document without a configuration of its own (the twin of a translated pair,
  often) is read through the template's.
* A file attached to the published version comes before the live file.
* An archived version without an attached file is left out rather than shown with
  today's text.
* A document that points to a **folder** (for instance a slide deck's folder holding
  "date - Name - FR|EN.pdf|pptx") gives the file of its language, PDF first. The language
  is also read as "(FR)" or "- ENG"; when only the other language is tagged, the untagged
  files are the document's. A folder without a single file for the language is reported,
  never exported as a file.
* A configuration that cannot authenticate is tried once per export, and reported once in
  the readme with the list of documents it kept out. A missing file is reported per
  document.

## Requirements

* Odoo 18
* `bf_document_export`, `bf_document_nextcloud_sync`

## Licence

Business Source License 1.1 (see `LICENSE`): each version reverts to LGPL-3.0-or-later
on its Change Date.
