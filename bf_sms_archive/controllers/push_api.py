"""Endpoints Web Push / PWA pour la messagerie SMS.

- ``/bf_sms_archive/push-sw.js``        → service worker (reçoit les push, affiche
                                          la notif OS, ouvre la messagerie au clic)
- ``/bf_sms_archive/manifest.webmanifest`` → manifeste PWA (installer sur l'écran
                                          d'accueil ; base du wrapper APK/TWA)
- ``/.well-known/assetlinks.json``      → Digital Asset Links pour la TWA signée
                                          (masque la barre d'URL de l'APK)

L'abonnement lui-même passe par les méthodes RPC ``push_subscribe`` /
``push_unsubscribe`` de ``sms.archive.thread`` (JSON-RPC authentifié), pas par un
contrôleur public.
"""
import json
import logging
import os

from odoo import http
from odoo.http import request
from odoo.modules.module import get_module_path

_logger = logging.getLogger(__name__)


class BfSmsPushController(http.Controller):

    @http.route(
        "/bf_sms_archive/push-sw.js", type="http", auth="public",
        methods=["GET"], csrf=False, save_session=False,
    )
    def push_service_worker(self, **kw):
        path = os.path.join(
            get_module_path("bf_sms_archive"), "static", "src", "sw", "push_sw.js",
        )
        try:
            with open(path, "rb") as fh:
                content = fh.read()
        except OSError:
            _logger.error("Service worker push introuvable : %s", path)
            return request.make_response(
                b"// service worker manquant",
                headers=[("Content-Type", "application/javascript; charset=utf-8")],
                status=404,
            )
        return request.make_response(content, headers=[
            ("Content-Type", "application/javascript; charset=utf-8"),
            # Autorise un scope racine ; on enregistre en réalité sur un scope
            # étroit côté SPA pour ne pas interférer avec le SW natif d'Odoo.
            ("Service-Worker-Allowed", "/"),
            ("Cache-Control", "no-cache"),
        ])

    @http.route(
        "/bf_sms_archive/manifest.webmanifest", type="http", auth="public",
        methods=["GET"], csrf=False,
    )
    def push_manifest(self, **kw):
        company = request.env.company
        try:
            action_id = request.env.ref(
                "bf_sms_archive.sms_messenger_client_action").id
            start_url = "/odoo/action-%d" % action_id
        except Exception:
            start_url = "/odoo"
        manifest = {
            "name": "%s — Messagerie SMS" % (company.name or "SMS"),
            "short_name": "SMS",
            "description": "Messagerie SMS/MMS via VOIP.ms",
            "start_url": start_url,
            "scope": "/",
            "display": "standalone",
            "orientation": "portrait",
            "background_color": "#ffffff",
            "theme_color": "#29ABE2",
            "lang": "fr-CA",
            "icons": [
                {
                    "src": "/web/static/img/odoo-icon-192x192.png",
                    "sizes": "192x192", "type": "image/png",
                    "purpose": "any maskable",
                },
                {
                    "src": "/web/static/img/odoo-icon-512x512.png",
                    "sizes": "512x512", "type": "image/png",
                    "purpose": "any maskable",
                },
            ],
        }
        return request.make_response(
            json.dumps(manifest),
            headers=[("Content-Type", "application/manifest+json; charset=utf-8")],
        )

    @http.route(
        "/.well-known/assetlinks.json", type="http", auth="public",
        methods=["GET"], csrf=False, save_session=False,
    )
    def assetlinks(self, **kw):
        """Digital Asset Links. La ou les empreintes SHA-256 de la clé de
        signature de l'APK sont posées en paramètre système après génération du
        keystore (pas de modification de code au moment du build)."""
        ICP = request.env["ir.config_parameter"].sudo()
        fingerprints = ICP.get_param("bf_sms_archive.twa_sha256_fingerprint") or ""
        package = ICP.get_param("bf_sms_archive.twa_package_name") \
            or "com.example.sms"
        statements = []
        fps = [f.strip() for f in fingerprints.split(",") if f.strip()]
        if fps:
            statements = [{
                "relation": ["delegate_permission/common.handle_all_urls"],
                "target": {
                    "namespace": "android_app",
                    "package_name": package,
                    "sha256_cert_fingerprints": fps,
                },
            }]
        # ⚠️ Une seule route peut servir ce chemin dans tout Odoo, donc les
        # autres applications maison doivent passer par ici. Plutôt que de
        # modifier ce code à chaque fois, un paramètre porte des déclarations
        # supplémentaires en JSON brut : ajouter une application devient un
        # réglage, pas un déploiement.
        #
        # 🔴 Le contenu est validé avant d'être servi. Un JSON mal formé rendrait
        # le fichier entier illisible pour Android, et l'application EXISTANTE
        # perdrait sa vérification de lien sans que rien ne le signale ici.
        extra = (ICP.get_param("bf_assetlinks.extra_statements") or "").strip()
        if extra:
            try:
                supplement = json.loads(extra)
                if isinstance(supplement, dict):
                    supplement = [supplement]
                if isinstance(supplement, list):
                    statements.extend(
                        d for d in supplement if isinstance(d, dict))
                else:
                    _logger.warning(
                        "assetlinks : bf_assetlinks.extra_statements n'est ni "
                        "un objet ni une liste, ignoré")
            except ValueError:
                _logger.warning(
                    "assetlinks : bf_assetlinks.extra_statements n'est pas du "
                    "JSON valide, ignoré pour ne pas casser le fichier")
        return request.make_response(
            json.dumps(statements),
            headers=[("Content-Type", "application/json; charset=utf-8")],
        )
