"""One unblock notice, from the moment a task is unblocked until it is sent.

Without Gen's game plan, the notice is sent in the transaction that unblocked
the task, as it always was. With it, the notice waits: a game plan takes about
a minute to write (69 s median, 150 s at the 9th decile, measured on a
production instance), far too long for the person closing a blocker to wait on their
screen. The event is recorded, a thread prepares the plan after the commit,
and ONE email leaves with the plan in it. Past `PLAN_BUDGET`, or when anything
fails, the notice leaves without a plan and says so.

🔴 The plan is written by a LOCKED pass of the bridge: no shell, no file, no
tool but the two that read this context and hand the plan back. Odoo assembles
what the pass reads, under the rights of the person who will receive it, so the
plan cannot tell anyone about a record they cannot open. The thread of a task
can carry emails from outside: at worst, a crafted one skews the text of the
plan, which is why the text comes back plain, without links, addresses or
domains. It cannot make Gen read or do anything else.

🔴 The notice itself obeys the same rule: the tasks it lists are the ones the
recipient can open (version 1.8.0 named any blocker).
"""
import json
import logging
import re
import socket
import threading
import time
import unicodedata
from datetime import timedelta

import babel.dates
import babel.numbers
import pytz

from odoo import SUPERUSER_ID, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.modules.registry import Registry
from odoo.tools import clean_context, html2plaintext
from odoo.tools.misc import babel_locale_parse, get_lang

_logger = logging.getLogger(__name__)

#: The bridge route that writes the plan (a locked pass, see the module docstring).
PLAN_ENDPOINT = "/task-unblock-plan"
#: Seconds the notice may wait for the plans of all its recipients.
PLAN_BUDGET = 180
#: Below this, a pass would not finish: the recipient gets no plan.
MIN_BUDGET = 45
#: A notice still waiting this long was never picked up (server restarted
#: between the commit and the thread): it leaves without a plan.
LATE_AFTER = timedelta(minutes=4)
#: A notice whose preparation started this long ago lost its thread.
STALE_AFTER = timedelta(minutes=6)
#: Sending attempts of the cron before a notice is marked failed.
MAX_ATTEMPTS = 3
#: Sent and failed notices are kept this long, then purged.
KEEP_FOR = timedelta(days=60)
#: The system parameter that allows the game plan on this database. The bridge
#: may serve some databases only (it runs on its operator's subscription):
#: without the parameter, the setting stays hidden, whatever Gen is installed.
PLAN_ALLOWED_PARAM = "bf_task_unblock_notify.gen_plan_allowed"

#: Bounds of what the pass reads, in characters.
MAX_DESCRIPTION = 4000
MAX_MESSAGE = 1200
MAX_MESSAGES = 8
MAX_BLOCKER_MESSAGES = 2
MAX_BLOCKERS = 6
MAX_ACTIVITIES = 10
MAX_PAYLOAD = 24000

#: Bounds of what comes back: the shape of Gen's usual brief.
MAX_SITUATION = 3
MAX_ACTIONS = 3
MAX_LINE = 500

_HTTP_STATUS = re.compile(r"Bridge HTTP (\d{3})")
#: What the bridge's route answers, in a word for `plan_outcome`.
_STATUS_WORDS = {
    404: "no_route",      # a bridge without the route
    403: "refused",       # tenant not served
    429: "busy",          # too many passes in flight for the tenant
    502: "no_plan",       # the pass ran and gave no usable plan
    503: "unavailable",   # relay, CLI or service down: the next recipient would fail too
}

# What the plan may not carry: nothing a mail client would make clickable.
# Schemes go (`[...]`, ASCII: stable under NFKC). Domains, addresses and IPs are
# NEUTRALISED instead of removed, so that a file name or a version stays
# readable: a dot between two letters or digits becomes U+2024 (one dot
# leader), an @ becomes U+FF20. No list of TLDs to keep up to date, and a domain
# cut by the line limit is already neutralised.
MARK = "[...]"
LEADER = "\u2024"
FULL_AT = "\uff20"
_SCHEME = re.compile(
    r"(?i)[a-z][a-z0-9+.\-]*:[/\\]\S*"
    r"|(?<![\w/\\])[/\\]{2}\S*"
    r"|\bwww\.\S*"
    r"|(?<![a-z])(?:mailto|tel|sms|callto|javascript|data|file|ftp|hxxps?|https?|wss?|search-ms"
    r"|ms-[a-z0-9-]+|skype|sips?|im|facetime(?:-audio)?|webcal|itms-services|news|nntp|ircs?|smb"
    r"|telnet|ldaps?):(?=\S)\S*")
# EVERY dot but one before a space, a closing punctuation or the end: what
# follows a dot in a host name can be a letter of any script, an emoji, or an
# invisible mark that browsers drop (U+180B: "evil.\u180bcom" opens evil.com).
# Listing what may follow a dot would only list what we knew of.
_DOT = re.compile(r"[.\u3002](?![\s.,;:!?)\]}\u00bb\u201d\u2019\"'\u2026]|$)")
# A North American phone number, which mobile clients make tappable: the line goes.
_PHONE = re.compile(
    r"(?<!\d)(?:\+?1[\s.\-\u2010-\u2015\u2212/\u2024]?)?\(?\d{3}\)?[\s.\-\u2010-\u2015\u2212/\u2024]?\d{3}[\s.\-\u2010-\u2015\u2212/\u2024]?\d{4}(?!\d)")
_AT = re.compile(r"@")
_EXTRA_INVISIBLE = {0x034F, 0x115F, 0x1160, 0x3164, 0xFFA0}


def _visible_char(char):
    code = ord(char)
    return not (unicodedata.category(char) == "Cf" or code in _EXTRA_INVISIBLE
                or 0xFE00 <= code <= 0xFE0F or 0xE0100 <= code <= 0xE01EF)


def _clean_pass(text):
    # Cut again after NFKC, which can turn one character into several.
    text = unicodedata.normalize("NFKC", text)[:4 * MAX_LINE]
    text = "".join(char for char in text if _visible_char(char))
    # Before the patterns: `**`, `*` or a backtick between the letters of a
    # domain used to be removed AFTER them and rebuild it.
    text = text.replace("*", "").replace("`", "")
    text = _SCHEME.sub(MARK, text)
    text = _DOT.sub(LEADER, text)
    text = _AT.sub(FULL_AT, text)
    return re.sub(r"\s+", " ", text).strip()


def clean_text(text):
    """One line of the plan, plain, with nothing a mail client would link.

    The plan goes into an email signed by the company, and a thread can carry
    an email from outside that steered it. Cleaned to a fixed point, cut, and
    cleaned again: nothing that a pass removes can rebuild what another one
    took out, and no cut can leave a live domain behind.
    """
    # Cut first: the scheme pattern backtracks, and a very long line would cost
    # seconds before the cut that follows.
    text = text[:4 * MAX_LINE]
    for _round in range(2):
        for _guard in range(5):
            cleaned = _clean_pass(text)
            if cleaned == text:
                break
            text = cleaned
        text = text[:MAX_LINE]
    return text if is_clean(text) else ""


def is_clean(text):
    """No scheme, no live dot before a letter or digit, no ASCII @, no phone number."""
    return not (_SCHEME.search(text) or _DOT.search(text) or "@" in text
                or _PHONE.search(text))


def _clean_lines(value, limit):
    """Plain lines only, bounded in number and length."""
    if not isinstance(value, list):
        return []
    lines = []
    for item in value[:limit * 2]:
        if not isinstance(item, str):
            continue
        text = clean_text(item)
        if text:
            lines.append(text)
    return lines[:limit]


def clean_plan(answer):
    """The plan as the notice shows it, or None when the answer is unusable."""
    if not isinstance(answer, dict):
        return None
    situation = _clean_lines(answer.get("situation"), MAX_SITUATION)
    actions = _clean_lines(answer.get("next_actions"), MAX_ACTIONS)
    if not situation and not actions:
        return None
    return {"situation": situation, "next_actions": actions}


_PENDING_KEY = "bf_task_unblock_notify.event_ids"


def _start(dbname, event_ids):
    """ONE thread for all the notices of a transaction, which it takes in turn.

    A thread per notice let a blocker freeing a dozen tasks open a dozen
    threads in one worker, each asking for database connections from a pool of
    a dozen (db_maxconn is often 12). Never raises: it runs in a post-commit hook,
    after the user's save is committed, and an exception there would show them
    an error and skip the hooks that follow.
    """
    if not event_ids:
        return
    try:
        threading.Thread(
            target=_prepare_and_send, args=(dbname, event_ids),
            name="unblock-plan-%s" % event_ids[0], daemon=True,
        ).start()
    except Exception:  # noqa: BLE001 - the cron sends them without a plan
        _logger.exception("Unblock notices %s: thread not started", event_ids)


def _prepare_and_send(dbname, event_ids):
    for event_id in event_ids:
        try:
            _prepare_one(dbname, event_id)
        except Exception:  # noqa: BLE001 - the cron sends it without a plan
            _logger.exception("Unblock notice %s: preparation failed", event_id)


def _prepare_one(dbname, event_id):
    """Claim, read, ask the bridge with no cursor open, then send.

    Three steps, two short transactions: the minute spent waiting for the
    bridge holds no database connection.
    """
    registry = Registry(dbname)
    with registry.cursor() as cr:
        env = api.Environment(cr, SUPERUSER_ID, {})
        event = env["bf.task.unblock.event"].browse(event_id)
        token = event._claim()
        if not token:
            return
        requests, eligible = event._plan_requests()
        target = event._bridge_target() if requests else None
    plans = dict.fromkeys(eligible)
    if requests:
        outcome = _ask_bridge(target, requests, plans)
    else:
        outcome = "no_context" if eligible else "not_eligible"
    with registry.cursor() as cr:
        env = api.Environment(cr, SUPERUSER_ID, {})
        env["bf.task.unblock.event"].browse(event_id)._send(plans, outcome, token=token)


def _ask_bridge(target, requests, plans):
    """Fill `plans` (partner id → plan) and return what happened, in a word."""
    if not target:
        return "unavailable"
    socket_path, tenant, transport = target
    deadline = time.monotonic() + PLAN_BUDGET
    outcomes = []
    for request in requests:
        remaining = deadline - time.monotonic()
        if remaining < MIN_BUDGET:
            outcomes.append("timeout")
            break
        try:
            # The bridge is told how long Odoo will wait: past that, its pass
            # would run and be paid for nothing.
            answer = transport.post(
                socket_path, PLAN_ENDPOINT,
                dict(request["payload"], tenant=tenant, budget_s=int(remaining)),
                timeout=remaining)
        except socket.timeout:
            outcomes.append("timeout")
            break
        except (FileNotFoundError, ConnectionRefusedError):
            outcomes.append("unavailable")
            break
        except ValueError as error:
            status = _HTTP_STATUS.search(str(error))
            word = _STATUS_WORDS.get(int(status.group(1)) if status else 0, "error")
            _logger.warning("Unblock plan: bridge answered %s", str(error)[:200])
            outcomes.append(word)
            # Only a pass that ran and gave nothing is worth trying for the next
            # recipient; a missing route, a refusal, a busy or masked-off bridge
            # would answer the same.
            if word != "no_plan":
                break
            continue
        except Exception:  # noqa: BLE001
            _logger.warning("Unblock plan: bridge call failed", exc_info=True)
            outcomes.append("error")
            continue
        plan = clean_plan(answer)
        plans[request["partner_id"]] = plan
        outcomes.append("ok" if plan else "invalid")
    return ",".join(dict.fromkeys(outcomes)) or "error"


class TaskUnblockEvent(models.Model):
    _name = "bf.task.unblock.event"
    _description = "Task unblock notice"
    _order = "id desc"
    _rec_name = "task_id"

    task_id = fields.Many2one(
        "project.task", string="Task", required=True, ondelete="cascade", index=True)
    user_id = fields.Many2one("res.users", string="Unblocked by", ondelete="set null")
    kind = fields.Selection(
        [("blocker_closed", "Blocking tasks closed"),
         ("dependency_removed", "Dependency removed")],
        required=True)
    other_task_ids = fields.Many2many(
        "project.task", "bf_task_unblock_event_task_rel", "event_id", "task_id",
        string="Tasks that unblocked it")
    partner_ids = fields.Many2many(
        "res.partner", "bf_task_unblock_event_partner_rel", "event_id", "partner_id",
        string="Recipients")
    to_manager = fields.Boolean(
        "Sent to the project manager",
        help="Nobody was assigned to the task: the project manager was told instead.")
    with_plan = fields.Boolean("With a game plan")
    state = fields.Selection(
        [("pending", "Waiting for the game plan"),
         ("preparing", "Game plan in preparation"),
         ("sent", "Sent"),
         ("failed", "Failed")],
        default="pending", required=True, index=True)
    attempts = fields.Integer("Late sending attempts", readonly=True)
    claimed_at = fields.Datetime(readonly=True)
    sent_at = fields.Datetime(readonly=True)
    plan_outcome = fields.Char("Game plan outcome", readonly=True)

    # Layouts of the notice, in order of preference: the first one present
    # serves. `bluefox_branding` is not a dependency; installed, it puts the
    # notice with the company's other emails (banner, accent, font, footer).
    _LAYOUTS = (
        "bluefox_branding.bf_mail_layout",
        "mail.mail_notification_light",
    )

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------
    @api.model
    def _record(self, task, kind, others):
        """Record the notice of one unblocked task, and send it or schedule it."""
        partners, to_manager = self._recipients(task)
        if not partners:
            _logger.info("Task %s unblocked: nobody to notify", task.id)
            return self.browse()
        with self.env.cr.savepoint():
            event = self.create({
                # Explicit: a `default_state` in the caller's context (a grouped
                # kanban by status) would otherwise land here and fail.
                "state": "pending",
                "task_id": task.id,
                "user_id": self.env.uid,
                "kind": kind,
                "other_task_ids": [(6, 0, others.ids)],
                "partner_ids": [(6, 0, partners.ids)],
                "to_manager": to_manager,
                "with_plan": self._plan_wanted(task),
            })
            if not event.with_plan:
                event._send({}, False)
        if event.with_plan:
            # One hook per transaction, one thread: the ids accumulate.
            data = self.env.cr.postcommit.data
            if _PENDING_KEY not in data:
                data[_PENDING_KEY] = []
                dbname = self.env.cr.dbname
                self.env.cr.postcommit.add(lambda: _start(dbname, data.pop(_PENDING_KEY, [])))
            data[_PENDING_KEY].extend(event.ids)
        return event

    @api.model
    def _company_of(self, task):
        return task.company_id or task.project_id.company_id or self.env.company

    @api.model
    def _recipients(self, task):
        """The assignees; failing them, the project manager if the company asks."""
        partners = task.user_ids.partner_id
        if partners:
            return partners, False
        manager = task.project_id.user_id
        if manager and self._company_of(task).unblock_notify_manager:
            return manager.partner_id, True
        return self.env["res.partner"], False

    @api.model
    def _gen_installed(self):
        return "claude.chat.session" in self.env and "bf.ai.bridge" in self.env

    @api.model
    def _plan_available(self):
        """Gen installed, and the database allowed to use the plan (`PLAN_ALLOWED_PARAM`)."""
        allowed = self.env["ir.config_parameter"].sudo().get_param(PLAN_ALLOWED_PARAM, "False")
        return allowed == "True" and self._gen_installed()

    @api.model
    def _plan_wanted(self, task):
        return bool(self._company_of(task).unblock_gen_plan) and self._plan_available()

    # ------------------------------------------------------------------
    # Preparation (thread, then cron for what the thread dropped)
    # ------------------------------------------------------------------
    def _claim(self):
        """Take the notice for preparation; False if someone else has it."""
        self.ensure_one()
        self.env.cr.execute("""
            UPDATE bf_task_unblock_event
               SET state = 'preparing', claimed_at = (now() at time zone 'UTC')
             WHERE id = %s AND state = 'pending'
         RETURNING claimed_at
        """, [self.id])
        row = self.env.cr.fetchone()
        self.invalidate_recordset(["state", "claimed_at"])
        return row and row[0]

    def _bridge_target(self):
        """(socket, tenant, transport) of the bridge, or None if it cannot be asked."""
        if not self._gen_installed():
            return None
        try:
            from odoo.addons.bf_ai_bridge.tools import transport
        except ImportError:
            return None
        bridge = self.env["bf.ai.bridge"]
        try:
            if not bridge.available():
                return None
            # No default tenant, on purpose (see bf_ai_bridge): a system that
            # does not say who it is gets no plan rather than someone else's.
            return bridge.socket_path(), bridge.tenant(), transport
        except Exception:  # noqa: BLE001
            _logger.warning("Unblock plan: bridge not configured", exc_info=True)
            return None

    def _gen_allowed(self, user):
        """Gen is open to every internal user while it is switched on."""
        enabled = self.env["ir.config_parameter"].sudo().get_param(
            "bf_claude_chat.enabled", "True") == "True"
        return enabled and not user.share and user.active

    def _eligible(self):
        """(partner, user) of the recipients who would get a plan."""
        self.ensure_one()
        pairs = []
        for partner in self.partner_ids:
            user = partner.user_ids.filtered(lambda u: not u.share)[:1]
            if user and self._gen_allowed(user):
                pairs.append((partner, user))
        return pairs

    def _plan_requests(self):
        """What the pass reads, one request per recipient allowed to use Gen.

        Returns (requests, eligible partner ids). A recipient who is eligible
        but gets no plan sees a line saying Gen could not prepare one.
        """
        self.ensure_one()
        requests, eligible = [], []
        for partner, user in self._eligible():
            eligible.append(partner.id)
            try:
                payload = self._plan_context(user)
            except Exception:  # noqa: BLE001
                _logger.warning("Unblock plan: context for %s failed", user.id, exc_info=True)
                payload = None
            if payload:
                requests.append({"partner_id": partner.id, "payload": payload})
        return requests, eligible

    def _plan_context(self, user):
        """The task as `user` sees it: nothing they could not open themselves."""
        env = self._viewer_env(user)
        env = env(context=dict(env.context, lang=user.lang or "en_US", tz=user.tz or "UTC"))
        task = self.task_id.with_env(env)
        try:
            task.check_access("read")
        except AccessError:
            return None
        state_labels = dict(task._fields["state"]._description_selection(env))
        data = {
            "lang": user.lang or "en_US",
            "recipient": user.name,
            "today": fields.Date.context_today(task).isoformat(),
            "unblock": {
                "kind": self.kind,
                # Named only when the notice names them too (some blocker visible).
                "by": "",
            },
            "task": self._task_facts(task, state_labels),
            "blockers": [],
            "thread": self._thread(env, task, MAX_MESSAGES),
            "activities": self._activities(env, task),
            "unblocks_next": [],
        }
        for other in self.other_task_ids.with_env(env):
            try:
                other.check_access("read")
            except AccessError:
                continue
            data["blockers"].append({
                "name": other.display_name,
                "state": state_labels.get(other.state, other.state),
                "thread": self._thread(env, other, MAX_BLOCKER_MESSAGES),
            })
        for nxt in task.dependent_ids:
            try:
                nxt.check_access("read")
            except AccessError:
                continue
            data["unblocks_next"].append({
                "name": nxt.display_name,
                "state": state_labels.get(nxt.state, nxt.state),
            })
        if data["blockers"]:
            data["unblock"]["by"] = self.user_id.sudo().name or ""
        data["blockers"] = data["blockers"][:MAX_BLOCKERS]
        data["unblocks_next"] = data["unblocks_next"][:MAX_BLOCKERS]
        # Bounded in size, as the JSON the bridge receives: the oldest messages
        # of the task first, then those of the blockers, then the description.
        size = lambda: len(json.dumps(data, ensure_ascii=False))  # noqa: E731
        while size() > MAX_PAYLOAD and data["thread"]:
            data["thread"].pop()
        for blocker in data["blockers"]:
            while size() > MAX_PAYLOAD and blocker["thread"]:
                blocker["thread"].pop()
        if size() > MAX_PAYLOAD:
            excess = size() - MAX_PAYLOAD
            description = data["task"]["description"]
            data["task"]["description"] = description[:max(0, len(description) - excess)]
        return data

    @api.model
    def _task_facts(self, task, state_labels):
        facts = {
            "id": task.id,
            "name": task.name,
            "project": task.project_id.sudo().display_name or "",
            # Names read as the chatter shows them: a contact of another company
            # would otherwise cost the whole plan an AccessError.
            "client": (task.partner_id.sudo().display_name
                       or task.project_id.partner_id.sudo().display_name or ""),
            "stage": task.stage_id.display_name or "",
            "state": state_labels.get(task.state, task.state),
            "deadline": (fields.Datetime.context_timestamp(task, task.date_deadline).date().isoformat()
                         if task.date_deadline else ""),
            "planned_hours": task.allocated_hours or 0.0,
            "high_priority": task.priority == "1",
            "tags": task.tag_ids.mapped("name")[:10],
            "description": (html2plaintext(task.description or "") or "")[:MAX_DESCRIPTION],
        }
        if "effective_hours" in task._fields:
            facts["logged_hours"] = task.effective_hours or 0.0
        return facts

    @api.model
    def _thread(self, env, record, limit):
        """The last messages a person wrote on the record, newest first."""
        messages = env["mail.message"].search([
            ("model", "=", record._name), ("res_id", "=", record.id),
            ("message_type", "in", ("comment", "email", "email_outgoing")),
        ], order="id desc", limit=limit)
        lines = []
        for message in messages:
            text = (html2plaintext(message.body or "") or "").strip()
            if not text:
                continue
            day = fields.Datetime.context_timestamp(record, message.date).date().isoformat()
            author = message.sudo().author_id
            name = author.name or message.sudo().email_from or ""
            # An incoming email is matched to its author by address alone, which
            # nothing authenticates: said as such. Otherwise, said plainly when
            # the author is not someone of the company.
            if message.message_type == "email":
                name = "%s (by email)" % name
            elif not author.with_context(active_test=False).user_ids.filtered(
                    lambda u: not u.share):
                name = "%s (external)" % name
            lines.append("%s · %s: %s" % (day, name, text[:MAX_MESSAGE]))
        return lines

    @api.model
    def _activities(self, env, task):
        activities = env["mail.activity"].search([
            ("res_model", "=", task._name), ("res_id", "=", task.id)])
        return [{
            "type": (activity.activity_type_id.name or "")[:100],
            "summary": (activity.summary or "")[:300],
            "due": activity.date_deadline.isoformat() if activity.date_deadline else "",
            "for": activity.user_id.sudo().name or "",
        } for activity in activities[:MAX_ACTIVITIES]]

    @api.model
    def _cron_send_late(self):
        """Send without a plan what the threads dropped, and purge old notices.

        The recipients who would have had a plan read that Gen could not
        prepare one. A notice that fails `MAX_ATTEMPTS` times is marked
        failed instead of being retried forever.
        """
        now = fields.Datetime.now()
        late = self.search([
            ("state", "=", "pending"), ("with_plan", "=", True),
            ("create_date", "<", now - LATE_AFTER)])
        stale = self.search([
            ("state", "=", "preparing"), ("claimed_at", "<", now - STALE_AFTER)])
        for event in late | stale:
            try:
                with self.env.cr.savepoint():
                    plans = dict.fromkeys(partner.id for partner, _user in event._eligible())
                    event._send(
                        plans, "late",
                        token=event.claimed_at if event.state == "preparing" else None)
            except Exception:  # noqa: BLE001
                _logger.exception("Unblock notice %s: late send failed", event.id)
                with self.env.cr.savepoint():
                    self.env.cr.execute("""
                        UPDATE bf_task_unblock_event
                           SET attempts = COALESCE(attempts, 0) + 1,
                               state = CASE WHEN COALESCE(attempts, 0) + 1 >= %s
                                            THEN 'failed' ELSE state END
                         WHERE id = %s
                    """, [MAX_ATTEMPTS, event.id])
                event.invalidate_recordset(["attempts", "state"])
        self.search([("state", "in", ("sent", "failed")),
                     ("write_date", "<", now - KEEP_FOR)]).unlink()

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------
    def _layout(self):
        for xmlid in self._LAYOUTS:
            if self.env.ref(xmlid, raise_if_not_found=False):
                return xmlid
        return "mail.mail_notification_layout"

    def _send(self, plans, outcome, token=None):
        """Mark the notice sent and send it, once, whoever gets there first.

        `plans` maps the partner ids that were eligible for a plan to their
        plan, or to None when it could not be prepared. `token` is the claim
        of the thread that prepared it; without one, only a notice still
        waiting can be sent.
        """
        self.ensure_one()
        if token is None:
            self.env.cr.execute("""
                UPDATE bf_task_unblock_event
                   SET state = 'sent', sent_at = (now() at time zone 'UTC'), plan_outcome = %s
                 WHERE id = %s AND state = 'pending' RETURNING id
            """, [outcome or None, self.id])
        else:
            self.env.cr.execute("""
                UPDATE bf_task_unblock_event
                   SET state = 'sent', sent_at = (now() at time zone 'UTC'), plan_outcome = %s
                 WHERE id = %s AND state = 'preparing' AND claimed_at = %s RETURNING id
            """, [outcome or None, self.id, token])
        if not self.env.cr.fetchone():
            return False
        self.invalidate_recordset(["state", "sent_at", "plan_outcome"])
        odoobot = self.env.ref("base.partner_root", raise_if_not_found=False)
        author_id = odoobot.id if odoobot else self.env.company.partner_id.id
        failures = 0
        for partner in self.partner_ids:
            tz = partner.tz or partner.user_ids[:1].tz or "UTC"
            # One recipient's failure does not cost the others their notice.
            try:
                with self.env.cr.savepoint():
                    self.with_context(lang=partner.lang or "en_US", tz=tz)._notify_partner(
                        partner, plans, author_id)
            except Exception:
                failures += 1
                _logger.exception("Unblock notice %s: not sent to partner %s", self.id, partner.id)
        if failures and failures == len(self.partner_ids):
            raise UserError("Unblock notice %s reached nobody" % self.id)
        return True

    def _locale(self):
        """The recipient's locale, read from the context.

        Not through `res.lang`: a language that is not active in the database
        (en_US on a database where en_CA serves) would fall back on the
        company's, and an English notice would carry French dates.
        """
        return babel_locale_parse(self.env.context.get("lang") or get_lang(self.env).code)

    def _local(self, value):
        tz = pytz.timezone(self.env.context.get("tz") or "UTC")
        return pytz.utc.localize(value).astimezone(tz)

    def _when(self, value):
        """« 10 oct., 13 h 15 » in the language and time zone of the context."""
        local, locale = self._local(value), self._locale()
        day = babel.dates.format_skeleton("MMMd", local, locale=locale)
        hour = babel.dates.format_time(local, format="short", locale=locale)
        return "%s, %s" % (day, hour)

    def _deadline(self, task):
        if not task.date_deadline:
            return ""
        return babel.dates.format_skeleton(
            "yMMMd", self._local(task.date_deadline), locale=self._locale())

    def _hours(self, value):
        return "%s h" % babel.numbers.format_decimal(value, format="#,##0.##", locale=self._locale())

    def _viewer_env(self, user):
        """`user`'s environment, without the companies active for whoever acts.

        🔴 The context of the person closing the blocker carries their
        `allowed_company_ids`; checked under the recipient, a company the
        recipient does not have raised an AccessError and the notice was lost
        for everyone. Without the key, Odoo takes all the recipient's companies.
        """
        context = {key: value for key, value in self.env.context.items()
                   if key != "allowed_company_ids"}
        return self.env(user=user.id, su=False, context=context)

    def _visible_to(self, partner, tasks):
        """The tasks `partner` can open; none if they have no user to open them with."""
        viewer = (partner.user_ids.filtered(lambda u: not u.share)[:1]
                  or partner.user_ids[:1])
        if not viewer:
            return tasks.browse()
        env = self._viewer_env(viewer)
        visible = tasks.browse()
        for task in tasks:
            try:
                if task.with_env(env).has_access("read"):
                    visible |= task
            except AccessError:
                continue
        return visible

    def _notify_partner(self, partner, plans, author_id):
        """Render the notice for one recipient, in their language and time zone."""
        task = self.task_id.with_env(self.env)
        involved = self.other_task_ids.with_env(self.env)
        # 🔴 Only what the recipient can open is named, linked, or said to
        # belong to a project or a client; the rest is counted.
        others = self._visible_to(partner, involved)
        state_labels = dict(task._fields["state"]._description_selection(self.env))
        client = task.project_id.partner_id
        other_info = [{
            "name": other.display_name,
            "url": other._notify_get_action_link("view"),
            "state_label": state_labels.get(other.state, other.state),
            # Said only when it differs from the unblocked task's.
            "project_name": (other.project_id.display_name
                             if other.project_id != task.project_id else ""),
            "client_name": (other.project_id.partner_id.name
                            if other.project_id.partner_id
                            and other.project_id.partner_id != client else ""),
        } for other in others]
        planned = self._hours(task.allocated_hours) if task.allocated_hours else ""
        body = self.env["ir.qweb"]._render(
            "bf_task_unblock_notify.task_unblocked_notification",
            {
                "task": task,
                "kind": self.kind,
                "other_info": other_info,
                "hidden_count": len(involved) - len(others),
                "closing_user": self.user_id.name or "",
                "closing_time": self._when(self.create_date),
                "access_link": task._notify_get_action_link("view"),
                "to_manager": self.to_manager,
                "deadline": self._deadline(task),
                "planned": planned,
                "high_priority": task.priority == "1",
                "plan": plans.get(partner.id),
                "plan_missing": partner.id in plans and not plans.get(partner.id),
            },
            minimal_qcontext=True,
        )
        body = self.env["mail.render.mixin"]._replace_local_links(body)
        task.with_context(mail_notify_author=True).message_notify(
            subject=self.env._("Task unblocked: %s", task.display_name),
            body=body,
            partner_ids=partner.ids,
            author_id=author_id,
            email_layout_xmlid=self._layout(),
            model_description=self.env["ir.model"]._get("project.task").display_name,
            mail_auto_delete=False,
            # The layout signs for the company; the author's own signature
            # was the « -- System » at the bottom of every notice.
            email_add_signature=False,
        )
