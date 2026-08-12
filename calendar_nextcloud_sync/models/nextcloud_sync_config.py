# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).

import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree

import pytz
import requests
from dateutil import parser as dt_parser

from odoo import api, fields, models
from odoo.addons.bf_timezone.tools import tz as tz_tools

_logger = logging.getLogger(__name__)

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:
    Fernet = None
    InvalidToken = Exception
    _logger.warning(
        "cryptography package not installed. "
        "Nextcloud credential encryption will not be available."
    )


class NextcloudCalendarSyncConfig(models.Model):
    """Configuration for Nextcloud calendar synchronization."""

    _name = "nextcloud.calendar.sync.config"
    _description = "Nextcloud Calendar Sync Configuration"
    _order = "sequence, name"

    name = fields.Char(
        string="Calendar Name",
        required=True,
        help="Display name for this calendar configuration",
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    # Backend selection — determines which protocol/API this config uses.
    # 'nextcloud' keeps the original CalDAV+n8n path; 'google' uses the
    # Google Calendar API v3 via calendar.google.backend.
    backend_type = fields.Selection(
        [
            ("nextcloud", "Nextcloud CalDAV"),
            ("google", "Google Calendar"),
        ],
        string="Backend",
        required=True,
        default="nextcloud",
        help="Which remote calendar system this configuration syncs with.",
    )

    # Nextcloud CalDAV configuration (required when backend_type='nextcloud')
    nextcloud_base_url = fields.Char(
        string="Nextcloud URL",
        help="Base URL of Nextcloud instance (e.g., https://nextcloud.example.com)",
    )
    caldav_path = fields.Char(
        string="CalDAV Path",
        help="Path to the calendar (e.g., /remote.php/dav/calendars/username/personal/)",
    )
    nextcloud_user = fields.Char(
        string="Nextcloud User",
        help="Username of the calendar owner in Nextcloud",
    )
    nextcloud_app_password = fields.Char(
        string="App Password",
        compute="_compute_app_password",
        inverse="_inverse_app_password",
        store=False,
        help="Nextcloud app password for CalDAV authentication",
    )
    nextcloud_app_password_encrypted = fields.Char(
        string="App Password (encrypted)",
    )
    calendar_color = fields.Char(
        string="Calendar Color",
        help="Color for visual identification (hex code)",
    )
    odoo_color = fields.Integer(
        string="Odoo Color",
        default=0,
        help="Color index for events in Odoo calendar views (0-11)",
    )
    calendar_owner_id = fields.Many2one(
        "res.users",
        string="Calendar Owner (Odoo)",
        help="Odoo user who owns this calendar. Synced events will include "
        "this user as attendee so they appear in their calendar view. "
        "For Google backend, the OAuth token of this user authenticates "
        "all API calls. For Nextcloud, falls back to matching the NC "
        "username if not set.",
    )

    # Google Calendar configuration (used when backend_type='google')
    google_calendar_id = fields.Char(
        string="Google Calendar ID",
        help="Google calendar identifier (e.g. 'primary', 'user@example.com', "
        "or a secondary calendar's UUID@group.calendar.google.com).",
    )

    # Sync configuration
    sync_direction = fields.Selection(
        [
            ("both", "Bidirectional"),
            ("nc_to_odoo", "Nextcloud → Odoo only"),
            ("odoo_to_nc", "Odoo → Nextcloud only"),
        ],
        string="Sync Direction",
        default="both",
        required=True,
    )

    # n8n webhook configuration
    webhook_url = fields.Char(
        string="n8n Webhook URL",
        default=lambda self: self.env["ir.config_parameter"]
        .sudo()
        .get_param("calendar_nextcloud_sync.webhook_url", ""),
        help="URL of the n8n webhook for Odoo → Nextcloud sync",
    )
    webhook_secret = fields.Char(
        string="Webhook Secret",
        compute="_compute_webhook_secret",
        inverse="_inverse_webhook_secret",
        store=False,
        help="Secret token for webhook authentication (min 32 chars)",
    )
    webhook_secret_encrypted = fields.Char(
        string="Webhook Secret (encrypted)",
    )

    # Statistics
    event_count = fields.Integer(
        string="Synced Events",
        compute="_compute_event_count",
    )
    last_sync = fields.Datetime(
        string="Last Sync",
        readonly=True,
    )
    last_sync_status = fields.Selection(
        [
            ("success", "Success"),
            ("error", "Error"),
            ("partial", "Partial"),
        ],
        string="Last Sync Status",
        readonly=True,
    )
    last_sync_message = fields.Text(
        string="Last Sync Message",
        readonly=True,
    )
    caldav_sync_token = fields.Char(
        string="CalDAV Sync Token",
        help="Opaque token from last sync for incremental pull (RFC 6578)",
        copy=False,
    )

    @api.constrains("backend_type", "nextcloud_base_url", "caldav_path",
                    "nextcloud_user", "google_calendar_id", "calendar_owner_id")
    def _check_backend_required_fields(self):
        """Enforce backend-specific required fields at save time."""
        for record in self:
            if record.backend_type == "nextcloud":
                missing = [
                    f for f, v in (
                        ("Nextcloud URL", record.nextcloud_base_url),
                        ("CalDAV Path", record.caldav_path),
                        ("Nextcloud User", record.nextcloud_user),
                    ) if not v
                ]
                if missing:
                    from odoo.exceptions import ValidationError
                    raise ValidationError(
                        f"Nextcloud backend requires: {', '.join(missing)}"
                    )
            elif record.backend_type == "google":
                if not record.google_calendar_id:
                    from odoo.exceptions import ValidationError
                    raise ValidationError(
                        "Google backend requires a Google Calendar ID "
                        "(use 'primary' for the main calendar)."
                    )
                if not record.calendar_owner_id:
                    from odoo.exceptions import ValidationError
                    raise ValidationError(
                        "Google backend requires a Calendar Owner (the Odoo "
                        "user whose Google OAuth token will be used)."
                    )

    @api.model_create_multi
    def create(self, vals_list):
        """Apply default webhook secret from settings if not provided."""
        ICP = self.env["ir.config_parameter"].sudo()
        default_secret = ICP.get_param(
            "calendar_nextcloud_sync.webhook_secret", ""
        )
        if default_secret:
            for vals in vals_list:
                if not vals.get("webhook_secret_encrypted") and not vals.get(
                    "webhook_secret"
                ):
                    vals["webhook_secret"] = default_secret
        return super().create(vals_list)

    @api.model
    def _get_event_uid_domain(self):
        """Return the domain suffix used when generating new event UIDs.

        Configurable via system parameter
        `calendar_nextcloud_sync.event_uid_domain`. Falls back to the
        hostname from `web.base.url`. Avoids hardcoding a tenant domain
        so the module is portable across BF, PME, and future tenants.
        """
        ICP = self.env["ir.config_parameter"].sudo()
        explicit = ICP.get_param("calendar_nextcloud_sync.event_uid_domain")
        if explicit:
            return explicit
        base = ICP.get_param("web.base.url", "")
        if base:
            from urllib.parse import urlparse
            host = urlparse(base).hostname or ""
            if host:
                return "odoo." + host
        return "odoo.local"

    # === Encryption Methods ===

    def _get_encryption_key(self):
        """Get or generate encryption key from system parameters."""
        if not Fernet:
            return None
        ICP = self.env["ir.config_parameter"].sudo()
        key = ICP.get_param("calendar_nextcloud_sync.encryption_key")
        if not key:
            key = Fernet.generate_key().decode()
            ICP.set_param("calendar_nextcloud_sync.encryption_key", key)
        return key.encode()

    def _encrypt_value(self, value):
        """Encrypt a string value using Fernet symmetric encryption."""
        if not value:
            return False
        key = self._get_encryption_key()
        if not key:
            _logger.warning("Encryption key not available, storing value as-is")
            return value
        try:
            f = Fernet(key)
            return f.encrypt(value.encode()).decode()
        except Exception as e:
            _logger.error("Encryption failed: %s", e)
            return value

    def _decrypt_value(self, encrypted_value):
        """Decrypt a Fernet-encrypted value. Falls back to returning as-is.

        Fallback rationale: legacy DB data (pre-encryption migration) may
        contain plaintext values. We detect this by InvalidToken and return
        as-is so migrations don't break. WARNING is logged so admins can
        notice if a previously-encrypted value suddenly becomes unreadable
        (e.g., encryption key rotation went wrong).
        """
        if not encrypted_value:
            return False
        key = self._get_encryption_key()
        if not key:
            return encrypted_value
        try:
            f = Fernet(key)
            return f.decrypt(encrypted_value.encode()).decode()
        except InvalidToken:
            # Distinguish "looks like Fernet but key mismatch" from
            # "is plaintext legacy value". Fernet tokens start with 'gAAAAAB'
            # (base64 of version byte + timestamp). If it looks encrypted but
            # we can't decrypt, key is wrong — loud warning.
            looks_like_fernet = (
                isinstance(encrypted_value, str)
                and encrypted_value.startswith("gAAAAAB")
            )
            if looks_like_fernet:
                _logger.warning(
                    "Encryption key mismatch: stored value appears Fernet-"
                    "encrypted but cannot be decrypted with the current key. "
                    "Check calendar_nextcloud_sync.encryption_key."
                )
            else:
                _logger.debug(
                    "Value appears to be unencrypted legacy, returning as-is"
                )
            return encrypted_value
        except Exception as e:
            _logger.error("Decryption failed: %s", e)
            return encrypted_value

    # === Computed Fields ===

    def _compute_app_password(self):
        """Decrypt app password for display."""
        for record in self:
            record.nextcloud_app_password = record._decrypt_value(
                record.nextcloud_app_password_encrypted
            )

    def _inverse_app_password(self):
        """Encrypt app password on write."""
        for record in self:
            if record.nextcloud_app_password:
                record.nextcloud_app_password_encrypted = record._encrypt_value(
                    record.nextcloud_app_password
                )
            else:
                record.nextcloud_app_password_encrypted = False

    def _compute_webhook_secret(self):
        """Decrypt webhook secret for display."""
        for record in self:
            record.webhook_secret = record._decrypt_value(
                record.webhook_secret_encrypted
            )

    def _inverse_webhook_secret(self):
        """Encrypt webhook secret on write."""
        for record in self:
            if record.webhook_secret:
                record.webhook_secret_encrypted = record._encrypt_value(
                    record.webhook_secret
                )
            else:
                record.webhook_secret_encrypted = False

    @api.depends("name")
    def _compute_event_count(self):
        """Count calendar events linked to this configuration."""
        for record in self:
            record.event_count = self.env["calendar.event"].search_count(
                [("x_nc_calendar_id", "=", record.id)]
            )

    @property
    def caldav_url(self):
        """Return full CalDAV URL for this calendar."""
        base = self.nextcloud_base_url.rstrip("/")
        path = self.caldav_path
        if not path.startswith("/"):
            path = "/" + path
        return base + path

    def action_view_events(self):
        """Open view of calendar events synced from this configuration."""
        self.ensure_one()
        cal_view = self.env.ref(
            "calendar_nextcloud_sync.view_calendar_event_calendar_nc_sync",
            raise_if_not_found=False,
        )
        cal_view_id = cal_view.id if cal_view else False
        return {
            "type": "ir.actions.act_window",
            "name": f"Events: {self.name}",
            "res_model": "calendar.event",
            "views": [
                [cal_view_id, "calendar"],
                [False, "list"],
                [False, "form"],
            ],
            "domain": [("x_nc_calendar_id", "=", self.id)],
            "context": {"default_x_nc_calendar_id": self.id},
        }

    def action_test_connection(self):
        """Test connection to Nextcloud CalDAV endpoint via PROPFIND."""
        self.ensure_one()

        password = self.nextcloud_app_password
        if not password:
            self.write(
                {
                    "last_sync": fields.Datetime.now(),
                    "last_sync_status": "error",
                    "last_sync_message": "No app password configured.",
                }
            )
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Connection Test",
                    "message": "No app password configured. "
                    "Enter the Nextcloud app password first.",
                    "type": "warning",
                    "sticky": False,
                },
            }

        url = self.caldav_url
        propfind_body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<d:propfind xmlns:d="DAV:" xmlns:cs="http://calendarserver.org/ns/">'
            "<d:prop>"
            "<d:displayname/>"
            "<d:resourcetype/>"
            "</d:prop>"
            "</d:propfind>"
        )

        try:
            response = requests.request(
                "PROPFIND",
                url,
                data=propfind_body,
                headers={
                    "Content-Type": "application/xml",
                    "Depth": "0",
                },
                auth=(self.nextcloud_user, password),
                timeout=15,
            )
        except requests.ConnectionError:
            msg = f"Cannot reach {self.nextcloud_base_url} — check URL and network."
            self.write(
                {
                    "last_sync": fields.Datetime.now(),
                    "last_sync_status": "error",
                    "last_sync_message": msg,
                }
            )
            return self._test_notification(msg, "danger")
        except requests.Timeout:
            msg = f"Connection to {self.nextcloud_base_url} timed out (15s)."
            self.write(
                {
                    "last_sync": fields.Datetime.now(),
                    "last_sync_status": "error",
                    "last_sync_message": msg,
                }
            )
            return self._test_notification(msg, "danger")
        except requests.RequestException as e:
            msg = f"Request error: {e}"
            self.write(
                {
                    "last_sync": fields.Datetime.now(),
                    "last_sync_status": "error",
                    "last_sync_message": msg,
                }
            )
            return self._test_notification(msg, "danger")

        if response.status_code in (401, 403):
            msg = (
                f"Authentication failed (HTTP {response.status_code}). "
                "Check username and app password."
            )
            self.write(
                {
                    "last_sync": fields.Datetime.now(),
                    "last_sync_status": "error",
                    "last_sync_message": msg,
                }
            )
            return self._test_notification(msg, "danger")

        if response.status_code == 404:
            msg = f"Calendar not found at {self.caldav_path}. Check the CalDAV path."
            self.write(
                {
                    "last_sync": fields.Datetime.now(),
                    "last_sync_status": "error",
                    "last_sync_message": msg,
                }
            )
            return self._test_notification(msg, "danger")

        if response.status_code != 207:
            msg = f"Unexpected response: HTTP {response.status_code}"
            self.write(
                {
                    "last_sync": fields.Datetime.now(),
                    "last_sync_status": "error",
                    "last_sync_message": msg,
                }
            )
            return self._test_notification(msg, "danger")

        # Parse 207 Multi-Status XML
        display_name = self._parse_propfind_displayname(response.text)
        msg = (
            f"Connected successfully! Calendar: {display_name}"
            if display_name
            else "Connected successfully! (could not parse calendar name)"
        )
        self.write(
            {
                "last_sync": fields.Datetime.now(),
                "last_sync_status": "success",
                "last_sync_message": msg,
            }
        )
        return self._test_notification(msg, "success")

    def _parse_propfind_displayname(self, xml_text):
        """Extract displayname from PROPFIND 207 Multi-Status response."""
        try:
            root = ElementTree.fromstring(xml_text)
            # Namespaced search — DAV: namespace
            ns = {"d": "DAV:"}
            dn = root.find(".//d:displayname", ns)
            if dn is not None and dn.text:
                return dn.text
        except ElementTree.ParseError:
            _logger.warning("Failed to parse PROPFIND response XML")
        return None

    def _test_notification(self, message, notif_type):
        """Return a display_notification action."""
        type_map = {"success": "success", "danger": "danger", "warning": "warning"}
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Connection Test",
                "message": message,
                "type": type_map.get(notif_type, "info"),
                "sticky": notif_type == "danger",
            },
        }

    def update_sync_status(self, status, message=None):
        """Update sync status (called from webhook handler)."""
        self.write(
            {
                "last_sync": fields.Datetime.now(),
                "last_sync_status": status,
                "last_sync_message": message or "",
            }
        )

    # === Puller quarantine ===
    # Stored in ir.config_parameter rather than in a model field: this avoids a
    # schema migration, hence a module upgrade and production downtime.
    # Format: {"<google_event_id>": <consecutive_failure_count>}.

    def _quarantine_param_key(self):
        self.ensure_one()
        return "calendar_nextcloud_sync.pull_quarantine.%s" % self.id

    def _get_pull_quarantine(self):
        """Return {uid: consecutive_failures} for this config."""
        self.ensure_one()
        raw = self.env["ir.config_parameter"].sudo().get_param(
            self._quarantine_param_key(), "{}"
        )
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except (ValueError, TypeError):
            _logger.warning(
                "Unreadable quarantine for config %s, resetting.", self.name
            )
            return {}

    def _set_pull_quarantine(self, data):
        """Persist the quarantine. Size is bounded so the system parameter
        cannot grow without limit."""
        self.ensure_one()
        data = {k: v for k, v in (data or {}).items() if v}
        if len(data) > 200:
            data = dict(
                sorted(data.items(), key=lambda kv: kv[1], reverse=True)[:200]
            )
        self.env["ir.config_parameter"].sudo().set_param(
            self._quarantine_param_key(), json.dumps(data)
        )

    def action_clear_pull_quarantine(self):
        """Empty the quarantine: skipped events are retried on the next pass.
        Use once the cause of the failures has been fixed."""
        for config in self:
            self.env["ir.config_parameter"].sudo().set_param(
                config._quarantine_param_key(), "{}"
            )
            _logger.info("Quarantine cleared for config %s.", config.name)
        return True

    # === Unified dispatch ===

    def action_pull(self):
        """Dispatch pull to the backend's implementation.

        For Google configs: only the calendar_owner_id or an admin
        (base.group_system) can trigger a pull, to prevent a regular
        user from forcing syncs on another user's Google account.
        """
        self.ensure_one()
        if self.backend_type == "google":
            env_user = self.env.user
            is_owner = self.calendar_owner_id == env_user
            is_admin = env_user.has_group("base.group_system")
            if not (is_owner or is_admin or self.env.su):
                from odoo.exceptions import AccessError
                raise AccessError(
                    "Only the calendar owner (%s) or an administrator "
                    "can trigger a Google Calendar pull." % (
                        self.calendar_owner_id.name or "unassigned"
                    )
                )
            return self.env["calendar.google.backend"].pull_events(self)
        # Default: Nextcloud CalDAV path
        if self.caldav_sync_token:
            return self._pull_incremental()
        return self.action_pull_from_nextcloud()

    @api.model
    def _cron_sync_all(self):
        """Cron job: pull remote → Odoo, retry failed pushes.

        Dispatches per config.backend_type:
        - 'nextcloud' → CalDAV REPORT (+ sync-token RFC 6578) + n8n webhook retry
        - 'google'    → Google Calendar API events.list (+ syncToken) + direct push retry
        """
        # --- Pull phase ---
        nc_pull = self.search([
            ("active", "=", True),
            ("backend_type", "=", "nextcloud"),
            ("sync_direction", "in", ("both", "nc_to_odoo")),
            ("nextcloud_app_password_encrypted", "!=", False),
        ])
        for config in nc_pull:
            try:
                if config.caldav_sync_token:
                    config._pull_incremental()
                else:
                    config.action_pull_from_nextcloud()
            except Exception:
                _logger.exception(
                    "Cron NC pull failed for config %s (id=%s)",
                    config.name, config.id,
                )
                config.update_sync_status("error", "Cron pull error: see logs")

        google_pull = self.search([
            ("active", "=", True),
            ("backend_type", "=", "google"),
            ("sync_direction", "in", ("both", "nc_to_odoo")),
            ("calendar_owner_id", "!=", False),
        ])
        GoogleBackend = self.env["calendar.google.backend"]
        for config in google_pull:
            try:
                GoogleBackend.pull_events(config)
            except Exception:
                _logger.exception(
                    "Cron Google pull failed for config %s (id=%s)",
                    config.name, config.id,
                )
                config.update_sync_status(
                    "error", "Cron Google pull error: see logs"
                )

        # --- Push retry phase ---
        nc_push = self.search([
            ("active", "=", True),
            ("backend_type", "=", "nextcloud"),
            ("sync_direction", "in", ("both", "odoo_to_nc")),
            ("webhook_url", "!=", False),
        ])
        for config in nc_push:
            try:
                config._retry_failed_pushes()
            except Exception:
                _logger.exception(
                    "Cron NC push retry failed for config %s (id=%s)",
                    config.name, config.id,
                )

        google_push = self.search([
            ("active", "=", True),
            ("backend_type", "=", "google"),
            ("sync_direction", "in", ("both", "odoo_to_nc")),
            ("calendar_owner_id", "!=", False),
        ])
        for config in google_push:
            try:
                config._retry_failed_google_pushes()
            except Exception:
                _logger.exception(
                    "Cron Google push retry failed for config %s (id=%s)",
                    config.name, config.id,
                )

    def _retry_failed_pushes(self):
        """Retry pushing Odoo-created events that have no x_caldav_href.

        Silently skips if nothing to push. This is the failsafe for
        webhook failures during create/write — the cron picks them up.
        """
        self.ensure_one()
        CalendarEvent = self.env["calendar.event"]
        pending = CalendarEvent.search([
            ("x_nc_calendar_id", "=", self.id),
            ("x_sync_source", "!=", "nextcloud"),
            ("recurrency", "=", False),
            "|",
            ("x_caldav_href", "=", False),
            ("x_caldav_href", "=", ""),
        ])
        if not pending:
            return

        pushed = 0
        errors = 0
        uid_domain = self._get_event_uid_domain()
        for event in pending:
            vals = {}
            if not event.x_nc_uid:
                vals["x_nc_uid"] = f"{uuid.uuid4()}@{uid_domain}"
            if not event.x_sync_source:
                vals["x_sync_source"] = "odoo"
            if vals:
                event.with_context(
                    skip_nc_sync=True, no_mail_to_attendees=True
                ).write(vals)
            try:
                event._trigger_sync_webhook("create")
                pushed += 1
            except Exception as e:
                _logger.error(
                    "Push retry failed for event %s: %s", event.id, e
                )
                errors += 1

        if pushed or errors:
            _logger.info(
                "Push retry for %s: %d pushed, %d errors",
                self.name, pushed, errors,
            )

    @api.model
    def _cron_full_resync(self):
        """Cron job: periodic full resync as a consistency safety net.

        Clears sync tokens and performs a full pull for all active configs
        (Nextcloud and Google), ensuring no drift between remote and Odoo.
        """
        configs = self.search([
            ("active", "=", True),
            ("sync_direction", "in", ("both", "nc_to_odoo")),
        ])
        GoogleBackend = self.env["calendar.google.backend"]
        for config in configs:
            try:
                config.caldav_sync_token = False
                if config.backend_type == "google":
                    if config.calendar_owner_id:
                        GoogleBackend.pull_events(config)
                elif config.nextcloud_app_password_encrypted:
                    config.action_pull_from_nextcloud()
            except Exception:
                _logger.exception(
                    "Cron full resync failed for config %s (id=%s)",
                    config.name, config.id,
                )
                config.update_sync_status("error", "Full resync error: see logs")

    def _retry_failed_google_pushes(self):
        """Retry pushing Odoo-created events that have no Google event ID.

        Mirrors _retry_failed_pushes for the Google backend: if an event
        was created in Odoo but couldn't be pushed (network glitch,
        Google down, expired token), the cron picks it up later.
        """
        self.ensure_one()
        CalendarEvent = self.env["calendar.event"]
        GoogleBackend = self.env["calendar.google.backend"]
        pending = CalendarEvent.search([
            ("x_nc_calendar_id", "=", self.id),
            ("x_sync_source", "!=", "google"),
            ("recurrency", "=", False),
            "|",
            ("x_nc_uid", "=", False),
            ("x_nc_uid", "=", ""),
        ])
        if not pending:
            return
        pushed = 0
        errors = 0
        for event in pending:
            try:
                gid, etag = GoogleBackend.push_create(self, event)
                event.with_context(
                    skip_nc_sync=True,
                    skip_google_sync=True,
                    no_mail_to_attendees=True,
                ).write({
                    "x_nc_uid": gid,
                    "x_caldav_etag": etag,
                    "x_sync_source": "odoo",
                    "x_last_sync": fields.Datetime.now(),
                })
                pushed += 1
            except Exception as e:
                _logger.error(
                    "Google push retry failed for event %s: %s", event.id, e,
                )
                errors += 1
        if pushed or errors:
            _logger.info(
                "Google push retry for %s: %d pushed, %d errors",
                self.name, pushed, errors,
            )

    # === CalDAV Full Pull Sync ===

    def action_pull_from_nextcloud(self):
        """Pull all events from Nextcloud via CalDAV REPORT and upsert."""
        self.ensure_one()

        password = self.nextcloud_app_password
        if not password:
            return self._sync_notification(
                "No app password configured.", "warning"
            )

        if self.sync_direction == "odoo_to_nc":
            return self._sync_notification(
                "Sync direction is Odoo → Nextcloud only. "
                "Cannot pull from Nextcloud.",
                "warning",
            )

        # CalDAV REPORT to fetch all VEVENTs
        report_body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<c:calendar-query xmlns:d="DAV:" '
            'xmlns:c="urn:ietf:params:xml:ns:caldav">'
            "<d:prop>"
            "<d:getetag/>"
            "<c:calendar-data/>"
            "</d:prop>"
            "<c:filter>"
            '<c:comp-filter name="VCALENDAR">'
            '<c:comp-filter name="VEVENT"/>'
            "</c:comp-filter>"
            "</c:filter>"
            "</c:calendar-query>"
        )

        url = self.caldav_url
        try:
            response = requests.request(
                "REPORT",
                url,
                data=report_body,
                headers={
                    "Content-Type": "application/xml",
                    "Depth": "1",
                },
                auth=(self.nextcloud_user, password),
                timeout=60,
            )
        except requests.ConnectionError:
            msg = f"Cannot reach {self.nextcloud_base_url} — check URL and network."
            self.update_sync_status("error", msg)
            return self._sync_notification(msg, "danger")
        except requests.Timeout:
            msg = f"Connection to {self.nextcloud_base_url} timed out (60s)."
            self.update_sync_status("error", msg)
            return self._sync_notification(msg, "danger")
        except requests.RequestException as e:
            msg = f"Request error: {e}"
            self.update_sync_status("error", msg)
            return self._sync_notification(msg, "danger")

        if response.status_code in (401, 403):
            msg = (
                f"Authentication failed (HTTP {response.status_code}). "
                "Check username and app password."
            )
            self.update_sync_status("error", msg)
            return self._sync_notification(msg, "danger")

        if response.status_code == 404:
            msg = f"Calendar not found at {self.caldav_path}. Check the CalDAV path."
            self.update_sync_status("error", msg)
            return self._sync_notification(msg, "danger")

        if response.status_code != 207:
            msg = f"Unexpected response: HTTP {response.status_code}"
            self.update_sync_status("error", msg)
            return self._sync_notification(msg, "danger")

        # Parse 207 Multi-Status response
        events_data = self._parse_report_response(response.text)

        # Upsert events
        created = 0
        updated = 0
        skipped = 0
        errors = 0
        nc_uids_seen = set()
        CalendarEvent = self.env["calendar.event"]

        # Pre-fetch existing etags and colors for this config
        existing_events = CalendarEvent.search([
            ("x_nc_calendar_id", "=", self.id),
            ("x_nc_uid", "!=", False),
        ])
        existing_etags = {
            ev.x_nc_uid: ev.x_caldav_etag for ev in existing_events
        }
        existing_by_uid = {ev.x_nc_uid: ev for ev in existing_events}
        target_color = self.odoo_color or 0

        for ev_data in events_data:
            uid = ev_data.get("uid")
            if not uid:
                errors += 1
                continue
            nc_uids_seen.add(uid)

            # ETag-based skip: unchanged events
            new_etag = ev_data.get("etag")
            if new_etag and uid in existing_etags:
                if existing_etags[uid] == new_etag:
                    # Re-process if ICS has RRULE but Odoo event is not recurring
                    ev = existing_by_uid.get(uid)
                    if ev and ev_data.get("rrule") and not ev.recurrency:
                        pass  # fall through to create_from_nextcloud
                    else:
                        # Fix color if it doesn't match the config
                        if ev and ev.color != target_color:
                            ev.with_context(
                                skip_nc_sync=True, no_mail_to_attendees=True
                            ).write({"color": target_color})
                        skipped += 1
                        continue

            try:
                with self.env.cr.savepoint():
                    result = CalendarEvent.create_from_nextcloud(ev_data, self.id)
                    # Flush inside savepoint so deferred recomputes
                    # (e.g. resource_booking state) raise here, not in
                    # the cron's final flush_all() where they'd crash
                    # the entire job.
                    self.env.flush_all()
                if result.get("error"):
                    _logger.warning(
                        "Sync error for UID %s: %s", uid, result["error"]
                    )
                    errors += 1
                elif result.get("action") == "created":
                    created += 1
                elif result.get("action") == "updated":
                    updated += 1
            except Exception as e:
                _logger.error("Error syncing UID %s: %s", uid, e)
                errors += 1

        # Detect deletions: events in Odoo but not in Nextcloud
        # Only consider base events (no recurrence_id) — generated
        # recurrence instances should not be treated as orphans.
        deleted = 0
        if nc_uids_seen:
            orphans = CalendarEvent.search([
                ("x_nc_calendar_id", "=", self.id),
                ("x_nc_uid", "!=", False),
                ("x_nc_uid", "not in", list(nc_uids_seen)),
                ("recurrence_id", "=", False),
            ])
            for orphan in orphans:
                # Guard: orphan may have been deleted as part of a
                # previous recurrence deletion in this same loop
                if not orphan.exists():
                    continue
                try:
                    with self.env.cr.savepoint():
                        CalendarEvent.delete_from_nextcloud(
                            orphan.x_nc_uid, self.id
                        )
                        # Flush inside savepoint so deferred recomputes
                        # (e.g. resource_booking state) raise here, not
                        # in the cron's final flush_all().
                        self.env.flush_all()
                    deleted += 1
                except Exception as e:
                    _logger.error(
                        "Error deleting orphan UID %s: %s", orphan.x_nc_uid, e
                    )
                    errors += 1

        # Build summary
        parts = []
        if created:
            parts.append(f"{created} created")
        if updated:
            parts.append(f"{updated} updated")
        if deleted:
            parts.append(f"{deleted} deleted")
        if skipped:
            parts.append(f"{skipped} unchanged")
        if errors:
            parts.append(f"{errors} errors")
        summary = ", ".join(parts) if parts else "No events found"
        msg = f"Sync complete: {summary}"

        status = "error" if errors and not (created or updated) else (
            "partial" if errors else "success"
        )
        self.update_sync_status(status, msg)

        # After successful full pull, fetch and store sync-token for
        # subsequent incremental pulls (RFC 6578)
        if status in ("success", "partial"):
            self._store_sync_token()

        return self._sync_notification(msg, "success" if not errors else "warning")

    def action_push_to_nextcloud(self):
        """Push pending Odoo events to Nextcloud via n8n webhook.

        Finds events linked to this config that were created in Odoo
        but never successfully pushed (no x_caldav_href).
        """
        self.ensure_one()

        if self.sync_direction == "nc_to_odoo":
            return self._sync_notification(
                "Sync direction is Nextcloud \u2192 Odoo only. "
                "Cannot push to Nextcloud.",
                "warning",
            )

        if not self.webhook_url:
            return self._sync_notification(
                "No webhook URL configured.", "warning"
            )

        # Find events linked to this config that haven't been pushed
        CalendarEvent = self.env["calendar.event"]
        pending = CalendarEvent.search([
            ("x_nc_calendar_id", "=", self.id),
            ("x_sync_source", "!=", "nextcloud"),
            ("recurrency", "=", False),
            "|",
            ("x_caldav_href", "=", False),
            ("x_caldav_href", "=", ""),
        ])

        if not pending:
            return self._sync_notification(
                "No pending events to push.", "info"
            )

        pushed = 0
        errors = 0
        uid_domain = self._get_event_uid_domain()
        for event in pending:
            # Ensure sync fields are populated
            vals = {}
            if not event.x_nc_uid:
                vals["x_nc_uid"] = f"{uuid.uuid4()}@{uid_domain}"
            if not event.x_sync_source:
                vals["x_sync_source"] = "odoo"
            if vals:
                event.with_context(
                    skip_nc_sync=True, no_mail_to_attendees=True
                ).write(vals)

            try:
                event._trigger_sync_webhook("create")
                pushed += 1
            except Exception as e:
                _logger.error(
                    "Failed to push event %s: %s", event.id, e
                )
                errors += 1

        parts = []
        if pushed:
            parts.append(f"{pushed} pushed")
        if errors:
            parts.append(f"{errors} errors")
        summary = ", ".join(parts)
        msg = f"Push complete: {summary}"

        status = "error" if errors and not pushed else (
            "partial" if errors else "success"
        )
        self.update_sync_status(status, msg)
        return self._sync_notification(msg, "success" if not errors else "warning")

    def action_force_full_sync(self):
        """Clear sync token and perform a full pull from Nextcloud."""
        self.ensure_one()
        self.caldav_sync_token = False
        return self.action_pull_from_nextcloud()

    # === Incremental Sync (RFC 6578) ===

    def _store_sync_token(self):
        """Fetch current sync-token via PROPFIND and store it.

        Sends a Depth:0 PROPFIND requesting <d:sync-token/>.
        If the server doesn't support it, leaves the field False.
        """
        self.ensure_one()
        password = self.nextcloud_app_password
        if not password:
            return

        propfind_body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<d:propfind xmlns:d="DAV:">'
            "<d:prop>"
            "<d:sync-token/>"
            "</d:prop>"
            "</d:propfind>"
        )

        try:
            response = requests.request(
                "PROPFIND",
                self.caldav_url,
                data=propfind_body,
                headers={
                    "Content-Type": "application/xml",
                    "Depth": "0",
                },
                auth=(self.nextcloud_user, password),
                timeout=15,
            )
        except requests.RequestException as e:
            _logger.warning(
                "Failed to fetch sync-token for %s: %s", self.name, e
            )
            return

        if response.status_code != 207:
            _logger.debug(
                "PROPFIND for sync-token returned HTTP %s for %s",
                response.status_code, self.name,
            )
            return

        try:
            ns = {"d": "DAV:"}
            root = ElementTree.fromstring(response.text)
            token_el = root.find(".//d:sync-token", ns)
            if token_el is not None and token_el.text:
                self.caldav_sync_token = token_el.text.strip()
                _logger.info(
                    "Stored sync-token for %s: %s",
                    self.name, self.caldav_sync_token[:60],
                )
            else:
                _logger.debug(
                    "Server did not return sync-token for %s", self.name
                )
        except ElementTree.ParseError:
            _logger.warning(
                "Failed to parse PROPFIND sync-token response for %s",
                self.name,
            )

    def _pull_incremental(self):
        """Incremental pull using WebDAV sync-collection (RFC 6578).

        Sends a REPORT with the stored sync-token to get only changes
        since the last sync. Falls back to full pull on 412 (token expired).
        """
        self.ensure_one()

        password = self.nextcloud_app_password
        if not password:
            return

        report_body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<d:sync-collection xmlns:d="DAV:" '
            'xmlns:c="urn:ietf:params:xml:ns:caldav">'
            "<d:sync-token>{token}</d:sync-token>"
            "<d:sync-level>1</d:sync-level>"
            "<d:prop>"
            "<d:getetag/>"
            "<c:calendar-data/>"
            "</d:prop>"
            "</d:sync-collection>"
        ).format(token=self.caldav_sync_token)

        url = self.caldav_url
        try:
            response = requests.request(
                "REPORT",
                url,
                data=report_body,
                headers={
                    "Content-Type": "application/xml",
                    "Depth": "1",
                },
                auth=(self.nextcloud_user, password),
                timeout=60,
            )
        except requests.ConnectionError:
            msg = f"Cannot reach {self.nextcloud_base_url} — check URL and network."
            self.update_sync_status("error", msg)
            return
        except requests.Timeout:
            msg = f"Connection to {self.nextcloud_base_url} timed out (60s)."
            self.update_sync_status("error", msg)
            return
        except requests.RequestException as e:
            msg = f"Request error: {e}"
            self.update_sync_status("error", msg)
            return

        # 412 Precondition Failed = token expired → full pull
        if response.status_code == 412:
            _logger.info(
                "Sync token expired for %s, falling back to full pull",
                self.name,
            )
            self.caldav_sync_token = False
            self.action_pull_from_nextcloud()
            return

        if response.status_code in (401, 403):
            msg = (
                f"Authentication failed (HTTP {response.status_code}). "
                "Check username and app password."
            )
            self.update_sync_status("error", msg)
            return

        if response.status_code == 404:
            msg = f"Calendar not found at {self.caldav_path}. Check the CalDAV path."
            self.update_sync_status("error", msg)
            return

        if response.status_code != 207:
            msg = f"Unexpected response: HTTP {response.status_code}"
            self.update_sync_status("error", msg)
            return

        # Parse sync-collection response
        result = self._parse_sync_collection_response(response.text)

        # Process changes
        created = 0
        updated = 0
        deleted = 0
        errors = 0
        CalendarEvent = self.env["calendar.event"]

        for ev_data in result["changed"]:
            uid = ev_data.get("uid")
            if not uid:
                errors += 1
                continue
            try:
                with self.env.cr.savepoint():
                    res = CalendarEvent.create_from_nextcloud(ev_data, self.id)
                    self.env.flush_all()
                if res.get("error"):
                    _logger.warning(
                        "Incremental sync error for UID %s: %s",
                        uid, res["error"],
                    )
                    errors += 1
                elif res.get("action") == "created":
                    created += 1
                elif res.get("action") == "updated":
                    updated += 1
            except Exception as e:
                _logger.error("Error syncing UID %s: %s", uid, e)
                errors += 1

        # Process deletions
        for href in result["deleted_hrefs"]:
            event = CalendarEvent.search([
                ("x_caldav_href", "=", href),
                ("x_nc_calendar_id", "=", self.id),
            ], limit=1)
            if event:
                try:
                    with self.env.cr.savepoint():
                        CalendarEvent.delete_from_nextcloud(
                            event.x_nc_uid, self.id
                        )
                        self.env.flush_all()
                    deleted += 1
                except Exception as e:
                    _logger.error(
                        "Error deleting event href %s: %s", href, e
                    )
                    errors += 1
            else:
                _logger.debug(
                    "Deleted href %s not found in Odoo, skipping", href
                )

        # Store new sync-token
        new_token = result.get("sync_token")
        if new_token:
            self.caldav_sync_token = new_token

        # Build summary
        parts = []
        if created:
            parts.append(f"{created} created")
        if updated:
            parts.append(f"{updated} updated")
        if deleted:
            parts.append(f"{deleted} deleted")
        if errors:
            parts.append(f"{errors} errors")
        summary = ", ".join(parts) if parts else "no changes"
        msg = f"Incremental sync: {summary}"

        status = "error" if errors and not (created or updated or deleted) else (
            "partial" if errors else "success"
        )
        self.update_sync_status(status, msg)
        _logger.info("Incremental sync for %s: %s", self.name, msg)

    def _parse_sync_collection_response(self, xml_text):
        """Parse WebDAV sync-collection 207 response.

        Returns dict with:
            sync_token: new opaque token from response
            changed: list of parsed ICS event dicts (created/modified)
            deleted_hrefs: list of href strings for deleted resources
        """
        ns = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav"}
        result = {"sync_token": None, "changed": [], "deleted_hrefs": []}

        try:
            root = ElementTree.fromstring(xml_text)
        except ElementTree.ParseError:
            _logger.error("Failed to parse sync-collection response XML")
            return result

        # Extract new sync-token from response root
        token_el = root.find("d:sync-token", ns)
        if token_el is not None and token_el.text:
            result["sync_token"] = token_el.text.strip()

        for resp in root.findall("d:response", ns):
            href_el = resp.find("d:href", ns)
            href = href_el.text if href_el is not None else None

            # Check status — 404 means deleted
            status_el = resp.find("d:status", ns)
            if status_el is not None and "404" in (status_el.text or ""):
                if href:
                    result["deleted_hrefs"].append(href)
                continue

            # Check for propstat-level status (Nextcloud wraps in propstat)
            propstat = resp.find("d:propstat", ns)
            if propstat is not None:
                ps_status = propstat.find("d:status", ns)
                if ps_status is not None and "404" in (ps_status.text or ""):
                    if href:
                        result["deleted_hrefs"].append(href)
                    continue

            # Changed/created — extract ICS data
            etag_el = resp.find(".//d:getetag", ns)
            caldata_el = resp.find(".//c:calendar-data", ns)

            etag = (
                etag_el.text.strip('"')
                if etag_el is not None and etag_el.text
                else None
            )
            ics_text = caldata_el.text if caldata_el is not None else None

            if not ics_text:
                # Response without calendar-data but with 200 status
                # could be a collection response — skip it
                continue

            ev_data = self._parse_ics_vevent(ics_text)
            if ev_data:
                ev_data["href"] = href
                ev_data["etag"] = etag
                result["changed"].append(ev_data)

        return result

    def _parse_report_response(self, xml_text):
        """Parse CalDAV REPORT 207 Multi-Status response.

        Returns list of dicts with parsed ICS event data.
        """
        ns = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav"}
        events = []
        try:
            root = ElementTree.fromstring(xml_text)
        except ElementTree.ParseError:
            _logger.error("Failed to parse REPORT response XML")
            return events

        for resp in root.findall("d:response", ns):
            href_el = resp.find("d:href", ns)
            etag_el = resp.find(".//d:getetag", ns)
            caldata_el = resp.find(".//c:calendar-data", ns)

            href = href_el.text if href_el is not None else None
            etag = etag_el.text.strip('"') if etag_el is not None and etag_el.text else None
            ics_text = caldata_el.text if caldata_el is not None else None

            if not ics_text:
                continue

            ev_data = self._parse_ics_vevent(ics_text)
            if ev_data:
                ev_data["href"] = href
                ev_data["etag"] = etag
                events.append(ev_data)

        return events

    def _parse_ics_vevent(self, ics_text):
        """Parse ICS text and extract the first VEVENT data.

        Handles line folding (RFC 5545 §3.1) and common date formats.

        Returns:
            dict with keys: uid, summary, start, end, allday, location,
                description, attendees
            None if no VEVENT found
        """
        # Unfold lines: continuation lines start with space or tab
        unfolded = re.sub(r"\r?\n[ \t]", "", ics_text)
        lines = unfolded.splitlines()

        in_vevent = False
        in_nested = 0  # depth counter for VALARM, VTIMEZONE, etc.
        props = {}
        attendees = []

        for line in lines:
            stripped = line.strip()
            if stripped == "BEGIN:VEVENT":
                in_vevent = True
                in_nested = 0
                props = {}
                attendees = []
                continue
            if stripped == "END:VEVENT":
                break
            if not in_vevent:
                continue
            # Skip nested components (VALARM, etc.) whose properties
            # (SUMMARY, DESCRIPTION, ATTENDEE) would overwrite VEVENT ones.
            if stripped.startswith("BEGIN:"):
                in_nested += 1
                continue
            if stripped.startswith("END:"):
                in_nested = max(0, in_nested - 1)
                continue
            if in_nested:
                continue

            # Parse property;params:value
            if ":" not in stripped:
                continue

            # Split on first colon, but handle params with semicolons
            prop_part, _, value = stripped.partition(":")
            prop_name = prop_part.split(";")[0].upper()
            params_str = prop_part[len(prop_name):]

            if prop_name == "UID":
                props["uid"] = value
            elif prop_name == "SUMMARY":
                props["summary"] = self._unescape_ics(value)
            elif prop_name == "DTSTART":
                props["start_raw"] = value
                props["start_params"] = params_str
            elif prop_name == "DTEND":
                props["end_raw"] = value
                props["end_params"] = params_str
            elif prop_name == "DURATION":
                props["duration"] = value
            elif prop_name == "LOCATION":
                props["location"] = self._unescape_ics(value)
            elif prop_name == "DESCRIPTION":
                props["description"] = self._unescape_ics(value)
            elif prop_name == "RRULE":
                props["rrule"] = value
            elif prop_name == "EXDATE":
                exdates_raw = props.setdefault("exdates_raw", [])
                exdates_raw.append((value, params_str))
            elif prop_name == "ATTENDEE":
                # value is mailto:email
                email = value.replace("mailto:", "").replace("MAILTO:", "")
                if email:
                    attendees.append(email)

        if not props.get("uid"):
            return None

        # Parse dates
        start_dt, allday = self._parse_ics_datetime(
            props.get("start_raw"), props.get("start_params", "")
        )
        end_dt, _ = self._parse_ics_datetime(
            props.get("end_raw"), props.get("end_params", "")
        )

        # If no DTEND but DURATION, compute end from start
        if not end_dt and start_dt and props.get("duration"):
            dur = self._parse_ics_duration(props["duration"])
            if dur:
                end_dt = start_dt + dur

        # For all-day events with no end, default to start + 1 day
        if allday and start_dt and not end_dt:
            end_dt = start_dt + timedelta(days=1)

        # For non-allday events with no end, default to start + 1 hour
        if not allday and start_dt and not end_dt:
            end_dt = start_dt + timedelta(hours=1)

        if not start_dt:
            return None

        # Extract event timezone from DTSTART params for recurring events
        event_tz = None
        tzid_match = re.search(
            r"TZID=([^;:]+)", props.get("start_params", ""), re.IGNORECASE
        )
        if tzid_match:
            event_tz = self._normalize_timezone(tzid_match.group(1))

        # Parse EXDATE values
        exdates = self._parse_exdates(props.get("exdates_raw", []))

        # Format for Odoo
        if allday:
            start_str = start_dt.strftime("%Y-%m-%d")
            # RFC 5545: DTEND for VALUE=DATE is exclusive (the day after
            # the last day).  Odoo stop_date is inclusive, so subtract 1d.
            if end_dt:
                end_inclusive = end_dt - timedelta(days=1)
                end_str = end_inclusive.strftime("%Y-%m-%d")
            else:
                end_str = start_str
        else:
            start_str = fields.Datetime.to_string(start_dt)
            end_str = fields.Datetime.to_string(end_dt) if end_dt else start_str

        return {
            "uid": props["uid"],
            "summary": props.get("summary", ""),
            "start": start_str,
            "end": end_str,
            "allday": allday,
            "location": props.get("location", ""),
            "description": props.get("description", ""),
            "attendees": attendees,
            "rrule": props.get("rrule"),
            "exdates": exdates,
            "event_tz": event_tz,
        }

    def _parse_ics_datetime(self, value, params_str):
        """Parse an ICS date/datetime value.

        Args:
            value: raw date string (e.g., '20260214', '20260214T100000Z')
            params_str: parameter string (e.g., ';VALUE=DATE', ';TZID=America/Montreal')

        Returns:
            tuple: (datetime_utc, is_allday)
        """
        if not value:
            return None, False

        params_upper = params_str.upper() if params_str else ""

        # All-day event: VALUE=DATE or 8-digit date
        if "VALUE=DATE" in params_upper or (len(value) == 8 and value.isdigit()):
            try:
                dt = datetime.strptime(value, "%Y%m%d")
                return dt, True
            except ValueError:
                return None, False

        # Extract TZID if present
        tzid = None
        tzid_match = re.search(r"TZID=([^;:]+)", params_str or "", re.IGNORECASE)
        if tzid_match:
            tzid = self._normalize_timezone(tzid_match.group(1))

        try:
            # UTC indicator
            if value.endswith("Z"):
                dt = datetime.strptime(value, "%Y%m%dT%H%M%SZ")
                dt = dt.replace(tzinfo=timezone.utc)
                return dt, False

            # Try basic ISO format first (no separators)
            if "T" in value and len(value) == 15:
                dt = datetime.strptime(value, "%Y%m%dT%H%M%S")
            else:
                dt = dt_parser.parse(value)

            # Apply timezone if specified
            if tzid:
                try:
                    tz = pytz.timezone(tzid)
                    if dt.tzinfo is None:
                        dt = tz.localize(dt)
                    dt = dt.astimezone(timezone.utc)
                except pytz.UnknownTimeZoneError:
                    _logger.warning("Unknown timezone: %s", tzid)
                    # Treat as UTC
                    dt = dt.replace(tzinfo=timezone.utc)
            elif dt.tzinfo is None:
                # No timezone info — assume UTC
                dt = dt.replace(tzinfo=timezone.utc)

            # Strip tzinfo for Odoo (expects naive UTC)
            dt = dt.replace(tzinfo=None)
            return dt, False
        except (ValueError, OverflowError) as e:
            _logger.warning("Failed to parse ICS datetime '%s': %s", value, e)
            return None, False

    @staticmethod
    def _normalize_timezone(tzid):
        """Normalize timezone name, mapping Windows names to IANA.

        Delegates to the shared ``bf_timezone`` helper (single source of truth
        for the Windows->IANA map)."""
        return tz_tools.normalize_name(tzid)

    def _parse_exdates(self, exdates_raw):
        """Parse raw EXDATE values into a list of datetime objects.

        Args:
            exdates_raw: list of (value, params_str) tuples from ICS parsing

        Returns:
            list of datetime objects (UTC, naive)
        """
        result = []
        for value, params_str in exdates_raw:
            # EXDATE can have multiple comma-separated dates
            for date_str in value.split(","):
                date_str = date_str.strip()
                if not date_str:
                    continue
                dt, _ = self._parse_ics_datetime(date_str, params_str or "")
                if dt:
                    result.append(dt)
        return result

    @staticmethod
    def _parse_ics_duration(duration_str):
        """Parse ICS DURATION value (e.g., 'PT1H', 'P1D', 'PT30M').

        Returns timedelta or None.
        """
        match = re.match(
            r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?",
            duration_str,
        )
        if not match:
            return None
        days = int(match.group(1) or 0)
        hours = int(match.group(2) or 0)
        minutes = int(match.group(3) or 0)
        seconds = int(match.group(4) or 0)
        return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)

    @staticmethod
    def _unescape_ics(value):
        """Unescape ICS text values (RFC 5545 §3.3.11)."""
        return (
            value.replace("\\n", "\n")
            .replace("\\N", "\n")
            .replace("\\,", ",")
            .replace("\\;", ";")
            .replace("\\\\", "\\")
        )

    def _sync_notification(self, message, notif_type):
        """Return a display_notification action for sync operations."""
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Calendar Sync",
                "message": message,
                "type": notif_type,
                "sticky": notif_type == "danger",
            },
        }
