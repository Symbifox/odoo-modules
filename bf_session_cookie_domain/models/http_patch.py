import logging

from odoo.http import _Response, FutureResponse, request

_logger = logging.getLogger(__name__)

# The apex domain whose subdomains should share one session cookie, e.g.
# ``example.com`` so that ``odoo.example.com`` and ``www.example.com`` stop
# authenticating separately. Read per request from ir.config_parameter.
#
# ⚠️ Empty by design: with no value the patch is a NO-OP and Odoo's cookie
# handling is untouched. A shared session cookie widens where a session is
# valid, which is a security-relevant choice — it is opted into per deployment,
# never assumed from the request host. (Deriving it by stripping the leftmost
# label would guess wrong on ``odoo.example.co.uk`` and quietly hand the session
# to a whole public suffix.)
PARAM_ROOT_DOMAIN = "bf_session_cookie_domain.root_domain"
TARGET_KEY = "session_id"


def _root_domain():
    """The configured apex domain, or "" when the patch should stand aside.

    Called from inside ``set_cookie``, so a request is in flight — but not
    necessarily an authenticated one, and not necessarily one with a usable
    environment (error paths, very early responses). Anything unexpected here
    must mean "do nothing", never an exception: raising would break the
    response Odoo is in the middle of writing.
    """
    try:
        value = request.env["ir.config_parameter"].sudo().get_param(
            PARAM_ROOT_DOMAIN, "")
    except Exception:
        return ""
    return (value or "").strip().lower().lstrip(".")


def _wrap(orig):
    def set_cookie(self, key, value="", max_age=None, expires=-1, path="/",
                   domain=None, secure=False, httponly=False, samesite=None,
                   cookie_type="required"):
        if key == TARGET_KEY and not domain:
            root = _root_domain()
            if root:
                try:
                    host = (request.httprequest.host or "").split(":")[0].lower()
                except Exception:
                    host = ""
                if host == root or host.endswith("." + root):
                    # Evict any legacy host-only session_id cookie left over
                    # from before this addon was deployed. Sent as a same-name
                    # Set-Cookie with no Domain attribute and Max-Age=0; the
                    # browser deletes the host-only cookie that was shadowing
                    # the new domain-wide one.
                    orig(self, key, value="", max_age=0, expires=0, path=path,
                         domain=None, secure=secure, httponly=httponly,
                         samesite=samesite, cookie_type=cookie_type)
                    domain = "." + root
        return orig(self, key, value=value, max_age=max_age, expires=expires,
                    path=path, domain=domain, secure=secure, httponly=httponly,
                    samesite=samesite, cookie_type=cookie_type)
    set_cookie._bf_session_cookie_patched = True
    return set_cookie


if not getattr(_Response.set_cookie, "_bf_session_cookie_patched", False):
    _Response.set_cookie = _wrap(_Response.set_cookie)
    _logger.info("bf_session_cookie_domain: patched _Response.set_cookie")

if not getattr(FutureResponse.set_cookie, "_bf_session_cookie_patched", False):
    FutureResponse.set_cookie = _wrap(FutureResponse.set_cookie)
    _logger.info("bf_session_cookie_domain: patched FutureResponse.set_cookie")
