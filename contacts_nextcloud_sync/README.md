# Contacts Nextcloud Sync

Synchronize Odoo 18 contacts with a Nextcloud address book via CardDAV. Direct HTTP (no middleware, no `vobject` dependency).

## Features

- **Direct CardDAV**: PUT/GET/DELETE/PROPFIND from within Odoo &mdash; no external middleware required
- **Exclusion-tag filtering**: All contacts sync by default; tag specific contacts to *exclude* them
- **Companies & individuals**: Syncs both `is_company=True` and `is_company=False` partners (type=contact)
- **Configurable direction**: Bidirectional, Odoo&rarr;NC, or NC&rarr;Odoo (default: Odoo&rarr;NC)
- **ETag change detection**: Unchanged contacts are skipped during push and pull
- **Stale UID recovery**: Before each push, cross-references Odoo UIDs against NC PROPFIND; contacts whose vCards are missing from NC are automatically recreated
- **Orphan detection**: vCards in NC that don't correspond to any Odoo contact are deleted during push
- **Incremental dirty tracking**: Editing a synced contact in Odoo clears its ETag, marking it for re-push on next cron/manual push
- **Delete propagation**: Deleting a synced contact in Odoo automatically removes it from Nextcloud
- **Resilient bulk operations**: Periodic `cr.commit()` during push ensures progress survives gateway timeouts or interruptions
- **Force Full Resync**: Wipes NC address book, clears all Odoo sync fields, and pushes fresh &mdash; guaranteed clean state
- **Custom vCard parser**: RFC 6350 line folding, no external library dependency (Odoo Docker images lack `vobject`)
- **Encrypted credentials**: App passwords stored with Fernet symmetric encryption (auto-generated key)
- **Cron automation**: 15-minute catch-up sync + 6-hour full resync safety net
- **Anti-loop protection**: `x_contact_sync_source` + `skip_nc_contact_sync` context flag prevents infinite sync loops
- **Settings integration**: Cron toggle and config link under Settings &gt; General Settings
- **Email-based matching on pull**: Incoming vCards are matched to existing Odoo contacts by email before creating new ones

## Architecture

```
                  Nextcloud                            Odoo 18
             (CardDAV Server)                     (Your Instance)
          +---------------------+              +---------------------+
          |                     |              |                     |
          | /remote.php/dav/    |<-- PROPFIND -| List UIDs + ETags   |
          | addressbooks/users/ |              |                     |
          | jdoe/contacts/   |<-- GET ------| Pull vCard          |
          |                     |              |                     |
          |                     |<-- PUT ------| Push vCard          |
          |                     |              |                     |
          |                     |<-- DELETE ---| Remove vCard        |
          |                     |              |                     |
          +---------------------+              +---------------------+
                                                       |
                                               nextcloud.contacts.
                                               sync.config model
                                                       |
                                               res.partner (inherited)
                                               x_nc_contact_uid
                                               x_carddav_etag
                                               x_contact_sync_source
```

### Sync Paths

| Direction | Mechanism | Trigger |
|-----------|-----------|---------|
| Odoo &rarr; NC (push) | CardDAV PUT for each dirty/new contact | Manual button or cron |
| NC &rarr; Odoo (pull) | PROPFIND + GET, email-match or create | Manual button or cron |
| Bidirectional | Pull first, then push | Manual button or cron |

## File Structure

```
contacts_nextcloud_sync/
+-- __init__.py
+-- __manifest__.py
+-- README.md
+-- models/
|   +-- __init__.py
|   +-- nextcloud_contacts_sync_config.py  # Config model, CardDAV ops, vCard parser
|   +-- res_partner.py                     # Inherited res.partner + sync fields
|   +-- res_config_settings.py             # General Settings integration
+-- views/
|   +-- menu.xml                           # Settings > Technical menu
|   +-- nextcloud_contacts_sync_config_views.xml  # Config form/list/search
|   +-- res_partner_views.xml              # Sync fields on partner form
|   +-- res_config_settings_views.xml      # General Settings section
+-- data/
|   +-- nextcloud_contacts_sync_cron.xml   # 2 crons: 15 min + 6 hours
+-- security/
    +-- ir.model.access.csv                # ACL: read for users, full for admins
```

## Installation

```bash
# Copy module to addons directory
cp -r contacts_nextcloud_sync /path/to/odoo/addons/

# Restart Odoo
docker restart my-odoo

# Install via Apps menu (search "Contacts Nextcloud Sync")
# Or via command line:
docker exec my-odoo odoo -d my-database -i contacts_nextcloud_sync \
    --stop-after-init --http-port=9665
```

**Dependencies**: `contacts`, `base_setup`

**Python dependencies**: `requests`, `cryptography` (both typically pre-installed in Odoo Docker images)

## Configuration

### 1. Create an Exclusion Tag

Go to **Contacts > Configuration > Contact Tags** and create a tag (e.g., "No NC Sync"). Any contact with this tag will be excluded from synchronization.

### 2. Create an Address Book Configuration

Go to **Settings > Technical > Nextcloud Contacts Sync > Address Book Configurations**:

| Field | Description | Example |
|-------|-------------|---------|
| Address Book Name | Display name | `Nextcloud Contacts` |
| Nextcloud URL | Base URL | `https://nextcloud.example.com` |
| CardDAV Path | Address book path | `/remote.php/dav/addressbooks/users/jdoe/contacts/` |
| Nextcloud User | Username for auth | `jdoe` |
| App Password | Nextcloud app password (encrypted at rest) | *(Settings > Security > App passwords)* |
| Sync Direction | `Bidirectional`, `NC -> Odoo`, or `Odoo -> NC` | `Odoo -> NC` |
| Exclusion Tag | Contacts with this tag are skipped | `No NC Sync` |

### 3. Test and Sync

1. Click **Test Connection** to verify CardDAV access (PROPFIND Depth:0)
2. Click **Push to Nextcloud** or **Pull from Nextcloud** depending on your sync direction

### 4. Settings (Optional)

Under **Settings > General Settings > Nextcloud Contacts Sync**:

| Setting | Description |
|---------|-------------|
| Default Address Book | Pre-selected config for convenience |
| Enable Cron | Toggle the 15-minute sync cron on/off |
| Sync Interval | Minutes between cron runs (default: 15) |

## Models

### nextcloud.contacts.sync.config

Configuration and sync logic for a Nextcloud address book connection.

**Key Methods:**

| Method | Description |
|--------|-------------|
| `action_push_to_nextcloud()` | Push all eligible contacts to NC (with stale UID recovery + orphan cleanup) |
| `action_pull_from_nextcloud()` | Pull all vCards from NC address book (ETag skip + email matching) |
| `action_sync_now()` | Dispatch to push/pull based on `sync_direction` |
| `action_force_full_resync()` | Wipe NC address book, clear all Odoo sync fields, push fresh |
| `action_test_connection()` | PROPFIND Depth:0 to verify CardDAV endpoint |
| `action_view_contacts()` | Opens partner list filtered to this config |
| `_cron_sync_all()` | Called by 15-minute cron; iterates active configs |
| `_cron_full_resync()` | Called by 6-hour cron; force full resync on all configs |

**CardDAV Operations (direct HTTP):**

| Method | HTTP | Description |
|--------|------|-------------|
| `_carddav_propfind_vcards()` | PROPFIND Depth:1 | List all UIDs + ETags in address book |
| `_carddav_get_vcard(uid)` | GET | Fetch single vCard text + ETag |
| `_carddav_put_vcard(uid, text, etag)` | PUT | Create/update vCard (optional If-Match) |
| `_carddav_delete_vcard(uid)` | DELETE | Remove vCard (no-op on 404) |

**vCard Operations (custom parser, no vobject):**

| Method | Description |
|--------|-------------|
| `_parse_vcard(text)` | Parse vCard 3.0 text into field dict (handles line folding) |
| `_partner_to_vcard(partner)` | Generate vCard 3.0 text from `res.partner` (companies and individuals) |
| `_vcard_data_to_partner_vals(data)` | Convert parsed vCard dict to Odoo partner field values |

**Push Flow (`action_push_to_nextcloud`):**

1. Search all partners with `type=contact` and without the exclusion tag
2. PROPFIND NC address book to get existing UIDs + ETags
3. **Stale UID recovery**: If an Odoo contact has a UID not found in NC, clear its sync fields so it gets recreated
4. For each partner:
   - No UID &rarr; generate UUID, PUT vCard, save UID + ETag
   - UID but no ETag (dirty) &rarr; PUT vCard, save new ETag
   - UID + ETag (unchanged) &rarr; skip
5. Commit progress every 10 writes (resilience against timeouts)
6. **Orphan detection**: Delete NC vCards whose UIDs don't correspond to any Odoo contact
7. Return summary notification

**Pull Flow (`action_pull_from_nextcloud`):**

1. PROPFIND Depth:1 to list all UIDs + ETags
2. Compare against existing `x_carddav_etag` on Odoo partners &mdash; skip unchanged
3. GET changed/new vCards, parse into field dicts
4. **Email matching**: Search existing partners by email before creating new ones
5. Create/update partners with `skip_nc_contact_sync` context (anti-loop)
6. **Orphan detection**: Clear sync fields on Odoo partners whose UIDs are absent from NC

### res.partner (Inherited)

**Added Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `x_nc_contact_uid` | Char | vCard UID in Nextcloud |
| `x_carddav_etag` | Char | ETag from NC for change detection |
| `x_carddav_href` | Char | Full CardDAV URL of contact |
| `x_nc_contacts_config_id` | Many2one | Link to address book config |
| `x_contact_sync_source` | Selection | `odoo` or `nextcloud` (origin marker) |
| `x_contact_last_sync` | Datetime | Last synchronization timestamp |

All fields have `copy=False` (not duplicated when partner is copied).

Sync fields are visible on the partner form only in Developer Mode (`base.group_no_one`).

**Model Overrides:**

- `write()`: If sync-relevant fields change on a tracked contact (source != `nextcloud`), clears `x_carddav_etag` to mark as dirty
- `unlink()`: Calls `_carddav_delete_vcard()` on the linked config before deletion

**Sync-relevant fields** (trigger dirty flag on change):

`name`, `email`, `phone`, `mobile`, `function`, `parent_id`, `company_name`, `street`, `street2`, `city`, `state_id`, `zip`, `country_id`, `website`, `comment`

### res.config.settings (Inherited)

Adds a section under **Settings > General Settings**:

| Field | Storage | Description |
|-------|---------|-------------|
| `nc_contacts_sync_default_config_id` | `ir.config_parameter` | Default address book config |
| `nc_contacts_sync_cron_enabled` | Cron `active` field | Toggle catch-up cron |
| `nc_contacts_sync_cron_interval` | Cron `interval_number` field | Minutes between syncs (default: 15) |

## vCard Field Mapping

| Odoo `res.partner` | vCard 3.0 Property | Notes |
|---|---|---|
| `name` | `FN` | Full name (required) |
| *(derived from name)* | `N` | `family;given;;;` (empty for companies) |
| `email` | `EMAIL;TYPE=INTERNET` | First email |
| `phone` | `TEL;TYPE=WORK` | Work phone |
| `mobile` | `TEL;TYPE=CELL` | Mobile phone |
| `function` | `TITLE` | Job title (individuals only) |
| `parent_id.name` / `company_name` | `ORG` | Organization (individuals: parent company; companies: self) |
| `street`, `street2`, `city`, `state_id.name`, `zip`, `country_id.name` | `ADR;TYPE=WORK` | `;;street;city;state;zip;country` |
| `website` | `URL` | Website |
| `comment` | `NOTE` | Internal notes |
| `image_1920` | `PHOTO;ENCODING=b;TYPE=JPEG\|PNG` | Embedded base64 photo, both directions. Folded per RFC 6350 §3.2 on export; import also accepts vCard 4.0 data URIs (`data:image/...;base64,...`). External URL references and images over 5 MB are skipped. |

**Companies** use `FN` = company name, `N` = empty (`;;;;`), and `ORG` = company name. No `TITLE` is generated.

**Pull direction**: `state` and `country` in vCard ADR are resolved by name lookup against `res.country.state` and `res.country`.

## Anti-Loop Protection

```
Odoo partner write/unlink
    |
    +-- context has skip_nc_contact_sync?    --> SKIP (sync operation)
    +-- x_contact_sync_source == 'nextcloud'? --> SKIP (came from NC)
    |
    +-- Otherwise: clear x_carddav_etag --> picked up by next push
```

Contacts pulled from Nextcloud are created with `x_contact_sync_source='nextcloud'` and `skip_nc_contact_sync=True`, preventing them from bouncing back.

## Cron Jobs

| Cron | Interval | Method | Description |
|------|----------|--------|-------------|
| Nextcloud Contacts Sync | 15 minutes | `_cron_sync_all()` | Push/pull all active configs |
| Nextcloud Contacts Full Resync | 6 hours | `_cron_full_resync()` | Force full resync (safety net) |

Both are `noupdate=1` (sync cron) and `noupdate=0` (full resync, updated on upgrade).

## Resilience

The module is designed to self-heal from interrupted operations:

| Scenario | Recovery |
|----------|----------|
| Gateway timeout during bulk push | `cr.commit()` every 10 contacts preserves progress; stale UID detection on next push recreates missing contacts |
| vCards missing from NC (manual deletion, migration) | Stale UID recovery detects UIDs absent from PROPFIND and clears sync fields for re-creation |
| Orphan vCards in NC (old sync tool, manual upload) | Orphan detection compares NC UIDs against Odoo tracked UIDs and deletes extras |
| Corrupted state (mixed UIDs, partial sync) | "Force Full Resync" wipes NC address book + Odoo sync fields, then pushes fresh |
| Contact edited during sync | `skip_nc_contact_sync` context prevents recursive dirty-flagging |
| Config deleted while contacts exist | `unlink()` override attempts CardDAV DELETE; failures are logged but don't block |

## Security

| Model | Group | Read | Write | Create | Delete |
|-------|-------|------|-------|--------|--------|
| `nextcloud.contacts.sync.config` | Internal User | Yes | No | No | No |
| `nextcloud.contacts.sync.config` | System (Admin) | Yes | Yes | Yes | Yes |

- **Encrypted credentials**: App passwords encrypted at rest with Fernet symmetric encryption (key auto-generated in `ir.config_parameter`). Encryption is mandatory &mdash; saving a password without the `cryptography` package installed will raise an error rather than storing plaintext.
- **Field-level access control**: Both `nextcloud_app_password` and `nextcloud_app_password_encrypted` fields are restricted to `base.group_system` (admin only). Non-admin users cannot read or decrypt credentials, even via RPC.
- **HTTPS enforced**: A `@api.constrains` validation rejects any `nextcloud_base_url` that does not start with `https://`, preventing credentials from being sent in cleartext.
- **Sync fields on `res.partner`**: Visible only in Developer Mode (`base.group_no_one`)
- **No `sudo()` escalation**: Only used for `ir.config_parameter` access (encryption key) and cron writes &mdash; standard Odoo patterns

## Troubleshooting

| Issue | Check |
|-------|-------|
| Test connection fails | URL correct? Path ends with `/`? App password (not main password)? |
| Push shows 0 contacts | Any contacts with `type=contact`? Exclusion tag applied to all? |
| Duplicates in NC | Run "Force Full Resync" to wipe and re-push clean |
| Pull creates duplicates | Check email matching &mdash; contacts without email can't be matched |
| Contacts not syncing after edit | Field must be in sync-relevant set; check `x_contact_sync_source` is not `nextcloud` |
| Gateway timeout on large push | Normal for first sync; progress is committed every 10 contacts. Run push again to pick up remaining |
| Cron not running | Check Settings > General Settings > Nextcloud Contacts Sync > cron toggle |
| Encryption errors | `cryptography` library installed? Check `ir.config_parameter` for `contacts_nextcloud_sync.encryption_key` |

## Changelog

### 18.0.1.2.0
- **Contact photo sync**: bidirectional `PHOTO` &harr; `image_1920` mapping. Export emits embedded base64 (`PHOTO;ENCODING=b;TYPE=JPEG|PNG`), folded per RFC 6350 §3.2; import accepts both vCard 3.0 inline base64 and vCard 4.0 data URIs. External URL references and images over 5 MB are skipped, and `image_1920` is now a sync-relevant field (a photo change triggers a push).

### 18.0.1.1.x
- CardDAV push/pull, anti-loop protection, encrypted credentials, cron sync.

## License

LGPL-3.0

