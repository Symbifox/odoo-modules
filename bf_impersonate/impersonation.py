"""Ce que porte une session incarnée, et ce qu'elle a le droit de faire.

Tout passe par la session HTTP : c'est elle qui donne l'uid de chaque requête.
Rien ici n'existe hors requête. Un cron, un appel XML-RPC ou une clé d'API
n'incarnent jamais personne, et les gardes ci-dessous les laissent passer.

En lecture seule, deux gestes :

* les écritures explicites (enregistrer, un bouton, publier un message,
  téléverser) sont refusées, avec un message qui dit pourquoi ;
* tout le reste s'exécute « à blanc » : dans un point de sauvegarde SQL annulé
  à la fin de la requête. Les pastilles et tableaux de bord des modules maison
  appellent en fond des méthodes de lecture qu'Odoo ne marque pas comme telles
  (mesuré le 2026-10-08 : 34 méthodes sur 10 applications) ; une liste
  blanche serait sans fin, et une liste noire de modèles fuit, parce que
  beaucoup d'écritures passent en ``sudo()`` après un contrôle d'accès. À blanc,
  rien ne persiste, ``sudo()`` compris.

Ce que le point de sauvegarde n'annule pas, un appel réseau fait par une
méthode de lecture maison (IMAP, API), reste possible : les envois, eux, sont
refusés dans les deux modes (``mail.mail``, ``send_email``, tout message autre
qu'une note interne sans destinataire, et les modèles ``sms.*`` à l'appel).

Aucun ``_inherit`` de ``base`` ni de ``mail.thread`` : ils font réinitialiser
tous les modèles qui en héritent à chaque installation ou montée (pose sur un
locataire morte le 2026-10-08 sur une clé étrangère de ``mail_tracking_email``).
"""
import logging
import time

from odoo.http import request
from odoo.service import security

_logger = logging.getLogger(__name__)

SESSION_KEY = "bf_impersonate"

MODE_READ = "read"
MODE_WRITE = "write"

# Routes servies normalement dans tous les cas : sortir de l'incarnation, se
# déconnecter, relire sa session.
ALWAYS_ALLOWED_ROUTES = frozenset({
    "/bf_impersonate/stop",
    "/web/session/logout",
    "/web/session/destroy",
    "/web/session/get_session_info",
    "/web/session/check",
})

# Actifs, traductions et images : servis normalement. À blanc, un paquet
# d'actifs généré pendant une incarnation serait annulé, puis régénéré à la
# requête suivante, en laissant ses fichiers orphelins au magasin.
STATIC_ROUTE_PREFIXES = (
    "/web/assets/",
    "/web/bundle/",
    "/web/image",
    "/web/webclient/translations",
    "/website/translations/",
)

# Routes refusées dans les deux modes : prendre l'identité de la personne
# (mot de passe, double authentification), changer d'usager encore, ou parler
# à Gen, qui passe par XML-RPC avec la clé d'opérateur de la personne et
# échappe donc à la session et aux gardes.
DENIED_ROUTE_PREFIXES = (
    "/web/become",
    "/web/session/authenticate",
    "/auth_totp/",
    "/claude-chat/",
    # Rejoindre ou lancer un appel Discuss sous la personne.
    "/mail/rtc/",
)

# Morceaux de chemin refusés où qu'ils soient : appairer un téléphone, une
# extension ou un compte OAuth au nom de la personne lui laisserait un jeton
# qui survit à l'incarnation (porte de publication du 2026-10-08 : routes
# /…/mobile/v1/auth/start et /consent de cinq modules maison).
DENIED_ROUTE_PARTS = ("/auth/start", "/auth/consent", "/oauth/")

# Exceptions de lecture au refus de Gen : relire les conversations.
GEN_READ_ROUTES = frozenset({
    "/claude-chat/sessions",
    "/claude-chat/messages",
    "/claude-chat/search-tasks",
})

# Routes qui écrivent par nature : refusées en lecture seule, consignées en
# écriture. /web/action/run n'y est pas (des menus s'ouvrent par une action
# serveur) mais se consigne toujours en écriture : voir LOGGED_ROUTES.
LOGGED_ROUTES = frozenset({"/web/action/run"})

WRITE_ROUTES = frozenset({
    "/mail/message/post",
    "/mail/message/update_content",
    "/mail/message/reaction",
    "/mail/attachment/delete",
    "/mail/attachment/upload",
    "/web/binary/upload",
    "/web/binary/upload_attachment",
    "/web/dataset/resequence",
    "/html_editor/attachment/add_data",
    "/html_editor/attachment/add_url",
    "/discuss/channel/join",
    "/discuss/channel/sub_channel/create",
    "/mail/rtc/channel/join_call",
})

CALL_KW_ROUTE = "/web/dataset/call_kw"
CALL_BUTTON_ROUTE = "/web/dataset/call_button"
CALL_KW_ROUTES = (CALL_KW_ROUTE, CALL_BUTTON_ROUTE)

# Méthodes de lecture que le client web appelle et qui ne portent pas le
# décorateur ``@api.readonly`` d'Odoo. ``onchange`` calcule en mémoire.
READ_METHODS = frozenset({
    "check_access", "check_access_rights", "default_get", "display_name_for",
    "fields_get", "get_available_models",
    "get_activity_data", "get_empty_list_help", "get_formview_action",
    "get_formview_id", "get_views", "has_access", "has_group", "has_groups",
    "name_search", "onchange", "read", "read_group", "read_progress_bar",
    "search", "search_count", "search_panel_select_multi_range",
    "search_panel_select_range", "search_read", "systray_get_activities",
    "web_read", "web_read_group", "web_search_read",
})

# Méthodes qui écrivent par nature : refusées en lecture seule (plutôt que
# jouées à blanc, ce qui ferait croire à un enregistrement), consignées en
# écriture. Les boutons (call_button) le sont tous. Sur un modèle transitoire
# (assistant, tableau de bord calculé), enregistrer n'est qu'un état d'écran :
# joué à blanc. /web/action/run non plus n'est pas refusé : des menus s'ouvrent
# par une action serveur (To-do, CRM, mesuré le 2026-10-08).
WRITE_METHODS = frozenset({
    "action_archive", "action_feedback", "action_feedback_schedule_next",
    "action_unarchive", "activity_schedule", "copy", "create", "message_post",
    "message_subscribe", "message_unsubscribe", "name_create", "toggle_active",
    "unlink", "web_resequence", "web_save", "write",
})

# Méthodes refusées sur tout modèle, dans les deux modes : elles touchent à
# l'identité de la personne.
DENIED_METHODS = frozenset({
    "action_reset_password", "action_revoke_all_devices",
    "action_totp_disable", "action_totp_enable_wizard", "action_totp_invite",
    "api_key_wizard", "change_password", "preference_change_password",
})

# Le téléphone (bf_softphone) : sa configuration porte le secret SIP de la
# personne, qui survit à l'incarnation, et un appel joué « à blanc » part quand
# même sur sa ligne (le central ne s'annule pas). Toute méthode qui le nomme est
# refusée, dans les deux modes (porte de publication du 2026-10-08).
PHONE_METHOD_PART = "softphone"

# Une méthode d'écriture qui dit « send » envoie : refusée dans les deux modes,
# quel que soit le transport (un module maison peut passer par son propre
# fournisseur, que ni mail.mail ni sms.sms ne voient).
SEND_METHOD_PART = "send"

# Les SMS que message_post envoie sur-le-champ, sans abonné ni destinataire.
SMS_KWARGS = ("sms_numbers", "sms_pid_to_number")

# Un bouton reste un bouton quand le client l'appelle par call_kw plutôt que
# par call_button : en lecture seule, il serait joué à blanc, et ses effets hors
# de la base (un désabonnement RFC 8058, un SMS) ne s'annulent pas. Seuls les
# noms de navigation, qui rendent une action à ouvrir, passent.
BUTTON_PREFIXES = ("action_", "button_")
NAVIGATION_PREFIXES = ("action_open", "action_view", "action_get", "action_show", "action_download")

# En lecture seule, ce que l'écran déclenche TOUT SEUL en affichant une fiche
# reçoit « fait » sans rien exécuter, par call_kw seulement (un bouton, lui,
# reste refusé avec son message) : la personne ne retrouve pas ses courriels
# marqués lus par vous, et aucune boîte d'erreur ne s'ouvre. Liste nommée, pas
# un préfixe : un « marquer fait » cliqué doit être refusé, pas feint.
SILENT_IN_READ = frozenset({
    ("bf.email", "action_mark_read"),
    ("bf.bookmark", "action_mark_used"),
})

# Modèles qu'on ne touche jamais sous la personne : identité, Gen, coffre OTP,
# signature, SMS, infolettres. Dans les deux modes, seules les lectures connues
# et les méthodes à préfixe de lecture (READ_LIKE_PREFIXES) passent.
DENIED_MODEL_PREFIXES = (
    "auth_totp.",
    "bf.otp",
    "bf.sign",
    "change.password.",
    "claude.chat",
    "res.users.apikeys",
    "res.users.identitycheck",
    "sms.",
    # Une infolettre mise en file part ensuite par cron, hors de toute garde.
    "mailing.",
    # Droits et configuration : jamais modifiés sous quelqu'un d'autre. La règle
    # « pas plus de droits que soi » de l'assistant ferme déjà l'escalade pour un
    # incarnateur qui n'est pas administrateur ; ceci la ferme aussi quand la
    # cible est administratrice (réglage permis), relecture adverse du 2026-10-08.
    "base.automation",
    "ir.actions.",
    "ir.config_parameter",
    "ir.cron",
    "ir.model",
    "ir.module.",
    "ir.rule",
    "res.company",
    "res.config.settings",
    "res.groups",
    # La fusion de contacts relie les usagers au contact gagnant par SQL, hors
    # de l'ORM (septième tour de la porte).
    "base.partner.merge.",
    "base.module.",
)

# Données intimes de la personne : invisibles pendant une incarnation, en
# lecture comme en écriture, administrateurs compris (décision du
# 2026-10-08). Un modèle l'est s'il déclare ``_gen_scope`` (santé, journal
# d'humeur, et les modules personnels qui suivront) ou par son préfixe, par
# sécurité. Les conversations Gen marquées ``gen_private`` le sont aussi.
PRIVATE_MODEL_PREFIXES = ("health.",)
# Le coffre des identifiants (bf_credentials) : la personne y lit des mots de
# passe en clair, qui survivraient à l'incarnation.
PRIVATE_MODEL_NAMES = frozenset({"project.credential"})
# Conversations Gen marquées privées au premier tour (``gen_private``) ou
# rattachées à une fiche intime (``gen_scope``), et leurs messages.
PRIVATE_FLAG_PATHS = {
    "claude.chat.session": "",
    "claude.chat.message": "session_id.",
}
PRIVATE_FLAG_FIELDS = ("gen_private", "gen_scope")
# Ce qui se rattache à une fiche par (modèle, id) et se lit sans passer par la
# fiche : Odoo laisse chacun relire les messages qu'il a écrits et les pièces
# jointes qu'il a déposées, quel que soit le document (mesuré le
# 2026-10-08 : la note de la personne sur sa fiche santé restait lisible).
PRIVATE_LINK_FIELDS = {
    "mail.message": "model",
    "mail.activity": "res_model",
    "mail.followers": "res_model",
    "ir.attachment": "res_model",
    "rating.rating": "res_model",
}

# Tout modèle d'appareil appairé (jeton durable) : jamais créé ni modifié.
DENIED_MODEL_PARTS = (".device",)

# Sur un modèle refusé, seules ces formes passent, en plus des lectures
# connues : les pastilles et compteurs (le SMS interroge à chaque page). Une
# méthode maison inconnue y est refusée, même en lecture seule : jouée à blanc,
# elle pourrait déjà avoir appelé l'API d'un fournisseur (un SMS parti ne
# s'annule pas par un point de sauvegarde).
READ_LIKE_PREFIXES = ("get_", "load_", "read_", "search_", "systray_", "count_", "has_", "is_")

# Champs de res.users que l'incarnation n'écrit jamais, même en écriture.
SENSITIVE_USER_FIELDS = frozenset({
    "active", "api_key_ids", "company_ids", "email", "groups_id", "login",
    "new_password", "oauth_access_token", "oauth_provider_id", "oauth_uid",
    "password", "share", "totp_secret", "bf_impersonate_protected",
    # Les coordonnées que res.users écrit sur la fiche contact de la personne :
    # par délégation (phone, mobile) ou par les champs liés de l'employé (hr)
    # dont l'inverse écrit en sudo() sur ce même contact (work_email,
    # mobile_phone). À l'ORM, on les juge à la valeur : réécrire la même passe.
    "phone", "mobile", "work_email", "mobile_phone",
    # Relier l'usager à un autre contact, c'est changer l'adresse de
    # réinitialisation d'un coup ; le relier, depuis sa fiche d'usager, à une
    # autre fiche employée change ses coordonnées liées.
    "partner_id", "work_contact_id", "employee_ids",
})

# Coordonnées de res.users jugées à la valeur par la garde de l'ORM (réécrire
# la même passe).
CONTACT_USER_FIELDS = frozenset({"email", "phone", "mobile", "work_email", "mobile_phone"})

# Champs de la fiche contact de la personne qui servent à la joindre ou à lui
# rendre son compte (réinitialisation du mot de passe) : jamais changés sous elle.
SENSITIVE_OWN_PARTNER_FIELDS = frozenset({"email", "mobile", "phone"})
SENSITIVE_USER_FIELD_PREFIXES = ("in_group_", "sel_groups_")

# Écritures que la simple navigation provoque : jamais consignées au journal.
NOISE_MODELS = frozenset({
    "bf.impersonate.session",
    "bf.impersonate.session.line",
    "bus.presence",
    "res.users.log",
    "res.users.settings",
    "res.users.settings.volumes",
})


def current():
    """La charge d'incarnation de la requête en cours, ou ``None``.

    La charge ne vaut que si la session sert encore l'usager visé : une
    déconnexion suivie d'une reconnexion la rend caduque.
    """
    try:
        if not request:
            return None
        session = request.session
    except RuntimeError:
        return None
    if session is None:
        return None
    payload = session.get(SESSION_KEY)
    if not payload or session.uid != payload.get("target_uid"):
        return None
    return payload


def in_dry():
    """Vrai pendant une requête jouée à blanc (lecture seule)."""
    try:
        return bool(request) and bool(getattr(request, "_bf_impersonate_dry", False))
    except RuntimeError:
        return False


def switch_to(target, payload):
    """Servir désormais la session sous ``target``.

    La langue et le fuseau deviennent les siens : voir l'écran de la personne,
    c'est aussi le voir dans sa langue.
    """
    session = request.session
    payload = dict(
        payload,
        target_uid=target.id,
        target_partner_id=target.partner_id.id,
        from_context=dict(session.context or {}),
        from_cids=request.httprequest.cookies.get("cids") or "",
    )
    session[SESSION_KEY] = payload
    session.uid = target.id
    session.context = dict(target.with_user(target).context_get())
    _reseal(target.id)
    # Les sociétés actives de l'incarnateur ne sont pas celles de la personne :
    # partir de sa société à elle, et rendre les siennes au retour.
    request.future_response.set_cookie("cids", str(target.company_id.id))
    _logger.info(
        "bf_impersonate: uid %s voit Symbifox comme uid %s (mode %s, journal %s)",
        payload["from_uid"], target.id, payload["mode"], payload["journal_id"],
    )
    return payload


def restore(payload, reason):
    """Rendre la session à l'incarnateur, sans écrire en base.

    Appelée aussi depuis ``_authenticate``, souvent sur un curseur en lecture
    seule : la fermeture du journal se fait ailleurs (route d'arrêt ou cron).
    """
    session = request.session
    session.pop(SESSION_KEY, None)
    session.uid = payload["from_uid"]
    if payload.get("from_context"):
        session.context = payload["from_context"]
    _reseal(payload["from_uid"])
    if payload.get("from_cids"):
        request.future_response.set_cookie("cids", payload["from_cids"])
    else:
        request.future_response.set_cookie("cids", max_age=0)
    _logger.info(
        "bf_impersonate: uid %s revient à son compte (journal %s, %s)",
        payload["from_uid"], payload.get("journal_id"), reason,
    )


def _reseal(uid):
    # Même geste que /web/become : le jeton de session est mis en cache par
    # sid, pas par usager, donc changer l'uid exige de vider le cache.
    request.env.registry.clear_cache()
    request.session.session_token = security.compute_session_token(
        request.session, request.env)
    request.update_env(user=uid)


def expired(payload):
    return time.time() >= float(payload.get("expires") or 0)


def method_is_read(env, model, method):
    """Même lecture que ``DataSet._call_kw_readonly`` d'Odoo, plus nos ajouts."""
    if method in READ_METHODS:
        return True
    if not model or model not in env.registry:
        return False
    for cls in env.registry[model].mro():
        attr = getattr(cls, method, None)
        if attr is not None and hasattr(attr, "_readonly"):
            return bool(attr._readonly)
    return False


def route_denied(path):
    """Route refusée dans les deux modes (identité, Gen, appairage)."""
    if path in GEN_READ_ROUTES:
        return False
    return path.startswith(DENIED_ROUTE_PREFIXES) or any(part in path for part in DENIED_ROUTE_PARTS)


def model_denied(model):
    """Modèle dont les écritures ne passent jamais sous la personne."""
    return bool(model) and (
        model.startswith(DENIED_MODEL_PREFIXES) or any(part in model for part in DENIED_MODEL_PARTS))


def model_private(env, model):
    """Modèle dont aucune fiche n'est visible pendant une incarnation."""
    if not model:
        return False
    if model.startswith(PRIVATE_MODEL_PREFIXES) or model in PRIVATE_MODEL_NAMES:
        return True
    return model in env.registry and bool(getattr(env.registry[model], "_gen_scope", None))


# Liste des modèles intimes, une fois par registre : posée SUR le registre, elle
# meurt avec lui. Les essais qui posent ``_gen_scope`` à chaud l'oublient.
_PRIVATE_ATTR = "_bf_impersonate_private_models"


def private_models(env):
    found = getattr(env.registry, _PRIVATE_ATTR, None)
    if found is None:
        found = [name for name in env.registry.models if model_private(env, name)]
        setattr(env.registry, _PRIVATE_ATTR, found)
    return found


def forget_private_models(env):
    env.registry.__dict__.pop(_PRIVATE_ATTR, None)


def method_denied(method):
    return method in DENIED_METHODS or PHONE_METHOD_PART in (method or "")


def method_is_button(method):
    method = method or ""
    return method.startswith(BUTTON_PREFIXES) and not method.startswith(NAVIGATION_PREFIXES)


def private_domain(env, model):
    """Domaine à ajouter aux règles d'accès de ``model`` pendant une incarnation,
    ou ``None``. Le marqueur ``gen_private`` est récent : sans lui, rien à cacher."""
    if model_private(env, model):
        return [(0, "=", 1)]
    link = PRIVATE_LINK_FIELDS.get(model)
    if link and model in env.registry:
        private = private_models(env)
        return [(link, "not in", private)] if private else None
    prefix = PRIVATE_FLAG_PATHS.get(model)
    if prefix is not None and "claude.chat.session" in env.registry:
        session_fields = env["claude.chat.session"]._fields
        leaves = [(prefix + name, "=", False) for name in PRIVATE_FLAG_FIELDS if name in session_fields]
        return leaves or None
    return None


def _sensitive_user_fields(vals):
    """Les champs sensibles d'un dictionnaire, ou de chaque dictionnaire d'une
    liste : un ``create`` en lot ne se contrôle pas sur sa première fiche."""
    vals_list = vals if isinstance(vals, list) else [vals]
    return sorted({
        key
        for one in vals_list if isinstance(one, dict)
        for key in one
        if key in SENSITIVE_USER_FIELDS
        or key.startswith(SENSITIVE_USER_FIELD_PREFIXES)
    })


def call_kw_summary(params):
    """Modèle, méthode, fiches et champs d'un appel call_kw, pour le journal."""
    model = params.get("model") or ""
    method = params.get("method") or ""
    args = params.get("args") or []
    kwargs = params.get("kwargs") or {}
    ids = []
    first = args[0] if args else None
    if isinstance(first, int):
        ids = [first]
    elif isinstance(first, list) and all(isinstance(i, int) for i in first):
        ids = first
    vals = None
    if method in ("write", "web_save") and len(args) > 1:
        vals = args[1]
    elif method in ("write", "web_save"):
        vals = kwargs.get("vals") or kwargs.get("values")
    elif method == "create":
        vals = args[0] if args else (kwargs.get("vals_list") or kwargs.get("vals"))
    vals_list = vals if isinstance(vals, list) else [vals]
    fields = sorted({key for one in vals_list if isinstance(one, dict) for key in one})
    return {"model": model, "method": method, "ids": ids, "fields": fields, "vals": vals}
