import hmac
import imaplib
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class BfWebmailController(http.Controller):

    @http.route("/bf_webmail/config", type="json", auth="user")
    def get_config(self):
        ICP = request.env["ir.config_parameter"].sudo()
        return {
            "webmail_url": ICP.get_param("bf_webmail.webmail_url", ""),
        }

    @http.route("/bf_webmail/unread", type="json", auth="user")
    def get_unread_count(self):
        ICP = request.env["ir.config_parameter"].sudo()
        host = ICP.get_param("bf_webmail.imap_host", "")
        port = int(ICP.get_param("bf_webmail.imap_port", "993"))
        user = ICP.get_param("bf_webmail.imap_user", "")
        from ..models.res_config_settings import ResConfigSettings
        encrypted = ICP.get_param("bf_webmail.imap_password_encrypted", "")
        password = ResConfigSettings._decrypt_imap_password(request.env, encrypted)

        if not all([host, user, password]):
            return {"unread": 0, "configured": False}

        mail = None
        try:
            mail = imaplib.IMAP4_SSL(host, port, timeout=5)
            mail.login(user, password)
            mail.select("INBOX", readonly=True)
            _, data = mail.search(None, "UNSEEN")
            unread = len(data[0].split()) if data[0] else 0
            return {"unread": unread, "configured": True}
        except Exception as e:
            _logger.warning("bf_webmail: IMAP check failed: %s", e)
            return {"unread": 0, "configured": True, "error": True}
        finally:
            if mail:
                try:
                    mail.logout()
                except Exception:
                    pass
