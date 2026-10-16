# bf_stepbystep_securetransfer: client portal deposits through secure transfer storage

Bridge between **Progression** (`bf_stepbystep_clients`) and **Secure transfer**
(`bf_securetransfer`). Install it on the tenants whose secure transfer has an S3
storage: it does not install by itself.

When the secure transfer has an S3 storage configured (endpoint, bucket and keys),
documents that the client deposits on the Progression portal go **straight from the
browser to the bucket** through presigned URLs (multipart above the threshold of
the secure transfer, 64 MB by default). The bytes do not cross Odoo during the
upload. Without S3 storage, the portal keeps the direct deposit of Progression.

## What happens to a file

- **Up to the ingest threshold** (100 MB by default): once the upload is complete,
  the server checks the object, reads it, files it like a direct deposit
  (attachment of the stage's task, SHA-256, internal note) and purges the object
  from the bucket.
- **Above it**: the file stays in the bucket. The deposit record points to it, the
  team downloads it with a short presigned link (**Download** on the project's
  *Client portal* tab), and it is purged at its due date: the longest retention the
  brand offers. A daily cron purges due files and abandoned uploads (24 h).

## Security

- The upload token of the underlying transfer draft is **never** given to the
  browser: the `/secrets/api/<ut>/finalize` route of the secure transfer can rewrite
  the recipients, which would turn the portal into an email relay. Each upload has
  its own token, which opens only that upload.
- Every route goes through the portal guard (project, token, published), then the
  upload guard (same project, still uploading, right upload token). A document must
  belong to a completed or current step of the project.
- The file checks of Progression apply before anything is signed (type, size, rate),
  and the rate of *started* uploads is capped too, so that nobody can have URLs signed
  in a loop without ever finishing.
- Each operation runs in a savepoint: an error leaves no half-written upload or
  draft. The visitor reads only the messages written for them (refused file, file
  that did not arrive in full); an error of the storage (endpoint, bucket, keys)
  reaches them as "The file could not be stored. Try again later."
- An upload is closed before its S3 object is purged, and the purge never fails the
  deposit: a second *complete* cannot file the document twice.
- The download link is signed only for a user who can read the project.
- The draft carries the client's IP: the daily quota of the secure transfer is
  counted per IP. The hourly draft cleanup of the secure transfer leaves alone the
  drafts that hold a large file kept in the bucket.

## Settings (system parameters)

| Parameter | Default | Meaning |
|---|---|---|
| `bf_stepbystep_securetransfer.ingest_max_mb` | 100 | Above this size, the file stays in the bucket. It can lower the threshold, never raise it above 100 MB: below it, the server reads the whole object in memory. |
| `bf_stepbystep_securetransfer.max_upload_mb` | 2048 | Largest file accepted on the portal through S3. |

The bucket must allow the portal's origin in its CORS rules
(`bf_securetransfer.cors_origins`, then *Set up the bucket*).

Odoo dependencies: `bf_stepbystep_clients`, `bf_securetransfer`.

## Licence

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations.
- **Requires a written agreement**: providing the module as a product or
  service to third parties, whether hosted, managed or resold.
- **Change Date**: on 2030-10-16, this version converts automatically to
  **LGPL-3.0-or-later**.
