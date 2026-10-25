# Part of bf_dav_server. See LICENSE file for full copyright and licensing details.
import hashlib
import logging
import threading
import time

from odoo import _, api, fields, models
from odoo.addons.base.models.res_users import INDEX_SIZE, KEY_CRYPT_CONTEXT

_logger = logging.getLogger(__name__)

DAV_SCOPE = "dav"

# Verified (login, key) pairs, so that a sync of a few hundred requests does not
# hash the key a few hundred times. Short-lived: a revoked key stops working
# within the TTL.
_AUTH_CACHE = {}
_AUTH_LOCK = threading.Lock()
_AUTH_TTL = 60
_FAILS = {}


class ResUsers(models.Model):
    _inherit = "res.users"

    bf_dav_calendar_ids = fields.One2many("bf.dav.calendar", "owner_id", string="CalDAV calendars")
    bf_dav_key_count = fields.Integer(compute="_compute_bf_dav_key_count", string="App passwords")

    def _compute_bf_dav_key_count(self):
        keys = self.env["res.users.apikeys"].sudo()
        for user in self:
            user.bf_dav_key_count = keys.search_count([("user_id", "=", user.id), ("scope", "=", DAV_SCOPE)])

    @api.model
    def _bf_dav_authenticate(self, login, key, remote_addr=""):
        """Return the user id for a CalDAV/CardDAV app password, or None.

        Only keys of scope ``dav`` are accepted: an RPC key (no scope) opens
        the whole API and is refused here, and a ``dav`` key is refused by RPC.
        """
        if not login or not key or len(key) > 200:
            return None
        digest = hashlib.sha256(("%s\0%s\0%s" % (self.env.cr.dbname, login, key)).encode()).hexdigest()
        now = time.monotonic()
        fail_key = "%s|%s" % (remote_addr, login)
        with _AUTH_LOCK:
            hit = _AUTH_CACHE.get(digest)
            if hit and hit[1] > now:
                return hit[0]
            fails = _FAILS.get(fail_key)
            if fails and fails[0] >= 10 and fails[1] > now:
                return None
        cr = self.env.cr
        uid = None
        cr.execute("""
            SELECT k.user_id, k.key FROM res_users_apikeys k JOIN res_users u ON u.id = k.user_id
             WHERE k.index = %s AND k.scope = %s AND u.active AND lower(u.login) = lower(%s) AND u.share IS NOT TRUE
               AND (k.expiration_date IS NULL OR k.expiration_date >= now() at time zone 'utc')
        """, [key[:INDEX_SIZE], DAV_SCOPE, login])
        for user_id, hashed in cr.fetchall():
            if KEY_CRYPT_CONTEXT.verify(key, hashed):
                uid = user_id
                break
        with _AUTH_LOCK:
            if uid:
                _AUTH_CACHE[digest] = (uid, now + _AUTH_TTL)
                _FAILS.pop(fail_key, None)
            else:
                count, _until = _FAILS.get(fail_key, (0, 0))
                _FAILS[fail_key] = (count + 1, now + 300)
                _logger.warning("bf_dav_server: refused app password for %r from %s", login, remote_addr)
        return uid

    def action_bf_dav_new_password(self):
        """Opens the wizard; the wizard confirms the password before minting a key."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("New app password"),
            "res_model": "bf.dav.password.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_user_id": self.id},
        }

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + ["bf_dav_key_count", "bf_dav_calendar_ids"]


def clear_auth_cache():
    with _AUTH_LOCK:
        _AUTH_CACHE.clear()
        _FAILS.clear()
