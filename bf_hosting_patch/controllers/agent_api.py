# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""API de l'agent `symbifox-hostd`.

Cinq points d'entrée, et rien d'autre :

    POST /symbifox/patch/v1/enrol    code à usage unique  -> jeton porteur
    POST /symbifox/patch/v1/report   jeton                -> dépose un relevé
    GET  /symbifox/patch/v1/ping     jeton                -> le lien est vivant
    POST /symbifox/patch/v1/poll     jeton                -> « as-tu un ordre ? »
    POST /symbifox/patch/v1/result   jeton                -> issue d'un ordre

⚠️ `poll` ne POUSSE rien. C'est l'agent qui demande, et le serveur qui répond
au plus un ordre. Odoo n'a toujours aucun chemin vers les machines : une
machine éteinte ne rate pas l'ordre, elle le prend au réveil.

⚠️ Le jeton authentifie une MACHINE, jamais une personne. Il ne peut que
déposer un relevé sur la fiche qu'il désigne. Il ne peut pas créer une fiche de
parc, en lire une autre, ni rien déclencher : la fiche est créée par un humain,
et l'agent ne fait que la remplir.

⚠️ Les gardes rendent une réponse AVANT le travail, jamais depuis l'intérieur
d'un `try` qui journalise et continue : une garde qui laisse la route répondre
« ok » ne garde rien.
"""

import json
import logging

from odoo import fields, http
from odoo.exceptions import UserError
from odoo.http import request

_logger = logging.getLogger(__name__)

BASE = "/symbifox/patch/v1"

# Un relevé de 150 paquets pèse quelques dizaines de kilooctets. Le plafond est
# là pour qu'un agent en dérive ne puisse pas remplir la base.
MAX_BODY_BYTES = 512 * 1024
MAX_PACKAGES = 2000

# Ce que le relevé a le droit de porter, et sous quelle forme. Tout ce qui n'est
# pas dans cette table est ignoré en silence : l'agent peut donc devancer le
# serveur sans le casser.
REPORT_FIELDS = {
    "agent_version": "char",
    "os_release": "char",
    "kernel_running": "char",
    "kernel_installed": "char",
    "boot_time": "datetime",
    "reboot_required": "bool",
    "reboot_pending_since": "datetime",
    "reboot_packages": "char",
    "package_manager": "char",
    "pending_known": "bool",
    "pending_count": "int",
    "pending_security_count": "int",
    "auto_update_mode": "char",
    "auto_update_detail": "char",
    "disk_root_pct": "int",
    "disk_boot_pct": "int",
    "os_support_end": "date",
    "os_support_state": "char",
    # Lot 3 : le consentement local. L'agent l'envoyait depuis le lot 1 et le
    # serveur le jetait, faute d'être dans cette table.
    "apply_allowed": "bool",
}

PACKAGE_FIELDS = {
    "name": "char",
    "version_installed": "char",
    "version_candidate": "char",
    "origin": "char",
    "is_security": "bool",
}

# Les sélections sont validées ici plutôt que laissées à l'ORM : une valeur
# inconnue venant du réseau doit retomber sur une valeur sûre, pas lever.
PACKAGE_MANAGERS = ("apt", "dnf", "pacman", "zypper", "apk", "bootc", "other")
AUTO_UPDATE_MODES = ("unknown", "off", "download", "security", "all")
SUPPORT_STATES = ("supported", "ending_soon", "ended", "rolling", "unknown")


def _json(data, status=200):
    return request.make_response(
        json.dumps(data, default=str),
        headers=[("Content-Type", "application/json; charset=utf-8")],
        status=status,
    )


def _body():
    """Lire et décoder le corps, sous plafond. Rend None si illisible."""
    raw = request.httprequest.get_data(cache=False, as_text=False)
    if not raw or len(raw) > MAX_BODY_BYTES:
        return None
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _token():
    header = request.httprequest.headers.get("Authorization", "")
    return header[7:].strip() if header.startswith("Bearer ") else None


def _cast(value, kind):
    """Typer une valeur venue du réseau, ou rendre None."""
    if value is None or value == "":
        return None
    try:
        if kind == "int":
            return int(value)
        if kind == "bool":
            return bool(value)
        if kind == "char":
            return str(value)[:512]
        if kind == "text":
            # ⚠️ PAS `char` : la sortie d'un `apt-get upgrade` fait des milliers
            # de caractères, et la tronquer à 512 effacerait précisément la
            # partie qui explique un échec.
            return str(value)[:65536]
        if kind == "datetime":
            return fields.Datetime.to_datetime(str(value))
        if kind == "date":
            return fields.Date.to_date(str(value))
    except (TypeError, ValueError):
        return None
    return None


def _clean_report(payload):
    data = {}
    for name, kind in REPORT_FIELDS.items():
        cast = _cast(payload.get(name), kind)
        if cast is not None:
            data[name] = cast

    # ⚠️ `_cast` rend None pour un booléen faux : sans ce rattrapage,
    # `pending_known: false` se perdait et le champ retombait sur son défaut,
    # « fiable ». Un compte inconnu serait redevenu un compte à zéro, ce qui
    # est exactement le mensonge qu'on cherche à empêcher. En cas de doute,
    # c'est INCONNU qui gagne, pas fiable.
    data["pending_known"] = bool(payload.get("pending_known", False)) \
        if "pending_known" in payload else False

    # 🔴 Même rattrapage, et l'enjeu est plus grave encore. Le consentement se
    # RETIRE en effaçant /etc/symbifox/apply-allowed : l'agent envoie alors
    # `false`. Si ce `false` se perdait, la fiche garderait son `true` et le
    # serveur continuerait de remettre des ordres à une machine dont le
    # propriétaire vient de les refuser. Une révocation qui ne révoque pas est
    # pire que pas de révocation du tout. Un agent trop vieux pour envoyer le
    # champ compte donc comme un REFUS, jamais comme un consentement tacite.
    data["apply_allowed"] = bool(payload.get("apply_allowed", False))

    if data.get("package_manager") not in PACKAGE_MANAGERS:
        data.pop("package_manager", None)
    if data.get("auto_update_mode") not in AUTO_UPDATE_MODES:
        data["auto_update_mode"] = "unknown"
    if data.get("os_support_state") not in SUPPORT_STATES:
        data["os_support_state"] = "unknown"

    packages = []
    for entry in (payload.get("packages") or [])[:MAX_PACKAGES]:
        if not isinstance(entry, dict):
            continue
        name = _cast(entry.get("name"), "char")
        if not name:
            continue
        line = {"name": name}
        for field_name, kind in PACKAGE_FIELDS.items():
            if field_name == "name":
                continue
            cast = _cast(entry.get(field_name), kind)
            if cast is not None:
                line[field_name] = cast
        packages.append(line)
    data["packages"] = packages
    return data


class SymbifoxPatchAgentApi(http.Controller):

    @http.route(f"{BASE}/enrol", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    def enrol(self, **kw):
        payload = _body()
        if payload is None:
            return _json({"ok": False, "error": "corps illisible"}, 400)
        code = payload.get("code")
        machine_id = payload.get("machine_id")
        if not isinstance(code, str) or not isinstance(machine_id, str):
            return _json({"ok": False, "error": "code ou machine_id manquant"}, 400)

        os_family = str(payload.get("os_family") or "linux")
        if os_family not in ("linux", "windows", "macos", "other"):
            os_family = "other"
        try:
            system, token = request.env["hosting.endpoint"].sudo()._enrol_agent(
                code.strip(), machine_id.strip()[:64],
                hostname=str(payload.get("hostname") or "")[:128] or None,
                os_family=os_family,
                # Lisible en root seulement : l'agent ne l'envoie qu'ici,
                # depuis l'installateur.
                machine_uuid=str(payload.get("machine_uuid") or "")[:64] or None,
                os_release=str(payload.get("os_release") or "")[:256] or None,
            )
        except UserError as exc:
            # Message d'enrôlement rendu tel quel : il ne parle qu'à une machine
            # qui détient déjà un code valide ou vient d'en présenter un faux.
            return _json({"ok": False, "error": str(exc)}, 403)

        _logger.info("bf_hosting_patch : système %s enrôlé sur %s (%s)",
                     system.name, system.endpoint_id.name,
                     system.endpoint_id.code)
        return _json({
            "ok": True,
            "token": token,
            "endpoint": system.endpoint_id.code,
            "name": system.endpoint_id.name,
            "system": system.name,
        })

    @http.route(f"{BASE}/ping", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def ping(self, **kw):
        system = request.env["bf.patch.system"].sudo()._resolve_agent(_token())
        if not system:
            return _json({"ok": False, "error": "jeton refusé"}, 401)
        return _json({
            "ok": True,
            "endpoint": system.endpoint_id.code,
            "system": system.name,
            "last_report": system.agent_last_report,
        })

    @http.route(f"{BASE}/report", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    def report(self, **kw):
        system = request.env["bf.patch.system"].sudo()._resolve_agent(_token())
        if not system:
            return _json({"ok": False, "error": "jeton refusé"}, 401)

        payload = _body()
        if payload is None:
            return _json({"ok": False, "error": "corps illisible ou trop gros"},
                         400)

        # Garde de cadence, AVANT le travail : un agent qui déposerait un relevé
        # par seconde remplirait la base. La garde répond et sort, elle ne
        # journalise pas pour laisser passer.
        if system._report_too_soon():
            return _json({"ok": False, "error": "relevé trop rapproché"}, 429)

        data = _clean_report(payload)
        report = system._apply_report(
            data, payload=json.dumps(payload, ensure_ascii=False)[:MAX_BODY_BYTES]
        )
        return _json({
            "ok": True,
            "report": report.id,
            "state": system.patch_state,
            "machine_state": system.endpoint_id.patch_state,
            "pending": system.pending_count,
            "pending_known": system.pending_known,
            "delta": system.pending_delta,
        })

    @http.route(f"{BASE}/poll", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    def poll(self, **kw):
        """« As-tu quelque chose pour moi ? » — au plus UN ordre à la fois.

        Un seul ordre par passe, volontairement : deux ordres remis ensemble
        s'appliqueraient sans que le second connaisse l'issue du premier, et un
        redémarrage demandé par le premier couperait le second en deux.
        """
        system = request.env["bf.patch.system"].sudo()._resolve_agent(_token())
        if not system:
            return _json({"ok": False, "error": "jeton refusé"}, 401)
        if system._poll_too_soon():
            return _json({"ok": False, "error": "interrogation trop rapprochée"},
                         429)
        system.sudo()._touch_poll()

        job, refusal = request.env["bf.patch.job"].sudo()._claim_for(system)
        if refusal:
            # Ce n'est PAS une erreur : l'agent a bien demandé, la réponse est
            # « rien pour toi, et voici pourquoi ». Il l'imprime dans son
            # journal, ce qui rend le refus lisible sur la machine.
            return _json({"ok": True, "job": None, "reason": refusal})
        if not job:
            return _json({"ok": True, "job": None})
        _logger.info("bf_hosting_patch : ordre %s remis à %s",
                     job.id, system.name)
        return _json({"ok": True, "job": job._payload_for_agent()})

    @http.route(f"{BASE}/result", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    def result(self, **kw):
        """L'issue d'un ordre, rapportée par l'agent qui l'a exécuté."""
        system = request.env["bf.patch.system"].sudo()._resolve_agent(_token())
        if not system:
            return _json({"ok": False, "error": "jeton refusé"}, 401)
        payload = _body()
        if payload is None:
            return _json({"ok": False, "error": "corps illisible ou trop gros"},
                         400)

        try:
            job_id = int(payload.get("job_id") or 0)
        except (TypeError, ValueError):
            job_id = 0
        job = request.env["bf.patch.job"].sudo().browse(job_id).exists()
        # 🔴 L'ordre doit appartenir à CE système. Sans ce contrôle, un jeton
        # valide quelconque pourrait écrire l'issue de l'ordre d'une autre
        # machine — la fuite inter-machine que l'audit a trouvée sur les relevés.
        if not job or job.system_id != system:
            return _json({"ok": False, "error": "ordre inconnu"}, 404)

        state = payload.get("state")
        # `expired` est ici parce que la garde des 24 h est celle de l'AGENT :
        # une machine rallumée après trois semaines refuse d'appliquer une
        # décision prise dans un autre monde, et doit pouvoir le dire.
        if state not in ("running", "done", "failed", "expired"):
            return _json({"ok": False, "error": "état refusé"}, 400)

        written = job._record_result(
            state,
            exit_code=_cast(payload.get("exit_code"), "int"),
            output=_cast(payload.get("output"), "text"),
            packages_changed=_cast(payload.get("packages_changed"), "int"),
        )
        return _json({"ok": True, "recorded": written, "state": job.state})
