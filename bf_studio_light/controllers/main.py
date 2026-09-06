import logging

from psycopg2.errors import QueryCanceled

from odoo import http
from odoo.exceptions import AccessError
from odoo.http import request

_logger = logging.getLogger(__name__)


class StudioLightSmartButtonController(http.Controller):
    """JSON endpoint backing the studio_light_smart_button_widget.

    The widget calls this once per record load to fetch the count and the
    optional ``ir.actions.act_window`` dict. We deliberately keep the
    surface minimal:

    * args validated as ints, refused otherwise
    * smart-button definition fetched via sudo (admin metadata) but the
      target-model count runs as the calling user so record rules apply
    * a per-statement timeout caps DoS via expensive domains
    * any exception is masked into a uniform error envelope; the
      traceback only goes to the server log
    """

    @http.route(
        "/studio_light/smart_button/count",
        type="json",
        auth="user",
    )
    def smart_button_count(self, smart_button_id=None, source_id=None):
        try:
            sb_id = int(smart_button_id)
            src_id = int(source_id)
        except (TypeError, ValueError):
            return _envelope(error="invalid_args")
        if sb_id <= 0 or src_id <= 0:
            return _envelope(error="invalid_args")

        sb = (
            request.env["studio.light.smart.button"]
            .sudo()
            .browse(sb_id)
            .exists()
        )
        if not sb or not sb.active:
            return _envelope(error="unavailable")

        target = sb.target_model_name
        try:
            request.env[target].check_access_rights(
                "read", raise_exception=True
            )
        except AccessError:
            return _envelope(error="forbidden")
        except KeyError:
            return _envelope(error="unavailable")

        try:
            request.env.cr.execute("SET LOCAL statement_timeout = '2s'")
            domain = [(sb.relation_field_id.name, "=", src_id)]
            domain.extend(sb._parse_domain())
            count = request.env[target].search_count(domain)
        except QueryCanceled:
            return _envelope(error="timeout")
        except Exception:
            _logger.exception(
                "Forge: smart_button_count failed "
                "(sb_id=%s, src_id=%s)",
                sb_id,
                src_id,
            )
            return _envelope(error="unavailable")
        finally:
            try:
                request.env.cr.execute(
                    "SET LOCAL statement_timeout = DEFAULT"
                )
            except Exception:
                pass

        action = sb._build_action_dict(src_id) if sb.open_on_click else False
        return _envelope(count=count, action=action)


def _envelope(count=0, action=False, error=None):
    """Uniform response shape so the front-end never has to handle
    surprises. The caching header is best-effort (Odoo's JSON dispatcher
    uses POST so most proxies skip caching anyway)."""
    try:
        request.future_response.headers["Cache-Control"] = (
            "private, no-store"
        )
    except (AttributeError, KeyError):
        pass
    return {
        "count": int(count),
        "action": action,
        "error": error,
    }
