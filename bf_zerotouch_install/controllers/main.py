"""Public controller serving /blue-fox-install.ks for Blue Fox OS zero-touch.

Anaconda's dracut module fetches this URL when a user picks the zero-touch
GRUB entry and types the organisation's domain. The response is a kickstart
populated from data/blue-fox-install.ks.template.

The v2 template carries an OIDC device-flow %pre (bf_policy): it
authenticates the operator on a 2nd device, pulls the org's policy JSON, and
applies the install block in %post. Two scripts are embedded verbatim from
data/ into the {{PROVISION_SCRIPT}} / {{APPLY_SCRIPT}} heredocs so the rendered
kickstart is self-contained (no second fetch at install time). The device flow
is fail-safe: it never aborts the install and falls back to org defaults.

Drift between this module's data/ artifacts and the canonical copies in the
Blue Fox OS image repository (install/blue-fox-install.ks.template,
install/bfos_provision.py, install/bfos_apply.py) is gated by that
repository's CI.
"""

from __future__ import annotations

import datetime
import logging
import os
import re

from odoo import http
from odoo.http import Response, request

_logger = logging.getLogger(__name__)

# Canonical template + embedded scripts live under <addon>/data/.
# Mirror of the Blue Fox OS image repository's install/ directory.
_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
)
_TEMPLATE_PATH = os.path.join(_DATA_DIR, "blue-fox-install.ks.template")
_PROVISION_SCRIPT_PATH = os.path.join(_DATA_DIR, "bfos_provision.py")
_APPLY_SCRIPT_PATH = os.path.join(_DATA_DIR, "bfos_apply.py")

# Tenant config now lives in bf.policy.org (bf_policy): one Policy row per tenant
# is the single source of truth, resolved from the request Host. Per-tenant
# template vars come from org._kickstart_vars(); an org with no OCI image
# configured has zero-touch disabled and yields a 404.

_PLACEHOLDER_RE = re.compile(r"\{\{([A-Z_][A-Z0-9_]*)\}\}")

# A line starting with a kickstart section keyword would be read as a section
# delimiter by pykickstart even inside a heredoc, corrupting the install. Guard
# the embedded scripts against it (same guard as the offline renderer).
_KS_SECTION_RE = re.compile(r"^%(pre|post|end|packages|onerror|traceback|addon)\b")


def _embed_script(path: str) -> str:
    """Read a script for verbatim embedding into a %pre/%post heredoc, failing
    loudly if any line would be mistaken for a kickstart section delimiter."""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read().rstrip("\n")
    for i, line in enumerate(text.splitlines(), 1):
        if _KS_SECTION_RE.match(line):
            raise ValueError(
                f"{os.path.basename(path)}:{i} starts with a kickstart section "
                f"keyword ({line[:20]!r}); reword so no embedded line begins "
                f"with %pre/%post/%end/etc."
            )
    return text


def _render_kickstart(tenant_vars: dict) -> str:
    with open(_TEMPLATE_PATH, "r", encoding="utf-8") as f:
        template = f.read()
    vars_ = dict(tenant_vars)
    vars_["GENERATED_AT"] = (
        datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    # Embed the install-time scripts verbatim into the %pre/%post heredocs.
    vars_["PROVISION_SCRIPT"] = _embed_script(_PROVISION_SCRIPT_PATH)
    vars_["APPLY_SCRIPT"] = _embed_script(_APPLY_SCRIPT_PATH)

    def replace(match):
        key = match.group(1)
        if key not in vars_:
            raise ValueError(
                f"unknown placeholder {{{{ {key} }}}} in zerotouch template"
            )
        return vars_[key]

    rendered = _PLACEHOLDER_RE.sub(replace, template)
    leftover = _PLACEHOLDER_RE.findall(rendered)
    if leftover:
        raise ValueError(f"unresolved placeholders after render: {leftover}")
    return rendered


class ZeroTouchInstallController(http.Controller):
    @http.route(
        "/blue-fox-install.ks",
        type="http",
        auth="public",
        csrf=False,
        methods=["GET"],
    )
    def install_kickstart(self, **kwargs):
        # Route by the domain the operator typed at GRUB. Behind a reverse proxy the
        # original host arrives in X-Forwarded-Host; fall back to the werkzeug
        # host for non-proxied dev. sudo(): the public user reads tenant config
        # (no secrets — image ref + service URLs are public anyway).
        host = (
            request.httprequest.headers.get("X-Forwarded-Host")
            or request.httprequest.host
            or ""
        )
        # The Policy org (bf_policy) is the single source of truth. _select_for_host
        # gives single-org fallback (the common case) and refuses an unmatched host
        # when several tenants exist. sudo(): the public user reads tenant config
        # (no secrets — image ref + OIDC endpoints are public anyway).
        org, status = request.env["bf.policy.org"].sudo()._select_for_host(host)
        if status or not org or not org.oci_image_ref:
            _logger.warning(
                "[bf_zerotouch] no zero-touch image configured for host=%r (status=%s)",
                host, status)
            return Response(
                "# No Blue Fox OS zero-touch tenant is configured for this domain.\n"
                "# Use the built-in defaults GRUB entry instead.\n",
                status=404,
                mimetype="text/plain; charset=utf-8",
            )
        kvars = org._kickstart_vars()
        try:
            ks = _render_kickstart(kvars)
        except Exception:
            _logger.exception(
                "[bf_zerotouch] failed to render kickstart for %s", kvars["TENANT_SLUG"])
            return Response(
                "# Failed to render Blue Fox OS install kickstart.\n"
                "# Server logs have details. Try again or use built-in defaults.\n",
                status=500,
                mimetype="text/plain; charset=utf-8",
            )
        # X-Forwarded-For first hop is the real client; the rest of the chain
        # is preserved for forensic tracing (CDN/proxy hops). remote_addr
        # fallback is for non-proxied dev setups.
        xff = request.httprequest.headers.get("X-Forwarded-For", "").strip()
        client_ip = xff.split(",")[0].strip() if xff else request.httprequest.remote_addr
        _logger.info(
            "[bf_zerotouch] served slug=%s ip=%s ua=%s chain=%s",
            kvars["TENANT_SLUG"],
            client_ip,
            request.httprequest.headers.get("User-Agent", "-"),
            xff or "-",
        )
        # Anaconda dracut respects Content-Type; text/plain is what fetch-kickstart-net
        # expects. Disable caching so a tenant template update propagates immediately.
        headers = [
            ("Content-Type", "text/plain; charset=utf-8"),
            ("Cache-Control", "no-store, no-cache, must-revalidate"),
            ("X-BF-Zerotouch-Version", "v2"),
        ]
        return request.make_response(ks, headers=headers)
