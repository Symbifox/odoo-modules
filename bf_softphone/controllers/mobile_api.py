"""API mobile du téléphone — surface REST pour un client SANS pile SIP.

Le client demande un appel ; le PBX fait sonner l'appareil de l'utilisateur puis
compose le correspondant. Aucun média ne transite par le téléphone, donc aucune
pile SIP à embarquer : c'est ce qui rend cette étape livrable tout de suite.

⚠️ Le jeton d'appareil de ``bf_sms_archive`` est réutilisé TEL QUEL. Le module en
dépend déjà (call.archive.call, sms.archive.thread) et le téléphone doit rester
une CAPACITÉ de la session Messages, pas un troisième compte à ouvrir sur
l'appareil. Corollaire assumé : sans jeton Messages, pas de bouton d'appel.
"""

import logging

from odoo import http
from odoo.exceptions import AccessError, UserError
from odoo.http import request

# Import direct plutôt que copie : la sémantique du jeton (résolution, bascule
# d'environnement, 401) doit rester la MÊME que celle des messages. Une copie
# divergerait le jour où l'une des deux est corrigée.
from odoo.addons.bf_sms_archive.controllers.mobile_api import _authed, _body, _json

_logger = logging.getLogger(__name__)

BASE = "/bf_softphone/mobile/v1"


class BfSoftphoneMobileApi(http.Controller):

    # ── Découverte ────────────────────────────────────────────────────
    @http.route(f"{BASE}/ping", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def ping(self, **kw):
        """Sonde de capacité : l'app n'offre le bouton que si le module répond."""
        module = request.env["ir.module.module"].sudo().search(
            [("name", "=", "bf_softphone")], limit=1)
        return _json({
            "ok": True,
            "module": "bf_softphone",
            # api 2 : /sip est apparu, donc l'app peut devenir un poste. Une
            # app plus ancienne lit 2 et ignore ce qu'elle ne connaît pas.
            "api": 2,
            "version": module.installed_version or "",
        })

    # ── Configuration de l'utilisateur ────────────────────────────────
    @http.route(f"{BASE}/config", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    @_authed
    def config(self, device, **kw):
        try:
            return _json(request.env["res.users"].softphone_call_config())
        except AccessError:
            # Pas dans le groupe : ce n'est pas une erreur, c'est une absence de
            # capacité. L'app cache le bouton et n'insiste pas.
            return _json({"enabled": False, "extension": "",
                          "callback_number": "", "default_ring": "callback"})

    @http.route(f"{BASE}/sip", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    @_authed
    def sip(self, device, **kw):
        """Identifiants SIP de l'appareil, pour qu'il devienne un poste.

        ⚠️ Ce point d'entrée rend un MOT DE PASSE en clair. Trois choses le
        rendent acceptable, et il faut que les trois restent vraies :

        1. ``_authed`` a déjà basculé l'environnement sur l'utilisateur de
           l'appareil, et ``get_softphone_config`` lit ``self.env.user`` — pas
           un identifiant fourni par le client. On ne peut donc servir que son
           propre secret, même en trafiquant la requête.
        2. C'est EXACTEMENT ce que le navigateur reçoit déjà par RPC depuis la
           v1 : le téléphone n'ouvre pas une surface nouvelle, il rejoint une
           surface existante.
        3. Le jeton d'appareil est révocable d'un clic, et le mot de passe SIP
           est propre au poste — le compromettre ne donne pas accès à Odoo.

        Le poste rendu est celui de l'utilisateur, le même que le navigateur :
        ``Dial(PJSIP/<poste>)`` fait sonner TOUS les appareils enregistrés, et
        on décroche où l'on veut. C'est pour ça que l'AOR doit accepter
        plusieurs contacts.
        """
        try:
            cfg = request.env["res.users"].get_softphone_config()
        except AccessError:
            return _json({"error": "forbidden"}, 403)
        if not cfg:
            # Pas une erreur : un compte sans poste SIP n'a simplement rien à
            # enregistrer. L'app se rabat sur le mode « rappel ».
            return _json({"enabled": False})
        cfg["enabled"] = True
        return _json(cfg)

    # ── De quoi composer : contacts et journal ────────────────────────
    @http.route(f"{BASE}/contacts", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    @_authed
    def contacts(self, device, **kw):
        """Recherche pour le clavier. Passe par le modèle : ACL de l'appelant
        et étranglement y sont déjà, on ne les réécrit pas ici.

        ⚠️ Après ``device``, uniquement ``**kw`` : le décorateur ``_authed``
        insère l'appareil en premier argument, et un paramètre nommé de plus
        entre en collision avec celui que le routage lie depuis l'URL
        (« got multiple values for argument »). Tout le module suit cette règle.
        """
        try:
            rows = request.env["res.users"].softphone_search_contacts(
                kw.get("q") or "", limit=int(kw.get("limit") or 8))
        except AccessError:
            return _json({"contacts": []})
        return _json({"contacts": rows})

    @http.route(f"{BASE}/calls", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    @_authed
    def calls(self, device, **kw):
        """Journal d'appels de l'utilisateur — ce qu'un téléphone appelle
        « Récents ». Borné à ses propres fils, jamais à ceux d'un autre.

        Même règle que ci-dessus : rien de nommé après ``device``."""
        Call = request.env["call.archive.call"].sudo()
        rows = Call.search_read(
            [("owner_id", "=", request.env.user.id)],
            ["call_type", "date", "duration", "contact_name", "thread_id"],
            order="date desc", limit=max(1, min(int(kw.get("limit") or 40), 100)),
        )
        # ⚠️ active_test=False : un fil archivé reste le porteur du numéro d'un
        # appel passé. Sans ça, la recherche ne rend rien et le journal sort
        # avec des numéros vides — sans la moindre erreur pour le signaler.
        Thread = request.env["sms.archive.thread"].sudo().with_context(active_test=False)
        # ⚠️ Le NOM vit sur le fil, pas sur l'appel : `call.archive.call.contact_name`
        # n'est pas renseigné à l'ingestion, il est resté vide sur la totalité du
        # journal. Le fil, lui, porte le nom du correspondant depuis toujours.
        # Sans ça le téléphone affiche une colonne de numéros bruts, et on croit
        # que l'appareil ne connaît pas ses contacts.
        fils = {
            t["id"]: t for t in Thread.search_read(
                [("id", "in", [r["thread_id"][0] for r in rows if r.get("thread_id")])],
                ["phone_normalized", "contact_name", "partner_id"],
            )
        }

        def _fil(r):
            return fils.get(r["thread_id"][0], {}) if r.get("thread_id") else {}

        def _nom(r):
            fil = _fil(r)
            partenaire = fil.get("partner_id")
            return (r.get("contact_name")
                    or fil.get("contact_name")
                    or (partenaire[1] if partenaire else "")
                    or "")

        return _json({"calls": [{
            "id": r["id"],
            "direction": r["call_type"],
            "number": _fil(r).get("phone_normalized") or "",
            "name": _nom(r),
            # L'app propose « ajouter aux contacts » quand c'est faux, et ouvre
            # la fiche quand c'est vrai : il lui faut donc savoir lequel.
            "partner_id": (_fil(r).get("partner_id") or [False])[0] or 0,
            "date": r.get("date") or "",
            "duration": r.get("duration") or 0,
        } for r in rows]})

    # ── Appel en cours : voir et raccrocher ───────────────────────────
    @http.route(f"{BASE}/active", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    @_authed
    def active(self, device, **kw):
        """Ce qui sonne ou parle en ce moment pour cet utilisateur."""
        try:
            return _json({"calls": request.env["res.users"].softphone_active_calls()})
        except AccessError:
            return _json({"calls": []})

    @http.route(f"{BASE}/dtmf", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    @_authed
    def dtmf(self, device, **kw):
        """Touches vers le correspondant. Corps : {digits: "1"} ou "123#"."""
        try:
            return _json(request.env["res.users"].softphone_send_dtmf(
                _body().get("digits") or ""))
        except AccessError:
            return _json({"error": "forbidden"}, 403)
        except UserError as exc:
            return _json({"error": str(exc)}, 400)

    @http.route(f"{BASE}/hangup", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    @_authed
    def hangup(self, device, **kw):
        """Raccroche. Le combiné ne porte pas l'appel : c'est le PBX qui coupe."""
        try:
            return _json(request.env["res.users"].softphone_hangup(
                _body().get("channel") or None))
        except AccessError:
            return _json({"error": "forbidden"}, 403)

    # ── Composition ───────────────────────────────────────────────────
    @http.route(f"{BASE}/call", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    @_authed
    def call(self, device, **kw):
        """Demande au PBX de monter l'appel. Corps : {number, ring?}.

        ``ring`` vaut ``callback`` (le numéro de rappel de l'utilisateur sonne,
        par le trunk) ou ``extension`` (son poste SIP sonne). À défaut, le mode
        par défaut de sa fiche.
        """
        body = _body()
        try:
            result = request.env["res.users"].softphone_originate(
                body.get("number"), ring=body.get("ring") or None)
        except AccessError:
            return _json({"error": "forbidden"}, 403)
        return _json(result)
