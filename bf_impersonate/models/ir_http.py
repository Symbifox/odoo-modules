"""Les gardes du point d'entrée : qui sert la requête, et ce qu'elle peut faire."""
import logging
import time

from odoo import SUPERUSER_ID, _, models
from odoo.exceptions import AccessError
from odoo.http import request

from .. import impersonation as imp

_logger = logging.getLogger(__name__)

PASS = "pass"     # servir normalement
DRY = "dry"       # lecture seule : jouer à blanc
ACT = "act"       # écriture : servir, relever les écritures, consigner
NOOP = "noop"     # lecture seule : répondre « fait » sans rien exécuter


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    # ------------------------------------------------------------------
    # Fin d'incarnation : durée passée ou journal fermé par un administrateur

    @classmethod
    def _authenticate(cls, endpoint):
        super()._authenticate(endpoint)
        payload = request.session.get(imp.SESSION_KEY)
        if not payload:
            return
        if request.session.uid != payload.get("target_uid"):
            # Déconnexion puis reconnexion : la charge ne décrit plus rien.
            request.session.pop(imp.SESSION_KEY, None)
            return
        if imp.expired(payload):
            reason = "expired"
        elif cls._bf_impersonate_journal_closed(payload):
            reason = "forced"
        else:
            return
        imp.restore(payload, reason)
        if not cls._bf_impersonate_explicit_write(endpoint):
            return
        # La requête écrivait sous la personne : la jouer sous l'incarnateur la
        # ferait signer d'un autre nom. Odoo ne sauve la session que sur
        # succès, d'où la sauvegarde explicite avant le refus.
        request._save_session()
        raise AccessError(_(
            "The impersonation has ended. Reload the page to continue under "
            "your own account."))

    @classmethod
    def _bf_impersonate_journal_closed(cls, payload):
        journal = request.env(user=SUPERUSER_ID)["bf.impersonate.session"].browse(
            payload.get("journal_id") or 0).exists()
        return not journal or bool(journal.date_end)

    @classmethod
    def _bf_impersonate_explicit_write(cls, endpoint):
        """Écriture explicite, lue avant que ``request.params`` existe (appelé
        depuis ``_authenticate``) : on relit le JSON brut, comme le fait Odoo."""
        path = request.httprequest.path
        if path in imp.WRITE_ROUTES:
            return True
        if not path.startswith(imp.CALL_KW_ROUTES):
            return False
        try:
            params = request.get_json_data().get("params") or {}
        except Exception:  # noqa: BLE001 - corps illisible : on refuse
            return True
        method = params.get("method")
        if path.startswith(imp.CALL_BUTTON_ROUTE) or imp.method_is_button(method):
            return not imp.method_is_read(request.env, params.get("model"), method)
        return method in imp.WRITE_METHODS

    # ------------------------------------------------------------------
    # Le garde de chaque requête incarnée

    @classmethod
    def _dispatch(cls, endpoint):
        payload = imp.current()
        if not payload:
            return super()._dispatch(endpoint)
        kind, call = cls._bf_impersonate_verdict(endpoint, payload)
        if kind == PASS:
            return super()._dispatch(endpoint)
        if kind == DRY:
            return cls._bf_impersonate_dry(endpoint)
        if kind == NOOP:
            return True
        request._bf_impersonate_writes = []
        result = super()._dispatch(endpoint)
        cls._bf_impersonate_record(payload, call, request._bf_impersonate_writes)
        return result

    @classmethod
    def _bf_impersonate_verdict(cls, endpoint, payload):
        """``(PASS|DRY|ACT, appel)``, ou refuser."""
        path = request.httprequest.path
        read_only = payload.get("mode") != imp.MODE_WRITE

        if path.startswith(imp.STATIC_ROUTE_PREFIXES) and request.httprequest.method in ("GET", "HEAD"):
            return PASS, None

        if path in imp.ALWAYS_ALLOWED_ROUTES:
            if path in ("/web/session/logout", "/web/session/destroy"):
                # Dans cet ordre. Fermer le journal d'abord : ces routes sont en
                # lecture seule, l'écriture fait rejouer la requête sur un
                # curseur d'écriture, et la session ne doit pas avoir bougé entre
                # les deux passes. Rendre la session ensuite, puis aviser : sous
                # la personne, le garde refuserait l'avis.
                journal = request.env(user=SUPERUSER_ID)["bf.impersonate.session"].browse(
                    payload["journal_id"])
                closed = journal._bf_close("logout", notify=False)
                # L'ORM garde l'écriture en cache : la pousser maintenant, pour
                # que le refus du curseur en lecture seule tombe ici.
                closed.flush_recordset()
                imp.restore(payload, "logout")
                for entry in closed:
                    entry._bf_notify("end")
            return PASS, None

        if imp.route_denied(path):
            cls._bf_impersonate_refuse(
                _("This is never available while you see Symbifox as someone else."))

        if path.startswith(imp.CALL_KW_ROUTES):
            return cls._bf_impersonate_call_kw(path, read_only)

        explicit = path in imp.WRITE_ROUTES or path in imp.LOGGED_ROUTES
        if read_only:
            if path in imp.WRITE_ROUTES:
                cls._bf_impersonate_refuse_read()
            return DRY, None
        # Les routes du fil (/mail/message/post...) nomment la fiche visée.
        params = request.params or {}
        thread_id = params.get("thread_id")
        return ACT, {
            "route": path,
            "explicit": explicit,
            "model": params.get("thread_model") or False,
            "record_ids": [thread_id] if isinstance(thread_id, int) else [],
        }

    @classmethod
    def _bf_impersonate_call_kw(cls, path, read_only):
        summary = imp.call_kw_summary(request.params)
        model, method = summary["model"], summary["method"]
        is_read = imp.method_is_read(request.env, model, method)
        button = path.startswith(imp.CALL_BUTTON_ROUTE) or imp.method_is_button(method)
        transient = model in request.env.registry and request.env[model]._transient
        explicit = not is_read and (button or (method in imp.WRITE_METHODS and not transient))

        if imp.PHONE_METHOD_PART in (method or ""):
            cls._bf_impersonate_refuse(
                _("The phone is never available while you see Symbifox as someone "
                  "else: it would use their line and their SIP secret."))
        if imp.model_private(request.env, model):
            cls._bf_impersonate_refuse(
                _("This person's private data (health, private conversations) is "
                  "never shown while you see Symbifox as them."))
        if imp.method_denied(method):
            cls._bf_impersonate_refuse(
                _("Changing someone's password, keys or two-factor authentication "
                  "is never available while you see Symbifox as them."))
        if imp.SEND_METHOD_PART in method and not is_read and not method.startswith(imp.READ_LIKE_PREFIXES):
            cls._bf_impersonate_refuse(
                _("Nothing is sent while you see Symbifox as someone else: no "
                  "email, no text message. Go back to your own account to send it."))
        if method == "message_post":
            kwargs = request.params.get("kwargs") or {}
            if any(kwargs.get(key) for key in imp.SMS_KWARGS) or kwargs.get("message_type", "comment") != "comment":
                cls._bf_impersonate_refuse(
                    _("While you see Symbifox as someone else, only internal notes without "
                      "recipients can be posted. Go back to your own account to send a message."))
        if imp.model_denied(model) and not is_read and not method.startswith(imp.READ_LIKE_PREFIXES):
            cls._bf_impersonate_refuse(
                _("This is never available while you see Symbifox as someone else."))
        if model == "res.users" and method in ("create", "write", "web_save"):
            if imp._sensitive_user_fields(summary["vals"]):
                cls._bf_impersonate_refuse(
                    _("Logins, passwords, contact details and access rights are never "
                      "changed while you see Symbifox as someone else."))
        if (
            model == "res.partner"
            and method in ("write", "web_save")
            and request.env.user.partner_id.id in summary["ids"]
            and imp.SENSITIVE_OWN_PARTNER_FIELDS.intersection(summary["fields"])
        ):
            # Changer l'adresse de la personne, c'est se faire envoyer sa
            # prochaine réinitialisation de mot de passe.
            cls._bf_impersonate_refuse(
                _("The person's own email address and phone numbers are never "
                  "changed while you see Symbifox as them."))

        if read_only:
            if (model, method) in imp.SILENT_IN_READ and not path.startswith(imp.CALL_BUTTON_ROUTE):
                return NOOP, None
            if explicit:
                cls._bf_impersonate_refuse_read()
            return DRY, None
        return ACT, {
            "route": path,
            "model": model,
            "method": method,
            "explicit": explicit,
            "record_ids": summary["ids"],
            "field_names": summary["fields"],
        }

    @classmethod
    def _bf_impersonate_dry(cls, endpoint):
        """Servir dans un point de sauvegarde annulé : rien ne persiste.

        Le point de sauvegarde d'Odoo vide le cache et les crochets d'avant
        validation en annulant ; ceux d'après validation (avis du bus, appels
        différés) sont remis à leur état d'avant à la main.
        """
        cr = request.env.cr
        kept = list(cr.postcommit._funcs)
        savepoint = cr.savepoint(flush=True)
        request._bf_impersonate_dry = True
        # Un commit explicite pendant la requête ferait sortir ce qui précède du
        # point de sauvegarde : refusé le temps du jeu à blanc.
        cr.commit = cls._bf_impersonate_refuse_commit
        try:
            result = super()._dispatch(endpoint)
            request.env.flush_all()
            cr.flush()
            return result
        finally:
            del cr.commit
            request._bf_impersonate_dry = False
            savepoint.close(rollback=True)
            cr.postcommit._funcs.clear()
            cr.postcommit._funcs.extend(kept)

    @staticmethod
    def _bf_impersonate_refuse_commit(*args, **kwargs):
        raise AccessError(_(
            "Read only: nothing is saved while you see Symbifox as someone else."))

    @classmethod
    def _bf_impersonate_refuse_read(cls):
        cls._bf_impersonate_refuse(_(
            "Read only: you are seeing Symbifox as %s. Go back to your own "
            "account to make changes.", request.env.user.name))

    @classmethod
    def _bf_impersonate_refuse(cls, message):
        params = getattr(request, "params", None) or {}
        detail = ""
        if request.httprequest.path.startswith(imp.CALL_KW_ROUTES):
            detail = f" {params.get('model')}.{params.get('method')}"
        _logger.warning(
            "bf_impersonate: refusé sous uid %s : %s %s%s",
            request.session.uid, request.httprequest.method, request.httprequest.path, detail)
        raise AccessError(message)

    @classmethod
    def _bf_impersonate_record(cls, payload, call, writes):
        """Une ligne de journal par requête qui a écrit, ou qui l'a demandé."""
        writes = [w for w in writes if w["model"] not in imp.NOISE_MODELS]
        if not writes and not call.get("explicit"):
            return
        ids = list(call.get("record_ids") or [])
        fields = list(call.get("field_names") or [])
        for write in writes:
            if write.get("sudo"):
                continue
            if write["model"] == call.get("model"):
                ids += [i for i in write["ids"] if i not in ids]
            fields += [f for f in write["fields"] if f not in fields]
        details = "\n".join(
            "%s %s [%s] %s%s" % (w["model"], w["op"], ",".join(map(str, w["ids"])),
                                 ", ".join(w["fields"]), " (sudo)" if w.get("sudo") else "")
            for w in writes[:100])
        request.env(user=SUPERUSER_ID)["bf.impersonate.session.line"].create({
            "session_id": payload["journal_id"],
            "route": call.get("route"),
            "model": call.get("model") or (writes[0]["model"] if writes else False),
            "method": call.get("method") or (writes[0]["op"] if writes else False),
            "record_ids": ",".join(map(str, ids[:50])),
            "field_names": ", ".join(fields[:50]),
            "details": details,
        })

    # ------------------------------------------------------------------
    # Ce que le client web doit savoir

    def session_info(self):
        info = super().session_info()
        payload = imp.current()
        user = request.env.user
        if payload:
            origin = request.env(user=SUPERUSER_ID)["res.users"].browse(payload["from_uid"])
            info["bf_impersonate"] = {
                "from_name": origin.name,
                "from_uid": origin.id,
                "target_name": user.name,
                "mode": payload.get("mode"),
                "seconds_left": max(0, int(float(payload.get("expires") or 0) - time.time())),
                "journal_id": payload.get("journal_id"),
            }
            # Le client web inscrit l'usager servi dans la liste des derniers
            # usagers connectés du navigateur (web.lastConnectedUser), que la
            # page de connexion propose ensuite. Sans quick_login, il vide la
            # liste au lieu d'y ajouter la personne incarnée.
            info["quick_login"] = False
        else:
            info["bf_impersonate"] = False
        info["bf_impersonate_can"] = bool(
            not payload
            and user._is_internal()
            and user.has_group("bf_impersonate.group_impersonate_read"))
        return info
