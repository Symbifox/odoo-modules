# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Dépôt de l'agent de conteneurs — l'hôte raconte, Odoo juge.

🔴 **Pourquoi une route et pas une sonde.** Dix-huit services actifs n'étaient
sondés par rien : ni URL publique, ni port publié, ni réseau Docker commun avec
le conteneur Odoo. Et ce conteneur n'a ni socket Docker ni clé privée SSH, par
décision : un socket Docker dans Odoo ferait de n'importe quelle faille
Odoo un root sur l'hôte, et `docker inspect` expose en plus l'environnement
complet des conteneurs, mots de passe compris. La machine parle, le centre
écoute.

⚠️ **La charge est authentifiée, pas digne de confiance.** Le jeton prouve
seulement que l'appelant est l'agent. Chaque valeur est donc contrainte ici :
le nom de conteneur doit apparier un motif, l'état de la sonde doit figurer dans
une liste fermée, le compteur de redémarrages est borné, le détail est tronqué.
Et la route n'écrit que sur des fiches QUI EXISTENT DÉJÀ, en `health_kind =
docker` : elle ne crée aucun service, n'en archive aucun, n'écrit aucun relevé
de santé. Le relevé, c'est le cron qui le fabrique, en jugeant ce dépôt.
"""

import hmac
import json
import logging
import re

from odoo import fields, http
from odoo.http import request

_logger = logging.getLogger(__name__)

NOM_CONTENEUR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
SANTES = ("healthy", "unhealthy", "starting", "none")
PLAFOND_CONTENEURS = 500
DETAIL_MAX = 200
IGNORES_NOMMES = 10


class HostingAgentApi(http.Controller):
    """Réception des relevés de conteneurs poussés par l'agent de l'hôte."""

    def _valider_jeton(self):
        """Rendre (ok, réponse d'erreur) — jeton propre à l'agent.

        ⚠️ Un jeton par usage : celui des rapports de sauvegarde raconte ce qui
        a tourné, celui-ci peint des services en vert ou en rouge. Un porteur du
        premier ne doit pas pouvoir faire passer un conteneur mort pour vivant.
        """
        jeton = request.httprequest.headers.get("X-Agent-Token")
        attendu = (
            request.env["ir.config_parameter"]
            .sudo()
            .get_param("hosting.agent_api_token", "")
            .strip()
        )
        if not attendu or attendu == "CHANGE_ME":
            _logger.warning(
                "Dépôt de l'agent de conteneurs refusé : hosting.agent_api_token "
                "n'est pas configuré"
            )
            return False, request.make_json_response(
                {"success": False, "error": "Agent token not configured"},
                status=503,
            )
        try:
            recu = (jeton or "").encode("utf-8")
            attendu_octets = attendu.encode("utf-8")
        except (AttributeError, UnicodeError):
            recu, attendu_octets = b"", b"x"
        if not jeton or not hmac.compare_digest(recu, attendu_octets):
            return False, request.make_json_response(
                {"success": False, "error": "Invalid agent token"}, status=401,
            )
        return True, None

    @http.route(
        "/api/hosting/agent/containers/public",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def recevoir_etat_conteneurs(self, **kwargs):
        ok, err = self._valider_jeton()
        if not ok:
            return err
        try:
            charge = json.loads(request.httprequest.data or b"{}")
        except (json.JSONDecodeError, ValueError) as e:
            return request.make_json_response(
                {"success": False, "error": f"Invalid JSON: {e}"}, status=400,
            )
        if not isinstance(charge, dict):
            return request.make_json_response(
                {"success": False, "error": "Payload must be an object"},
                status=400,
            )
        conteneurs = charge.get("containers")
        if not isinstance(conteneurs, list):
            return request.make_json_response(
                {"success": False, "error": "containers must be a list"},
                status=400,
            )
        if len(conteneurs) > PLAFOND_CONTENEURS:
            return request.make_json_response(
                {"success": False,
                 "error": f"Too many containers (max {PLAFOND_CONTENEURS})"},
                status=413,
            )

        faits = {}
        for brut in conteneurs:
            if not isinstance(brut, dict):
                continue
            nom = brut.get("container")
            if not isinstance(nom, str) or not NOM_CONTENEUR.match(nom):
                continue
            # ⚠️ `running` est le seul champ dont dépend un DOWN : il doit être
            # un vrai booléen. `bool("false")` rend True, et une coercition
            # permissive ici ferait d'une charge mal formée une panne (ou
            # l'inverse). Un relevé qui ne dit pas clairement si le conteneur
            # tourne n'est pas un relevé.
            if not isinstance(brut.get("running"), bool):
                continue
            sante = brut.get("health")
            if sante not in SANTES:
                sante = "none"
            try:
                # ⚠️ `json.loads('1e400')` rend `inf`, et `int(inf)` lève
                # OverflowError — hors de (TypeError, ValueError), donc un 500
                # avec trace pour un porteur du jeton.
                redemarrages = max(0, min(int(brut.get("restart_count") or 0), 10 ** 6))
            except (TypeError, ValueError, OverflowError):
                redemarrages = 0
            detail = brut.get("detail")
            faits[nom] = {
                "agent_container_running": brut["running"],
                "agent_container_health": sante,
                "agent_restart_count": redemarrages,
                "agent_detail": (detail if isinstance(detail, str) else "")[:DETAIL_MAX],
            }

        if not faits:
            return request.make_json_response(
                {"success": False, "error": "No usable container reading"},
                status=400,
            )

        Service = request.env["hosting.service"].sudo()
        # ⚠️ L'agent envoie TOUS les conteneurs de l'hôte, sans rien savoir de
        # ce qu'Odoo suit : c'est ici qu'on apparie, et seulement sur les fiches
        # qui ont demandé cette sonde. Un conteneur inconnu n'est pas une
        # erreur — c'est le cas normal pour la plupart.
        #
        # Un même conteneur peut porter plusieurs fiches (allocations par
        # client) : l'appariement est volontairement `in`, pas `=`, et toutes
        # les fiches reçoivent le même dépôt.
        # ⚠️ Le MÊME périmètre que `attendus` plus bas : une fiche annulée
        # ou déclarée hors de portée ne doit pas recevoir un dépôt frais, et
        # surtout pas être comptée dans `matched` — le nombre que le journal de
        # l'hôte publie ne vaut que s'il désigne les mêmes fiches.
        services = Service.search([
            ("health_kind", "=", "docker"),
            ("state", "=", "active"),
            ("health_exempt", "=", False),
            ("docker_container", "in", list(faits)),
        ])
        maintenant = fields.Datetime.now()
        apparies = 0
        for service in services:
            vals = dict(faits[service.docker_container])
            vals["agent_report_at"] = maintenant
            service.write(vals)
            apparies += 1

        attendus = set(Service.search([
            ("health_kind", "=", "docker"),
            ("state", "=", "active"),
            ("health_exempt", "=", False),
        ]).mapped("docker_container"))
        # 🔴 Un conteneur attendu dont l'agent ne parle PAS est le cas qui
        # compte : son dépôt va vieillir et la sonde passera « sans nouvelles ».
        # Le dire dans la réponse met la cause dans le journal de l'hôte, là où
        # on la cherche.
        manquants = sorted(n for n in attendus if n and n not in faits)
        if manquants:
            _logger.warning(
                "Dépôt de l'agent : %d conteneur(s) suivi(s) absent(s) du relevé : %s",
                len(manquants), ", ".join(manquants[:IGNORES_NOMMES]),
            )

        inconnus = sorted(n for n in faits if n not in attendus)
        # ⚠️ La réponse ne nomme QUE ce que l'appelant a lui-même envoyé. La
        # version d'avant y mettait les conteneurs suivis dont il n'avait pas
        # parlé — donc des noms de clients, à qui porte le jeton, pour un
        # journal que l'agent ne lisait même pas. Le compte suffit ; les noms
        # sont au journal d'Odoo, du côté qui les connaît déjà.
        return request.make_json_response({
            "success": True,
            "matched": apparies,
            "ignored": inconnus[:IGNORES_NOMMES],
            "ignored_count": len(inconnus),
            "expected_missing_count": len(manquants),
        })
