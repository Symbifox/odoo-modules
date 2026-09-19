"""Sonde canari du réveil par push — fabriquer la preuve au lieu de l'attendre.

🔴 Pourquoi une sonde SYNTHÉTIQUE, et pas un guetteur du trafic réel.

Une sauvegarde de la page des réglages a rendu le réveil inerte, et la panne
n'a été vue que huit jours plus tard. Deux raisons, et chacune condamne un type
de contrôle :

* **la route répondait 200.** ``{"ok": true, "sent": 0}`` à chaque appel. Une
  sonde HTTP qui regarde le code de statut serait restée VERTE du premier au
  dernier jour. C'est le contrôle qui ment, pas le service qui se tait.
* **il n'y avait presque pas de trafic.** Tout l'historique du réveil tenait en
  quelques dizaines de lignes de journal, et une poignée d'appels entrants réels
  sur la période. Un guetteur qui attend qu'un client appelle aurait attendu les
  mêmes huit jours — et un contrôle « ``sent == 0`` → alerte » aurait en plus
  crié à tort une douzaine de fois, une séance de mise au point produisant des
  ``sent: 0`` parfaitement légitimes.

D'où celle-ci : elle **fabrique son propre appel** sur une horloge, au lieu
d'espérer qu'un correspondant en fournisse un.

## Ce qu'elle traverse, et ce qu'elle ne traverse pas

Elle emprunte le MÊME chemin de décision que le vrai réveil — même lecture de
l'interrupteur, même fonction ``cibles()`` pour choisir les appareils — puis
publie sur un sujet **canari** au lieu des endpoints de l'usager. Elle couvre
donc les cinq façons dont ce lot meurt en silence :

    interrupteur lu faux · usager sans poste · aucun appareil inscrit ·
    jeton ntfy refusé · serveur ntfy injoignable

Elle ne fait PAS sonner de téléphone, et c'est ce qui la rend utilisable toutes
les heures. Ce qu'elle ne couvre pas : la route HTTP elle-même et son jeton
partagé avec le PBX — vérifiés séparément côté `hosting_management`, par un
appel délibérément mal authentifié dont on attend un 403.

⚠️ ``Cache: no`` et ``Firebase: no`` sur la publication : le message n'est ni
stocké ni relayé. Sans abonné sur le sujet canari, il ne laisse aucune trace
tout en exerçant le vrai chemin de publication et en rendant un vrai statut HTTP.
"""

import logging
import time
from urllib.parse import urlsplit

import requests

from odoo import api, models

from odoo.addons.bf_sms_archive.models.push_transport import (
    NTFY_BASE_PARAM, NTFY_TOKEN_PARAM, ntfy_auth_allowed, safe_push_endpoint,
)

from ..controllers.pbx_api import WAKE_PARAM, _truthy, cibles

_logger = logging.getLogger(__name__)

# Sujet de publication du canari.
#
# ⚠️ Le préfixe « up » n'est pas décoratif. Le jeton de publication
# (`bf_sms_archive.ntfy_publish_token`) appartient à un compte ntfy dont l'ACL
# n'accorde l'écriture qu'aux sujets commençant par « up » — la forme
# qu'emploient les endpoints UnifiedPush. Tout autre nom se fait refuser par un
# 403, et la sonde crierait au loup sur son propre défaut ; le premier essai,
# nommé hors de cet espace, a fait exactement ça.
#
# Le rester dans cet espace rend la sonde PLUS fidèle, pas moins : elle éprouve
# le droit d'écriture sur le motif même qu'empruntent les endpoints réels des
# appareils.
#
# Constante et non réglage : un sujet configurable qu'on oublie de configurer
# donne une sonde qui se déclare verte sans avoir rien publié — exactement le
# genre de contrôle qu'on cherche à éliminer.
SUJET_CANARI = "up-canari-softphone"

DELAI = 6.0


class ResUsersWakeSelftest(models.Model):
    _inherit = "res.users"

    @api.private
    @api.model
    def softphone_wake_selftest(self):
        """Éprouver la chaîne de réveil sans faire sonner personne.

        Rend un dictionnaire ``{etat, detail, ms, poste, appareils}`` où ``etat``
        vaut ``up``, ``degraded`` ou ``down`` — le vocabulaire de
        ``hosting.health.check``, pour que l'appelant n'ait rien à traduire.

        🔴 ``api.private`` : une sonde n'est pas un point d'entrée. Sans le
        décorateur, n'importe quel usager authentifié l'appelait par
        ``call_kw`` et récupérait, dans ``detail``, les postes SIP du parc et
        l'hôte ntfy interne — et surtout il faisait émettre au serveur une
        requête vers l'endpoint de SON appareil, qu'il choisit lui-même
        (``push_endpoint`` n'est pas un champ protégé). Le code HTTP revenant
        dans la réponse, ça faisait un oracle. Le cron et
        ``hosting_management`` l'appellent en Python : ``api.private`` ne
        ferme que la porte RPC.
        """
        debut = time.monotonic()

        def _fin(etat, detail, **extra):
            out = {"etat": etat, "detail": detail,
                   "ms": int((time.monotonic() - debut) * 1000)}
            out.update(extra)
            return out

        ICP = self.env["ir.config_parameter"].sudo()

        # ⚠️ Un interrupteur volontairement coupé n'est PAS une panne. Le lire
        # comme telle ferait sonner l'alarme sur une décision, et une alarme qui
        # se trompe finit désarmée. Depuis que la lecture est tolérante, un
        # « éteint » ne peut plus venir que d'un vrai décochage.
        if not _truthy(ICP.get_param(WAKE_PARAM), defaut=True):
            return _fin("up", "Réveil désactivé par configuration : "
                              "rien à éprouver.")

        postes = self.sudo().search([
            ("sip_enabled", "=", True),
            ("sip_extension", "!=", False),
            ("active", "=", True),
        ])
        if not postes:
            return _fin("down", "Aucun usager actif ne porte de poste SIP : "
                                "personne ne peut être réveillé.")

        pires = []
        for usager in postes:
            pires.append(self._selftest_un_poste(usager, ICP, _fin))

        # Le pire état gouverne : une sonde qui moyenne cache la panne d'un poste
        # derrière la santé des autres.
        ordre = {"down": 0, "degraded": 1, "up": 2}
        pire = min(pires, key=lambda r: ordre.get(r["etat"], 0))
        if len(pires) > 1:
            pire = dict(pire)
            pire["detail"] = f"{len(pires)} postes éprouvés — {pire['detail']}"
        pire["ms"] = int((time.monotonic() - debut) * 1000)
        return pire

    # ── interne ──────────────────────────────────────────────────────────

    def _selftest_un_poste(self, usager, ICP, _fin):
        poste = usager.sip_extension
        # Des ``Cible`` (endpoint et clés) : la sonde ne publie pas chez
        # l'appareil, elle n'a donc que faire des clés, mais elle lit la MÊME
        # liste que le réveil.
        endpoints = cibles(self.env, usager)
        if not endpoints:
            return _fin("down",
                        f"Poste {poste} : aucun appareil avec endpoint "
                        f"UnifiedPush. App fermée, le combiné restera muet et "
                        f"rien d'autre ne le signale.",
                        poste=poste, appareils=0)

        endpoint = endpoints[0].endpoint
        # 🔴 La MÊME garde que le vrai réveil (`pbx_api._push`), et pour la même
        # raison. L'endpoint vient de l'appareil, donc de son propriétaire, qui
        # peut l'écrire : sans ce contrôle la sonde émet une requête serveur
        # vers l'adresse de son choix et lui rend le code HTTP. Le réveil, lui,
        # l'avait ; la sonde, qui copie son chemin de décision, ne l'avait pas —
        # c'est exactement la dérive que ce module se prêche ailleurs.
        if not safe_push_endpoint(endpoint):
            return _fin("down",
                        f"Poste {poste} : l'endpoint de l'appareil ne résout "
                        f"pas vers une adresse publique, la sonde n'y touche "
                        f"pas.",
                        poste=poste, appareils=len(endpoints))
        base = self._base_ntfy(endpoint)
        if not base:
            return _fin("down",
                        f"Poste {poste} : endpoint d'appareil illisible "
                        f"({endpoint[:40]}…).",
                        poste=poste, appareils=len(endpoints))

        jeton = ICP.get_param(NTFY_TOKEN_PARAM)
        entetes = {
            "Title": "Canari du réveil softphone",
            "Priority": "min",
            # Ni stocké, ni relayé : la publication est exercée pour de vrai,
            # mais elle ne laisse rien derrière elle.
            "Cache": "no",
            "Firebase": "no",
        }
        # ⚠️ Même règle que le vrai réveil : le jeton ne part que vers
        # l'hôte de `bf_sms_archive.ntfy_base_url`. Un appareil inscrit ailleurs
        # se réveille donc SANS jeton ; la sonde publie de la même façon, et
        # c'est ce refus-là qu'elle doit voir, pas un vert emprunté.
        chez_nous = ntfy_auth_allowed(endpoint, ICP.get_param(NTFY_BASE_PARAM))
        if jeton and chez_nous:
            entetes["Authorization"] = "Bearer %s" % jeton

        try:
            rep = requests.post(f"{base}/{SUJET_CANARI}",
                                data=b"canari", headers=entetes, timeout=DELAI,
                                allow_redirects=False)
        except requests.RequestException as e:
            return _fin("down",
                        f"Poste {poste} : serveur ntfy injoignable ({base}) — "
                        f"{type(e).__name__}. Aucun réveil ne partira.",
                        poste=poste, appareils=len(endpoints))

        if (rep.status_code == 401 or rep.status_code == 403) and not chez_nous:
            return _fin("down",
                        f"Poste {poste} : l'appareil est inscrit hors de l'hôte de "
                        f"bf_sms_archive.ntfy_base_url, le réveil part donc sans "
                        f"jeton et ntfy le refuse (HTTP {rep.status_code}).",
                        poste=poste, appareils=len(endpoints))
        if rep.status_code == 401 or rep.status_code == 403:
            return _fin("down",
                        f"Poste {poste} : ntfy refuse le jeton de publication "
                        f"(HTTP {rep.status_code}). Le jeton a probablement "
                        f"tourné — bf_sms_archive.ntfy_publish_token.",
                        poste=poste, appareils=len(endpoints))
        if rep.status_code >= 300:
            return _fin("down",
                        f"Poste {poste} : ntfy répond HTTP {rep.status_code} "
                        f"à la publication.",
                        poste=poste, appareils=len(endpoints))

        return _fin("up",
                    f"Poste {poste} : {len(endpoints)} appareil(s), "
                    f"publication ntfy acceptée (HTTP {rep.status_code}).",
                    poste=poste, appareils=len(endpoints))

    @staticmethod
    def _base_ntfy(endpoint):
        """Le serveur ntfy DE L'APPAREIL, pas un réglage à part.

        ⚠️ Déduire la base de l'endpoint réel garantit que le canari frappe le
        même hôte que les vrais réveils. Un serveur canari configuré séparément
        finirait par pointer ailleurs, et la sonde rendrait vert sur une machine
        que la production n'utilise plus.
        """
        try:
            parts = urlsplit(endpoint)
            if parts.scheme and parts.netloc:
                return f"{parts.scheme}://{parts.netloc}"
        except ValueError:
            pass
        return None
