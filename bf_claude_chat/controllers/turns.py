"""Un tour de Gen qui survit à son écran.

🔴 Relevé du 2026-09-14 sur une instance réelle : près d'une question sur trois
posée au bureau restait sans réponse enregistrée. Le tour vivait aussi
longtemps que la chaîne HTTP navigateur, proxy, travailleur Odoo, pont, et le
contrôleur n'écrivait la réponse qu'à la fin du flux : un rechargement de page,
un délai de proxy ou un redémarrage d'Odoo la perdait, même quand le pont
l'avait produite. Le téléphone, qui lit la réponse en base, n'en perdait
presque aucune.

Ce fichier donne au bureau le modèle du téléphone, et le complète :

- **Un fil d'exécution possède le tour.** Il consomme le pont, écrit texte,
  étapes et signe de vie dans le message « en cours », et passe les octets à
  l'écran qui l'a lancé tant que cet écran écoute. Si l'écran part, le fil
  continue et enregistre.
- **Le pont garde le tour vivant sans spectateur** (`turn_key`) : un fil mort
  avec son processus Odoo est remplacé par un autre qui se rattache
  (`/chat-attach`), à la demande d'un écran ou du cron.
- **Les fins propres du pont reprennent seules** : délai dépassé, limite
  d'étapes, surcharge de l'API, tour perdu par le pont. Deux fois au plus, sur
  la même conversation Claude, avec une consigne qui interdit de refaire une
  action déjà faite. Jamais après le bouton Arrêter ni sur une limite
  d'abonnement.

Tout ici tourne sans `request` : les fils ouvrent leur propre curseur.
"""

import json
import logging
import os
import queue
import random
import re
import secrets
import socket
import threading
import time

import odoo
from psycopg2 import OperationalError
from odoo import fields
from odoo.modules.registry import Registry
from odoo.service.model import PG_CONCURRENCY_ERRORS_TO_RETRY

from odoo.addons.bf_ai_bridge.tools import transport

_logger = logging.getLogger(__name__)

# Cadence d'écriture de l'avancement : un jeton par écriture noierait
# Postgres, une par seconde ne « pousserait » pas à l'œil du téléphone.
FLUSH_SECONDS = 0.4
# Signe de vie écrit au moins à cette cadence, même quand rien ne bouge
# (le pont envoie un battement toutes les 15 s).
HEARTBEAT_SECONDS = 10
# Plafond d'un tour chez le pont, et nombre de reprises automatiques.
DEFAULT_WALL_SECONDS = 1200
DEFAULT_AUTO_CONTINUE = 2
# Garde-fou d'un fil, toutes reprises comprises.
RUNNER_MAX_SECONDS = 90 * 60

# Consigne d'une reprise automatique. Elle part au pont comme un message du
# tour, jamais enregistrée comme une prise de parole de la personne.
CONTINUE_PROMPT = (
    "Your previous step was cut off before you finished (reason: {reason}). "
    "Continue exactly where you left off, in the same language. Do not repeat "
    "what you already wrote. Before redoing any action that changes something "
    "(sending, posting, writing, deleting), check whether it already happened."
)

# Une fin qui ne se règle pas en recommençant.
_NO_RETRY = re.compile(
    r"session limit|usage limit|hit your|resets \d|not logged in|invalid api key"
    r"|authentication|credit balance|prompt is too long|no conversation found",
    re.I,
)
# Une panne passagère, qui mérite une seconde chance.
_TRANSIENT = re.compile(
    r"\b5\d\d\b|overloaded|internal server error|timed? ?out|connection (?:reset|error|refused)"
    r"|econnreset|socket hang up|outil a plant|tool crashed",
    re.I,
)
_CLIENT_TOKEN = re.compile(r"[A-Za-z0-9_\-]{8,64}")


def retrying(fn, tries=5):
    """Rejouer `fn` (qui ouvre son propre curseur) sur un conflit de concurrence.

    🔴 Vu au banc le 2026-09-14 : le bouton Arrêter écrit le drapeau d'arrêt au
    moment où le fil enregistre la fin du tour, et Postgres refuse la seconde
    écriture (« could not serialize access due to concurrent update »). Hors
    d'une requête HTTP, personne ne rejoue à notre place.
    """
    for essai in range(tries):
        try:
            return fn()
        except OperationalError as exc:
            if exc.pgcode not in PG_CONCURRENCY_ERRORS_TO_RETRY or essai == tries - 1:
                raise
            time.sleep(0.2 * (essai + 1) + random.random() * 0.2)
    return None


def new_turn_key(db_name, message_id):
    return f"{db_name[:32]}:{message_id}:{secrets.token_urlsafe(18)}"


def valid_client_token(token):
    token = str(token or "")
    return token if _CLIENT_TOKEN.fullmatch(token) else ""


def sse(event, data):
    return (
        f"event: {event}\n"
        f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    ).encode("utf-8")


def continue_reason(final, stopped=False):
    """Pourquoi ce tour peut reprendre seul, ou '' s'il ne le peut pas."""
    if stopped or final.get("_event") != "error":
        return ""
    reason = final.get("reason") or ""
    text = final.get("response") or ""
    if reason == "stopped" or _NO_RETRY.search(text):
        return ""
    if reason in ("timeout", "max_turns", "unknown_turn", "bridge_lost"):
        return reason
    if reason == "cli_error" and (
            _TRANSIENT.search(text) or not text.strip() or final.get("interrupted")):
        return reason
    return ""


def turn_settings(env):
    ICP = env["ir.config_parameter"].sudo()

    def entier(cle, defaut):
        try:
            return max(0, int(ICP.get_param(cle, str(defaut))))
        except (TypeError, ValueError):
            return defaut

    return {
        "wall_seconds": entier("bf_claude_chat.turn_wall_seconds", DEFAULT_WALL_SECONDS),
        "auto_continue": min(entier("bf_claude_chat.auto_continue_max",
                                    DEFAULT_AUTO_CONTINUE), 5),
    }


def relay_frames(chunks):
    """Recoupe un flux SSE du pont en trames complètes, sans l'événement `turn`.

    Rend (trame, [(événement, données)]) par trame, battements de cœur compris.
    🔴 La trame `turn` du pont porte la clé du tour : relayée telle quelle, elle
    arrivait au navigateur (relecture adverse du 2026-09-14). Elle ne sert
    qu'au pont ; on la retire ici, avant tout relais.
    """
    tampon = b""
    for morceau in chunks:
        tampon += morceau
        while b"\n\n" in tampon:
            trame, tampon = tampon.split(b"\n\n", 1)
            trame += b"\n\n"
            evenements = [e for _m, evs in iter_events([trame]) for e in evs]
            if any(nom == "turn" for nom, _d in evenements):
                continue
            yield trame, evenements


def iter_events(chunks):
    """Découpe un flux SSE brut en (octets, événement, données) au fil de l'eau.

    Rend chaque morceau reçu tel quel (à relayer), accompagné des événements
    complets qu'il termine. Un battement de cœur rend (octets, None, None).
    """
    tampon = b""
    courant = None
    for morceau in chunks:
        evenements = []
        tampon += morceau
        while b"\n" in tampon:
            brut, tampon = tampon.split(b"\n", 1)
            ligne = brut.strip()
            if ligne.startswith(b"event:"):
                courant = ligne[6:].strip().decode("utf-8", "replace")
            elif ligne.startswith(b"data:") and courant:
                try:
                    evenements.append((courant, json.loads(ligne[5:])))
                except ValueError:
                    pass
        yield morceau, evenements


class Listener:
    """Ce que le fil passe à la réponse SSE qui l'a lancé, tant qu'elle lit."""

    def __init__(self):
        self._queue = queue.Queue(maxsize=20000)
        self.alive = True

    def put(self, data):
        if not self.alive:
            return
        try:
            self._queue.put_nowait(data)
        except queue.Full:
            # Un lecteur qui ne lit plus : on cesse de tamponner pour lui.
            self.alive = False

    def close(self):
        if self.alive:
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                self.alive = False

    def iterate(self, beat=15):
        try:
            while True:
                try:
                    item = self._queue.get(timeout=beat)
                except queue.Empty:
                    yield b": keepalive\n\n"
                    continue
                if item is None:
                    return
                yield item
        finally:
            self.alive = False


class _NullListener:
    alive = False

    def put(self, data):
        pass

    def close(self):
        pass


class TurnProgress:
    """Accumule le tour et l'écrit dans le message, sans marteler la base."""

    def __init__(self, db_name, message_id, prefix="", tools=None, attempt=0):
        self.db_name = db_name
        self.message_id = message_id
        self.prefix = prefix
        self.text = ""
        self.tools = list(tools or [])
        self.attempt = attempt
        self._last = 0.0

    @property
    def content(self):
        return self.prefix + self.text

    def on_text(self, delta):
        self.text += delta or ""
        self.write()

    def on_tool(self, name):
        self.tools.append({"name": name, "at": len(self.content), "attempt": self.attempt})
        self.write(force=True)  # un outil qui démarre mérite d'être vu tout de suite

    def on_detail(self, name, detail):
        """La ligne lisible d'un outil, arrivée quand son entrée est complète.

        Le pont ne connaît la description d'une commande qu'à la fin de son
        écriture : on la pose sur le dernier outil de ce nom qui n'en a pas.
        """
        detail = (detail or "").strip()[:120]
        if not detail:
            return
        for tool in reversed(self.tools):
            if tool.get("name") == name and not tool.get("detail"):
                tool["detail"] = detail
                self.write(force=True)
                return

    def restart_attempt(self):
        """Le tour en cours sera relu depuis le début : oublier sa part."""
        self.text = ""
        self.tools = [t for t in self.tools if t.get("attempt", 0) < self.attempt]

    def beat(self):
        if time.monotonic() - self._last >= HEARTBEAT_SECONDS:
            self.write(force=True)

    def write(self, force=False, **extra):
        maintenant = time.monotonic()
        if not force and maintenant - self._last < FLUSH_SECONDS:
            return None
        self._last = maintenant
        vals = {"tool_log": json.dumps(self.tools),
                "runner_heartbeat": fields.Datetime.now()}
        if self.content:
            vals["content"] = self.content
        vals.update(extra)
        def _ecrire():
            with Registry(self.db_name).cursor() as cr:
                env = odoo.api.Environment(cr, odoo.SUPERUSER_ID, {})
                message = env["claude.chat.message"].browse(self.message_id)
                if not message.exists():
                    return None
                message.write(vals)
                return message.stop_requested
        try:
            return retrying(_ecrire)
        except Exception:  # noqa: BLE001
            # L'avancement est un confort : son échec ne doit pas casser le tour.
            _logger.warning("Gen : écriture d'avancement échouée", exc_info=True)
            return None


def _usage_add(total, final):
    u = (final or {}).get("usage") or {}
    if not isinstance(u, dict):
        return
    for key in ("input_tokens", "output_tokens", "cache_read_tokens",
                "cache_write_tokens", "duration_ms"):
        total[key] = total.get(key, 0) + int(u.get(key) or 0)
    total["cost_usd"] = total.get("cost_usd", 0.0) + float(u.get("cost_usd") or 0.0)


def _wait_for_bridge(socket_path, seconds, beat=None):
    """Attendre que la socket du pont réponde de nouveau (redémarrage).

    ⚠️ `beat` garde le signe de vie pendant l'attente : sans lui, une attente
    plus longue que STALE_SECONDS laissait un écran ou le cron prendre le tour
    en même temps, et deux fils envoyaient la consigne de reprise.
    """
    fin = time.monotonic() + seconds
    while time.monotonic() < fin:
        if beat:
            beat()
        if os.path.exists(socket_path):
            sonde = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sonde.settimeout(2)
            try:
                sonde.connect(socket_path)
                return True
            except OSError:
                pass
            finally:
                sonde.close()
        time.sleep(3)
    return False


def _swap_turn_key(db_name, message_id, old_key, new_key):
    """Poser la clé d'une reprise, seulement si personne ne l'a changée."""
    def _poser():
        with Registry(db_name).cursor() as cr:
            cr.execute(
                "UPDATE claude_chat_message SET turn_key = %s, "
                "runner_heartbeat = now() at time zone 'UTC' "
                "WHERE id = %s AND state = 'pending' AND turn_key = %s RETURNING id",
                (new_key, message_id, old_key))
            return bool(cr.fetchone())
    try:
        return retrying(_poser)
    except Exception:  # noqa: BLE001
        _logger.warning("Gen : clé de reprise du tour %s non posée", message_id,
                        exc_info=True)
        return False


def _load(db_name, message_id):
    with Registry(db_name).cursor() as cr:
        env = odoo.api.Environment(cr, odoo.SUPERUSER_ID, {})
        message = env["claude.chat.message"].browse(message_id)
        if not message.exists():
            return None
        session = message.session_id
        try:
            payload = json.loads(message.turn_payload or "{}")
        except ValueError:
            payload = {}
        try:
            tools = json.loads(message.tool_log or "[]")
        except ValueError:
            tools = []
        # 🔴 Le fil tourne en superutilisateur : il ne relance que ce que le
        # contrôleur a lui-même préparé pour le propriétaire de la conversation.
        # Un message « en cours » fabriqué sans charge, ou dont la charge ne
        # nomme pas cet usager et un locataire, est refusé et clos.
        # Le locataire vient des paramètres de l'instance, jamais de la charge,
        # et par bf_ai_bridge, qui lit le paramètre courant puis l'ancien.
        try:
            tenant = env["bf.ai.bridge"].tenant()
        except Exception:  # noqa: BLE001
            tenant = ""
        if (not isinstance(payload, dict) or not tenant
                or payload.get("user_id") != session.user_id.id
                or message.role != "assistant"):
            _logger.warning("Gen : tour %s refusé, charge absente ou étrangère",
                            message_id)
            message.write({"state": "error", "end_reason": "invalid",
                           "turn_payload": False})
            return None
        payload["tenant"] = tenant
        from .main import _get_api_key
        content = message.content if message.content != "…" else ""
        return {
            "turn_key": message.turn_key,
            "payload": payload,
            "api_key": _get_api_key(env),
            "prefix": (content or "")[:message.prefix_len or 0],
            "tools": tools if isinstance(tools, list) else [],
            "attempt": message.auto_continue_count or 0,
            "stop_requested": message.stop_requested,
            "claude_sid": session.claude_session_id or "",
            "session_id": session.id,
            "session_name": session.name,
            "origin": session.origin,
            "user_id": session.user_id.id,
        }


def run_turn(db_name, message_id, socket_path, timeout, *, attach=False,
             listener=None, max_continue=DEFAULT_AUTO_CONTINUE,
             wall_seconds=DEFAULT_WALL_SECONDS, session_was_new=False):
    """Fil d'exécution : possède le tour jusqu'à son enregistrement."""
    listener = listener or _NullListener()
    etat = _load(db_name, message_id)
    if etat is None:
        listener.close()
        return
    base = dict(etat["payload"])
    tenant = base["tenant"]
    question = base.get("message") or ""
    turn_key = etat["turn_key"]
    claude_sid = etat["claude_sid"]
    progress = TurnProgress(db_name, message_id, prefix=etat["prefix"],
                            tools=etat["tools"], attempt=etat["attempt"])
    usage = {}
    debut = time.monotonic()
    mode = "attach" if attach else "start"
    start_payload = dict(base, turn_key=turn_key, wall_seconds=wall_seconds)
    if etat["api_key"]:
        start_payload["api_key"] = etat["api_key"]
    rattache_tente = False
    final = {}
    stopped = bool(etat["stop_requested"])
    # Le pont a-t-il ouvert ce tour (événement meta) ? Sinon une reprise
    # renvoie la question elle-même, pas une consigne de reprise.
    a_parle = bool(attach)

    while True:
        final = {}
        if mode == "attach":
            progress.restart_attempt()
        try:
            if mode == "start":
                chunks = transport.stream(socket_path, "/chat-stream", start_payload, timeout)
            else:
                chunks = transport.stream(socket_path, "/chat-attach", {
                    "turn_key": turn_key, "tenant": tenant, "offset": 0}, timeout)
            for morceau, evenements in relay_frames(chunks):
                listener.put(morceau)
                progress.beat()
                for nom, data in evenements:
                    if nom == "meta":
                        a_parle = True
                        if data.get("session_id"):
                            claude_sid = data["session_id"]
                    elif nom == "text":
                        progress.on_text(data.get("delta"))
                    elif nom == "tool":
                        progress.on_tool(data.get("name") or "tool")
                    elif nom == "tool_detail":
                        progress.on_detail(data.get("name") or "tool", data.get("detail"))
                    elif nom in ("done", "error"):
                        final = dict(data, _event=nom)
                        if data.get("session_id"):
                            claude_sid = data["session_id"]
        except ValueError as exc:
            # Un pont d'avant les tours détachés ne connaît pas /chat-attach.
            _logger.info("Gen : pont sans ce point (%s)", exc)
            final = final or {"_event": "error", "reason": "unknown_turn",
                              "response": "", "interrupted": True}
        except Exception:  # noqa: BLE001
            _logger.warning("Gen : flux du pont interrompu (tour %s)", message_id,
                            exc_info=True)
            final = final or {"_event": "error", "reason": "bridge_lost",
                              "response": "", "interrupted": True}
        if not final:
            final = {"_event": "error", "reason": "bridge_lost", "response": "",
                     "interrupted": True}
        _usage_add(usage, final)

        if final.get("reason") == "bridge_lost" and not rattache_tente:
            # Le pont a peut-être gardé le tour : un hoquet de socket, ou un
            # travailleur recyclé. On se rattache une fois avant de conclure.
            rattache_tente = True
            _wait_for_bridge(socket_path, 60, beat=progress.beat)
            mode = "attach"
            continue

        stopped = stopped or final.get("reason") == "stopped" \
            or bool(progress.write(force=True))
        pourquoi = continue_reason(final, stopped=stopped)
        if (pourquoi and progress.attempt < max_continue
                and time.monotonic() - debut < RUNNER_MAX_SECONDS):
            if pourquoi in ("bridge_lost", "unknown_turn"):
                _wait_for_bridge(socket_path, 90, beat=progress.beat)
            deja_ecrit = progress.content.strip()
            if deja_ecrit or a_parle:
                progress.prefix = (deja_ecrit + "\n\n") if deja_ecrit else ""
                progress.text = ""
                start_payload = dict(
                    base, message=CONTINUE_PROMPT.format(reason=pourquoi),
                    session_id=claude_sid or None, wall_seconds=wall_seconds)
                if etat["api_key"]:
                    start_payload["api_key"] = etat["api_key"]
            # Sinon le tour n'a jamais commencé : start_payload porte encore la
            # question, on la renvoie telle quelle sous une clé neuve.
            nouvelle_cle = new_turn_key(db_name, message_id)
            if not _swap_turn_key(db_name, message_id, turn_key, nouvelle_cle):
                # Un autre fil a pris ce tour : lui seul le reprend.
                _logger.info("Gen : tour %s repris ailleurs, ce fil s'arrête", message_id)
                listener.close()
                return
            progress.attempt += 1
            turn_key = nouvelle_cle
            start_payload["turn_key"] = turn_key
            progress.write(force=True, auto_continue_count=progress.attempt,
                           prefix_len=len(progress.prefix), end_reason=pourquoi)
            listener.put(sse("resume", {"attempt": progress.attempt, "max": max_continue,
                                        "reason": pourquoi}))
            mode = "start"
            rattache_tente = False
            continue
        break

    message_final = _finalize(db_name, message_id, progress, final, usage, claude_sid,
                              stopped, question, etat, session_was_new, socket_path)
    if message_final:
        listener.put(sse("final", message_final))
    listener.put(sse("saved", {"message_id": message_id,
                               "session_id": etat["session_id"]}))
    listener.close()


def _finalize(db_name, message_id, progress, final, usage, claude_sid, stopped,
              question, etat, session_was_new, socket_path):
    from .main import _generate_smart_title
    en_erreur = final.get("_event") == "error"
    reponse = (final.get("response") or "").strip()
    if not en_erreur:
        corps = reponse or progress.text.strip()
    else:
        # La réponse d'une erreur est soit le texte partiel, soit un message de
        # repli du pont (« j'ai pris trop de temps ») : utile s'il n'y a rien
        # d'autre, bruit après un arrêt voulu.
        corps = progress.text.strip() or ("" if stopped else reponse)
    contenu = (progress.prefix + corps).strip()
    if not contenu:
        contenu = "(No response)"
    raison = "" if not en_erreur else (
        "stopped" if stopped else (final.get("reason") or "error"))
    vals = {
        "content": contenu,
        "state": "error" if en_erreur else "done",
        "tool_log": json.dumps(progress.tools),
        "end_reason": raison or False,
        "runner_heartbeat": fields.Datetime.now(),
        # Adresse, résumé de persona, consignes : plus rien à reprendre, rien
        # à garder.
        "turn_payload": False,
    }
    for key in ("input_tokens", "output_tokens", "cache_read_tokens",
                "cache_write_tokens", "duration_ms", "cost_usd"):
        if key in usage:
            vals[key] = usage[key]

    def _enregistrer():
        with Registry(db_name).cursor() as cr:
            env = odoo.api.Environment(cr, odoo.SUPERUSER_ID, {})
            message = env["claude.chat.message"].browse(message_id)
            # Un autre fil a déjà enregistré ce tour : ne rien écraser, et ne
            # pas prévenir le téléphone une seconde fois.
            if not message.exists() or message.state != "pending":
                return None
            message.write(vals)
            session = message.session_id
            svals = {}
            if claude_sid and claude_sid != session.claude_session_id:
                svals["claude_session_id"] = claude_sid
            # Anti-poison : un fil qui échoue en série sera forké au tour
            # suivant plutôt que repris. Un arrêt voulu n'est pas un échec.
            if en_erreur and not stopped:
                svals["stream_fail_count"] = (session.stream_fail_count or 0) + 1
                svals["last_stream_error"] = raison[:64]
            elif session.stream_fail_count:
                svals["stream_fail_count"] = 0
                svals["last_stream_error"] = False
            titre = False
            if session.name in ("New Chat", False) and not en_erreur and question:
                titre = (question[:60] + "...") if len(question) > 60 else question
                svals["name"] = titre
            if svals:
                session.write(svals)
            return {
                "session_id": session.id,
                "mobile": session.origin == "mobile",
                "titre": titre,
            }

    try:
        enregistre = retrying(_enregistrer)
    except Exception:  # noqa: BLE001
        _logger.exception("Gen : enregistrement du tour %s en échec", message_id)
        return None
    if not enregistre:
        return None
    if enregistre["mobile"] and not en_erreur:
        # Après l'enregistrement, jamais dedans : une écriture rejouée ne doit
        # pas prévenir le téléphone deux fois.
        try:
            with Registry(db_name).cursor() as cr:
                env = odoo.api.Environment(cr, odoo.SUPERUSER_ID, {})
                session = env["claude.chat.session"].browse(enregistre["session_id"])
                from .mobile_api import _push
                _push(env, session.user_id, session, contenu)
        except Exception:  # noqa: BLE001
            _logger.warning("Gen : poussée mobile impossible", exc_info=True)
    resultat = {
        "message_id": message_id,
        "content": contenu,
        "state": vals["state"],
        "end_reason": raison,
        "usage": {k: vals.get(k, 0) for k in (
            "input_tokens", "output_tokens", "cache_read_tokens",
            "cache_write_tokens", "duration_ms", "cost_usd")},
    }
    api_key = etat.get("api_key") or ""
    session_id = enregistre["session_id"]
    nouveau_titre = enregistre["titre"]
    if session_was_new and nouveau_titre:
        threading.Thread(
            target=_generate_smart_title,
            args=(db_name, session_id, nouveau_titre, question, contenu, api_key,
                  socket_path),
            daemon=True,
        ).start()
    return resultat


def start_runner(db_name, message_id, socket_path, timeout, **kwargs):
    thread = threading.Thread(
        target=_run_safely,
        args=(db_name, message_id, socket_path, timeout),
        kwargs=kwargs,
        name=f"gen-turn-{message_id}",
        daemon=True,
    )
    thread.start()
    return thread


def _run_safely(db_name, message_id, socket_path, timeout, **kwargs):
    try:
        run_turn(db_name, message_id, socket_path, timeout, **kwargs)
    except Exception:  # noqa: BLE001
        _logger.exception("Gen : le fil du tour %s est tombé", message_id)
        listener = kwargs.get("listener")
        if listener:
            listener.close()


def claim(db_name, message_id, stale_seconds):
    """Prendre possession d'un tour dont le fil ne donne plus signe de vie.

    UPDATE conditionnel : deux travailleurs qui arrivent en même temps ne
    lancent jamais deux fils sur le même tour.
    """
    try:
        return _claim(db_name, message_id, stale_seconds)
    except Exception:  # noqa: BLE001
        # Le fil vivant écrivait la même ligne au même instant : il vit, on
        # ne prend rien.
        _logger.info("Gen : prise du tour %s refusée", message_id, exc_info=True)
        return False


def _claim(db_name, message_id, stale_seconds):
    with Registry(db_name).cursor() as cr:
        cr.execute(
            """
            UPDATE claude_chat_message
               SET runner_heartbeat = now() at time zone 'UTC'
             WHERE id = %s AND state = 'pending' AND turn_key IS NOT NULL
               AND (runner_heartbeat IS NULL
                    OR runner_heartbeat < (now() at time zone 'UTC')
                                          - make_interval(secs => %s))
         RETURNING id
            """,
            (message_id, stale_seconds),
        )
        return bool(cr.fetchone())


def resume_detached(env, message, listener=None, synchronous=False):
    """Reprendre un tour orphelin : se rattacher au pont par un nouveau fil."""
    db_name = env.cr.dbname
    if not claim(db_name, message.id, message.STALE_SECONDS):
        return False
    socket_path = env["bf.ai.bridge"].socket_path()
    reglages = turn_settings(env)
    ICP = env["ir.config_parameter"].sudo()
    try:
        timeout = int(ICP.get_param("bf_claude_chat.timeout", "660"))
    except (TypeError, ValueError):
        timeout = 660
    kwargs = dict(attach=True, listener=listener,
                  max_continue=reglages["auto_continue"],
                  wall_seconds=reglages["wall_seconds"])
    if synchronous:
        run_turn(db_name, message.id, socket_path, timeout, **kwargs)
    else:
        start_runner(db_name, message.id, socket_path, timeout, **kwargs)
    return True
