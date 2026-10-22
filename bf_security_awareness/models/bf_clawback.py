"""Clawback (PhishRIP-style) remediation: pull a confirmed-malicious email out
of every internal mailbox, reversibly.

Flow, all manager-driven from a confirmed :class:`bf.reported.phish`:

1. ``action_preview`` (dry-run) — searches each mailbox, counts matches, moves
   nothing. Mandatory before executing a *heuristic* (From/Subject) clawback.
2. ``action_execute`` — moves matches to the connector's quarantine folder.
3. ``action_restore`` — moves them back to INBOX.

Matching is exact when a ``Message-ID`` is known (parsed from the reported .eml),
otherwise a From + Subject + date-window heuristic that a human reviews first.
One :class:`bf.clawback.hit` per mailbox carries the per-box outcome; processing
commits per mailbox so a long sweep is resumable by ``_cron_process_clawback``.
"""

import logging
import threading
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .reader_lang import company_env

_logger = logging.getLogger(__name__)


class BfClawbackOperation(models.Model):
    _name = "bf.clawback.operation"
    _description = "Email removal operation (clawback)"
    _inherit = ["mail.thread"]
    _order = "create_date desc"

    name = fields.Char(compute="_compute_name", store=True)
    reported_phish_id = fields.Many2one(
        "bf.reported.phish", string="Report", ondelete="cascade",
        index=True, tracking=True)
    company_id = fields.Many2one(
        "res.company", string="Company",
        default=lambda self: self.env.company, index=True)
    connector_id = fields.Many2one(
        "bf.mail.clawback.connector", string="Connector", required=True,
        default=lambda self: self._default_connector(), tracking=True)

    mode = fields.Selection(
        [("quarantine", "Quarantine (dedicated folder, reversible)"),
         ("delete", "Trash (reversible until emptied)")],
        string="Removal mode", required=True,
        default=lambda self: self._default_mode(), tracking=True,
        help="Quarantine: moves to a hidden \"Quarantaine BF\" folder. "
             "Trash: moves to each mailbox's Trash. Both can be restored "
             "to INBOX with \"Restore\".")
    match_strategy = fields.Selection(
        [("message_id", "Exact Message-ID"),
         ("heuristic", "Sender + subject (heuristic)")],
        string="Strategy", required=True, default="heuristic", tracking=True)

    message_id = fields.Char(string="Message-ID")
    email_from = fields.Char(string="Sender")
    subject = fields.Char(string="Subject")
    since_date = fields.Date(string="Received since")

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("preview", "Previewed"),
            ("running", "In progress"),
            ("done", "Removed"),
            ("restored", "Restored"),
            ("failed", "Failed"),
        ],
        string="Status", default="draft", required=True, tracking=True)
    launched_by = fields.Many2one("res.users", string="Launched by", readonly=True)

    blast_confirmed = fields.Boolean(
        string="High blast radius confirmed", readonly=True,
        help="Set to true by a manager to authorize a removal whose "
             "preview exceeds the configured cap.")
    authorized = fields.Boolean(
        string="Removal authorized", readonly=True, copy=False,
        help="Set to true ONLY by \"Run removal\", under the Purge "
             "privilege and after every safeguard. The sweep (including "
             "the scheduled resume) refuses to act without this flag, so "
             "it cannot be bypassed through the state.")

    hit_ids = fields.One2many(
        "bf.clawback.hit", "operation_id", string="Swept mailboxes")
    mailboxes_swept = fields.Integer(compute="_compute_totals", store=True)
    messages_found = fields.Integer(compute="_compute_totals", store=True)
    messages_removed = fields.Integer(compute="_compute_totals", store=True)

    # ------------------------------------------------------------------ #
    # Defaults / compute
    # ------------------------------------------------------------------ #
    @api.model
    def _icp(self, key, default=None):
        return self.env["ir.config_parameter"].sudo().get_param(
            "bf_security_awareness.%s" % key, default)

    @api.model
    def _default_connector(self):
        return self.env["bf.mail.clawback.connector"].search(
            [("active", "=", True)], limit=1)

    @api.model
    def _default_mode(self):
        return self._icp("clawback_mode", "quarantine") or "quarantine"

    @api.depends("reported_phish_id", "message_id", "subject")
    def _compute_name(self):
        # Stored, read by the security team: their company's language.
        for rec in self:
            env = company_env(rec, rec.company_id).env
            label = rec.subject or rec.message_id or env._("email")
            rec.name = env._("Removal: %s") % label

    @api.depends("hit_ids.state", "hit_ids.found_count", "hit_ids.removed_count")
    def _compute_totals(self):
        for rec in self:
            rec.mailboxes_swept = len(
                rec.hit_ids.filtered(lambda h: h.state != "pending"))
            rec.messages_found = sum(rec.hit_ids.mapped("found_count"))
            rec.messages_removed = sum(rec.hit_ids.mapped("removed_count"))

    def write(self, vals):
        # The execution gate is the ``authorized`` flag, not the writable
        # ``state``. Block anyone but a purge user (or the framework itself)
        # from setting it directly via ORM/RPC — otherwise a non-purge manager
        # could authorize + flag the op as running and let the resume cron move
        # mail on their behalf, bypassing _check_purge_rights.
        if vals.get("authorized") and not self.env.su:
            if not self.env.user.has_group(
                    "bf_security_awareness.group_bf_secaware_purge"):
                raise AccessError(_(
                    "Only the \"Email purge\" group can authorize a removal."))
        return super().write(vals)

    # ------------------------------------------------------------------ #
    # Search criteria
    # ------------------------------------------------------------------ #
    @staticmethod
    def _reject_control(label, value):
        """Fail loudly and EARLY (before any IMAP connection) if a search value
        carries control characters — the IMAP-injection vector. Defence in depth
        with the per-arg screen in connector._imap_quote."""
        if value and any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
            raise UserError(_(
                "%s contains forbidden control characters (IMAP injection "
                "risk).") % label)
        return value

    def _criteria(self):
        self.ensure_one()
        if self.match_strategy == "message_id":
            if not self.message_id:
                raise UserError(_(
                    "Message-ID strategy selected but no Message-ID provided."))
            return {"message_id": self._reject_control(
                "Message-ID", self.message_id)}
        if not (self.email_from or self.subject):
            raise UserError(_(
                "Heuristic strategy: a sender or a subject is required."))
        return {
            "email_from": self._reject_control(
                self.env._("Sender"), self.email_from) or None,
            "subject": self._reject_control(
                self.env._("Subject"), self.subject) or None,
            "since": self.since_date or None,
        }

    def _ensure_hits(self):
        """Create one hit per target mailbox if not already present."""
        self.ensure_one()
        existing = set(self.hit_ids.mapped("mailbox_email"))
        Hit = self.env["bf.clawback.hit"]
        for email in self.connector_id._target_mailboxes():
            if email not in existing:
                Hit.create({"operation_id": self.id, "mailbox_email": email})

    # ------------------------------------------------------------------ #
    # Core sweep — token minted once, commit per mailbox (resumable)
    # ------------------------------------------------------------------ #
    def _safe_commit(self):
        """Commit per mailbox so a long sweep is resumable — but never inside
        the test transaction (Odoo forbids it)."""
        if getattr(threading.current_thread(), "testing", False):
            return
        if self.env.registry.in_test_mode():
            return
        self.env.cr.commit()

    def _run(self, action):
        """action: 'search' | 'move' | 'restore'. Processes pending hits."""
        self.ensure_one()
        # Re-assert the destructive-action invariants HERE (not only in
        # action_execute), so the resume cron and any direct caller are bound
        # by them too — defence in depth behind the ``authorized`` gate.
        if action == "move":
            if not self.authorized:
                raise UserError(_(
                    "Removal not authorized. Use \"Run removal\"."))
            if self.mode == "delete" and self.match_strategy != "message_id":
                raise UserError(_(
                    "Trash mode: an exact Message-ID match is required."))
        connector = self.connector_id
        crit = self._criteria()
        token = (connector._mint_token()
                 if connector.backend == "m365_oauth" else None)
        src = connector.search_folder or "INBOX"
        quarantine = connector.quarantine_folder
        for hit in self.hit_ids:
            if action == "search" and hit.state not in ("pending",):
                continue
            if action == "move" and hit.state not in ("pending", "searched"):
                continue
            if action == "restore" and hit.state != "removed":
                continue
            try:
                conn = connector._login(hit.mailbox_email, token=token)
            except Exception as exc:  # noqa: BLE001
                hit.write({"state": "error", "error_message": str(exc)[:500]})
                self._safe_commit()
                continue
            try:
                if action == "search":
                    uids = connector._imap_search(conn, src, **crit)
                    hit.write({
                        "state": "searched",
                        "found_count": len(uids),
                        "imap_uids": b",".join(uids).decode() if uids else "",
                    })
                elif action == "move":
                    uids = connector._imap_search(conn, src, **crit)
                    dest = quarantine if self.mode == "quarantine" else "Trash"
                    moved = connector._imap_move(conn, src, uids, dest)
                    hit.write({
                        "state": "removed",
                        "found_count": len(uids),
                        "removed_count": moved,
                        "imap_uids": b",".join(uids).decode() if uids else "",
                    })
                elif action == "restore":
                    rsrc = quarantine if self.mode == "quarantine" else "Trash"
                    uids = connector._imap_search(conn, rsrc, **crit)
                    moved = connector._imap_move(conn, rsrc, uids, "INBOX")
                    hit.write({"state": "restored", "removed_count": moved})
            except Exception as exc:  # noqa: BLE001
                hit.write({"state": "error", "error_message": str(exc)[:500]})
            finally:
                try:
                    conn.logout()
                except Exception:  # noqa: BLE001
                    pass
            self._safe_commit()

    # ------------------------------------------------------------------ #
    # Manager actions
    # ------------------------------------------------------------------ #
    def action_preview(self):
        self.ensure_one()
        self._ensure_hits()
        self._run("search")
        self.state = "preview"
        self.message_post(body=_(
            "Preview: %(found)s email(s) found in %(boxes)s mailbox(es). "
            "Nothing was moved.") % {
                "found": self.messages_found, "boxes": self.mailboxes_swept})
        return True

    def _check_purge_rights(self):
        if not self.env.user.has_group(
                "bf_security_awareness.group_bf_secaware_purge"):
            raise UserError(_(
                "Action restricted to the \"Email purge (clawback)\" "
                "group. Managers can still use the preview."))

    def action_execute(self):
        self.ensure_one()
        self._check_purge_rights()
        # Hard rail: irreversible deletion must target an exact Message-ID,
        # never a fuzzy From/Subject match.
        if self.mode == "delete" and self.match_strategy != "message_id":
            raise UserError(_(
                "Trash mode requires an exact Message-ID match (attach "
                "the .eml). Use quarantine for a heuristic removal."))
        require_preview = (self._icp("clawback_require_preview", "1") or "1") == "1"
        if (self.match_strategy == "heuristic" and require_preview
                and self.state not in ("preview", "running")):
            raise UserError(_(
                "A preview is required before running a heuristic "
                "removal. Run \"Preview\" and check the matches first."))
        self._ensure_hits()
        # Make sure we have a live found count BEFORE applying the cap — even
        # for the message_id strategy (which skips the mandatory preview), so
        # the blast-radius cap can never be silently bypassed.
        if self.state not in ("preview", "running"):
            self._run("search")
        # Blast-radius cap: if more than the configured threshold was found,
        # require an explicit confirmation first.
        cap = int(float(self._icp("clawback_max_blast_messages", "25") or 25))
        if cap and self.messages_found > cap and not self.blast_confirmed:
            raise UserError(_(
                "High blast radius: %(found)s emails found (cap %(cap)s). "
                "Check the preview, then click \"Confirm high blast "
                "radius\" before running.") % {
                    "found": self.messages_found, "cap": cap})
        if not self.launched_by:
            self.launched_by = self.env.user
        # Authorize ONLY here — under purge rights and past every rail. The
        # sweep (and the resume cron) refuse to move without this flag.
        self.authorized = True
        self.state = "running"
        self._run("move")
        self.state = "done"
        body = _(
            "Removal run by %(user)s: %(removed)s email(s) moved to "
            "\"%(folder)s\" in %(boxes)s mailbox(es).") % {
                "user": self.env.user.display_name,
                "removed": self.messages_removed,
                "folder": (self.connector_id.quarantine_folder
                           if self.mode == "quarantine" else _("Trash")),
                "boxes": self.mailboxes_swept}
        self.message_post(body=body)
        if self.reported_phish_id:
            self.reported_phish_id.message_post(body=body)
            self.reported_phish_id._notify_security_team_clawback(self)
        return True

    def action_confirm_blast(self):
        """Manager override allowing an over-cap execution."""
        self.ensure_one()
        self._check_purge_rights()
        self.blast_confirmed = True
        self.message_post(body=_(
            "High blast radius confirmed by %s.") % self.env.user.display_name)
        return True

    def action_restore(self):
        self.ensure_one()
        self._check_purge_rights()
        self._run("restore")
        self.state = "restored"
        self.message_post(body=_(
            "Emails restored to INBOX (%(n)s mailbox(es)).") % {
                "n": len(self.hit_ids.filtered(lambda h: h.state == "restored"))})
        return True

    # ------------------------------------------------------------------ #
    # Cron — resume any interrupted execution
    # ------------------------------------------------------------------ #
    @api.model
    def _cron_process_clawback(self):
        # Only resume operations a purge user already AUTHORIZED. Never act on
        # an op merely flagged ``running`` without authorization.
        stuck = self.search([
            ("state", "=", "running"), ("authorized", "=", True)])
        for op in stuck:
            if op.hit_ids.filtered(lambda h: h.state in ("pending", "searched")):
                op._run("move")
            if not op.hit_ids.filtered(lambda h: h.state in ("pending", "searched")):
                op.state = "done"


class BfClawbackHit(models.Model):
    _name = "bf.clawback.hit"
    _description = "Swept mailbox (clawback)"
    _order = "mailbox_email"

    operation_id = fields.Many2one(
        "bf.clawback.operation", required=True, ondelete="cascade", index=True)
    mailbox_email = fields.Char(string="Mailbox", required=True)
    state = fields.Selection(
        [
            ("pending", "Pending"),
            ("searched", "Searched"),
            ("removed", "Removed"),
            ("restored", "Restored"),
            ("error", "Error"),
        ],
        string="Status", default="pending", required=True)
    found_count = fields.Integer(string="Found messages")
    removed_count = fields.Integer(string="Removed messages")
    # Snapshot of the source-folder UIDs at search time (audit only; restore
    # re-searches the quarantine folder by criteria rather than trusting these).
    imap_uids = fields.Text(string="IMAP UIDs (snapshot)")
    error_message = fields.Char(string="Error")
