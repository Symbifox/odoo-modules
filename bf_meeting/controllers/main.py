import hmac
import logging
import threading
import time
from collections import defaultdict

from markupsafe import Markup, escape

from odoo.http import Controller, request, route

_logger = logging.getLogger(__name__)

# ── Anti-brute-force on token validation (per client IP) ──────────────────────
# Best-effort, in-process limiter: a failed-token limiter to discourage guessing
# the token space, plus a POST-volume limiter to slow spam flooding of the
# chatter / topic queue. NOTE: counters are per-worker (multiplied across Odoo
# HTTP workers, reset on worker recycle) — defence-in-depth, not a hard global
# bound; pair with proxy-level rate limiting for a strict ceiling.
_token_fail_lock = threading.Lock()
_token_fail_data = defaultdict(list)
_TOKEN_FAIL_MAX = 10
_TOKEN_FAIL_WINDOW = 300  # seconds

_post_lock = threading.Lock()
_post_data = defaultdict(list)
_POST_MAX = 5
_POST_WINDOW = 60  # seconds

# Cap distinct tracked IPs so a flood of (real or spoofed) source IPs cannot grow
# the per-process limiter dicts without bound.
_MAX_TRACKED_IPS = 10000

# Hard length caps on every free-text input from the public.
_MAX_NAME = 200
_MAX_DESC = 4000
_MAX_COMMENT = 4000
_MAX_WHO = 120
_MAX_EMAIL = 254


def _client_ip():
    """Best-effort client IP for rate limiting.

    Use the socket peer only. When the deployment runs Odoo with
    ``proxy_mode = True``, werkzeug's ProxyFix has already rewritten
    ``remote_addr`` to the real client from a *trusted* number of proxy hops, so
    we never parse X-Forwarded-For / X-Real-IP ourselves — those headers are
    attacker-controlled when the endpoint is reachable directly.
    """
    try:
        return request.httprequest.remote_addr or "unknown"
    except Exception:
        return "unknown"


def _check_token_rate_limit():
    ip = _client_ip()
    now = time.monotonic()
    with _token_fail_lock:
        cutoff = now - _TOKEN_FAIL_WINDOW
        kept = [t for t in _token_fail_data[ip] if t > cutoff]
        if kept:
            _token_fail_data[ip] = kept
        else:
            _token_fail_data.pop(ip, None)  # bound memory: drop idle IPs
        return len(kept) < _TOKEN_FAIL_MAX


def _record_token_failure():
    ip = _client_ip()
    now = time.monotonic()
    with _token_fail_lock:
        if len(_token_fail_data) > _MAX_TRACKED_IPS:
            _token_fail_data.clear()  # bound memory under a distinct-IP flood
        _token_fail_data[ip].append(now)


def _check_post_rate_limit():
    ip = _client_ip()
    now = time.monotonic()
    with _post_lock:
        if len(_post_data) > _MAX_TRACKED_IPS:
            _post_data.clear()  # bound memory under a distinct-IP flood
        cutoff = now - _POST_WINDOW
        kept = [t for t in _post_data[ip] if t > cutoff]
        if len(kept) >= _POST_MAX:
            _post_data[ip] = kept
            return False
        kept.append(now)
        _post_data[ip] = kept
        return True


class MeetingContributionController(Controller):
    """Public, tokenized contribution endpoints for a sent (draft) agenda.

    The token alone resolves the record (no record id in the URL → no IDOR
    surface). Every GET and POST re-checks ``contributions_open`` server-side
    so a stale tab cannot write after the agenda is confirmed.
    """

    def _resolve_agenda(self, token):
        """Validate token → return the matching agenda (sudo) or False.

        Lookup is by token only, compared in constant time. Forged / expired
        tokens are indistinguishable from a missing record (caller returns 404).
        """
        if not token or not _check_token_rate_limit():
            return False
        # Indexed equality lookup (access_token is indexed): loads at most one
        # row, with a constant-time compare_digest guard as belt-and-suspenders.
        match = request.env['meeting.agenda'].sudo().search(
            [('access_token', '=', token)], limit=1)
        if not (match and hmac.compare_digest(match.access_token or '', token)):
            _record_token_failure()
            return False
        return match

    # ── Allow-listed render context ───────────────────────────────────────────
    def _page_ctx(self, agenda, token, ok=None, error=None):
        """Build the minimized, allow-listed context for the contribution page.

        Nothing beyond meeting title, formatted date, objectives (plain text)
        and *accepted* topic names ever reaches the template. Internal content
        (context, preparation, live notes, tasks, attachments, participants,
        chatter, other contributors' pending proposals) is never passed.
        """
        company = agenda.company_id or request.env.company
        accepted_topics = agenda.topic_ids.filtered(
            lambda t: t.moderation_state == 'accepted').sorted('sequence')
        return {
            'token': token,
            'company': company,
            'meeting_name': agenda.name or '',
            'meeting_date': _safe_date_display(agenda),
            'objectives': agenda.objectives or '',
            'topic_names': [t.name for t in accepted_topics],
            'ok': ok,
            'error': error,
        }

    # ── Contribution page ─────────────────────────────────────────────────────
    @route('/meeting/agenda/<token>', type='http', auth='public',
           methods=['GET'], csrf=False)
    def agenda_contrib_page(self, token, **kw):
        agenda = self._resolve_agenda(token)
        if not agenda:
            return request.not_found()
        if not agenda.contributions_open:
            return request.render('bf_meeting.agenda_contrib_closed',
                                  {'company': agenda.company_id or request.env.company})
        ok = kw.get('ok') if kw.get('ok') in ('topic', 'comment') else None
        return request.render('bf_meeting.agenda_contrib_page',
                              self._page_ctx(agenda, token, ok=ok))

    # ── Propose a topic ───────────────────────────────────────────────────────
    @route('/meeting/agenda/<token>/topic', type='http', auth='public',
           methods=['POST'], csrf=False)
    def agenda_contrib_topic(self, token, **post):
        agenda = self._resolve_agenda(token)
        if not agenda:
            return request.not_found()
        if not agenda.contributions_open:
            return request.render('bf_meeting.agenda_contrib_closed',
                                  {'company': agenda.company_id or request.env.company})
        if not _check_post_rate_limit():
            return request.render('bf_meeting.agenda_contrib_page',
                                  self._page_ctx(agenda, token, error='rate'))

        name = (post.get('topic_name') or '').strip()[:_MAX_NAME]
        if not name:
            return request.render('bf_meeting.agenda_contrib_page',
                                  self._page_ctx(agenda, token, error='topic_name'))
        desc = (post.get('topic_desc') or '').strip()[:_MAX_DESC]
        who = (post.get('contributor_name') or 'Anonyme').strip()[:_MAX_WHO] or 'Anonyme'
        mail = (post.get('contributor_email') or '').strip()[:_MAX_EMAIL]
        seq = max(agenda.topic_ids.mapped('sequence') or [0]) + 10

        try:
            request.env['meeting.agenda.topic'].sudo().create({
                'agenda_id': agenda.id,
                'sequence': seq,
                'name': name,
                'description': (Markup('<p>%s</p>') % escape(desc)) if desc else False,
                'source': 'contributed',
                'moderation_state': 'pending',
                'contributor_name': who,
                'contributor_email': mail,
            })
            agenda._notify_contribution('topic', who, name)
        except Exception:  # noqa: BLE001
            _logger.exception("bf_meeting: topic contribution failed")
            return request.render('bf_meeting.agenda_contrib_page',
                                  self._page_ctx(agenda, token, error='server'))
        # Post/Redirect/Get so a refresh does not resubmit.
        return request.redirect('/meeting/agenda/%s?ok=topic' % token)

    # ── Leave a comment / note ────────────────────────────────────────────────
    @route('/meeting/agenda/<token>/comment', type='http', auth='public',
           methods=['POST'], csrf=False)
    def agenda_contrib_comment(self, token, **post):
        agenda = self._resolve_agenda(token)
        if not agenda:
            return request.not_found()
        if not agenda.contributions_open:
            return request.render('bf_meeting.agenda_contrib_closed',
                                  {'company': agenda.company_id or request.env.company})
        if not _check_post_rate_limit():
            return request.render('bf_meeting.agenda_contrib_page',
                                  self._page_ctx(agenda, token, error='rate'))

        text = (post.get('comment') or '').strip()[:_MAX_COMMENT]
        if not text:
            return request.render('bf_meeting.agenda_contrib_page',
                                  self._page_ctx(agenda, token, error='comment'))
        who = (post.get('contributor_name') or 'Anonyme').strip()[:_MAX_WHO] or 'Anonyme'
        mail = (post.get('contributor_email') or '').strip()[:_MAX_EMAIL]

        who_suffix = (Markup(' (%s)') % escape(mail)) if mail else Markup('')
        body = Markup(
            '<p><strong>Contribution — %s%s</strong></p><p>%s</p>'
        ) % (escape(who), who_suffix, escape(text))
        try:
            # author_id=False: identity is carried in the body text — we never
            # trust / forge a res.partner from public input.
            agenda.sudo().message_post(
                body=body, message_type='comment',
                subtype_xmlid='mail.mt_note', author_id=False)
            agenda._notify_contribution('comment', who, text[:80])
        except Exception:  # noqa: BLE001
            _logger.exception("bf_meeting: comment contribution failed")
            return request.render('bf_meeting.agenda_contrib_page',
                                  self._page_ctx(agenda, token, error='server'))
        return request.redirect('/meeting/agenda/%s?ok=comment' % token)


def _safe_date_display(agenda):
    """Plain, public-safe formatted date for the contribution page.

    Avoids leaking organizer tz internals — formats in the agenda company's
    default timezone via the shared bf.timezone helper, falling back to a bare
    strftime when unavailable.
    """
    if not agenda.date:
        return ''
    try:
        tz_helper = agenda.env['bf.timezone']
        tz_name = (agenda.partner_id.tz if agenda.partner_id else None) \
            or tz_helper.default_tz()
        return tz_helper.to_tz(agenda.date, tz_name).strftime('%Y-%m-%d %H:%M')
    except Exception:  # noqa: BLE001
        return agenda.date.strftime('%Y-%m-%d %H:%M')
