"""Transport HTTP/1.1 minimal sur socket Unix, sans environnement Odoo.

Le service ``claude-chatbot-bridge`` n'écoute que sur une socket ``AF_UNIX``.
Rien dans le conteneur Odoo ne sait parler HTTP sur une socket Unix — ni
``requests``, ni ``urllib`` — d'où cette trame écrite à la main.

Ces fonctions ne prennent pas d'``env`` : le flux du panneau de clavardage est
consommé par un générateur qui survit à la fermeture du curseur de la requête,
et le titrage automatique tourne dans un fil détaché. Le modèle abstrait
``bf.ai.bridge`` les enveloppe pour les appelants qui, eux, ont un env.

Les exceptions ne sont pas enveloppées : les appelants distinguent depuis
toujours ``socket.timeout``, ``ConnectionRefusedError``, ``FileNotFoundError``
et ``ValueError``, et cette sémantique est conservée telle quelle.
"""
import json
import socket

#: Socket path as seen from inside the Odoo container. The host keeps the
#: socket elsewhere and bind-mounts it here; this is the path that matters.
DEFAULT_SOCKET = "/run/claude-bridge/bridge.sock"

_RECV = 65536


def _headers_block(headers):
    """Sérialise les en-têtes additionnels, en refusant toute coupure de ligne.

    La requête est bâtie à la main : un CR ou un LF dans un nom ou une valeur
    laisserait un appelant ajouter ses propres en-têtes, voire un second corps.
    Le refus tombe avant que la socket ne soit ouverte.
    """
    bloc = ""
    for nom, valeur in (headers or {}).items():
        texte = str(valeur)
        if "\r" in texte or "\n" in texte or "\r" in nom or "\n" in nom:
            raise ValueError("Invalid header value")
        bloc += f"{nom}: {texte}\r\n"
    return bloc


def _request_bytes(endpoint, body, headers):
    return (
        f"POST {endpoint} HTTP/1.1\r\n"
        f"Host: localhost\r\n"
        f"Content-Type: application/json\r\n"
        f"{_headers_block(headers)}"
        f"Content-Length: {len(body)}\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    ).encode() + body


def post(socket_path, endpoint, payload, timeout, headers=None):
    """POST JSON sur la socket, rend la réponse JSON décodée.

    Lit la réponse en entier avant de la rendre : c'est le mode d'appel des
    points de terminaison qui répondent d'un coup (/chat, /refine-meeting,
    /ocr/*, …). Pour un flux, voir :func:`stream`.
    """
    body = json.dumps(payload).encode()
    requete = _request_bytes(endpoint, body, headers)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(socket_path)
        sock.sendall(requete)

        morceaux = []
        while True:
            morceau = sock.recv(8192)
            if not morceau:
                break
            morceaux.append(morceau)
        brut = b"".join(morceaux).decode()

        fin_entetes = brut.find("\r\n\r\n")
        if fin_entetes == -1:
            raise ValueError("Malformed HTTP response from bridge")
        ligne_statut = brut[:brut.find("\r\n")]
        statut = int(ligne_statut.split(" ", 2)[1])
        corps = brut[fin_entetes + 4:]

        if "transfer-encoding: chunked" in brut[:fin_entetes].lower():
            corps = _decode_chunked(corps)

        if statut >= 400:
            raise ValueError(f"Bridge HTTP {statut}: {corps[:200]}")
        return json.loads(corps)
    finally:
        sock.close()


def _decode_chunked(corps):
    """Recompose un corps en codage par morceaux, déjà lu en entier."""
    decode = []
    pos = 0
    while pos < len(corps):
        saut = corps.find("\r\n", pos)
        if saut == -1:
            break
        taille = int(corps[pos:saut], 16)
        if taille == 0:
            break
        decode.append(corps[saut + 2:saut + 2 + taille])
        pos = saut + 2 + taille + 2
    return "".join(decode)


def stream(socket_path, endpoint, payload, timeout, headers=None):
    """Rend le corps de la réponse au fil de l'eau (générateur d'octets).

    Décode le codage par morceaux à mesure que les octets arrivent, sans jamais
    tamponner le corps entier, pour que les événements du bridge atteignent le
    navigateur en direct. Le délai s'applique à chaque ``recv`` : ce sont les
    battements de cœur du bridge qui maintiennent le flux.
    """
    body = json.dumps(payload).encode()
    requete = _request_bytes(endpoint, body, headers)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(socket_path)
        sock.sendall(requete)

        tampon = b""
        while b"\r\n\r\n" not in tampon:
            morceau = sock.recv(_RECV)
            if not morceau:
                return
            tampon += morceau
        entetes, tampon = tampon.split(b"\r\n\r\n", 1)
        try:
            statut = int(entetes.split(b" ", 2)[1])
        except (IndexError, ValueError):
            statut = 0
        par_morceaux = b"transfer-encoding: chunked" in entetes.lower()
        if statut >= 400:
            raise ValueError(f"Bridge returned HTTP {statut}")

        if not par_morceaux:
            # Réponse d'un bloc (défensif) : rendre ce qu'on a, puis vider.
            if tampon:
                yield tampon
            while True:
                morceau = sock.recv(_RECV)
                if not morceau:
                    return
                yield morceau

        while True:
            while b"\r\n" not in tampon:
                morceau = sock.recv(_RECV)
                if not morceau:
                    return
                tampon += morceau
            ligne_taille, tampon = tampon.split(b"\r\n", 1)
            ligne_taille = ligne_taille.strip()
            if not ligne_taille:
                continue
            try:
                taille = int(ligne_taille.split(b";")[0], 16)
            except ValueError:
                return
            if taille == 0:
                return  # morceau terminal
            while len(tampon) < taille + 2:
                morceau = sock.recv(_RECV)
                if not morceau:
                    if tampon:
                        yield tampon[:taille]
                    return
                tampon += morceau
            yield tampon[:taille]
            tampon = tampon[taille + 2:]  # données + CRLF
    finally:
        sock.close()
