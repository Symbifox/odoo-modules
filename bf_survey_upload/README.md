# BF Survey Upload

Adds a **File Upload** question type to Odoo Surveys.

## Features

- New question type `file_upload` selectable in the survey question form
- Per-question config: max size (MB), allowed extensions, single vs. multiple files, max number of files
- Files stored as `ir.attachment` records linked to the `survey.user_input`
- Public portal UI: file picker, list of uploaded files, remove button
- Server-side validation: size, extension, mandatory check, max file count
- Optional auto-copy of uploaded attachments to the respondent's project on completion (per-survey opt-in)

## Models

- `survey.question` — adds `question_type='file_upload'`, `file_upload_max_size_mb`, `file_upload_allowed_extensions`, `file_upload_multiple`, `max_file_count` (0 = unlimited; enforced when multiple files are allowed)
- `survey.user_input.line` — adds `bf_attachment_ids` (Many2many to `ir.attachment`)
- `survey.survey` — adds `bf_link_uploads_to_project` (opt-in)

## Endpoints

- `POST /survey/upload/<survey_token>/<answer_token>/<question_id>` — multipart upload, returns `{attachments: [{id, name, size, mimetype}]}`
- `POST /survey/upload/<survey_token>/<answer_token>/delete/<attachment_id>` — remove an upload before final submit

## Installation

```bash
docker exec <client>-odoo /usr/bin/odoo --stop-after-init -d <db> -u bf_survey_upload --no-http
```

## Compatibility

- Odoo 18 (community, with `survey` module)
- Tested with `survey` and `project` only; no PME-specific dependencies

## Changelog

### 18.0.1.2.0
- Added per-question `max_file_count` (0 = unlimited). When multiple files are allowed, the total number of files is capped both at upload time (controller, under the row lock, counting already-attached files) and at final answer validation. Exposed in the question config view next to the single/multiple toggle.

### 18.0.1.1.0
- Per-question max size, allowed extensions, single/multiple toggle; optional auto-copy to project.

---

<sub>Authored and maintained by Blue Fox Inc. AI coding assistants were used as productivity tools during development.</sub>
