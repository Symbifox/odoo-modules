"""Client AMI minimal (Asterisk Manager Interface) — composition d'appel.

Une seule action est utilisée ici : ``Originate`` en mode asynchrone. Le
protocole AMI n'est qu'un échange de blocs ``Clé: Valeur`` terminés par une
ligne vide ; une dépendance pip de plus sur l'image Odoo coûterait plus cher
que ces quatre-vingts lignes.

⚠️ **Injection d'en-tête.** AMI est un protocole ORIENTÉ LIGNE : un CR ou un LF
dans une valeur laisse écrire des en-têtes supplémentaires, voire une seconde
action (composer un numéro arbitraire, exécuter une commande). Toute valeur qui
vient d'un client passe donc par :meth:`_header` avant d'entrer dans la trame.

⚠️ Le secret AMI est chiffré au repos (Fernet, même clé que le secret SIP —
``bf_softphone_fernet_key`` dans odoo.conf) et ne sort jamais d'ici.

🔴 **Tout ce modèle est ``@api.private``, et c'est la garde qui compte.** Une
méthode de modèle sans « _ » est appelable par ``/web/dataset/call_kw`` par
n'importe quel usager authentifié, avec les arguments qu'IL choisit — et un
``AbstractModel`` n'a pas de table, donc aucun contrôle d'accès ne se déclenche
au passage. Sans ce décorateur, ``originate`` composait n'importe quel numéro
par le trunk en contournant TOUT ce que ``res.users`` pose devant : le groupe,
la validation nord-américaine, l'étranglement et la journalisation ; ``channels``
listait les appels de toute la maison ; ``hangup`` raccrochait celui d'autrui.
Les appels internes (``res.users``, la page des réglages, les essais) ne sont
pas concernés : ``api.private`` ne ferme que la porte RPC.
"""

import logging
import socket
import uuid

from odoo import api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Le PBX est sur le même hôte : une connexion qui traîne est une panne, pas de
# la latence. On échoue vite plutôt que de retenir un fil de travail Odoo.
_CONNECT_TIMEOUT = 5.0
_READ_TIMEOUT = 8.0
# Garde-fou de lecture : avec « Events: off » le PBX ne parle que pour répondre,
# mais on ne boucle jamais indéfiniment sur un flux inattendu.
_MAX_BLOCKS = 10


class SoftphoneAmi(models.AbstractModel):
    _name = "bf.softphone.ami"
    _description = "Client AMI (Asterisk) pour la composition d'appel"

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    @api.private
    @api.model
    def _ami_settings(self):
        ICP = self.env["ir.config_parameter"].sudo()
        return {
            "host": ICP.get_param("bf_softphone.ami_host") or "",
            "port": int(ICP.get_param("bf_softphone.ami_port") or 5038),
            "user": ICP.get_param("bf_softphone.ami_user") or "",
            "secret": self.env["res.users"]._sip_decrypt(
                ICP.get_param("bf_softphone.ami_password_enc")),
        }

    @api.private
    @api.model
    def is_configured(self):
        """Vrai si un appel peut être tenté. Sert à masquer le bouton côté client."""
        settings = self._ami_settings()
        return bool(settings["host"] and settings["user"] and settings["secret"])

    # ------------------------------------------------------------------
    # Trame
    # ------------------------------------------------------------------
    @staticmethod
    def _header(value):
        """Valeur d'en-tête sûre : refuse tout CR/LF (injection d'action AMI)."""
        text = "" if value is None else str(value)
        if "\r" in text or "\n" in text:
            raise UserError("Valeur d'appel invalide (retour de ligne).")
        return text.strip()

    @classmethod
    def _frame(cls, action, fields_):
        lines = ["Action: %s" % action]
        for key, value in fields_:
            if value == "" or value is None:
                continue
            lines.append("%s: %s" % (key, cls._header(value)))
        return ("\r\n".join(lines) + "\r\n\r\n").encode()

    @staticmethod
    def _read_raw(sock, buffer):
        """Lit le prochain bloc complet, tel quel. Retourne (texte, reste)."""
        while b"\r\n\r\n" not in buffer:
            chunk = sock.recv(4096)
            if not chunk:
                raise UserError("Le PBX a fermé la connexion.")
            buffer += chunk
        raw, buffer = buffer.split(b"\r\n\r\n", 1)
        return raw.decode("utf-8", "replace"), buffer

    @classmethod
    def _read_block(cls, sock, buffer):
        """Bloc analysé en dictionnaire. Retourne (dict en minuscules, reste).

        La bannière d'accueil (« Asterisk Call Manager/x.y ») n'est PAS un bloc :
        elle arrive collée à la première réponse. Elle ne contient pas de « : »,
        donc l'analyse l'ignore d'elle-même.

        ⚠️ Un dictionnaire ÉCRASE les clés répétées — parfait ici, inutilisable
        pour la sortie d'une commande CLI, qui arrive en lignes « Output: »
        multiples. Celle-là passe par :meth:`_read_raw`.
        """
        raw, buffer = cls._read_raw(sock, buffer)
        block = {}
        for line in raw.split("\r\n"):
            if ": " in line:
                key, value = line.split(": ", 1)
                block[key.lower()] = value
        return block, buffer

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------
    @api.private
    @api.model
    def _connect_and_login(self):
        """Ouvre une session AMI authentifiée. Retourne (socket, tampon lu)."""
        settings = self._ami_settings()
        if not (settings["host"] and settings["user"] and settings["secret"]):
            raise UserError("Le lien avec le PBX n'est pas configuré.")
        try:
            sock = socket.create_connection(
                (settings["host"], settings["port"]), _CONNECT_TIMEOUT)
        except OSError as exc:
            # L'adresse du PBX n'est pas un secret, mais elle n'a rien à faire
            # dans un message d'erreur rendu à un téléphone.
            _logger.warning("bf_softphone: connexion AMI impossible (%s:%s) : %s",
                            settings["host"], settings["port"], exc)
            raise UserError("Le PBX est injoignable.") from exc
        try:
            sock.settimeout(_READ_TIMEOUT)
            # « Events: off » : sans cela le compte clicktocall reçoit le flux
            # d'événements du PBX et la réponse à notre action arrive noyée.
            sock.sendall(self._frame("Login", [
                ("Username", settings["user"]),
                ("Secret", settings["secret"]),
                ("Events", "off"),
            ]))
            block, buffer = self._read_block(sock, b"")
            if block.get("response", "").lower() != "success":
                _logger.warning("bf_softphone: authentification AMI refusée")
                raise UserError("Le PBX a refusé l'authentification.")
            return sock, buffer
        except Exception:
            sock.close()
            raise

    @staticmethod
    def _logoff(sock):
        try:
            sock.sendall(b"Action: Logoff\r\n\r\n")
        except OSError:
            pass  # la session AMI meurt avec la connexion, ça suffit
        sock.close()

    @api.private
    @api.model
    def ping(self):
        """S'authentifie et repart — sonde de configuration, aucun appel composé."""
        sock, _buffer = self._connect_and_login()
        self._logoff(sock)
        return True

    # ------------------------------------------------------------------
    # Canaux : lire, raccrocher
    # ------------------------------------------------------------------
    @api.private
    @api.model
    def _command(self, cli):
        """Exécute une commande CLI par l'AMI et rend sa sortie en texte."""
        sock, buffer = self._connect_and_login()
        try:
            action_id = "bfsp-%s" % uuid.uuid4().hex[:12]
            sock.sendall(self._frame("Command", [
                ("ActionID", action_id), ("Command", cli)]))
            raw, buffer = self._read_raw(sock, buffer)
            return "\n".join(
                ligne[8:] for ligne in raw.split("\r\n")
                if ligne.startswith("Output: "))
        finally:
            self._logoff(sock)

    @api.private
    @api.model
    def channels(self, account=None):
        """Canaux actifs, filtrés sur leur code de compte si fourni.

        ⚠️ Format RELEVÉ sur Asterisk 22 — l'ordre des
        champs varie selon les versions, celui-ci est mesuré, pas supposé :

            [0] nom          [1] contexte   [2] exten     [3] priorité
            [4] état         [5] appli      [6] données   [7] cid_num
            [8] accountcode  [9] peeraccount            [10] amaflags
            [11] durée       [12] bridgeid  [13] uniqueid

        C'est le **code de compte** (8) qui rattache un canal à SON demandeur :
        le nom du canal ne dit rien de qui a lancé l'appel. On accepte aussi une
        correspondance sur `peeraccount` (9), qui porte le même code une fois les
        deux jambes pontées.

        La jambe SORTANTE (celle du correspondant) se reconnaît à son
        application : Asterisk marque `AppDial` / « (Outgoing Line) » sur le
        canal créé par le Dial. C'est celle-là qu'il faut viser pour répondre à
        un menu vocal — jouer un DTMF sur la jambe de l'usager le lui ferait
        entendre à lui.
        """
        lignes = self._command("core show channels concise")
        out = []
        for ligne in lignes.split("\n"):
            champs = ligne.split("!")
            if len(champs) < 12:
                continue
            if account and account not in (champs[8], champs[9]):
                continue
            # Étiquette utile = le CORRESPONDANT. Sur un clic-pour-appeler,
            # l'exten porte le numéro composé ; le cid_num, lui, porte NOTRE DID
            # (le contexte le force). On prend donc l'exten quand il ressemble à
            # un numéro, et le cid seulement à défaut (« s », vide…).
            exten, cid = champs[2], champs[7]
            peer = exten if exten.strip("+").isdigit() and len(exten) >= 3 else cid
            out.append({
                "channel": champs[0],
                "exten": exten,
                "state": champs[4],
                "peer": peer or exten or cid,
                "seconds": int(champs[11] or 0),
                # « outbound » = la jambe du correspondant.
                "role": "outbound" if champs[5] == "AppDial" else "local",
            })
        return out

    @api.private
    @api.model
    def play_dtmf(self, channel, digit):
        """Envoie une touche vers le correspondant du canal donné."""
        sock, buffer = self._connect_and_login()
        try:
            action_id = "bfsp-%s" % uuid.uuid4().hex[:12]
            sock.sendall(self._frame("PlayDTMF", [
                ("ActionID", action_id), ("Channel", channel), ("Digit", digit)]))
            for _ in range(_MAX_BLOCKS):
                block, buffer = self._read_block(sock, buffer)
                if block.get("actionid") == action_id:
                    return block.get("response", "").lower() == "success"
            return False
        finally:
            self._logoff(sock)

    @api.private
    @api.model
    def hangup(self, channel):
        """Raccroche un canal précis. Vrai si le PBX a accepté."""
        sock, buffer = self._connect_and_login()
        try:
            action_id = "bfsp-%s" % uuid.uuid4().hex[:12]
            sock.sendall(self._frame("Hangup", [
                ("ActionID", action_id), ("Channel", channel)]))
            for _ in range(_MAX_BLOCKS):
                block, buffer = self._read_block(sock, buffer)
                if block.get("actionid") == action_id:
                    return block.get("response", "").lower() == "success"
            return False
        finally:
            self._logoff(sock)

    # ------------------------------------------------------------------
    # Originate
    # ------------------------------------------------------------------
    @api.private
    @api.model
    def originate(self, *, channel, dialplan_context, exten, priority=1, callerid="",
                  timeout_ms=30000, account="", variables=None):
        """Fait sonner ``channel`` puis, à la réponse, exécute ``exten@contexte``.

        ⚠️ Le paramètre s'appelle ``dialplan_context`` et NON ``context`` : le
        répartiteur RPC d'Odoo confisque tout kwarg nommé ``context`` pour en
        faire le contexte d'environnement (``api.py``, `call_kw`). Un modèle qui
        expose un paramètre de ce nom n'est donc PAS appelable à distance — il
        échoue en `with_context('from-clicktocall')`. Découvert à la QA ;
        l'appel interne, lui, marchait très bien.

        Mode asynchrone : le PBX accuse réception tout de suite et sonne ensuite.
        Sans cela, la requête HTTP resterait ouverte pendant toute la sonnerie.
        """
        action_id = "bfsp-%s" % uuid.uuid4().hex[:12]
        call_frame = self._frame("Originate", [
            ("ActionID", action_id),
            ("Channel", channel),
            ("Context", dialplan_context),
            ("Exten", exten),
            ("Priority", priority),
            ("CallerID", callerid),
            ("Timeout", int(timeout_ms)),
            ("Account", account),
            ("Async", "true"),
        ] + [
            ("Variable", "%s=%s" % (name, value))
            for name, value in (variables or {}).items()
        ])

        sock, buffer = self._connect_and_login()
        try:
            sock.sendall(call_frame)
            for _ in range(_MAX_BLOCKS):
                block, buffer = self._read_block(sock, buffer)
                if block.get("actionid") == action_id:
                    break
            else:
                raise UserError("Le PBX n'a pas répondu à la demande d'appel.")

            if block.get("response", "").lower() != "success":
                raise UserError(block.get("message") or "Le PBX a refusé l'appel.")
            return {"ok": True, "action_id": action_id,
                    "message": block.get("message", "")}
        finally:
            self._logoff(sock)
