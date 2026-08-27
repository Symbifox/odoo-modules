"""Mobile-facing projection of ``bf.email`` — consumed by the Android app.

Kept out of bf_email.py (already ~3.6k lines) because none of it belongs to
the desktop flow: every method here returns plain JSON-able data instead of
``ir.actions`` dicts, and every write is headless.

Three ideas drive the shape:

* **Conversations, not rows.** ``bf.email`` stores one row per message. A
  phone shows threads, so rows are folded on ``thread_root_id`` (RFC 2822),
  falling back to ``id:<id>`` for messages that carry no References chain.
  The fold happens in SQL — grouping 30k rows through the ORM to render 25
  list items would be the whole latency budget.

* **The payload is scoped to the device's user, always.** These methods run
  after ``request.update_env(user=…)`` so the owner ir.rule applies, but the
  aggregate queries hit the table directly and therefore repeat
  ``user_id = %s`` themselves. ``group_email_admin`` grants read on every
  user's rows in the ORM; the mobile API deliberately ignores that — this is
  *your* mailbox on *your* phone, not an admin console.

* **Bodies are sanitized and remote content is blocked by default.** Inbound
  HTML is stored raw. The app renders it in a WebView, so it gets
  ``body_html_display`` (sanitized) with remote ``<img>`` sources parked in
  ``data-blocked-src`` until the reader asks for them — the usual defence
  against tracking pixels.
"""
import base64
import email as email_mod
import email.policy
import email.utils
import logging
import mimetypes
import re
from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models, tools
from odoo.exceptions import AccessError, UserError

from . import bf_email_imap
from .subject_utils import dedup_subject_prefix

_logger = logging.getLogger(__name__)

# Thread-list page size ceiling. The app asks for 25; anything past this is a
# client bug or someone probing, and a 5k-row page would pin a worker.
MAX_PAGE = 100
# Messages returned in one conversation payload. Threads longer than this are
# truncated from the top (oldest dropped) — the recent end is what's read.
MAX_THREAD_MESSAGES = 60
PREVIEW_CHARS = 160

# Recipients allowed on one send (To + Cc). Well above any real business
# thread, well below a mailing list. Two things it stops: a client bug or a
# stolen token turning the tenant's SMTP into a broadcaster, and the accidental
# reply-all to a sixty-person thread — which on a phone is one tap away and
# cannot be recalled.
MAX_RECIPIENTS = 50

# Rows one triage call may touch. Archiving moves each message on the IMAP
# server too, so an unbounded list is an unbounded number of mailbox
# operations from a single request.
MAX_BULK_IDS = 100

# Staged outbound uploads live under this fake res_model until a send claims
# them. It is what lets the send path distinguish "a file this user just
# uploaded" from "an arbitrary ir.attachment id".
# Odoo ships these on every fresh database. A company still wearing them has
# not chosen anything, so treating them as "the tenant's brand" would paint the
# app in stock Odoo purple — which is precisely the look this product exists to
# avoid. Treated as unbranded, so the product default applies instead.
ODOO_STOCK_COLOURS = {"#714B67", "#875A7B", "#212529", "#017E84"}

UPLOAD_MARKER_MODEL = "bf.email.mobile.upload"
UPLOAD_SINGLE_MAX = 25 * 1024 * 1024
UPLOAD_TOTAL_MAX = 25 * 1024 * 1024

# Returned by _mobile_attachment_bytes when the attachment is over the cap —
# distinct from None (not found) so the controller can answer 413, not 404.
TOO_LARGE = object()

# <img src="http…"> → data-blocked-src, so the WebView can't phone home until
# the reader taps "load images". Matches the src attribute only, leaving cid:
# and data: sources (inline, already local) untouched.
_REMOTE_IMG_RE = re.compile(
    r"""(<img\b[^>]*?\s)src\s*=\s*(["'])(\s*https?://[^"']*)\2""",
    re.IGNORECASE,
)

# Models the app may route an email into, or spawn a record on. An allowlist
# rather than "any model with a chatter": /route takes a model name from the
# device, and _import_into_chatter posts with the message author's rights.
ROUTABLE_MODELS = (
    "project.task",
    "res.partner",
    "crm.lead",
    "helpdesk.ticket",
    "account.move",
    "project.project",
    "sale.order",
    "purchase.order",
)

# kind → (model, bf.email method) for POST /create.
SPAWN_KINDS = {
    "task": ("project.task", "action_create_task"),
    "ticket": ("helpdesk.ticket", "action_create_helpdesk_ticket"),
    "lead": ("crm.lead", "action_create_crm_lead"),
    "expense": ("hr.expense", "action_create_expense"),
    "bill": ("account.move", "action_create_vendor_bill"),
    "invoice": ("account.move", "action_create_customer_invoice"),
}


class BfEmailMobile(models.Model):
    _inherit = "bf.email"

    # ------------------------------------------------------------------
    # Push on ingest
    # ------------------------------------------------------------------
    @api.model
    def _cron_sync_imap(self):
        """Sweep abandoned login attempts, then run the normal IMAP pull.

        ``/auth/start`` mints the device row (bearer token and all) before the
        app has collected anything. A user who closes the browser tab leaves
        that row behind, active, forever. The token was never disclosed — only
        the short-lived code was — so this is tidiness rather than exposure,
        but an unbounded table of live credentials is not something to keep.

        Hung off this cron rather than its own scheduled action: it already
        runs every few minutes and the sweep is a single indexed DELETE.
        """
        for sweep in (
            lambda: self.env["bf.email.mobile.device"]._gc_pending(),
            self._gc_uploads,
            lambda: self.env["bf.email.mobile.send"]._gc(),
        ):
            try:
                sweep()
            except Exception:  # noqa: BLE001
                # Never let housekeeping stop the mail from syncing.
                _logger.warning("bf.email: une purge mobile a échoué",
                                exc_info=True)
        return super()._cron_sync_imap()

    @api.model
    def _sync_account(self, account):
        """Notify the owner's phone about what this IMAP pull brought in.

        Wrapped around the whole account sync rather than hooked into
        ``_ingest_rfc822``: pushing per parsed message would fire one HTTP
        POST inside the fetch loop, so a 100-message catch-up would hold the
        IMAP connection open behind a hundred round-trips to ntfy.
        """
        Device = self.env["bf.email.mobile.device"].sudo()
        watching = Device.search_count([
            ("user_id", "=", account.user_id.id),
            ("active", "=", True),
            ("push_endpoint", "!=", False),
        ])
        if not watching:
            return super()._sync_account(account)

        last = self.sudo().search(
            [("account_id", "=", account.id)], order="id desc", limit=1)
        result = super()._sync_account(account)
        fresh = self.sudo().search([
            ("account_id", "=", account.id),
            ("id", ">", last.id or 0),
            ("direction", "=", "in"),
            ("is_handled", "=", False),
        ])
        if fresh:
            self.env["bf.email.unifiedpush"]._notify_new_emails(fresh)
        return result

    # ------------------------------------------------------------------
    # Identity helpers
    # ------------------------------------------------------------------
    def _mobile_thread_key(self):
        """Stable conversation key for this row.

        ``thread_root_id`` when the message carries a References chain,
        otherwise a per-row key so a standalone message is its own thread.
        Mirrors ``action_open_conversation``'s primary branch; the subject
        fallback it uses on the desktop is deliberately NOT reproduced —
        matching on ``subject ilike`` would merge unrelated "Suivi" threads
        from the same partner into one phone conversation.
        """
        self.ensure_one()
        return self.thread_root_id or "id:%d" % self.id

    def _push_sender_label(self):
        """Human label for the push title: contact name, else bare address."""
        self.ensure_one()
        if self.partner_id:
            return self.partner_id.display_name
        _name, addr = email.utils.parseaddr(self.email_from or "")
        return addr or self.email_from or _("Nouveau courriel")

    @staticmethod
    def _ms(value):
        """Odoo Datetime → epoch milliseconds (UTC), or False."""
        if not value:
            return False
        return int(value.replace(tzinfo=pytz.UTC).timestamp() * 1000)

    def _mobile_user_tz(self):
        return pytz.timezone(self.env.user.tz or "America/Montreal")

    # ------------------------------------------------------------------
    # Body rendering
    # ------------------------------------------------------------------
    def _mobile_body_html(self, load_images=False):
        """Sanitized body for the WebView, remote images blocked by default.

        Returns ``(html, blocked_count)`` so the app can show the "this
        message tried to load N remote images" bar only when there is
        something to unblock.
        """
        self.ensure_one()
        html = self.body_html_display or ""
        if load_images:
            return html, 0
        blocked = [0]

        def _park(match):
            blocked[0] += 1
            return '%sdata-blocked-src=%s%s%s' % (
                match.group(1), match.group(2), match.group(3), match.group(2))

        return _REMOTE_IMG_RE.sub(_park, html), blocked[0]

    # ------------------------------------------------------------------
    # Attachments (listed without materializing ir.attachment rows)
    # ------------------------------------------------------------------
    def _mobile_attachments(self):
        """Attachment metadata for this row.

        Two storage shapes: chatter rows delegate to
        ``mail_message_id.attachment_ids``; orphan IMAP rows keep the bytes
        inside ``raw_rfc822`` and are enumerated by walking the MIME parts.
        Neither branch writes to the database — ``_extract_orphan_attachments``
        does create ir.attachment rows, but only Forward needs that, and
        opening a message must not litter the attachment table.
        """
        self.ensure_one()
        if self.mail_message_id and self.attachment_ids:
            return [{
                "idx": idx,
                "name": att.name or "piece-jointe",
                "mimetype": att.mimetype or "application/octet-stream",
                "size": att.file_size or 0,
                "attachment_id": att.id,
            } for idx, att in enumerate(self.attachment_ids)]

        items = self._raw_attachment_parts()
        return [{
            "idx": idx,
            "name": name,
            "mimetype": mimetype,
            "size": len(payload),
            "attachment_id": False,
        } for idx, (name, mimetype, payload) in enumerate(items)]

    def _raw_attachment_parts(self):
        """[(filename, mimetype, bytes)] parsed out of ``raw_rfc822``."""
        self.ensure_one()
        if not self.raw_rfc822:
            return []
        try:
            raw = base64.b64decode(self.raw_rfc822)
            parsed = email_mod.message_from_bytes(raw, policy=email.policy.default)
        except Exception:  # noqa: BLE001
            _logger.warning("bf.email #%s: raw_rfc822 unparseable for "
                            "attachment listing", self.id, exc_info=True)
            return []
        out = []
        for filename, payload in bf_email_imap.extract_attachments(parsed):
            guessed = mimetypes.guess_type(filename)[0]
            out.append((filename, guessed or "application/octet-stream", payload))
        return out

    def _mobile_attachment_bytes(self, idx, max_bytes=None):
        """(filename, mimetype, bytes) for one attachment, or None.

        ``idx`` indexes the list ``_mobile_attachments`` returned, so the app
        never has to know which of the two storage shapes it is looking at —
        and never gets to name an arbitrary ir.attachment id.

        Returns [[TOO_LARGE]] past ``max_bytes``. The ORM branch consults
        ``file_size`` *before* decoding: a cap that only fires after the bytes
        are already in memory protects nothing.
        """
        self.ensure_one()
        if self.mail_message_id and self.attachment_ids:
            if idx < 0 or idx >= len(self.attachment_ids):
                return None
            att = self.attachment_ids[idx]
            if max_bytes and (att.file_size or 0) > max_bytes:
                return TOO_LARGE
            if not att.datas:
                return None
            return (att.name or "piece-jointe",
                    att.mimetype or "application/octet-stream",
                    base64.b64decode(att.datas))
        parts = self._raw_attachment_parts()
        if idx < 0 or idx >= len(parts):
            return None
        # The raw branch has to parse the message to know any size at all, so
        # the check can only happen here.
        if max_bytes and len(parts[idx][2]) > max_bytes:
            return TOO_LARGE
        return parts[idx]

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------
    def _mobile_record_ref(self):
        """The Odoo record this email was routed into, if any."""
        self.ensure_one()
        if not (self.res_model and self.res_id):
            return False
        return {
            "model": self.res_model,
            "id": self.res_id,
            "name": self.record_name or "",
        }

    def _mobile_message_dict(self, full=False, load_images=False):
        """One message. ``full`` adds the body, headers and attachments."""
        self.ensure_one()
        data = {
            "id": self.id,
            "thread_key": self._mobile_thread_key(),
            "direction": self.direction,
            "subject": self.subject or "",
            "from": self.email_from or "",
            "from_label": self._push_sender_label(),
            "date_ms": self._ms(self.date),
            "preview": (self.body_preview or "")[:PREVIEW_CHARS],
            "status": self.status,
            "is_handled": self.is_handled,
            "snoozed_until_ms": self._ms(self.snoozed_until),
            "category": self.category or "",
            "priority": self.priority or "",
            "has_attachments": self.has_attachments,
            "attachment_count": self.attachment_count,
            "partner_id": self.partner_id.id or False,
            "partner_name": self.partner_id.display_name or "",
            "account_id": self.account_id.id or False,
            "record": self._mobile_record_ref(),
            "is_question": self.is_question,
            "is_action_request": self.is_action_request,
        }
        if not full:
            return data
        body, blocked = self._mobile_body_html(load_images=load_images)
        attachments = self._mobile_attachments()
        data.update({
            "to": self.email_to or "",
            "cc": self.email_cc or "",
            "body_html": body,
            "blocked_images": blocked,
            "attachments": attachments,
            # The stored counter is what the ingest saw; this is what can
            # actually be downloaded right now. They diverge when the raw
            # message is gone from the filestore, and the app should draw the
            # paperclip from what it can open, not from a stale promise.
            "attachment_count": len(attachments),
            "message_id_header": self.message_id_header or "",
        })
        return data

    # ------------------------------------------------------------------
    # Thread list
    # ------------------------------------------------------------------
    @api.model
    def _mobile_filter_sql(self, name):
        """(sql_fragment, params) for a named mailbox filter.

        The inbox definition mirrors ``bf.email.dashboard.action_view_inbox_active``
        so phone and desktop agree on what "unhandled" means: an IMAP row must
        still be in the INBOX, and chatter/gateway rows always count.
        """
        now = fields.Datetime.now()
        clauses = {
            # ⚠️ Transcription SQL de `bf.email._inbox_domain` : un test
            # compare les deux sur un jeu de lignes, pas sur leur texte.
            "inbox": ("is_handled = false AND (imap_in_inbox = true "
                      "OR source IN ('chatter','gateway') "
                      "OR imap_folder IS NULL)", []),
            "unread": ("status = 'new' AND is_handled = false", []),
            "snoozed": ("is_handled = true AND snoozed_until IS NOT NULL "
                        "AND snoozed_until > %s", [now]),
            "handled": ("is_handled = true AND (snoozed_until IS NULL "
                        "OR snoozed_until <= %s)", [now]),
            "sent": ("direction = 'out'", []),
            "unrouted": ("source = 'imap' AND res_model IS NULL "
                         "AND is_handled = false", []),
            "all": ("true", []),
        }
        if name not in clauses:
            raise UserError(_("Filtre inconnu : %s") % name)
        return clauses[name]

    @api.model
    def get_mobile_threads(self, filter_name="inbox", search=None, account_id=None,
                           offset=0, limit=25, grouped=True):
        """One page of conversations, newest activity first.

        ``grouped=False`` turns conversation folding off: every message becomes
        its own row, the way a plain IMAP client shows a mailbox. Same query,
        different grouping key — so paging, counts and filters keep behaving
        identically instead of needing a second code path that could drift.

        Returns ``{"threads": [...], "has_more": bool}``. Aggregation runs in
        SQL; only the newest row of each thread is then browsed through the
        ORM, so access rules still gate what actually gets serialized.
        """
        # Same reason as _mobile_counts: the aggregate below is raw SQL, so
        # any pending write must reach the database first or the list renders
        # the state before the user's last action.
        self.env.flush_all()
        limit = max(1, min(int(limit or 25), MAX_PAGE))
        offset = max(0, int(offset or 0))
        where, params = self._mobile_filter_sql(filter_name)
        sql = ["user_id = %s", "active = true", where]
        args = [self.env.uid] + list(params)

        if account_id:
            sql.append("account_id = %s")
            args.append(int(account_id))
        if search:
            term = "%%%s%%" % search.strip()
            sql.append("(subject ILIKE %s OR email_from ILIKE %s "
                       "OR body_preview ILIKE %s)")
            args += [term, term, term]

        # One row per conversation: newest message id, counts, latest date.
        # COALESCE on date because gateway rows can land with date NULL and a
        # NULL sort key would float them to an arbitrary end of the page.
        # Folded on the RFC 2822 root, or on the row itself when the reader
        # has asked to see messages rather than conversations.
        key_sql = ("COALESCE(NULLIF(thread_root_id, ''), 'id:' || id::text)"
                   if grouped else "'id:' || id::text")
        query = """
            SELECT %s AS tkey,
                   MAX(COALESCE(date, create_date))                        AS last_date,
                   COUNT(*)                                                AS msg_count,
                   COUNT(*) FILTER (
                       WHERE status = 'new' AND direction = 'in')          AS unread_count,
                   (ARRAY_AGG(id ORDER BY COALESCE(date, create_date) DESC,
                                          id DESC))[1]                     AS last_id
              FROM bf_email
             WHERE %s
          GROUP BY tkey
          ORDER BY last_date DESC
             LIMIT %%s OFFSET %%s
        """ % (key_sql, " AND ".join(sql))
        self.env.cr.execute(query, args + [limit + 1, offset])
        rows = self.env.cr.dictfetchall()

        has_more = len(rows) > limit
        rows = rows[:limit]
        by_last_id = {r["last_id"]: r for r in rows}
        # browse() keeps SQL order only if we re-order ourselves afterwards.
        records = self.browse(list(by_last_id)).exists()
        serialized = {rec.id: rec for rec in records}

        threads = []
        for row in rows:
            rec = serialized.get(row["last_id"])
            if not rec:
                continue  # filtered out by an access rule between the two queries
            data = rec._mobile_message_dict()
            data.update({
                "thread_key": row["tkey"],
                "last_id": rec.id,
                "message_count": row["msg_count"],
                "unread_count": row["unread_count"],
                "last_date_ms": self._ms(row["last_date"]),
            })
            threads.append(data)
        return {"threads": threads, "has_more": has_more}

    @api.model
    def _thread_domain(self, thread_key):
        """Domain matching every row of a conversation."""
        if not thread_key:
            raise UserError(_("Clé de fil manquante."))
        if thread_key.startswith("id:"):
            try:
                return [("id", "=", int(thread_key[3:]))]
            except ValueError:
                raise UserError(_("Clé de fil invalide."))
        return [("thread_root_id", "=", thread_key)]

    @api.model
    def get_mobile_conversation(self, thread_key, load_images=False):
        """Every message of a conversation, oldest → newest.

        The last message comes back with its body inline: opening a thread to
        read the message that arrived is the whole point, and making the app
        round-trip for it would put a spinner on the common case. The rest
        carry previews and are fetched on expand.
        """
        domain = self._thread_domain(thread_key) + [("user_id", "=", self.env.uid)]
        records = self.search(domain, order="date asc, id asc")
        if not records:
            raise UserError(_("Fil introuvable."))
        truncated = len(records) > MAX_THREAD_MESSAGES
        if truncated:
            records = records[-MAX_THREAD_MESSAGES:]

        # Reading the thread is what "read" means. Without this the newest
        # message arrives already expanded — the reader sees it — yet the row
        # stays `new`, so the unread badge never clears from normal use. Marks
        # the whole conversation, like every mail client: the list counts
        # unread per thread, so clearing only the last one would leave the
        # badge lit on a thread with nothing left to read.
        records.action_mark_read()

        messages = [rec._mobile_message_dict() for rec in records[:-1]]
        messages.append(records[-1]._mobile_message_dict(
            full=True, load_images=load_images))
        return {
            "thread_key": thread_key,
            "subject": records[-1].subject or "",
            "messages": messages,
            "truncated": truncated,
        }

    @api.model
    def get_mobile_message(self, email_id, load_images=False):
        """Full payload for one message, and mark it read on the way out."""
        rec = self.browse(int(email_id)).exists()
        if not rec or rec.user_id.id != self.env.uid:
            raise UserError(_("Courriel introuvable."))
        # Mark first, serialize second: the other order hands the app a
        # payload still claiming status "new" for a row it just read, and the
        # list it refreshes from would disagree with the message it is showing.
        rec.action_mark_read()
        return rec._mobile_message_dict(full=True, load_images=load_images)

    # ------------------------------------------------------------------
    # Bootstrap payload
    # ------------------------------------------------------------------
    @api.model
    def get_mobile_config(self):
        """Everything the app needs once, at login and on resume."""
        accounts = self.env["bf.email.account"].search([
            ("user_id", "=", self.env.uid), ("active", "=", True),
        ])
        return {
            "user_name": self.env.user.name,
            "branding": self._mobile_branding(),
            "tz": self.env.user.tz or "America/Montreal",
            "signature": self.env.user.signature or "",
            # Only the addressing bits — never host/login/password, which live
            # on the same model and are readable by the owner.
            "accounts": [{
                "id": acc.id,
                "name": acc.name or acc.login or "",
                "login": acc.login or "",
                "aliases": acc.email_aliases or "",
                "state": acc.state,
            } for acc in accounts],
            "counts": self._mobile_counts(),
            "snooze_presets": self._mobile_snooze_presets(),
            "routable_models": self._mobile_routable_models(),
            "spawn_kinds": sorted(
                k for k, (model, _m) in SPAWN_KINDS.items()
                if model in self.env
            ),
        }

    @api.model
    def _mobile_branding(self):
        """Brand identity of the instance, so the app wears the tenant's colours.

        Read **defensively**. ``report_brand_*`` come from ``bluefox_branding``
        which may not be installed; ``primary_color`` / ``secondary_color`` are
        native ``res.company`` fields and are the fallback. Nothing here is
        required: an instance with no branding at all is a normal instance, not
        an error, and the app owns the default.

        Colours are validated as ``#RRGGBB`` before being sent. A malformed
        value in the database would otherwise reach a colour parser on the
        phone, and "the mailbox won't open" is a poor way to learn that someone
        typed "bleu" into a settings field.
        """
        company = self.env.company.sudo()

        def hex_colour(*field_names):
            for name in field_names:
                if name not in company._fields:
                    continue
                value = (company[name] or "").strip()
                if re.fullmatch(r"#[0-9A-Fa-f]{6}", value) \
                        and value.upper() not in ODOO_STOCK_COLOURS:
                    return value.upper()
            return None

        return {
            "name": company.name or "",
            # bluefox_branding first, native Odoo colours as the fallback.
            "primary": hex_colour("report_brand_primary", "primary_color"),
            "dark": hex_colour("report_brand_dark", "secondary_color"),
            # Odoo serves the company logo publicly on this route; no extra
            # endpoint and no base64 bloat in the JSON.
            "logo_url": "/web/binary/company_logo",
        }

    @api.model
    def _mobile_counts(self):
        """Badge counts per mailbox filter — returned after every mutation so
        the app never has to guess how a write moved the totals.

        ``flush_all`` first: these counts are read with raw SQL, which does not
        see ORM writes still sitting in the environment's cache. Without it,
        archiving an email returns the totals from *before* the archive and the
        phone's badge trails one action behind, forever.
        """
        self.env.flush_all()
        counts = {}
        for name in ("inbox", "unread", "snoozed", "unrouted"):
            where, params = self._mobile_filter_sql(name)
            self.env.cr.execute(
                "SELECT COUNT(*) FROM bf_email "
                "WHERE user_id = %%s AND active = true AND %s" % where,
                [self.env.uid] + list(params),
            )
            counts[name] = self.env.cr.fetchone()[0]
        return counts

    @api.model
    def _mobile_routable_models(self):
        """Allowlisted routing targets that exist and the user may read."""
        out = []
        for model in ROUTABLE_MODELS:
            if model not in self.env:
                continue
            if not self.env[model].check_access_rights("read", raise_exception=False):
                continue
            out.append({
                "model": model,
                "label": self.env["ir.model"]._get(model).name or model,
            })
        return out

    @api.model
    def _mobile_snooze_presets(self):
        """Snooze options resolved in the *user's* timezone.

        The desktop wizard builds "tonight (18h)" with a naive
        ``fields.Datetime.now().replace(hour=18)``, which is 18:00 UTC — early
        afternoon in Montreal. Computing in the user's tz and converting back
        is what makes the phone's "ce soir" actually mean this evening.
        """
        tz = self._mobile_user_tz()
        local_now = fields.Datetime.now().replace(tzinfo=pytz.UTC).astimezone(tz)

        def at(day_offset, hour):
            target = (local_now + timedelta(days=day_offset)).date()
            naive = datetime.combine(target, time(hour=hour))
            return tz.localize(naive).astimezone(pytz.UTC).replace(tzinfo=None)

        tonight = at(0, 18)
        if tonight <= fields.Datetime.now():
            tonight = at(1, 18)
        days_ahead = (7 - local_now.weekday()) % 7 or 7
        return [
            {"key": "1h", "label": _("Dans 1 heure"),
             "until_ms": self._ms(fields.Datetime.now() + timedelta(hours=1))},
            {"key": "3h", "label": _("Dans 3 heures"),
             "until_ms": self._ms(fields.Datetime.now() + timedelta(hours=3))},
            {"key": "tonight", "label": _("Ce soir (18 h)"),
             "until_ms": self._ms(tonight)},
            {"key": "tomorrow", "label": _("Demain matin (8 h)"),
             "until_ms": self._ms(at(1, 8))},
            {"key": "nextweek", "label": _("Lundi prochain (8 h)"),
             "until_ms": self._ms(at(days_ahead, 8))},
        ]

    # ------------------------------------------------------------------
    # Headless triage
    # ------------------------------------------------------------------
    @api.model
    def _mobile_browse(self, email_ids):
        """Browse ids owned by the calling user, or raise.

        The owner ir.rule would already refuse a foreign row, but
        ``group_email_admin`` members can read every user's mail: without this
        check an admin's phone could archive somebody else's inbox.
        """
        # Coerced defensively: a malformed email_ids from a buggy or hostile
        # client must come back as a 400, not blow up into a 500.
        if isinstance(email_ids, (int, str)):
            email_ids = [email_ids]
        if not isinstance(email_ids, (list, tuple)):
            raise UserError(_("Liste de courriels invalide."))
        try:
            ids = [int(i) for i in email_ids]
        except (TypeError, ValueError):
            raise UserError(_("Liste de courriels invalide."))
        if not ids:
            raise UserError(_("Aucun courriel visé."))
        if len(ids) > MAX_BULK_IDS:
            raise UserError(
                _("Trop de courriels d'un coup (%(count)d, maximum %(max)d).")
                % {"count": len(ids), "max": MAX_BULK_IDS})
        records = self.browse(ids).exists()
        foreign = records.filtered(lambda r: r.user_id.id != self.env.uid)
        if foreign:
            raise AccessError(_("Ces courriels appartiennent à un autre utilisateur."))
        return records

    @api.model
    def mobile_mark_read(self, email_ids):
        records = self._mobile_browse(email_ids)
        records.action_mark_read()
        push = self.env["bf.email.unifiedpush"]
        for rec in records:
            push._notify_clear(self.env.user, rec.id)
        return self._mobile_counts()

    @api.model
    def mobile_set_handled(self, email_ids, handled=True):
        """Archive (or restore) from the phone, IMAP write-back included.

        ``action_archive`` is reused verbatim rather than writing the fields
        directly: it is what moves the message to ``Archives/{YYYY}`` on the
        real mailbox and closes the row's reminder activities.
        """
        records = self._mobile_browse(email_ids)
        if handled:
            records.with_context(with_undo_redirect=False).action_archive()
            for rec in records:
                self.env["bf.email.unifiedpush"]._notify_clear(self.env.user, rec.id)
        else:
            records.action_unhandle()
        return self._mobile_counts()

    @api.model
    def mobile_snooze(self, email_ids, until_ms):
        """Defer out of the inbox until ``until_ms`` (epoch ms, UTC)."""
        records = self._mobile_browse(email_ids)
        try:
            until = datetime.fromtimestamp(
                int(until_ms) / 1000.0, tz=pytz.UTC).replace(tzinfo=None)
        except (TypeError, ValueError, OSError, OverflowError):
            raise UserError(_("Date de report invalide."))
        if until <= fields.Datetime.now():
            raise UserError(_("La date de report doit être dans le futur."))
        records.write({
            "is_handled": True,
            "handled_at": fields.Datetime.now(),
            "snoozed_until": until,
        })
        push = self.env["bf.email.unifiedpush"]
        for rec in records:
            push._notify_clear(self.env.user, rec.id)
        return self._mobile_counts()

    # ------------------------------------------------------------------
    # Outbound attachments
    # ------------------------------------------------------------------
    @api.model
    def mobile_stage_upload(self, device, filename, content, mimetype=None):
        """Park an uploaded file until a send claims it.

        Staged under a marker model of our own rather than left unattached:
        the send path must be able to tell "a file this user just uploaded
        from this device" apart from "any ir.attachment id in the database".
        See ``_mobile_claim_uploads`` for why that distinction is the whole
        point of this method existing.
        """
        name = (filename or "piece-jointe").strip() or "piece-jointe"
        attachment = self.env["ir.attachment"].sudo().create({
            "name": name[:180],
            "raw": content,
            "mimetype": mimetype or "application/octet-stream",
            "res_model": UPLOAD_MARKER_MODEL,
            "res_id": device.id,
            # create_uid is what _mobile_claim_uploads matches on; force it to
            # the device's user rather than whoever the sudo env belongs to.
            "create_uid": self.env.uid,
        })
        return {
            "ok": True,
            "attachment_id": attachment.id,
            "name": attachment.name,
            "size": attachment.file_size or len(content),
            "mimetype": attachment.mimetype or "",
        }

    @api.model
    def _mobile_claim_uploads(self, device, attachment_ids, target_model,
                              target_res_id):
        """Validate ids the client wants to attach, consume them, return them.

        **This is a security boundary, not a lookup.** Without it, ``/reply``
        would take any ``ir.attachment`` id and mail it out — a one-request
        exfiltration of every document in the database, from an account that
        may legitimately hold none of them. So an id is only accepted when it
        is all four of: staged under our marker model, staged by *this*
        device, created by *this* user, and still unclaimed.

        "Consume" is not bookkeeping. Leaving a sent attachment parked under
        the marker has two consequences, both bad: ``_gc_uploads`` would delete
        it 24 hours later — **stripping the file off mail already sent** — and
        the same id could be re-attached to further messages, which is what
        "single-use" was supposed to prevent. Re-parenting onto the record the
        message lands on is also simply where Odoo keeps chatter attachments,
        so the file ends up filed the way the desktop would have filed it.
        """
        ids = [int(i) for i in (attachment_ids or [])]
        if not ids:
            return []
        Attachment = self.env["ir.attachment"].sudo()
        staged = Attachment.search([
            ("id", "in", ids),
            ("res_model", "=", UPLOAD_MARKER_MODEL),
            ("res_id", "=", device.id),
            ("create_uid", "=", self.env.uid),
        ])
        if len(staged) != len(set(ids)):
            raise UserError(_("Pièce jointe inconnue ou déjà envoyée."))
        total = sum(att.file_size or 0 for att in staged)
        if total > UPLOAD_TOTAL_MAX:
            raise UserError(
                _("Les pièces jointes dépassent %d Mo au total.")
                % (UPLOAD_TOTAL_MAX // (1024 * 1024)))
        staged.write({"res_model": target_model, "res_id": target_res_id})
        return staged.ids

    @api.model
    def _gc_uploads(self, hours=24):
        """Drop staged uploads no send ever claimed (composer abandoned)."""
        cutoff = fields.Datetime.now() - timedelta(hours=hours)
        stale = self.env["ir.attachment"].sudo().search([
            ("res_model", "=", UPLOAD_MARKER_MODEL),
            ("create_date", "<", cutoff),
        ])
        count = len(stale)
        if stale:
            stale.unlink()
        return count

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------
    @api.model
    def _mobile_compose_body(self, body, is_html=False):
        """The user's typed body, as HTML fit to send.

        Two modes, and the distinction matters. Plain text is *escaped*: what
        someone types is text, and `<b>` in a sentence must survive as those
        four characters. Rich text is *sanitized*: the client sends real markup,
        so it is filtered rather than escaped — the phone is not a trusted
        source, and this body goes into the chatter and out by email.
        """
        if is_html:
            cleaned = tools.html_sanitize(body or "")
            # A sanitizer that ate everything means the payload was entirely
            # markup we refuse; sending an empty message is not what was meant.
            if not re.sub(r"<[^>]+>|&nbsp;|\s", "", cleaned):
                raise UserError(_("Le message est vide."))
            return cleaned
        return self._mobile_body_to_html(body)

    @staticmethod
    def _mobile_body_to_html(body):
        """Phone composer text → HTML, newlines preserved, markup escaped."""
        escaped = (body or "").replace("&", "&amp;").replace(
            "<", "&lt;").replace(">", "&gt;")
        paragraphs = [p.replace("\n", "<br/>") for p in escaped.split("\n\n")]
        return "".join('<p style="margin:0 0 12px 0;">%s</p>' % p
                       for p in paragraphs if p.strip()) or "<p><br/></p>"

    @api.model
    def _guard_recipient_count(self, to_ids, cc_ids):
        """Refuse a send addressed to an implausible number of people.

        Applies to the recipients the *server* derived as well as to a list the
        client supplied. Reply-all is the reason: a thread that reached sixty
        people is exactly where one tap on a phone does damage that cannot be
        undone. Refusing with an explanation is a worse feature and a much
        better outcome than sending it.
        """
        total = len(set(to_ids) | set(cc_ids))
        if total > MAX_RECIPIENTS:
            raise UserError(
                _("Ce message viserait %(count)d destinataires (maximum %(max)d "
                  "depuis le téléphone). Envoyez-le depuis Odoo si c'est voulu.")
                % {"count": total, "max": MAX_RECIPIENTS})

    @api.model
    def _mobile_partners_from_addresses(self, addresses):
        """['a@b.c', …] → partner ids, creating contacts for unknowns.

        Same lookup order as ``_build_reply_recipients``: exact email, then
        normalized, then create. Reused rather than re-derived so a manually
        typed recipient resolves to the same contact the Reply button would
        have picked.
        """
        Partner = self.env["res.partner"].sudo()
        ids = []
        for raw in addresses or []:
            display_name, bare = email.utils.parseaddr(raw or "")
            bare = (bare or "").strip()
            if not bare:
                continue
            partner = Partner.search([("email", "=ilike", bare)], limit=1)
            if not partner:
                partner = Partner.search(
                    [("email_normalized", "=", bare.lower())], limit=1)
            if not partner:
                partner = Partner.create({"name": display_name or bare,
                                          "email": bare})
            if partner.id not in ids:
                ids.append(partner.id)
        return ids

    def _mobile_post(self, target_model, target_res_id, subject, body_html,
                     partner_ids, cc_partner_ids, attachment_ids=None):
        """Create and fire the mail composer headlessly.

        Same wizard the desktop uses, so Cc/Bcc plumbing, outgoing server
        selection and chatter logging all behave identically — only the UI
        step is skipped.
        """
        # Cc travels through the *context*, not just the create values:
        # mail_composer_cc_bcc recomputes partner_cc_ids whenever model/res_ids
        # are set, and this module's _compute_partner_cc_bcc_ids override is
        # what makes the recompute stand down — but only for context defaults.
        composer = self.env["mail.compose.message"].with_context(
            mail_create_nosubscribe=True,
            force_email=True,
            default_partner_cc_ids=[(6, 0, cc_partner_ids)],
            default_partner_bcc_ids=[(6, 0, [])],
        ).create({
            "model": target_model,
            "res_ids": repr([target_res_id]),
            "composition_mode": "comment",
            "subject": subject,
            "body": body_html,
            "partner_ids": [(6, 0, partner_ids)],
            "partner_cc_ids": [(6, 0, cc_partner_ids)],
            "attachment_ids": [(6, 0, attachment_ids or [])],
        })
        composer._action_send_mail()
        return composer

    def mobile_reply(self, mode="reply", body="", to=None, cc=None,
                     device=None, attachment_ids=None, client_token=None,
                     body_is_html=False):
        """Send a reply / reply-all / forward without opening the composer.

        Recipients default to what the desktop buttons would compute; ``to``
        and ``cc`` (lists of addresses) override them, which is what makes
        Forward usable at all — it has no default recipient by design.
        """
        self.ensure_one()
        if mode not in ("reply", "reply_all", "forward"):
            raise UserError(_("Mode d'envoi inconnu : %s") % mode)
        if not (body or "").strip():
            raise UserError(_("Le message est vide."))
        # Claimed before anything is sent: a replay of a send that already went
        # out must stop here, not produce a second copy for the correspondent.
        if not self.env["bf.email.mobile.send"]._claim(client_token):
            return {"ok": True, "duplicate": True, "email_id": self.id,
                    "thread_key": self._mobile_thread_key()}
        if device:
            device._check_send_quota()

        if to:
            to_ids = self._mobile_partners_from_addresses(to)
            cc_ids = self._mobile_partners_from_addresses(cc)
        elif mode == "forward":
            raise UserError(_("Un transfert exige au moins un destinataire."))
        elif mode == "reply_all":
            to_ids, cc_ids = self._build_reply_all_recipients()
        else:
            to_ids, cc_ids = self._build_reply_recipients(), []
        if not to_ids:
            raise UserError(_("Aucun destinataire résolu."))
        self._guard_recipient_count(to_ids, cc_ids)

        prefix = "Fwd:" if mode == "forward" else "Re:"
        subject = dedup_subject_prefix(self.subject, force=prefix)
        quote = (self._build_forward_body() if mode == "forward"
                 else self._build_reply_quote_body())
        full_body = self._mobile_compose_body(body, body_is_html) + quote

        target_model, target_res_id = self._composer_target()
        attachments = list(self._mobile_claim_uploads(
            device, attachment_ids, target_model, target_res_id)) if device else []
        if mode == "forward" and not self.mail_message_id and self.raw_rfc822:
            attachments += self._extract_orphan_attachments()
        self._mobile_post(target_model, target_res_id, subject, full_body,
                          to_ids, cc_ids, attachments)

        # Same status transition the desktop composer applies, and for the
        # same reason: only a genuine answer to an inbound message counts.
        if mode != "forward" and self.direction == "in" \
                and self.status in ("new", "read"):
            self.write({"status": "replied"})
        return {"ok": True, "email_id": self.id,
                "thread_key": self._mobile_thread_key()}

    @api.model
    def mobile_compose(self, to, subject, body, cc=None, res_model=None,
                       res_id=None, device=None, attachment_ids=None,
                       client_token=None, body_is_html=False):
        """Send a brand-new email.

        With no source record, the message lands on the first recipient's
        contact card. That is not the fallback ``_composer_target`` retired —
        it dropped orphan threads on the *sender's own* card, where they told
        nobody anything. A message filed on the person it was sent to is where
        Odoo would have put it anyway.
        """
        if not (body or "").strip():
            raise UserError(_("Le message est vide."))
        if not self.env["bf.email.mobile.send"]._claim(client_token):
            return {"ok": True, "duplicate": True}
        if device:
            device._check_send_quota()
        # Counted BEFORE resolving: resolving creates a res.partner per unknown
        # address, so a 5 000-address payload would litter the database even if
        # the send were refused afterwards.
        if len(set(to or []) | set(cc or [])) > MAX_RECIPIENTS:
            raise UserError(
                _("Trop de destinataires (maximum %d depuis le téléphone).")
                % MAX_RECIPIENTS)
        to_ids = self._mobile_partners_from_addresses(to)
        cc_ids = self._mobile_partners_from_addresses(cc)
        if not to_ids:
            raise UserError(_("Aucun destinataire résolu."))
        self._guard_recipient_count(to_ids, cc_ids)

        if res_model and res_id:
            if res_model not in ROUTABLE_MODELS:
                raise UserError(_("Modèle non autorisé : %s") % res_model)
            target = self.env[res_model].browse(int(res_id)).exists()
            if not target:
                raise UserError(_("Enregistrement cible introuvable."))
            # BOTH checks, like bf_email_reroute and bf_email_guess_route: the
            # record rule alone says nothing about model-level rights, and the
            # posting path runs through sudo.
            self.env[res_model].check_access_rights("read")
            target.check_access_rule("read")
            target_model, target_res_id = res_model, int(res_id)
        else:
            target_model, target_res_id = self._compose_home(to_ids[0])

        attachments = self._mobile_claim_uploads(
            device, attachment_ids, target_model, target_res_id) if device else []
        self._mobile_post(target_model, target_res_id,
                          subject or _("(sans objet)"),
                          self._mobile_compose_body(body, body_is_html),
                          to_ids, cc_ids, attachments)
        return {"ok": True}

    @api.model
    def _compose_home(self, recipient_partner_id):
        """Where a brand-new message lives when no record was named.

        The contact card is the right home — it is where Odoo files
        correspondence — but posting there needs **write** on ``res.partner``,
        which a plain internal user does not have. Defaulting to it therefore
        made "compose" work for partner managers and fail with a bare
        AccessError for everybody else; every bench probe ran as admin, so it
        never showed.

        So: the contact card when the user may post there, otherwise a
        ``bf.email`` row of their own. That row is owner-scoped, always
        writable by its owner, and is already the module's home for a
        conversation with nowhere else to go (see ``_composer_target``, which
        made the same choice for orphan replies). It also puts the message in
        the app's "Envoyés" filter straight away.
        """
        Partner = self.env["res.partner"]
        if Partner.check_access_rights("write", raise_exception=False):
            partner = Partner.browse(recipient_partner_id)
            try:
                partner.check_access_rule("write")
                return "res.partner", recipient_partner_id
            except AccessError:
                pass
        recipient = Partner.sudo().browse(recipient_partner_id)
        row = self.sudo().create({
            "subject": _("(nouveau message)"),
            "email_from": self.env.user.email or self.env.user.login,
            "email_to": recipient.email or "",
            "direction": "out",
            "status": "replied",
            "source": "chatter",
            "user_id": self.env.uid,
            "partner_id": recipient_partner_id,
            "date": fields.Datetime.now(),
            "is_handled": True,
        })
        return "bf.email", row.id

    # ------------------------------------------------------------------
    # Odoo-side actions
    # ------------------------------------------------------------------
    @api.model
    def mobile_search_contacts(self, term, limit=20):
        """Address-book lookup for the To/Cc fields.

        Separate from ``mobile_search_records`` because that one answers "which
        record do I file this against" and returns display names; a composer
        needs the address itself. Only contacts that HAVE an email are
        returned — offering a name you cannot send to is worse than offering
        nothing.

        Matches name or address, so both "Acme" and "compta@" find the same
        person. Access rules apply through the ORM: the search sees exactly
        what this user could see in Contacts.
        """
        term = (term or "").strip()
        if len(term) < 2:
            return {"contacts": []}
        # `name` and `email`, not `display_name`: the latter is computed and
        # unstored in Odoo 18, so it can be matched but never ordered by —
        # sorting on it raises inside _order_to_sql.
        partners = self.env["res.partner"].search(
            [
                ("email", "!=", False),
                "|", ("name", "ilike", term), ("email", "ilike", term),
            ],
            limit=min(int(limit or 20), 30),
            order="name",
        )
        return {"contacts": [{
            "id": partner.id,
            "name": partner.display_name or "",
            "email": partner.email or "",
            # A company's people are worth telling apart from the company.
            "company": partner.parent_id.display_name or "",
        } for partner in partners]}

    @api.model
    def mobile_search_records(self, model, term, limit=20):
        """Records the app can offer as a routing target."""
        if model not in ROUTABLE_MODELS:
            raise UserError(_("Modèle non autorisé : %s") % model)
        if model not in self.env:
            raise UserError(_("Modèle absent de cette instance : %s") % model)
        term = (term or "").strip()
        if len(term) < 2:
            return {"records": []}
        records = self.env[model].search([("display_name", "ilike", term)],
                                         limit=min(int(limit or 20), 50))
        return {"records": [{"id": r.id, "name": r.display_name} for r in records]}

    def mobile_route(self, res_model, res_id):
        """Import this email into a record's chatter (the /reroute verb)."""
        self.ensure_one()
        if res_model not in ROUTABLE_MODELS:
            raise UserError(_("Modèle non autorisé : %s") % res_model)
        target = self.env[res_model].browse(int(res_id)).exists()
        if not target:
            raise UserError(_("Enregistrement cible introuvable."))
        # _import_into_chatter posts with elevated rights, so the caller's own
        # rights have to be asserted explicitly — and at BOTH levels, the way
        # the reroute wizards do it. check_access_rule covers the record;
        # check_access_rights covers the model, which a record rule never does.
        self.env[res_model].check_access_rights("write")
        target.check_access_rule("write")
        self._import_into_chatter(target)
        return {
            "ok": True,
            "record": {"model": res_model, "id": target.id,
                       "name": target.display_name},
        }

    def mobile_spawn(self, kind):
        """Create a task / ticket / lead / bill … from this email.

        Delegates to the existing ``action_create_*`` methods, which return an
        ``ir.actions`` dict pointing at the record they just made; the phone
        only needs the reference, so the action is unwrapped here.
        """
        self.ensure_one()
        if kind not in SPAWN_KINDS:
            raise UserError(_("Type de création inconnu : %s") % kind)
        model, method = SPAWN_KINDS[kind]
        if model not in self.env:
            raise UserError(_("Application absente de cette instance : %s") % model)
        action = getattr(self, method)()
        res_id = (action or {}).get("res_id")
        if not res_id:
            # The desktop verbs open a pre-filled *form* for some kinds rather
            # than committing a record. Say so instead of reporting a success
            # the user will not find anywhere.
            raise UserError(
                _("Cette création doit être terminée dans Odoo — l'action "
                  "ouvre un formulaire pré-rempli plutôt que d'enregistrer."))
        record = self.env[action["res_model"]].browse(res_id)
        return {
            "ok": True,
            "record": {"model": action["res_model"], "id": res_id,
                       "name": record.display_name},
        }
