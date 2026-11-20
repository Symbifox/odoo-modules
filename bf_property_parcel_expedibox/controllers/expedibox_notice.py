"""La seule porte ouverte de ce module, et ce qui la garde.

Un avis d'Expedibox arrive sans session, sans usager, sans jeton de formulaire.
La route est donc publique, et tout ce qui la protège tient en cinq gardes, dans
cet ordre :

1. **la taille** : le corps est lu borné, avant d'être analysé. Leur objet de
   réservation porte trois images QR en base64 — même élagué chez eux, un corps
   peut être gros, et un corps énorme ne doit pas entrer en mémoire ;
2. **le jeton de l'adresse** : comparé en temps constant à celui d'un
   connecteur Expedibox. Inconnu, la route répond 404 — pas 403 : une adresse
   fausse ne doit pas apprendre à son auteur qu'il a presque trouvé ;
3. **l'en-tête d'organisation** (``X-Expedibox-Organization-Id``) : c'est le
   second contrôle, et le connecteur refuse d'écouter sans lui ;
4. **le plafond** : un connecteur qui reçoit plus de 120 avis par minute est
   dans une boucle ou sous un flot ; la route répond 429 et n'écrit rien ;
5. **la forme** : un événement nommé, un horodatage lisible, une référence de
   réservation. Sans les trois, c'est 400 et rien n'est écrit.

🔴 **Ce qui n'est PAS une garde** : le corps lui-même. Il n'est pas signé, donc
il ne prouve rien. La route en retient trois valeurs (l'événement, l'heure, la
réservation) et jette le reste ; ce qui s'est vraiment passé est relu chez
Expedibox par le passage planifié, avec notre jeton d'application.

⚠️ La route répond 200 dès que l'avis est écrit. Elle ne relit rien, n'appelle
personne et ne touche à aucun colis : sa latence est celle d'un INSERT.
"""
import hmac
import json
import logging

from odoo import http
from odoo.http import request

from ..models.bf_property_parcel_expedibox_event import parse_instant

_logger = logging.getLogger(__name__)

# 512 ko : large pour un objet de réservation sans ses QR, étroit pour un flot.
MAX_BODY = 512 * 1024
MAX_PER_MINUTE = 120


class ExpediboxNoticeController(http.Controller):

    @http.route(
        "/symbifox/expedibox/avis/<string:jeton>",
        type="http", auth="public", methods=["POST"], csrf=False,
        save_session=False,
    )
    def expedibox_notice(self, jeton, **_kw):
        """Reçoit un avis de réservation d'Expedibox."""
        httprequest = request.httprequest
        taille = httprequest.content_length or 0
        # 🔴 Un corps en « chunked » n'annonce AUCUNE taille : le plafond ne
        # tirerait pas, et le corps entrerait en mémoire avant d'être mesuré.
        # Un avis d'Expedibox annonce la sienne ; ce qui ne l'annonce pas n'a
        # pas à être lu.
        if not taille:
            return self._answer(411, "length required")
        if taille > MAX_BODY:
            return self._answer(413, "body too large")
        connector = self._connector(jeton)
        if not connector:
            # Ni le motif, ni le nombre de connecteurs, ni l'existence du
            # chemin : une adresse fausse n'apprend rien.
            return self._answer(404, "not found")
        organization = httprequest.headers.get("X-Expedibox-Organization-Id") or ""
        if not connector._expedibox_notice_identity(organization):
            _logger.warning(
                "Avis Expedibox refusé sur le connecteur %s : en-tête "
                "d'organisation qui ne correspond pas", connector.id)
            return self._answer(403, "organization mismatch")
        events = request.env["bf.property.parcel.expedibox.event"].sudo()
        if events._bf_recent_count(connector) >= MAX_PER_MINUTE:
            _logger.warning("Avis Expedibox : plafond d'une minute atteint sur "
                            "le connecteur %s", connector.id)
            return self._answer(429, "too many notices")
        payload = self._payload(httprequest)
        if payload is None:
            return self._answer(400, "invalid payload")
        event, occurred_at, reservation_ref, locker_ref = payload
        avis, neuf = events._bf_receive(
            connector, event, occurred_at, reservation_ref, locker_ref)
        if neuf:
            # Un rejeu n'a rien écrit : le réveiller ferait 600 appels par
            # minute vers Expedibox avec le jeton du client, de quoi faire
            # brider le compte — et donc casser aussi l'inscription.
            events._bf_trigger_soon()
        return self._answer(200, "received")

    # ── Gardes ───────────────────────────────────────────────────────────

    def _connector(self, jeton):
        """Le connecteur dont l'adresse porte ce jeton, ou rien.

        La recherche se fait sur le type, pas sur le jeton : la comparaison du
        secret se fait ensuite en temps constant, sur des OCTETS.

        🔴 `hmac.compare_digest` sur deux `str` LÈVE dès qu'un caractère n'est
        pas ASCII. Un jeton accentué dans l'adresse rendait donc un 500 avec
        une trace au journal — et ce 500, obtenu seulement là où un connecteur
        écoute, disait au visiteur ce que le 404 refuse de dire (relecture
        adverse du 2026-11-19).
        """
        if not isinstance(jeton, str) or len(jeton) < 20:
            return None
        try:
            offert = jeton.encode("utf-8")
        except (UnicodeEncodeError, AttributeError):
            return None
        connectors = request.env["bf.property.access.connector"].sudo().search(
            [("kind", "=", "expedibox"), ("expedibox_notice_token", "!=", False)])
        for connector in connectors:
            mien = (connector.expedibox_notice_token or "").encode("utf-8")
            if hmac.compare_digest(mien, offert):
                return connector
        return None

    def _payload(self, httprequest):
        """Les trois valeurs qu'on retient, ou None.

        ⛔ Rien d'autre ne sort de cette fonction. Le corps porte le nom du
        destinataire, son courriel, son mobile, les codes de dépôt et de
        retrait et le code d'ouverture de la porte : il n'a aucune raison
        d'exister plus longtemps que cet appel.
        """
        try:
            # ⚠️ `stream.read()` rend du vide : pour une route « http », Odoo a
            # déjà consommé le flux avant d'entrer ici (il remplit `params`).
            # C'est `get_data` qui rend le corps, et la taille déclarée l'a
            # déjà borné plus haut.
            body = httprequest.get_data(cache=False, as_text=False)
        except Exception:  # noqa: BLE001 — un corps illisible est un 400
            return None
        if not body or len(body) > MAX_BODY:
            return None
        try:
            data = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        event = data.get("event")
        reservation = data.get("reservation")
        if not isinstance(event, str) or not event.strip():
            return None
        if not isinstance(reservation, dict):
            return None
        reference = reservation.get("id")
        if not isinstance(reference, (str, int)) or not str(reference).strip():
            return None
        occurred_at = parse_instant(data.get("timestamp"))
        if not occurred_at:
            return None
        locker = reservation.get("locker_id")
        locker = str(locker).strip()[:64] if isinstance(locker, (str, int)) else False
        return (event.strip()[:64], occurred_at, str(reference).strip()[:64], locker)

    def _answer(self, status, message):
        """Une réponse courte, en JSON, qui n'explique rien à l'appelant."""
        return request.make_json_response({"status": message}, status=status)
