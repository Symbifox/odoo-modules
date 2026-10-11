"""Who is really acting: a person in their own web session, or something else."""
from odoo.exceptions import AccessError
from odoo.http import request

# Paths where an API key, not a browser session, carries the identity.
API_PATHS = ("/xmlrpc", "/jsonrpc", "/json/1", "/json/2", "/api/")
IMPERSONATION_KEY = "bf_impersonate"  # bf_impersonate's session key


def acting_in_own_web_session(env):
    """False when the call comes through an API key or under an impersonation.

    Without an HTTP request (a job, the shell, the tests), the caller is
    server code: judged by the record rules and the other guards, not here.
    """
    if not request:
        return True
    try:
        session = request.session
        path = request.httprequest.path or ""
    except (AttributeError, RuntimeError):
        return True
    if session.get(IMPERSONATION_KEY):
        return False
    if path.startswith(API_PATHS):
        return False
    return session.uid == env.uid


def impersonating():
    """True when someone is impersonating the person in this request."""
    if not request:
        return False
    try:
        return bool(request.session.get(IMPERSONATION_KEY))
    except (AttributeError, RuntimeError):
        return False


def http_session_uid():
    """The uid of the HTTP session serving this call, or None without a request (a job, a sync)."""
    if not request:
        return None
    try:
        return request.session.uid
    except (AttributeError, RuntimeError):
        return None

def own_record_vals(env, vals, field, message):
    """Write the caller as the owner of a record about to be created, or refuse.

    🔴 BEFORE the insert: a unique index answers "duplicate key" before any
    record rule is checked, which told anyone whether a colleague had turned
    the feature on, or with whom they shared their days. A record for someone
    else is refused here, whether asked in ``vals`` or by a context default;
    then the owner is written explicitly, so no default can move it.
    """
    wanted = vals.get(field, env.context.get("default_" + field, env.uid))
    if wanted != env.uid:
        raise AccessError(message)
    vals[field] = env.uid
