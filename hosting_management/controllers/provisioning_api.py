# License LGPL-3.

import base64
import hmac
import json
import logging
import re
from datetime import datetime

from markupsafe import Markup, escape

from odoo import fields as odoo_fields, http
from odoo.http import request

_logger = logging.getLogger(__name__)

MAX_PAYLOAD_BYTES = 5 * 1024 * 1024  # 5 MB
_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def _sanitize_slug(raw: str, maxlen: int = 64) -> str:
    return _SLUG_RE.sub("-", raw.lower().strip())[:maxlen].strip("-") or "unknown"


class ProvisioningAPIController(http.Controller):
    """Receive tenant provisioning reports from host-side scripts."""

    @http.route(
        "/api/hosting/provision/report",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def receive_provision_report(self, **kwargs):
        """
        Token-authenticated endpoint. Header: X-Provision-Token.

        Expected JSON payload:
        {
            "slug": "acme",
            "client_name": "Acme Inc.",
            "software_code": "NC",          # or software_name fallback
            "software_name": "Nextcloud",
            "hostname": "server1.example.com",  # matches hosting.server.hostname or .code
            "server_url": "https://acme.cloud.example.com",
            "domain_name": "acme.cloud.example.com",
            "admin_url": "https://acme.cloud.example.com/login",
            "docker_container": "nextcloud-acme",
            "db_name": "nextcloud_acme",
            "npm_port": 9612,
            "started_at": "2026-04-15 20:40:00",
            "ended_at":   "2026-04-15 20:48:32",
            "status": "success",            # or "failure"
            "log": "<full verbose stdout/stderr>"
        }
        """
        try:
            token = request.httprequest.headers.get("X-Provision-Token", "")
            expected = (
                request.env["ir.config_parameter"]
                .sudo()
                .get_param("hosting.provision_api_token", "")
            )
            if not expected or expected == "CHANGE_ME_TO_SECURE_TOKEN":
                return request.make_json_response(
                    {"success": False, "error": "API token not configured"},
                    status=500,
                )
            if not token or not hmac.compare_digest(token, expected):
                return request.make_json_response(
                    {"success": False, "error": "Invalid API token"}, status=401
                )

            raw = request.httprequest.data or b"{}"
            if len(raw) > MAX_PAYLOAD_BYTES:
                return request.make_json_response(
                    {"success": False, "error": f"Payload too large (max {MAX_PAYLOAD_BYTES} bytes)"},
                    status=413,
                )

            data = json.loads(raw)
            slug = (data.get("slug") or "").strip()
            client_name = (data.get("client_name") or "").strip()
            sw_code = (data.get("software_code") or "").strip()
            sw_name = (data.get("software_name") or "").strip()
            if not slug or not (sw_code or sw_name):
                return request.make_json_response(
                    {"success": False, "error": "slug and software_code/name required"},
                    status=400,
                )

            env = request.env(user=1)  # run as superuser

            # Software lookup
            sw_domain = []
            if sw_code:
                sw_domain.append(("code", "=", sw_code))
            software = env["hosting.software"].search(sw_domain, limit=1) if sw_domain else env["hosting.software"]
            if not software and sw_name:
                software = env["hosting.software"].search(
                    [("name", "=ilike", sw_name)], limit=1
                )
            if not software:
                return request.make_json_response(
                    {"success": False, "error": f"Unknown software: {sw_code or sw_name}"},
                    status=400,
                )

            # Server lookup
            hostname = (data.get("hostname") or "").strip()
            server = env["hosting.server"]
            if hostname:
                server = env["hosting.server"].search(
                    ["|", ("hostname", "=", hostname), ("code", "=", hostname)],
                    limit=1,
                )

            # Partner resolution: ref=slug, then name, then create company stub
            partner = env["res.partner"].search(
                [("is_company", "=", True), ("ref", "=", slug)], limit=1
            )
            if not partner and client_name:
                partner = env["res.partner"].search(
                    [("is_company", "=", True), ("name", "=ilike", client_name)],
                    limit=1,
                )
            if not partner:
                partner = env["res.partner"].create({
                    "name": client_name or slug,
                    "is_company": True,
                    "ref": slug,
                    "comment": f"Auto-créé via provisioning script ({software.code}) le {odoo_fields.Datetime.now()}",
                })

            # Service upsert
            server_url = (data.get("server_url") or "").strip() or False
            domain_name = (data.get("domain_name") or "").strip() or False
            search_dom = [
                ("software_id", "=", software.id),
                ("partner_id", "=", partner.id),
            ]
            if server_url:
                service = env["hosting.service"].search(
                    search_dom + [("server_url", "=", server_url)], limit=1
                )
            elif domain_name:
                service = env["hosting.service"].search(
                    search_dom + [("domain_name", "=", domain_name)], limit=1
                )
            else:
                service = env["hosting.service"].search(search_dom, limit=1)

            status = (data.get("status") or "").lower()
            success = status == "success"

            vals = {
                "name": f"{partner.name} - {software.name}",
                "partner_id": partner.id,
                "software_id": software.id,
                "state": "active" if success else "draft",
            }
            if server:
                vals["server_id"] = server.id
            if server_url:
                vals["server_url"] = server_url
            if domain_name:
                vals["domain_name"] = domain_name
            if data.get("docker_container"):
                vals["docker_container"] = data["docker_container"]
            if data.get("db_name"):
                vals["storage_check_db_name"] = data["db_name"]

            if service:
                service.write(vals)
                action = "updated"
            else:
                service = env["hosting.service"].create(vals)
                action = "created"

            # Attach full verbose log to the service chatter
            log_content = data.get("log") or ""
            safe_slug = _sanitize_slug(slug)
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            attachment = env["ir.attachment"].create({
                "name": f"provisioning-{safe_slug}-{ts}.log",
                "datas": base64.b64encode(log_content.encode("utf-8", errors="replace")),
                "res_model": "hosting.service",
                "res_id": service.id,
                "mimetype": "text/plain",
            })

            summary_lines = [
                f"<strong>Provisioning {escape(status or 'unknown')}</strong> "
                f"({escape(action)})",
                f"Software: {escape(software.name)} ({escape(software.code or '')})",
                f"Server: {escape(server.name) if server else '(non lié)'}",
                f"URL: {escape(server_url or domain_name or '-')}",
                f"Container: {escape(data.get('docker_container') or '-')}",
                f"DB: {escape(data.get('db_name') or '-')}",
                f"NPM port: {escape(str(data.get('npm_port') or '-'))}",
                f"Started: {escape(data.get('started_at') or '-')}",
                f"Ended: {escape(data.get('ended_at') or '-')}",
            ]
            body = Markup("<p>" + "<br/>".join(summary_lines) + "</p>")
            service.message_post(
                body=body,
                attachment_ids=[attachment.id],
                message_type="comment",
                subtype_xmlid="mail.mt_note",
            )

            env["hosting.audit.log"]._log_event(
                action_type="create" if action == "created" else "config_change",
                category="config",
                description=(
                    f"Tenant {slug} {action} via provisioning script "
                    f"({software.code or software.name})"
                ),
                res_model="hosting.service",
                res_id=service.id,
                res_name=service.name,
                service_id=service.id,
                server_id=server.id if server else None,
                status="success" if success else "failure",
                severity="info" if success else "warning",
            )

            _logger.info(
                "Provisioning report: %s service %s (%s) for partner %s",
                action, service.code, software.code, partner.name,
            )
            return request.make_json_response({
                "success": True,
                "action": action,
                "service_id": service.id,
                "service_code": service.code,
                "partner_id": partner.id,
            })

        except Exception:
            _logger.exception("Error processing provisioning report")
            return request.make_json_response(
                {"success": False, "error": "Internal server error"}, status=500
            )
