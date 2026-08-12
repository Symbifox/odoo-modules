"""Portal page /my/feedback: read own feedback, drop an ad hoc comment.

Same containment pattern as bf_meeting_portal/controllers/portal.py:
the templates never receive records, only whitelisted dictionaries, and
every query is bound to the logged-in user's commercial partner. Reads
go through sudo() because the strict domain, not the ACL, is what bounds
them (the portal ACL/rule shipped by this module only covers direct RPC
access, which mirrors the same domain).

The write path is a single ad hoc comment: created with sudo() but with
every field forced server-side (kind 'verbatim', source 'other', partner
forced to the user's own partner). The only user-controlled value is the
comment text, capped in length and escaped at render time by QWeb.

No outbound solicitation of any kind lives here.
"""
import logging

from odoo import fields, http
from odoo.http import request
from odoo.tools import format_date

from odoo.addons.portal.controllers.portal import CustomerPortal

_logger = logging.getLogger(__name__)

# Hard cap on the ad hoc comment length (a fields.Text has none).
MAX_COMMENT_LENGTH = 5000

# How many entries the page shows at most, newest first.
PAGE_LIMIT = 100

# Error codes the list page knows how to display. Anything else coming
# back in the query string is ignored, so the template never echoes
# attacker-controlled text.
KNOWN_ERRORS = ("empty", "save")


def _feedback_domain():
    """Feedback visible to the logged-in portal user.

    Their commercial partner and all its children (a colleague's answer
    about the same company is shared context, not a secret), internal
    360 entries always excluded. Same domain as the ir.rule shipped in
    security/bf_cx_portal_security.xml.
    """
    commercial = request.env.user.partner_id.commercial_partner_id
    return [
        ("partner_id", "child_of", commercial.id),
        ("kind", "!=", "internal"),
    ]


def _fmt_score(value):
    """Render 9.0 as '9' and 4.5 as '4,5' (client-facing, FR locale)."""
    if value == int(value):
        return "%d" % value
    return ("%.1f" % value).replace(".", ",")


class PortalCxFeedback(CustomerPortal):

    # ------------------------------------------------------------------
    # Accueil du portail
    # ------------------------------------------------------------------

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if "cx_feedback_count" in counters:
            # sudo(): the domain above is what bounds the count, and the
            # home page must render even if the count fails.
            try:
                values["cx_feedback_count"] = (
                    request.env["bf.cx.feedback"]
                    .sudo()
                    .search_count(_feedback_domain())
                )
            except Exception:  # noqa: BLE001 - never break the portal home
                _logger.exception("bf_cx_portal: home counter failed")
                values["cx_feedback_count"] = 0
        return values

    # ------------------------------------------------------------------
    # Liste blanche de rendu
    # ------------------------------------------------------------------

    def _feedback_ctx(self, rec, kind_labels):
        """Whitelisted dictionary handed to the template.

        Deliberately excludes program, wave, assignee, state and every
        other operational field: the client sees what they said and how
        it was scored, nothing about how it is being worked internally.
        """
        score = ""
        if rec.kind in ("nps", "csat") and rec.score_max:
            score = "%s / %s" % (_fmt_score(rec.score), _fmt_score(rec.score_max))
        return {
            "date": format_date(request.env, rec.date, date_format="d MMMM y"),
            "kind": kind_labels.get(rec.kind, rec.kind),
            "key": rec.kind or "",
            "score": score,
            "comment": rec.comment or "",
        }

    # ------------------------------------------------------------------
    # Liste : /my/feedback
    # ------------------------------------------------------------------

    @http.route(["/my/feedback"], type="http", auth="user", website=True)
    def portal_my_cx_feedback(self, **kw):
        Feedback = request.env["bf.cx.feedback"].sudo()
        domain = _feedback_domain()
        total = Feedback.search_count(domain)
        records = Feedback.search(
            domain, order="date desc, id desc", limit=PAGE_LIMIT
        )
        # Translated labels of the kind field, same source as the model's
        # own _compute_display_name.
        kind_labels = dict(
            Feedback._fields["kind"]._description_selection(request.env)
        )
        error = kw.get("error")
        values = self._prepare_portal_layout_values()
        values.update({
            "feedbacks": [
                self._feedback_ctx(r, kind_labels) for r in records
            ],
            "total": total,
            "truncated": total > PAGE_LIMIT,
            "page_limit": PAGE_LIMIT,
            "thanks": kw.get("thanks") == "1",
            "error": error if error in KNOWN_ERRORS else "",
            "page_name": "cx_feedback",
            "default_url": "/my/feedback",
        })
        return request.render("bf_cx_portal.portal_my_cx_feedback", values)

    # ------------------------------------------------------------------
    # Soumission d'un commentaire ad hoc
    # ------------------------------------------------------------------

    @http.route(["/my/feedback/submit"], type="http", auth="user",
                methods=["POST"], website=True)
    def portal_my_cx_feedback_submit(self, **post):
        """Create a verbatim feedback from the portal form.

        Field whitelist: only the comment text is read from the request.
        Everything else (partner, kind, source, date) is forced here, so
        a crafted POST cannot plant a score, a program or someone else's
        partner. QWeb escapes the text at render time; it is stored as
        plain text in a fields.Text.
        """
        comment = (post.get("comment") or "").strip()
        if not comment:
            return request.redirect("/my/feedback?error=empty")
        comment = comment[:MAX_COMMENT_LENGTH]
        partner = request.env.user.partner_id
        try:
            Feedback = request.env["bf.cx.feedback"].sudo()
            today = fields.Date.context_today(Feedback)
            # Anti double-submit: a double click or an F5 replay of the
            # POST would create the same verbatim twice. An identical
            # comment from the same partner the same day is dropped
            # silently (the client still gets the thank-you message).
            already = Feedback.search_count([
                ("partner_id", "=", partner.id),
                ("kind", "=", "verbatim"),
                ("source", "=", "other"),
                ("date", "=", today),
                ("comment", "=", comment),
            ])
            if not already:
                Feedback.create({
                    "partner_id": partner.id,
                    "kind": "verbatim",
                    "source": "other",
                    "date": today,
                    "comment": comment,
                })
        except Exception:  # noqa: BLE001 - never crash the portal on a comment
            _logger.exception(
                "bf_cx_portal: ad hoc comment failed for partner %s",
                partner.id,
            )
            return request.redirect("/my/feedback?error=save")
        return request.redirect("/my/feedback?thanks=1")
