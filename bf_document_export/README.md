# Policies & Procedures Export

Your policy and procedure registry stays in Odoo. Many organisations, though, still look
for a procedure in a file explorer, on a shared drive, a Nextcloud, a Drive or a
SharePoint. This module lays the registry out as a folder tree: a **published, read-only
copy**, filed by a template, that staff can browse without opening Odoo.

Built on `project_knowledge_matrix` (the document registry).

## What an export contains

A ZIP whose root holds:

* `00 - Liste maîtresse.xlsx`: the master list (code, title, type, version, effective
  date, next review, owner, language, classification, path), every value kept as text:
  nothing in it is read as a formula or a link;
* `index.html`: the same list, browsable offline;
* `manifest.json`: the same list, with the SHA-256 of each document's file;
* `LISEZMOI.txt`: when the copy was made, from which template, which folders to protect,
  and the warnings of the run.

Each document is filed under a **stable name** (`PRO-002 - Incident response.pdf`) that
does not change from one version to the next. Documents written in Odoo are printed as
PDF, and **every page carries** their code, version, effective date and the notice "Copy
exported on …: the version in force is in the registry". A document whose body is a
knowledge matrix is printed with the matrix's text, one section per item. A document
whose body is an external file is exported as that file.

## Templates

| Template | Filing | For whom |
|---|---|---|
| A. Document pyramid (ISO 9001) | by type: policies, procedures, guides, forms, registers, appendices | the default; it needs nothing but the document type |
| B. By process (ISO 9001 §4.4) | management, operations, support | organisations that have mapped their processes |
| C. By function (ISO 15489) | function codes aligned on a retention schedule | organisations that keep a classification plan |
| D. By compliance framework | the four ISO/IEC 27001:2022 Annex A themes, plus privacy governance (Québec Law 25) | organisations working on information security |
| E. Full mirror | the whole registry: every frozen version, the body section by section, a record file per document | reversibility, or a safety archive |

Every template can be duplicated and adjusted: folders and the document types filed in
each, naming, maximum path length, what goes in and what stays out. Shipped templates
are `noupdate`: your adjustments survive module upgrades.

## Classification

Templates B, C and D file by domain. The module adds **classification plans** to the
registry (processes, records functions, compliance framework), each hierarchical, and a
**Classification** field on documents. A document can sit in several plans at once. Three
plans are shipped and can be edited freely.

## Rules of the tree

* **Only the version in force** in the browsable tree. Superseded versions go to an
  archive folder on request, with their version and date in the name.
* **No drafts by default.** On request they go to a separate folder, marked as drafts.
* **Confidential registers stay out by default**, since they may hold personal
  information. On request they are exported and flagged restricted in the manifest and
  the readme.
* **Names that work everywhere**: no character refused by Windows, OneDrive or SharePoint,
  no reserved name, and full paths kept under 200 characters. A title that is too long
  is shortened, a path that cannot be shortened enough is reported, and a duplicate name
  is numbered and reported.
* **Sections, when exported, are those frozen with the version in force.** A draft, or a
  document without a released version, gives its working copy, like its main file
  (exported as a draft, and marked).
* **Dates follow the requester's time zone**, not the server's or the scheduler's.

## Trigger

Per template:

* **On demand**, from the menu or from a selection in the document list;
* **On each publication** of a version: one export is queued, only once even for several
  publications in a row. Publishing never waits for the export.

Exports run in the background. The requester is notified when the archive is ready. The
last three archives of each template are kept; older exports keep their log only.

## Preview

`bf.document.export.template.preview_filing()` tells where each document would land,
with the same rules as the export and without rendering anything, read with the caller's
rights. It is meant for fitting a template to a registry before the first export, and is
callable over XML-RPC.

## Access rights

A document user asks for exports and sees their own. A document manager sees every export,
and can download its archive, and manages templates and classification plans. A person, manager or not, creates an
export request, never its outcome: who asked, the archive, the log and the delivery are
written by the export alone.

An export reads the registry with the rights of the person who asked for it; an export
triggered by a publication, with those of the person named on the template ("Export as"),
who must be an active internal user and never the superuser. A version's file or a
document's matrix that this person cannot read is left out and reported. And nobody can
link to a version or a document a file or a matrix they cannot read themselves.

## Targets

The ZIP is always available. Direct delivery lives in bridge modules:
`bf_document_export_nextcloud` deposits the tree in a Nextcloud folder. Delivery runs
after the archive is built: a target that is down leaves the archive intact, and the
export records separately what the delivery became (delivered, delivered with files left
untouched, or failed), with its own log.

## Requirements

* Odoo 18
* `project_knowledge_matrix`, `mail`
* `xlsxwriter` (shipped with Odoo) for the master list

## Licence

Business Source License 1.1 (see `LICENSE`): each version reverts to LGPL-3.0-or-later
on its Change Date.
