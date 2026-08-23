import base64
import threading
import time
from collections import defaultdict
import logging
import re

import werkzeug

from odoo import _, http
from odoo.http import request
from odoo.tools import plaintext2html

_logger = logging.getLogger(__name__)

# Basic RFC-5322-ish, good enough to reject obvious garbage. Server-side defense
# in depth — the input has type="email" client-side too.
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

# Per-attachment hard cap (MB). Defaults to ir.config_parameter
# bf_helpdesk.public_form_max_attachment_mb (default 10 MB).
ATTACHMENT_MB_PARAM = "bf_helpdesk.public_form_max_attachment_mb"
# Total request payload cap (MB) — sum of all attachments.
ATTACHMENT_TOTAL_MB_PARAM = "bf_helpdesk.public_form_max_total_attachment_mb"

# Block obvious dangerous extensions (defense in depth — most should be
# stopped at the proxy too).
#
# ⚠ Kept in step with bf_survey_upload.DENY_EXTENSIONS and
# bf_securetransfer._FALLBACK_DENY_EXTENSIONS. This list used to cover
# executables and scripts but NOT the browser-renderable formats those two
# classify first, under "Browser-renderable / XSS vectors" — yet the file lands
# here from an anonymous POST on /support/<slug>, and ir.attachment derives the
# mimetype from the name, so an .html arrived as text/html. Any addition below
# belongs in all three lists.
# Per-IP throttle on unauthenticated ticket creation (anti spam + auto-ack
# mail-bomb). Keyed on the ProxyFix-corrected client IP (proxy_mode = True).
_submit_lock = threading.Lock()
_submit_data = defaultdict(list)  # IP -> [timestamps of accepted submissions]
_SUBMIT_MAX = 5        # max submissions
_SUBMIT_WINDOW = 600   # per 10 minutes / IP


def _client_ip():
    try:
        return request.httprequest.remote_addr or "unknown"
    except Exception:
        return "unknown"


def _check_submit_rate_limit():
    """Return True and record the hit if this IP may submit another ticket."""
    ip = _client_ip()
    now = time.monotonic()
    with _submit_lock:
        cutoff = now - _SUBMIT_WINDOW
        _submit_data[ip] = [t for t in _submit_data[ip] if t > cutoff]
        if len(_submit_data[ip]) >= _SUBMIT_MAX:
            return False
        _submit_data[ip].append(now)
        return True

BLOCKED_EXTENSIONS = {
    # Browser-renderable / XSS vectors
    ".html", ".htm", ".xhtml", ".svg", ".svgz", ".mhtml", ".mht",
    ".shtml", ".xml", ".xsl", ".xslt",
    ".js", ".mjs", ".wasm",
    # Server-side execution
    ".php", ".php3", ".php4", ".php5", ".php7", ".phps", ".phtml", ".pht",
    ".asp", ".aspx", ".ashx", ".cer",
    ".jsp", ".jspx", ".jsv", ".jspf",
    ".cgi", ".pl", ".rb", ".lua",
    ".py", ".pyc", ".pyo",
    ".swf",
    ".class", ".jar", ".war", ".ear",
    # Native executables and scripts
    ".exe", ".bat", ".cmd", ".com", ".scr", ".pif",
    ".sh", ".bash", ".zsh", ".msi", ".dll",
    ".vbs", ".vbe", ".jse", ".wsf", ".wsh", ".hta",
    ".ps1", ".ps1xml", ".psm1",
    ".lnk", ".reg", ".chm",
    # Web server config honored if dropped in a served path
    ".htaccess", ".htpasswd",
}


class BFHelpdeskPublicForm(http.Controller):
    """Per-team public support form at /support/<slug>.

    Anonymous-friendly: visitors enter name/email; the ticket is created
    with channel = web (helpdesk_mgmt.helpdesk_ticket_channel_web) and the
    matching team_id.
    """

    def _team_by_slug(self, slug):
        if not slug:
            return False
        return request.env["helpdesk.ticket.team"].sudo().search([
            ("slug", "=", slug),
            ("active", "=", True),
            ("public_form_enabled", "=", True),
        ], limit=1)

    def _attachment_limits(self):
        IConf = request.env["ir.config_parameter"].sudo()
        try:
            per_file_mb = int(IConf.get_param(ATTACHMENT_MB_PARAM, "10"))
        except (TypeError, ValueError):
            per_file_mb = 10
        try:
            total_mb = int(IConf.get_param(ATTACHMENT_TOTAL_MB_PARAM, "25"))
        except (TypeError, ValueError):
            total_mb = 25
        return per_file_mb * 1024 * 1024, total_mb * 1024 * 1024

    def _is_extension_blocked(self, filename):
        name = (filename or "").lower()
        return any(name.endswith(ext) for ext in BLOCKED_EXTENSIONS)

    @http.route(["/support/<string:slug>"], type="http", auth="public", website=True, sitemap=True)
    def public_form(self, slug, **kw):
        team = self._team_by_slug(slug)
        if not team:
            return request.not_found()
        prefill = {
            "name": "",
            "email": "",
            "subject": "",
            "description": "",
        }
        if not request.env.user._is_public():
            prefill["name"] = request.env.user.partner_id.name or ""
            prefill["email"] = request.env.user.partner_id.email or ""
        return request.render("bf_helpdesk.public_form_page", {
            "team": team,
            "prefill": prefill,
            "tags": team.public_form_tag_ids,
            "max_upload_size": request.env["ir.http"].session_info().get(
                "max_file_upload_size", 25 * 1024 * 1024,
            ),
        })

    @http.route(["/support/<string:slug>/submit"], type="http", auth="public",
                website=True, methods=["POST"], csrf=True)
    def public_form_submit(self, slug, **kw):
        team = self._team_by_slug(slug)
        if not team:
            return request.not_found()

        # --- Spam honeypot: hidden 'website' field. Real users leave it empty;
        # naive bots fill all visible-looking inputs. Silently 200 + thanks
        # without creating anything (don't tip the bot off).
        if (kw.get("website") or "").strip():
            _logger.info(
                "bf_helpdesk: honeypot triggered on /support/%s, dropping submission",
                slug,
            )
            return request.render("bf_helpdesk.public_form_thanks", {
                "team": team, "ticket": False,
            })

        # Per-IP throttle: cap ticket creation / auto-ack emails. Renders the
        # same thanks page so a real over-eager user isn't shown an error and a
        # bot gets no signal.
        if not _check_submit_rate_limit():
            _logger.info(
                "bf_helpdesk: rate limit hit on /support/%s from %s",
                slug, _client_ip(),
            )
            return request.render("bf_helpdesk.public_form_thanks", {
                "team": team, "ticket": False,
            })

        subject = (kw.get("subject") or "").strip()
        description = (kw.get("description") or "").strip()
        name = (kw.get("name") or "").strip()
        email = (kw.get("email") or "").strip()

        # Tag selection (optional or required per team config)
        tag_id_raw = (kw.get("tag_id") or "").strip()
        selected_tag_id = False
        if tag_id_raw:
            try:
                tid = int(tag_id_raw)
                if tid in team.public_form_tag_ids.ids:
                    selected_tag_id = tid
            except ValueError:
                selected_tag_id = False

        # Validation
        error = None
        if not subject or not description or not email:
            error = _("Sujet, description et courriel sont requis.")
        elif not EMAIL_RE.match(email):
            error = _("Format de courriel invalide.")
        elif team.public_form_tag_required and not selected_tag_id:
            error = _("Veuillez sélectionner une catégorie.")

        if error:
            return request.render("bf_helpdesk.public_form_page", {
                "team": team,
                "tags": team.public_form_tag_ids,
                "prefill": {"name": name, "email": email,
                            "subject": subject, "description": description,
                            "tag_id": tag_id_raw},
                "error": error,
                "max_upload_size": request.env["ir.http"].session_info().get(
                    "max_file_upload_size", 25 * 1024 * 1024,
                ),
            })

        # Validate attachments BEFORE creating the ticket
        per_file_max, total_max = self._attachment_limits()
        attachment_files = request.httprequest.files.getlist("attachment")
        accepted = []  # list of (filename, data)
        total_size = 0
        for c_file in attachment_files:
            if not c_file or not c_file.filename:
                continue
            if self._is_extension_blocked(c_file.filename):
                _logger.warning(
                    "bf_helpdesk: blocked attachment extension on /support/%s: %s",
                    slug, c_file.filename,
                )
                return request.render("bf_helpdesk.public_form_page", {
                    "team": team,
                    "tags": team.public_form_tag_ids,
                    "prefill": {"name": name, "email": email,
                                "subject": subject, "description": description,
                                "tag_id": tag_id_raw},
                    "error": _("Type de fichier non autorisé : %s") % c_file.filename,
                    "max_upload_size": per_file_max,
                })
            data = c_file.read()
            if not data:
                continue
            if len(data) > per_file_max:
                return request.render("bf_helpdesk.public_form_page", {
                    "team": team,
                    "tags": team.public_form_tag_ids,
                    "prefill": {"name": name, "email": email,
                                "subject": subject, "description": description,
                                "tag_id": tag_id_raw},
                    "error": _("Fichier trop volumineux (max %s Mo) : %s") % (
                        per_file_max // (1024 * 1024), c_file.filename,
                    ),
                    "max_upload_size": per_file_max,
                })
            total_size += len(data)
            if total_size > total_max:
                return request.render("bf_helpdesk.public_form_page", {
                    "team": team,
                    "tags": team.public_form_tag_ids,
                    "prefill": {"name": name, "email": email,
                                "subject": subject, "description": description,
                                "tag_id": tag_id_raw},
                    "error": _("Pièces jointes totales trop volumineuses (max %s Mo).") % (
                        total_max // (1024 * 1024),
                    ),
                    "max_upload_size": per_file_max,
                })
            accepted.append((c_file.filename, data))

        Ticket = request.env["helpdesk.ticket"].sudo()
        partner_id = False
        if not request.env.user._is_public():
            partner_id = request.env.user.partner_id.id

        channel = request.env.ref(
            "helpdesk_mgmt.helpdesk_ticket_channel_web", raise_if_not_found=False,
        )

        vals = {
            "name": subject,
            "description": plaintext2html(description),
            "partner_name": name or email,
            "partner_email": email,
            "team_id": team.id,
            "channel_id": channel.id if channel else False,
            "company_id": team.company_id.id or request.env.company.id,
        }
        if partner_id:
            vals["partner_id"] = partner_id
        if selected_tag_id:
            vals["tag_ids"] = [(4, selected_tag_id)]
        # Set initial stage to ensure mail tracking template fires
        vals["stage_id"] = team._get_applicable_stages()[:1].id

        ticket = Ticket.create(vals)

        # Attach pre-validated files
        for filename, data in accepted:
            request.env["ir.attachment"].sudo().create({
                "name": filename,
                "datas": base64.b64encode(data),
                "res_model": "helpdesk.ticket",
                "res_id": ticket.id,
            })

        if partner_id:
            ticket.message_subscribe(partner_ids=[partner_id])

        # Auto-acknowledgement (per-team toggle) — fires immediate "we received"
        # email to the requester so they have a paper trail in their inbox.
        if team.public_form_auto_ack:
            ack_template = request.env.ref(
                "bf_helpdesk.mail_template_public_form_ack",
                raise_if_not_found=False,
            )
            if ack_template:
                try:
                    ack_template.sudo().send_mail(
                        ticket.id, force_send=False,
                        email_layout_xmlid="mail.mail_notification_light",
                    )
                except Exception:
                    _logger.exception(
                        "bf_helpdesk: auto-ack send failed for ticket %s", ticket.id,
                    )

        # Redirect logged-in users to portal; anonymous to a thank-you page
        if partner_id:
            return werkzeug.utils.redirect(f"/my/ticket/{ticket.id}")
        return request.render("bf_helpdesk.public_form_thanks", {
            "team": team,
            "ticket": ticket,
        })
