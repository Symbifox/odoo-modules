#!/usr/bin/env python3
"""Blue Fox OS install-time provisioning — runs in the Anaconda %pre stage.

BEFORE the install is underway, authenticate the operator to the org's Authentik
via the OAuth2 Device Authorization Grant (RFC 8628): no browser on the machine,
2FA preserved (the operator authorizes on a phone / second device). Then pull the
merged policy JSON from <org>/api/v1/policy/me and stage it for the %post applier
(bfos_apply.py) and the firstboot welcome agent.

Enrolment: while the operator's bearer is still in hand — the only
moment an authorized person is demonstrably in front of this machine — we also
POST to <org>/api/v1/policy/enroll and stage the per-machine secret it returns.
That secret is what lets the installed system re-fetch its policy on a timer,
with nobody present, so a policy edited in Odoo reaches machines that are
already installed. It reads that one policy and nothing else; see bf.policy.machine.

Config comes from the environment, exported by the kickstart %pre block from the
rendered template:
    BFOS_OIDC_DEVICE_URL   Authentik device authorization endpoint
    BFOS_OIDC_TOKEN_URL    Authentik token endpoint
    BFOS_OIDC_CLIENT_ID    public client id (e.g. 'blue-fox-os')
    BFOS_POLICY_URL        e.g. https://<domain>/api/v1/policy/me
    BFOS_ENROLL_URL        optional; defaults to the policy URL with /me → /enroll
    BFOS_FALLBACK_LANG / BFOS_FALLBACK_KEYMAP / BFOS_FALLBACK_TIMEZONE
                           org defaults used if the flow fails

Disk passphrase escrow: when the org asks for it AND its server says it
can store it, we draw the LUKS passphrase here rather than making someone type
one nobody records, deposit it in the same authenticated call as the enrolment,
and only then write it into the autopart line Anaconda includes. Odoo says no,
or never gets asked, and that include file keeps the prompting form the %pre
wrote before any of this ran. See stage_enrolment for why that order is the
whole safety argument.

Output: /tmp/bfos-provision.json (the policy, or a minimal fallback), when the
enrolment succeeded /tmp/bfos-machine.json (endpoint + machine secret, no disk
key), and /tmp/bfos-autopart.ks (the autopart line, with or without a generated
passphrase). The passphrase itself is never written anywhere else and never
reaches the installed system.

This NEVER aborts the install: any failure writes the fallback and exits 0, so
Anaconda proceeds with org defaults and the user can finish at firstboot. A
failed *enrolment* is milder still — the machine installs exactly as before,
it simply won't follow later policy changes on its own. The network is already
up (dracut fetched the kickstart over it).

stdlib-only by design — the Anaconda installer environment has no extra packages.
All I/O (post/get/sleep/console) is injectable so the logic is unit-testable.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

MODE_PRIVATE = 0o600


def write_private(path, content):
    """Écrire un secret, propriétaire seul, dès le premier octet.

    `open(path, "w")` puis `os.chmod(path, 0o600)` laisse le fichier lisible
    par tout le monde entre les deux instructions — au umask standard 0022,
    0644. Ici ça vise la ligne autopart, qui porte la passphrase LUKS : elle
    n'a pas à transiter par un mode qu'on corrige ensuite. `os.open` fait
    appliquer le mode par le noyau à la création ; le fchmod ne couvre que le
    fichier déjà présent, dont O_CREAT ignore le mode et dont O_TRUNC ne
    remet pas les permissions.
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, MODE_PRIVATE)
    try:
        os.fchmod(fd, MODE_PRIVATE)
        fh = os.fdopen(fd, "w")
    except BaseException:
        os.close(fd)
        raise
    with fh:
        fh.write(content)


def write_public(path, content, mode=0o644):
    """Écrire un fichier qui DOIT être lisible : une politique de conteneurs,
    une clé publique, une ligne d'installation.

    Séparé de `write_private` volontairement. Passer un mode à l'écrivain de
    secrets ferait lire « write_private(..., 0644) » à la relecture — une
    phrase qui se contredit, et exactement le genre d'appel qu'on finit par
    copier vers un vrai secret sans y penser.
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.fchmod(fd, mode)
        fh = os.fdopen(fd, "w")
    except BaseException:
        os.close(fd)
        raise
    with fh:
        fh.write(content)


STAGED_JSON = "/tmp/bfos-provision.json"
MACHINE_JSON = "/tmp/bfos-machine.json"
SCOPE = "openid profile email"
CONSOLE = "/dev/console"
_MAX_WAIT = 900  # cap the device-flow wait at 15 min regardless of expires_in

# --- Disk passphrase escrow -----------------------------------------------
# The kickstart %include Anaconda reads its autopart line from. The %pre writes
# the prompting version FIRST, before anything can fail; we only ever overwrite
# it once Odoo has confirmed it holds the passphrase. Getting that order wrong
# is the one way to produce the failure that matters: a disk sealed with a
# passphrase nobody on earth possesses.
AUTOPART_INCLUDE = "/tmp/bfos-autopart.ks"
AUTOPART_BASE = "autopart --type=btrfs --encrypted --nohome"
# La variante EN CLAIR. Elle n'existe que pour un repli delibere :
# sans TPM, l'operateur choisit entre une phrase a taper a chaque demarrage et
# pas de chiffrement du tout. Ce second choix se prend les yeux ouverts.
AUTOPART_CLAIR = "autopart --type=btrfs --nohome"
# Marqueur lu par le %post --nochroot : « enrole le TPM avec la phrase que je
# viens de tirer ». Explicite, parce que deduire l'intention de la presence
# d'un TPM confondrait le cas ou l'operateur l'a refuse.
TPM_MARKER = "/tmp/bfos-tpm-enrol"
# Compte de secours local. Anaconda REFUSE de commencer quand root est
# verrouille et qu'aucun usager n'existe : sans cette ligne il ouvre son volet
# de creation d'usager et attend quelqu'un — et quelqu'un finit par creer un
# compte local hors annuaire.
COMPTE_INCLUDE = "/tmp/bfos-compte.ks"
COMPTE_SECOURS = "bfos-secours"
# Un nom que l'annuaire ne sert pas : un homonyme LDAP serait masque par le
# compte local, puisque nsswitch lit `files` avant `sss`.
_COMPTE_SUR = re.compile(r"^[A-Za-z0-9._-]+$")
# Crockford's base32 alphabet: digits and uppercase letters minus I, L, O and U.
# The first three are the shapes people mistype reading a key off a screen
# (I/1, L/1, O/0); U goes because excluding it is what keeps a random string
# from spelling something someone has to read out loud. 30 symbols, 25 of them
# = ~122 bits.
# Layout-safe on purpose: A-Z, 0-9 and the hyphen sit identically on the
# Canadian CSA layout we ship and on US QWERTY, so the passphrase is typeable
# at the LUKS prompt whatever keymap the policy set.
_PASSPHRASE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
_PASSPHRASE_GROUPS = 5
_PASSPHRASE_GROUP_LEN = 5


class ProvisionError(Exception):
    pass


def _post_form(url, data, timeout=30):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode()


def _get(url, token, timeout=30):
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode()


def device_authorize(device_url, client_id, post=_post_form):
    """Start the device flow; returns the device authorization response dict."""
    try:
        status, raw = post(device_url, {"client_id": client_id, "scope": SCOPE})
    except Exception as exc:  # noqa: BLE001
        raise ProvisionError(f"device authorization request failed: {exc}") from exc
    if status != 200:
        raise ProvisionError(f"device authorization HTTP {status}")
    try:
        d = json.loads(raw)
    except ValueError as exc:
        raise ProvisionError(f"device authorization bad JSON: {exc}") from exc
    if "device_code" not in d:
        raise ProvisionError("device authorization missing device_code")
    return d


# =========================================================================
# Code QR — encodeur autonome
# =========================================================================
# Pourquoi du code maison plutot qu'une bibliotheque : le runtime d'Anaconda
# ne garantit ni `qrencode` ni `python3-qrcode`, et ce script voyage integre
# verbatim dans le kickstart — il ne peut rien importer qui ne soit pas deja
# la. Dependre du jeu de paquets de lorax, ce serait accepter qu'une mise a
# jour de Fedora eteigne l'ecran d'installation sans prevenir.
#
# Eprouve par aller-retour contre un vrai decodeur (zxing-cpp) sur les 106
# longueurs, pas par comparaison visuelle : un QR faux ressemble exactement a
# un QR juste. Voir install/tests/test_bfos_provision.py.
# --- corps de Galois GF(256), polynome primitif 0x11D -----------------------
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _gf_mul(a, b):
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _rs_generator(degree):
    """Polynome generateur de Reed-Solomon de degre donne."""
    poly = [1]
    for i in range(degree):
        nxt = [0] * (len(poly) + 1)
        for j, c in enumerate(poly):
            nxt[j] ^= c
            nxt[j + 1] ^= _gf_mul(c, _EXP[i])
        poly = nxt
    return poly


def _rs_ecc(data, ec_count):
    """Mots de correction pour un bloc de donnees."""
    gen = _rs_generator(ec_count)
    rem = [0] * ec_count
    for byte in data:
        factor = byte ^ rem[0]
        rem = rem[1:] + [0]
        for i, g in enumerate(gen[1:]):
            rem[i] ^= _gf_mul(g, factor)
    return rem


# --- tables par version, niveau de correction M -----------------------------
# version -> (mots de correction par bloc, [(nb de blocs, mots de donnees), ...])
_SPEC_M = {
    1: (10, [(1, 16)]),
    2: (16, [(1, 28)]),
    3: (26, [(1, 44)]),
    4: (18, [(2, 32)]),
    5: (24, [(2, 43)]),
    6: (16, [(4, 27)]),
}
_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34]}


def _capacity(version):
    """Nombre d'octets encodables en mode octet pour cette version."""
    total = sum(n * d for n, d in _SPEC_M[version][1])
    return (total * 8 - 4 - 8) // 8


def _pick_version(nbytes):
    for v in sorted(_SPEC_M):
        if nbytes <= _capacity(v):
            return v
    raise ValueError(
        f"{nbytes} octets depassent la version 6 en correction M "
        f"({_capacity(6)} octets) — hors de la portee de cet encodeur")


# --- flux binaire -----------------------------------------------------------
def _bitstream(payload, version):
    ec_per_block, groups = _SPEC_M[version]
    total_data = sum(n * d for n, d in groups)

    bits = []
    def put(value, length):
        for i in range(length - 1, -1, -1):
            bits.append((value >> i) & 1)

    put(0b0100, 4)              # mode octet
    put(len(payload), 8)        # compte (8 bits pour les versions 1 a 9)
    for byte in payload:
        put(byte, 8)

    # terminateur : jusqu'a 4 zeros, sans depasser la capacite
    put(0, min(4, total_data * 8 - len(bits)))
    # alignement sur l'octet
    if len(bits) % 8:
        put(0, 8 - len(bits) % 8)

    data = bytearray(int("".join(str(b) for b in bits[i:i + 8]), 2)
                     for i in range(0, len(bits), 8))
    # octets de bourrage alternes, prescrits par la norme
    for i in range(total_data - len(data)):
        data.append(0xEC if i % 2 == 0 else 0x11)

    # decoupage en blocs, puis entrelacement
    blocks, eccs, pos = [], [], 0
    for count, size in groups:
        for _ in range(count):
            blk = bytes(data[pos:pos + size])
            pos += size
            blocks.append(blk)
            eccs.append(_rs_ecc(blk, ec_per_block))

    out = bytearray()
    for i in range(max(len(b) for b in blocks)):
        for b in blocks:
            if i < len(b):
                out.append(b[i])
    for i in range(ec_per_block):
        for e in eccs:
            out.append(e[i])
    return out


# --- matrice ----------------------------------------------------------------
def _blank(size):
    return [[None] * size for _ in range(size)]


def _place_function_patterns(m, version):
    size = len(m)

    def finder(r0, c0):
        for r in range(-1, 8):
            for c in range(-1, 8):
                rr, cc = r0 + r, c0 + c
                if not (0 <= rr < size and 0 <= cc < size):
                    continue
                dark = (0 <= r <= 6 and c in (0, 6)) or \
                       (0 <= c <= 6 and r in (0, 6)) or \
                       (2 <= r <= 4 and 2 <= c <= 4)
                m[rr][cc] = 1 if dark else 0

    finder(0, 0)
    finder(0, size - 7)
    finder(size - 7, 0)

    # motifs de synchronisation
    for i in range(8, size - 8):
        bit = 1 if i % 2 == 0 else 0
        m[6][i] = bit
        m[i][6] = bit

    # motifs d'alignement, sauf la ou ils chevaucheraient un motif de reperage
    centers = _ALIGN[version]
    for r in centers:
        for c in centers:
            if (r, c) in ((6, 6), (6, size - 7), (size - 7, 6)):
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    m[r + dr][c + dc] = \
                        1 if max(abs(dr), abs(dc)) != 1 else 0

    m[size - 8][8] = 1  # module sombre, toujours


def _reserve_format(m):
    size = len(m)
    for i in range(9):
        if m[8][i] is None:
            m[8][i] = 0
        if m[i][8] is None:
            m[i][8] = 0
    for i in range(8):
        if m[8][size - 1 - i] is None:
            m[8][size - 1 - i] = 0
        if m[size - 1 - i][8] is None:
            m[size - 1 - i][8] = 0


def _place_data(m, stream, reserved):
    size = len(m)
    bits = [(byte >> i) & 1 for byte in stream for i in range(7, -1, -1)]
    idx = 0
    col = size - 1
    upward = True
    while col > 0:
        if col == 6:          # la colonne de synchronisation ne porte rien
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for row in rows:
            for c in (col, col - 1):
                if reserved[row][c]:
                    continue
                m[row][c] = bits[idx] if idx < len(bits) else 0
                idx += 1
        upward = not upward
        col -= 2


_MASKS = (
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
)


def _penalty(m):
    size = len(m)
    score = 0

    # N1 : suites de 5 modules ou plus de meme couleur
    for line in list(m) + [list(col) for col in zip(*m)]:
        run, prev = 1, line[0]
        for v in line[1:]:
            if v == prev:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run, prev = 1, v
        if run >= 5:
            score += 3 + (run - 5)

    # N2 : blocs 2x2 de meme couleur
    for r in range(size - 1):
        for c in range(size - 1):
            if m[r][c] == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                score += 3

    # N3 : motif 1:1:3:1:1 evoquant un motif de reperage
    patt_a = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0]
    patt_b = list(reversed(patt_a))
    for line in list(m) + [list(col) for col in zip(*m)]:
        for i in range(size - 10):
            window = line[i:i + 11]
            if window == patt_a or window == patt_b:
                score += 40

    # N4 : desequilibre clair/sombre
    dark = sum(sum(row) for row in m)
    ratio = dark * 100 // (size * size)
    score += 10 * (abs(ratio - 50) // 5)
    return score


_FORMAT_GEN = 0x537
_FORMAT_XOR = 0x5412


def _format_bits(mask):
    # niveau M = 0b00
    data = (0b00 << 3) | mask
    rem = data << 10
    for i in range(4, -1, -1):
        if rem & (1 << (i + 10)):
            rem ^= _FORMAT_GEN << i
    return ((data << 10) | rem) ^ _FORMAT_XOR


def _apply_format(m, mask):
    """Ecrit les deux copies de l'information de format.

    Repere mesure contre une implementation de reference, pas deduit : la
    COLONNE 8 porte les bits 0 a 5 en descendant, la RANGEE 8 porte les bits
    14 a 9 en allant vers la gauche. Les intervertir donne un QR d'allure
    parfaitement normale que plus aucun lecteur ne decode.
    """
    size = len(m)
    bits = _format_bits(mask)

    def bit(i):
        return (bits >> i) & 1

    # copie 1 : autour du motif de reperage superieur gauche
    for i in range(6):
        m[i][8] = bit(i)
    m[7][8] = bit(6)
    m[8][8] = bit(7)
    m[8][7] = bit(8)
    for i in range(6):
        m[8][5 - i] = bit(9 + i)

    # copie 2 : sous le reperage inferieur gauche, et a droite du superieur droit
    for i in range(8):
        m[8][size - 8 + i] = bit(7 - i)
    for i in range(7):
        m[size - 1 - i][8] = bit(14 - i)
    m[size - 8][8] = 1  # module sombre, toujours


def _qr_encode(text):
    """Rend la matrice du QR : liste de listes de 0/1, sans zone de silence."""
    payload = text.encode("utf-8")
    version = _pick_version(len(payload))
    size = 17 + 4 * version

    base = _blank(size)
    _place_function_patterns(base, version)
    _reserve_format(base)
    reserved = [[cell is not None for cell in row] for row in base]

    stream = _bitstream(payload, version)
    _place_data(base, stream, reserved)

    best, best_score = None, None
    for mask in range(8):
        cand = [row[:] for row in base]
        for r in range(size):
            for c in range(size):
                if not reserved[r][c] and _MASKS[mask](r, c):
                    cand[r][c] ^= 1
        _apply_format(cand, mask)
        sc = _penalty(cand)
        if best_score is None or sc < best_score:
            best, best_score = cand, sc
    return best


_QR_PLEIN = "\u2588"
_QR_HAUT = "\u2580"
_QR_BAS = "\u2584"
# Noir sur blanc, explicitement. La console est blanche sur noir : dessiner le
# QR tel quel donnerait un code INVERSE, que certains appareils photo refusent.
_QR_ENCRE = "\x1b[30;47m"
_QR_FIN = "\x1b[0m"


def render_qr_lines(text, quiet=4):
    """Rend le QR en demi-blocs : une colonne par module, deux modules par
    ligne. C'est ce qui garde les modules carres sur une console dont les
    caracteres sont deux fois plus hauts que larges — et ce qui fait tenir un
    code de version 4 en 21 lignes au lieu de 41.

    Retourne (lignes, largeur_en_colonnes), sans sequences ANSI.
    """
    m = _qr_encode(text)
    n = len(m)
    w = n + 2 * quiet
    grille = [[0] * w for _ in range(w)]
    for r in range(n):
        for c in range(n):
            grille[r + quiet][c + quiet] = m[r][c]
    glyphes = (" ", _QR_BAS, _QR_HAUT, _QR_PLEIN)
    lignes = []
    for r in range(0, w, 2):
        haut = grille[r]
        bas = grille[r + 1] if r + 1 < w else [0] * w
        lignes.append("".join(glyphes[(haut[c] << 1) | bas[c]] for c in range(w)))
    return lignes, w


def _grouper_code(code):
    """Coupe le code en groupes de trois. Neuf caracteres d'affilee se retapent
    mal sur un telephone ; trois par trois, on ne perd plus sa place."""
    brut = "".join(ch for ch in str(code) if ch.isalnum())
    if not brut:
        return str(code)
    return "   ".join(" ".join(brut[i:i + 3]) for i in range(0, len(brut), 3))


def _hote_lisible(uri):
    """L'adresse sans le schema : c'est ce qu'on retape, pas le https://."""
    for prefixe in ("https://", "http://"):
        if uri.startswith(prefixe):
            return uri[len(prefixe):]
    return uri


# Taille cible : 24 lignes sur 78 colonnes. Une console texte fait 80x25 au
# minimum ; deborder d'une seule ligne ferait defiler l'ecran et la premiere
# chose a sortir par le haut serait le QR.
_LARGEUR = 78
_HAUTEUR_MAX = 24
_ECART = 2  # colonnes entre le QR et le texte


# Couleurs de marque en console. La console du noyau n'a que 16 couleurs : le
# bleu Blue Fox #29ABE2 n'y existe pas, et le plus proche est le cyan VIF.
# `1;36` (gras + cyan) plutot que `96` : le cyan vif en 90-97 depend de la
# version du noyau, le gras-cyan rend vif sur TOUTES les consoles.
_MARQUE = "\x1b[1;36m"
_GRAS = "\x1b[1m"
_FIN = "\x1b[0m"

# Renard en caracteres ASCII purs. Les demi-blocs du QR sont eprouves dans la
# police de la console ; les symboles comme les triangles ne le sont pas, et un
# glyphe manquant s'affiche en losange a point d'interrogation. On reste donc
# sur l'ASCII, qui s'affiche partout.
_RENARD = (
    r" /\     /\ ",
    r"/  \___/  \ ",
    r"\  o   o  / ",
    r" \___v___/ ",
)


def _largeur_visible(ligne):
    """Largeur a l'ecran, sans les sequences ANSI. Les compter gonflerait la
    mesure et ferait basculer l'ecran en pile sans raison."""
    return len(_sans_ansi(ligne))


def _panneau(uri, code):
    renard = [_MARQUE + r + _FIN for r in _RENARD]
    renard[1] += _GRAS + "   BLUE FOX OS" + _FIN
    renard[2] += "   autorisation"
    return renard + [
        "",
        "Cette machine demande a rejoindre",
        "votre organisation.",
        "",
        "1.  Balayez le code ci-contre avec",
        "    l'appareil photo du telephone.",
        "",
        "2.  Ou ouvrez cette adresse :",
        "  " + _MARQUE + _hote_lisible(uri) + _FIN,
        "",
        "3.  Puis entrez ce code :",
        "  " + _GRAS + _grouper_code(code) + _FIN,
        "",
        "L'installation se poursuivra au nom",
        "de qui autorise.",
    ]


def composer_ecran(d, qr=True):
    """Compose l'ecran d'autorisation, sequences ANSI comprises.

    Separe de `announce` pour etre testable sans console.
    """
    uri = d.get("verification_uri", "") or ""
    uri_complete = d.get("verification_uri_complete") or uri
    code = d.get("user_code", "?")
    droite = _panneau(uri, code)

    gauche, largeur_qr = [], 0
    if qr and uri_complete:
        try:
            gauche, largeur_qr = render_qr_lines(uri_complete)
        except Exception:  # noqa: BLE001
            # Un QR absent ne doit jamais couter le code : on retombe sur le
            # texte seul, qui suffit a terminer l'installation.
            gauche, largeur_qr = [], 0

    # Cote a cote seulement si les deux colonnes tiennent vraiment. Une URL
    # plus longue donnerait un QR de version superieure, donc plus large.
    besoin = largeur_qr + _ECART + max(_largeur_visible(x) for x in droite)
    cote_a_cote = bool(gauche) and besoin <= _LARGEUR

    titre = " BLUE FOX OS "
    queue = " autorisation "
    regle = "-" * max(1, _LARGEUR - len(titre) - len(queue))
    lignes = [_MARQUE + titre + _FIN + regle + queue]

    if cote_a_cote:
        for i in range(max(len(gauche), len(droite))):
            g = gauche[i] if i < len(gauche) else " " * largeur_qr
            dte = droite[i] if i < len(droite) else ""
            lignes.append(_QR_ENCRE + g + _QR_FIN + " " * _ECART + dte)
    else:
        if gauche:
            lignes.extend(_QR_ENCRE + g + _QR_FIN for g in gauche)
        lignes.extend("  " + x for x in droite)
        lignes.append("")
        lignes.append("  Adresse complete : " + uri_complete)

    lignes += [
        "-" * _LARGEUR,
        "  En attente de l'autorisation (double facteur inclus)...",
    ]
    return "\n".join(lignes) + "\n"


def announce(d, out):
    """Affiche l'ecran d'autorisation sur la console d'installation."""
    out(composer_ecran(d, qr=_env_drapeau("BFOS_QR", True)))


def _env_drapeau(nom, defaut):
    """Drapeau d'environnement : absent = valeur par defaut ; '0'/'false'/'no'
    = desactive. Sert a eteindre le QR sur une console recalcitrante sans
    reconstruire quoi que ce soit."""
    brut = os.environ.get(nom)
    if brut is None or brut == "":
        return defaut
    return brut.strip().lower() not in ("0", "false", "no", "off")


# Pannes reseau tolerees d'affilee pendant l'attente de l'autorisation.
# ⚠️ POURQUOI CE COMPTEUR EXISTE (essai en VM reelle). La boucle
# traitait TOUTE erreur non-HTTP comme fatale. Une seule connexion refusee
# (Errno 111, un accident du chemin reseau : 40 requetes de suite depuis la
# meme machine, hors VM, n'en ont produit aucune) a donc tue l'enrolement a la
# 32e interrogation. L'operateur a autorise 35 secondes plus tard : son geste
# est parti nulle part, l'installation est retombee sur les defauts de l'org,
# et Anaconda a redemande la phrase du disque.
#
# Le bon comportement est celui de l'attente elle-meme : tant que le code n'est
# pas expire, on REESSAIE. On n'abandonne que si le reseau ne revient pas du
# tout, et on le dit alors clairement plutot que de citer la premiere erreur
# venue. La limite est en pannes CONSECUTIVES : une reussite la remet a zero.
_PANNES_TOLEREES = 10
# Reprises du tirage de la politique, et pause entre elles.
_REPRISES_RESEAU = 4
_PAUSE_REPRISE_RESEAU = 5


def poll_token(token_url, client_id, device_code, interval, expires_in,
               post=_post_form, sleep=time.sleep, now=time.monotonic, out=None):
    """Poll the token endpoint until the operator authorizes, or time out."""
    deadline = now() + min(int(expires_in), _MAX_WAIT)
    wait = max(int(interval), 1)
    grant = "urn:ietf:params:oauth:grant-type:device_code"
    pannes = 0
    while now() < deadline:
        sleep(wait)
        try:
            status, raw = post(token_url, {
                "grant_type": grant,
                "device_code": device_code,
                "client_id": client_id,
            })
        except urllib.error.HTTPError as exc:
            err = _error_code(exc)
            if err == "authorization_pending":
                pannes = 0
                continue
            if err == "slow_down":
                wait += 5
                pannes = 0
                continue
            raise ProvisionError(f"token error: {err or exc.code}") from exc
        except Exception as exc:  # noqa: BLE001
            pannes += 1
            if pannes >= _PANNES_TOLEREES:
                raise ProvisionError(
                    f"token request failed {pannes} times in a row, last: {exc}"
                ) from exc
            if out is not None:
                out(f"[bfos] reseau indisponible ({exc}); nouvel essai "
                    f"{pannes}/{_PANNES_TOLEREES}\n")
            continue
        pannes = 0
        try:
            d = json.loads(raw)
        except ValueError as exc:
            raise ProvisionError(f"token response bad JSON: {exc}") from exc
        if d.get("access_token"):
            return d["access_token"]
        err = d.get("error")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            wait += 5
            continue
        if err:
            raise ProvisionError(f"token error: {err}")
    raise ProvisionError("device authorization timed out")


def _error_code(http_error):
    try:
        return json.loads(http_error.read().decode()).get("error")
    except Exception:  # noqa: BLE001
        return None


def _valid_policy(d) -> bool:
    """Structural sanity check on a policy payload (no jsonschema in Anaconda).
    A wrong-shaped response is rejected so the caller falls back to org defaults
    rather than staging a malformed policy for the root-level %post applier."""
    return (isinstance(d, dict) and d.get("schema") == "bf-policy/v2"
            and isinstance(d.get("install"), dict)
            and isinstance(d.get("user"), dict))


def fetch_policy(policy_url, token, get=_get, sleep=time.sleep, out=None):
    """Tire la politique fusionnee pour l'operateur qui vient d'autoriser.

    ⚠️ Les reprises ne sont pas du confort : a ce point, la personne a DEJA
    autorise sur son telephone. Perdre la politique sur une coupure d'une
    seconde lui demanderait de tout recommencer, sans qu'elle sache pourquoi.
    Une reponse HTTP, elle, est une reponse : on ne la rejoue pas.
    """
    dernier = None
    for essai in range(1, _REPRISES_RESEAU + 1):
        try:
            status, raw = get(policy_url, token)
            break
        except urllib.error.HTTPError as exc:
            # Une reponse du serveur reste une reponse : meme forme d'erreur
            # qu'avant les reprises, pour que le journal se lise pareil.
            raise ProvisionError(f"policy fetch HTTP {exc.code}") from exc
        except Exception as exc:  # noqa: BLE001
            dernier = exc
            if essai == _REPRISES_RESEAU:
                raise ProvisionError(f"policy fetch failed: {exc}") from exc
            if out is not None:
                out(f"[bfos] politique injoignable ({exc}); nouvel essai "
                    f"{essai}/{_REPRISES_RESEAU}\n")
            sleep(_PAUSE_REPRISE_RESEAU)
    del dernier
    if status != 200:
        raise ProvisionError(f"policy fetch HTTP {status}")
    try:
        d = json.loads(raw)
    except ValueError as exc:
        raise ProvisionError(f"policy response bad JSON: {exc}") from exc
    if not _valid_policy(d):
        raise ProvisionError("policy response failed bf-policy/v2 schema check")
    return d


def _post_json(url, payload, token, timeout=30):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode()


def enroll_url_from(policy_url) -> str:
    """Derive the enrolment endpoint from the policy endpoint.

    They are two routes of the same controller, so deriving beats carrying a
    second placeholder through both kickstart renderers — one less value that
    can drift. An unrecognised policy URL yields "" and the caller skips
    enrolment rather than posting the operator's bearer somewhere unintended.
    """
    derived = re.sub(r"/me/?$", "/enroll", (policy_url or "").strip())
    return derived if derived.endswith("/enroll") else ""


def image_version(path="/etc/os-release") -> str:
    """Best-effort label for what this machine was installed from.

    Cosmetic — it only ever lands in the machine's Odoo record, to tell apart a
    fleet installed from different ISOs. Unreadable file → empty string.
    """
    fields = {}
    try:
        with open(path) as fh:
            for line in fh:
                key, _, value = line.partition("=")
                if _:
                    fields[key.strip()] = value.strip().strip('"')
    except OSError:
        return ""
    version = fields.get("VERSION_ID", "")
    build = fields.get("BUILD_ID") or fields.get("IMAGE_VERSION") or ""
    return f"{version} ({build})".strip() if build else version


def generate_disk_passphrase(rng=None):
    """Draw the LUKS passphrase this machine will be sealed with.

    Grouped like a recovery key rather than run together: the one moment this
    string is read out loud or copied off a screen is a bad moment, and groups
    of five are what make that survivable.
    """
    choice = (rng or secrets.choice)
    groups = [
        "".join(choice(_PASSPHRASE_ALPHABET)
                for _ in range(_PASSPHRASE_GROUP_LEN))
        for _ in range(_PASSPHRASE_GROUPS)
    ]
    return "-".join(groups)


def escrow_requested(policy) -> bool:
    """True when the org wants the installer to draw and deposit the passphrase.

    Two conditions, both from the server: the org turned escrow on, AND that
    server can actually store it (a key is configured). Without the second we
    must not draw a passphrase at all — asking Odoo to keep something it has no
    way to keep is exactly how a disk ends up sealed against everyone.

    Every level is type-checked rather than assumed. `_valid_policy` gates
    `schema`, `install` and `user` — it never looks at `policies`, so a server
    that answers with a list or a string there reaches this function intact,
    and `.get` on a non-mapping raises. Answering False to a malformed policy
    is the safe reading: no passphrase drawn, Anaconda prompts.
    """
    if not isinstance(policy, dict):
        return False
    policies = policy.get("policies")
    if not isinstance(policies, dict):
        return False
    block = policies.get("disk_escrow")
    if not isinstance(block, dict):
        return False
    return bool(block.get("enabled")) and bool(block.get("available"))


def secours_voulu(policy):
    """Le compte de secours est-il voulu par la politique ?

    Cle `install.login.break_glass`, pilotee par bf_policy.
    ⚠️ ABSENTE = OUI. Une politique anterieure a cette cle ne doit pas perdre en
    silence un chemin de recuperation : seule une politique qui dit
    EXPLICITEMENT non retire le compte. Cette fonction existe a l'identique
    dans bfos_provision.py et bfos_apply.py — deux scripts embarques separement
    dans le kickstart, qui ne peuvent pas s'importer l'un l'autre. Un test
    exige qu'ils tranchent pareil.
    """
    login = (((policy or {}).get("install") or {}).get("login") or {})
    valeur = login.get("break_glass")
    return True if valeur is None else bool(valeur)


def write_compte(passphrase=None, path=None, nom=COMPTE_SECOURS, out=None):
    """Ecrit la ligne `user` que le %include du kickstart lit.

    Sans phrase : la forme VERROUILLEE. Elle satisfait Anaconda sans ouvrir
    quoi que ce soit, et c'est le filet — toute panne du sequestre laisse la
    machine dependante de l'annuaire, ce qui est visible, plutot que dotee d'un
    compte dont personne ne connait le mot de passe.

    Avec une phrase : le meme compte, ouvert sur la PHRASE DU DISQUE. Un seul
    secret pour deux usages, deja sequestre, deja
    revelable par le seul groupe qui en a le droit, deja trace nominativement.

    ⚠️ La phrase est relue AVANT d'etre ecrite. Une valeur portant une espace
    ou un guillemet produirait une ligne `user` que pykickstart refuse, et un
    kickstart invalide n'installe RIEN — panne bien pire que l'absence de
    compte de secours. L'alphabet Crockford ne peut pas en produire ; on ne
    fait pas reposer l'amorcage sur cette certitude-la.
    """
    out = out or (lambda _m: None)
    path = path or COMPTE_INCLUDE
    base = f"user --name={nom} --groups=wheel"
    if passphrase and not _COMPTE_SUR.match(passphrase):
        out("[bfos] phrase de forme inattendue : le compte de secours reste "
            "verrouille plutot que de produire un kickstart invalide\n")
        passphrase = None
    ligne = (f"{base} --plaintext --password={passphrase}" if passphrase
             else f"{base} --lock")
    write_private(path, ligne + "\n")
    return path


def tpm2_present(chemins=("/dev/tpmrm0", "/dev/tpm0")) -> bool:
    """Y a-t-il un TPM2 utilisable dans cette machine ?

    On regarde le noeud de peripherique plutot que `systemd-analyze has-tpm2` :
    l'environnement de l'installateur est reduit, et un outil absent rendrait
    « pas de TPM » pour une mauvaise raison. Un noeud present et illisible
    serait un cas tordu ; l'enrolement echouerait alors bruyamment au %post,
    ce qui est le bon endroit pour l'apprendre.
    """
    return any(os.path.exists(c) for c in chemins)


def decider_chiffrement(out, tpm=None, lire=None) -> tuple:
    """Rend (chiffrer, enroler_tpm). Decision prise AVANT l'enrolement.

    ⚠️ L'ordre compte : decider apres coup reviendrait a sequestrer une phrase
    pour un disque qu'on s'apprete a laisser en clair, et la fiche machine
    mentirait.

    Avec TPM : on chiffre et on enrole a l'installation, sans rien demander.
    C'est possible grace au sequestre et ca ne l'etait pas avant —
    `systemd-cryptenroll` exige la phrase existante, et l'installateur la tire
    desormais lui-meme.

    Sans TPM : on demande. Le defaut, sur toute reponse inattendue comme sur
    une entree fermee, est de CHIFFRER : c'est la seule direction ou se tromper
    ne coute que du confort.
    """
    lire = lire or (lambda: sys.stdin.readline())
    if tpm is None:
        tpm = tpm2_present()
    if tpm:
        return True, True
    out("\n[bfos] Aucun TPM2 sur cette machine.\n"
        "       1) chiffrer le disque — la phrase est tiree et sequestree dans\n"
        "          Odoo, et il faudra la TAPER A CHAQUE DEMARRAGE ;\n"
        "       2) ne pas chiffrer — rien a taper, et un disque vole se lit.\n"
        "       Choix [1] : ")
    try:
        reponse = (lire() or "").strip()
    except Exception:  # noqa: BLE001 — entree fermee, install pilotee
        reponse = ""
    if reponse == "2":
        out("[bfos] disque NON chiffre, a la demande de l'operateur\n")
        return False, False
    out("[bfos] disque chiffre, sans deverrouillage automatique\n")
    return True, False


# =========================================================================
# Cohabitation avec un systeme deja present
# =========================================================================
# Jusqu'ici l'installation supposait un disque a elle. Le filet ecrit d'avance
# par le kickstart etait `autopart`, c'est-a-dire EFFACER LE DISQUE : sur une
# machine vierge c'est le bon defaut, sur une machine qui porte Windows c'est
# une perte de donnees si le %pre meurt en chemin. Le filet n'en etait un que
# pour la moitie des machines.
#
# Le defaut est donc renverse : le filet ne fait plus RIEN de destructif
# (aucune ligne de partitionnement = Anaconda pose la question), et c'est
# l'inspection qui a le droit de le renforcer. Une inspection qui echoue, un
# disque qu'on ne comprend pas, un doute quelconque : on demande a l'humain.
#
# ⚠️ CE CHEMIN N'A JAMAIS TOUCHE UN VRAI DISQUE PORTANT UN AUTRE SYSTEME.
# La decision et le rendu sont testes unitairement ; le retrecissement lui-meme
# doit etre eprouve sur une VM jetable avec Windows installe AVANT d'etre
# lache sur une machine de client.

_GIO = 1024 ** 3
BFOS_MINI_CONFORT = 64 * _GIO   # ce qu'on veut pour BFOS
BFOS_MINI_DUR = 40 * _GIO       # sous ce seuil on ne s'installe pas du tout
VOISIN_MARGE = 20 * _GIO        # ce qu'on laisse RESPIRER au systeme existant

# Ce qu'on sait retrecir avec un outil present dans le runtime d'Anaconda.
# xfs ne retrecit pas, point. btrfs le peut mais demande un montage et une
# sequence a lui : hors portee de cette version, donc traite comme non
# retrecissable plutot que tente a moitie.
FS_RETRECISSABLES = ("ext2", "ext3", "ext4", "ntfs")


def _ko(raison):
    return {"mode": "interactif", "raison": raison}


# ⚠️ MESURE EN VM, et ca a coute un essai complet.
# `lsblk` type « disk » des peripheriques qui n'en sont pas. Dans
# l'installateur Anaconda, zram0 — le swap compresse, 7,7 Gio, NON amovible —
# apparait exactement comme un vrai disque :
#
#   zram0  251:0  0  7.7G  0 disk [SWAP]
#   vda    253:0  0   40G  0 disk
#
# Le code a donc vu DEUX disques fixes sur une VM qui n'en avait qu'un, et il
# a fait ce qu'on lui demande en cas de doute : poser la question. Le
# comportement etait juste, la donnee ne l'etait pas.
_NOMS_VIRTUELS = re.compile(r"^(zram|ram|loop|fd|nbd)\d+$")


def inspecter_disques(lsblk_json):
    """Normalise la sortie de `lsblk -b -J`. Fonction pure, testable sans disque."""
    try:
        arbre = json.loads(lsblk_json)
    except ValueError as exc:
        raise ProvisionError(f"lsblk illisible : {exc}") from exc
    disques = []
    for noeud in arbre.get("blockdevices") or []:
        if noeud.get("type") != "disk":
            continue
        nom = (noeud.get("name") or "").strip()
        if _NOMS_VIRTUELS.match(nom):
            continue   # RAM, boucle : jamais une cible d'installation
        partitions = [{
            "path": e.get("path") or "",
            "taille": int(e.get("size") or 0),
            "fstype": (e.get("fstype") or "").lower(),
            "label": e.get("label") or "",
            "parttypename": (e.get("parttypename") or "").lower(),
        } for e in (noeud.get("children") or []) if e.get("type") == "part"]
        disques.append({
            "path": noeud.get("path") or "",
            "taille": int(noeud.get("size") or 0),
            "amovible": bool(noeud.get("rm")),
            "partitions": partitions,
        })
    return disques


def _esp(partitions):
    """La partition systeme EFI, si elle existe. On la REUTILISE plutot que
    d'en creer une seconde : deux ESP sur un disque, c'est un amorcage qui
    part une fois sur deux du mauvais cote."""
    for p in partitions:
        if p["fstype"] == "vfat" and "efi" in p["parttypename"]:
            return p
    return None


def choisir_plan(disques, taille_min_fs, mini=None, marge=None):
    """Decide comment partitionner. Ne touche a rien : rend un plan.

    `taille_min_fs(chemin, fstype)` rend la taille minimale en octets a
    laquelle le systeme de fichiers accepte de descendre, ou None s'il ne sait
    pas. Ne PAS savoir vaut refus : on ne devine pas la place libre sur le
    disque de quelqu'un.
    """
    mini = mini if mini is not None else BFOS_MINI_CONFORT
    marge = marge if marge is not None else VOISIN_MARGE

    fixes = [d for d in disques if not d["amovible"] and d["taille"] > 0]
    if not fixes:
        return _ko("aucun disque fixe detecte")
    if len(fixes) > 1:
        # Plusieurs disques : lequel est « le » disque ? Se tromper efface le
        # mauvais. C'est une question pour un humain, pas une heuristique.
        return _ko(f"{len(fixes)} disques fixes — le choix revient a l'operateur")

    disque = fixes[0]
    if disque["taille"] < BFOS_MINI_DUR:
        return _ko(f"disque de {disque['taille'] // _GIO} Gio, minimum "
                   f"{BFOS_MINI_DUR // _GIO} Gio")

    occupees = [p for p in disque["partitions"] if p["taille"] > 0]
    if not occupees:
        # Disque vierge : le zero-touche garde tout son sens.
        return {"mode": "disque_entier", "disque": disque["path"]}

    # Un systeme est la. On cherche la plus grosse partition retrecissable.
    candidates = [p for p in occupees if p["fstype"] in FS_RETRECISSABLES]
    if not candidates:
        types = ", ".join(sorted({p["fstype"] or "?" for p in occupees}))
        return _ko(f"rien de retrecissable sur ce disque (systemes de "
                   f"fichiers presents : {types})")

    cible = max(candidates, key=lambda p: p["taille"])
    plancher = taille_min_fs(cible["path"], cible["fstype"])
    if plancher is None:
        return _ko(f"place libre de {cible['path']} indeterminable")
    if plancher <= 0 or plancher > cible["taille"]:
        return _ko(f"mesure incoherente sur {cible['path']}")

    # On laisse au voisin son contenu PLUS une marge : retrecir au ras du
    # minimum rend un systeme qui ne peut plus rien ecrire, donc une machine
    # qu'on a techniquement preservee et pratiquement cassee.
    nouvelle_taille = plancher + marge
    libere = cible["taille"] - nouvelle_taille
    if libere < BFOS_MINI_DUR:
        return _ko(
            f"retrecir {cible['path']} ne libererait que {max(libere, 0) // _GIO} Gio "
            f"(minimum {BFOS_MINI_DUR // _GIO} Gio)")
    if libere < mini:
        out_mini = mini // _GIO
        return _ko(f"seulement {libere // _GIO} Gio liberables, {out_mini} Gio "
                   "souhaites — l'operateur tranche")

    return {
        "mode": "cohabitation",
        "disque": disque["path"],
        "partition": cible["path"],
        "taille_actuelle": cible["taille"],
        "nouvelle_taille": nouvelle_taille,
        "libere": libere,
        "esp": (_esp(occupees) or {}).get("path", ""),
    }


def taille_minimale_fs(chemin, fstype, run=None):
    """Plancher de retrecissement, en octets, ou None si on ne sait pas.

    ⚠️ Ne jamais rendre une estimation. Un chiffre invente ici se traduit par
    un retrecissement qui mord dans les donnees de quelqu'un.
    """
    import subprocess
    run = run or (lambda cmd: subprocess.run(
        cmd, capture_output=True, text=True, timeout=120))
    try:
        if fstype in ("ext2", "ext3", "ext4"):
            # `resize2fs -P` rend « Estimated minimum size of the filesystem:
            # <N> » en BLOCS ; il faut la taille de bloc pour convertir.
            r = run(["resize2fs", "-P", chemin])
            if r.returncode != 0:
                return None
            m = re.search(r":\s*(\d+)", r.stdout)
            if not m:
                return None
            blocs = int(m.group(1))
            rb = run(["dumpe2fs", "-h", chemin])
            mb = re.search(r"Block size:\s*(\d+)", rb.stdout or "")
            if rb.returncode != 0 or not mb:
                return None
            return blocs * int(mb.group(1))
        if fstype == "ntfs":
            r = run(["ntfsresize", "--info", "--force", chemin])
            if r.returncode != 0:
                return None
            # « You might resize at 12345678901 bytes or 12346 MB »
            m = re.search(r"resize at\s+(\d+)\s+bytes", r.stdout or "")
            return int(m.group(1)) if m else None
    except Exception:  # noqa: BLE001 — ne pas savoir = refuser, jamais lever
        return None
    return None


def render_partitionnement(plan, passphrase=None, chiffrer=True):
    """Rend les lignes de partitionnement pour le %include d'Anaconda.

    Mode « interactif » : un fichier de COMMENTAIRES seulement. Aucune ligne
    de partitionnement = Anaconda ouvre son volet et demande. C'est le seul
    etat qui ne peut rien detruire, donc le seul defaut acceptable.
    """
    mode = plan.get("mode")
    if mode == "interactif":
        return ("# Partitionnement laisse a l'operateur.\n"
                f"# Raison : {plan.get('raison', 'inconnue')}\n")

    chiffre = " --encrypted" if chiffrer else ""
    phrase = f" --passphrase={passphrase}" if (chiffrer and passphrase) else ""

    if mode == "disque_entier":
        return f"autopart --type=btrfs{chiffre} --nohome{phrase}\n"

    if mode == "cohabitation":
        mo = plan["nouvelle_taille"] // (1024 * 1024)
        lignes = [
            "# Cohabitation : on retrecit le systeme existant, on ne l'efface pas.",
            f"# {plan['partition']} : {plan['taille_actuelle'] // _GIO} Gio -> "
            f"{plan['nouvelle_taille'] // _GIO} Gio, "
            f"{plan['libere'] // _GIO} Gio liberes pour Blue Fox OS.",
            # Pas de clearpart : --none dit explicitement « ne rien effacer ».
            "clearpart --none",
            f"part --onpart={plan['partition']} --resize --size={mo}",
        ]
        if plan.get("esp"):
            # ⚠️ --noformat : formater l'ESP existante effacerait l'amorceur du
            # systeme voisin, donc le rendrait indemarrable tout en ayant
            # « preserve » sa partition.
            lignes.append(f"part /boot/efi --onpart={plan['esp']} --noformat "
                          "--fstype=efi")
        else:
            lignes.append("part /boot/efi --fstype=efi --size=600")
        lignes += [
            "part /boot --fstype=ext4 --size=1024",
            f"part btrfs.bfos --grow{chiffre}{phrase}",
            "btrfs none --label=bfos btrfs.bfos",
            "btrfs / --subvol --name=root LABEL=bfos",
            "btrfs /var --subvol --name=var LABEL=bfos",
        ]
        return "\n".join(lignes) + "\n"

    raise ProvisionError(f"plan de partitionnement inconnu : {mode!r}")


# =========================================================================
# Verification de la signature de l'image
# =========================================================================
# L'installation tirait l'image avec --no-signature-verification. Tant que
# l'URL est EN DUR dans le kickstart, le risque est borne : on fait confiance
# a ghcr.io et a TLS. Le jour ou c'est le domaine TAPE PAR LE CLIENT qui
# fournit la reference d'image, ce drapeau devient le trou : une faute de
# frappe vers un domaine hostile installe le systeme d'exploitation de
# quelqu'un d'autre, avec acces complet a la machine.
#
# POURQUOI PAR LA POLITIQUE ET NON PAR UN `cosign verify` PREALABLE.
# Verifier puis installer laisse un intervalle entre le controle et l'usage :
# le tag peut bouger entre les deux. `ostreecontainer` n'a qu'un drapeau
# NEGATIF ; le retirer fait que la poussee elle-meme est barree par
# /etc/containers/policy.json. C'est le pull qui verifie, donc pas
# d'intervalle. Mesure dans le runtime d'Anaconda : skopeo, podman et
# /etc/containers/policy.json y sont, et la politique par defaut y est
# `insecureAcceptAnything` — retirer le drapeau SEUL ne changerait rien.
#
# ⚠️ D'OU VIENT LA CLE, et pourquoi ca decide de tout.
# Une cle qui voyagerait dans le kickstart viendrait du DOMAINE, donc de
# l'attaquant dans le scenario meme qu'on veut couvrir. La seule copie qui
# vaille est celle GRAVEE DANS L'ISO. La copie embarquee ci-dessous n'est
# acceptable que tant que la reference d'image est elle aussi en dur : des que
# la reference vient d'une decouverte, `exiger=True` refuse la copie embarquee.

IMAGE_INCLUDE = "/tmp/bfos-image.ks"
POLICY_CONTAINERS = "/etc/containers/policy.json"
CLE_ISO = "/run/install/repo/bfos-cosign.pub"
CLE_POSEE = "/etc/pki/bfos-cosign.pub"

# Copie de secours. Publique par nature — ce n'est pas un secret, c'est un
# point d'ancrage de confiance.
CLE_EMBARQUEE = """-----BEGIN PUBLIC KEY-----
MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEaHUAdYyRGWeSzvkyiLn1Wp/uDhD9
7M3rVr32kljeuXsqRXc93Fv3Zi2afSQzNO3FSaeBwILDjndJf0EbrXAEow==
-----END PUBLIC KEY-----
"""


def lire_cle_cosign(chemin_iso=None, exiger=False):
    """Rend (cle, provenance). Leve si `exiger` et que l'ISO n'en porte pas."""
    chemin_iso = chemin_iso or CLE_ISO
    try:
        with open(chemin_iso, encoding="utf-8") as fh:
            cle = fh.read().strip()
        if "BEGIN PUBLIC KEY" in cle:
            return cle + "\n", "iso"
    except OSError:
        pass
    if exiger:
        raise ProvisionError(
            f"aucune cle de signature sur l'ISO ({chemin_iso}) : une reference "
            "d'image issue d'une decouverte ne peut pas etre validee par une "
            "cle qui voyage avec elle")
    return CLE_EMBARQUEE, "embarquee"


def _prefixe_registre(url):
    """`ghcr.io/org/depot:tag` -> `ghcr.io/org`. La politique s'ecrit par
    espace de noms : la restreindre au depot exact casserait au premier
    locataire de plus."""
    nu = re.sub(r"^[a-z0-9+.-]+://", "", url).split("@")[0]
    morceaux = nu.split("/")
    if len(morceaux) < 2:
        raise ProvisionError(f"reference d'image inattendue : {url!r}")
    return "/".join(morceaux[:2])


def render_containers_policy(url, chemin_cle=None):
    """Politique containers-image : tout refuser, sauf notre espace de noms
    signe par notre cle.

    Le defaut est `reject`, pas `insecureAcceptAnything` : une image hors de
    l'espace de noms attendu doit etre REFUSEE, pas acceptee faute de regle.
    """
    chemin_cle = chemin_cle or CLE_POSEE
    prefixe = _prefixe_registre(url)
    registre = prefixe.split("/")[0]
    return json.dumps({
        "default": [{"type": "reject"}],
        "transports": {
            "docker": {
                prefixe: [{
                    "type": "sigstoreSigned",
                    "keyPath": chemin_cle,
                    "signedIdentity": {"type": "matchRepoDigestOrExact"},
                }],
                registre: [{"type": "reject"}],
            },
            "containers-storage": {"": [{"type": "insecureAcceptAnything"}]},
        },
    }, indent=2) + "\n"


def render_image_line(url, verifier):
    """La ligne ostreecontainer qu'Anaconda %include."""
    ligne = f"ostreecontainer --url={url} --transport=registry"
    if not verifier:
        ligne += " --no-signature-verification"
    return ligne + "\n"


def armer_verification_image(url, exiger=False, out=None, chemin_iso=None,
                             policy_path=None, cle_path=None,
                             include_path=None):
    """Pose la politique et la ligne d'image. Rend True si la signature sera
    verifiee.

    `exiger=True` : la reference vient d'une DECOUVERTE (domaine tape). On
    echoue alors FERME — mieux vaut ne pas installer que d'installer le
    systeme de n'importe qui. `exiger=False` : reference en dur, on retombe
    sur le comportement d'aujourd'hui en le DISANT, plutot que de faire
    echouer une installation qui marchait.
    """
    out = out or (lambda msg: None)
    policy_path = policy_path or POLICY_CONTAINERS
    cle_path = cle_path or CLE_POSEE
    include_path = include_path or IMAGE_INCLUDE
    try:
        cle, provenance = lire_cle_cosign(chemin_iso, exiger=exiger)
        if exiger and provenance != "iso":
            raise ProvisionError("cle embarquee refusee pour une reference "
                                 "issue d'une decouverte")
        write_public(cle_path, cle)   # publique par nature
        write_public(policy_path, render_containers_policy(url, cle_path))
        write_public(include_path, render_image_line(url, True))
        out(f"[bfos] signature de l'image verifiee a la poussee "
            f"(cle {provenance}).\n")
        return True
    except Exception as exc:  # noqa: BLE001
        if exiger:
            # Echec FERME : on ecrit une ligne qui n'installera rien plutot
            # qu'une ligne qui installerait n'importe quoi.
            write_public(include_path,
                         "# Image refusee : signature non verifiable.\n"
                         f"# {exc}\n")
            out(f"[bfos] INSTALLATION REFUSEE : {exc}\n")
            return False
        write_public(include_path, render_image_line(url, False))
        out(f"[bfos] signature de l'image NON verifiee ({exc}) ; "
            "comportement inchange par rapport a avant.\n")
        return False


def plan_par_defaut(run=None):
    """Inspecte les disques reels et decide. Ne leve jamais : ne pas savoir
    inspecter, c'est un cas de plus ou l'operateur tranche."""
    import subprocess
    run = run or (lambda cmd: subprocess.run(
        cmd, capture_output=True, text=True, timeout=60))
    try:
        r = run(["lsblk", "-b", "-J", "-o",
                 "NAME,PATH,TYPE,SIZE,FSTYPE,LABEL,RM,PARTTYPENAME"])
        if r.returncode != 0:
            return _ko("lsblk en echec")
        plan = choisir_plan(inspecter_disques(r.stdout), taille_minimale_fs)
        # ⚠️ INTERRUPTEUR, et pourquoi il existe.
        # Ce changement en porte deux : ne plus effacer un disque a l'aveugle
        # (gain de surete, vrai des maintenant), et retrecir le voisin pour
        # cohabiter (code qui n'a JAMAIS touche un vrai disque portant un autre
        # systeme). Les livrer ensemble ferait dependre le premier du second.
        # Par defaut la cohabitation retombe donc sur la question a l'operateur
        # — ce qui reste tres au-dessus de l'ancien comportement, qui effacait.
        # A basculer a 1 une fois le retrecissement eprouve sur une VM jetable
        # avec Windows installe, pas sur une machine de client.
        if plan.get("mode") == "cohabitation" and not _env_drapeau(
                "BFOS_COHABITATION", False):
            return _ko(
                f"cohabitation possible sur {plan['partition']} "
                f"({plan['libere'] // _GIO} Gio liberables) mais le "
                "retrecissement automatique n'est pas encore active "
                "(BFOS_COHABITATION) — l'operateur tranche")
        return plan
    except Exception as exc:  # noqa: BLE001
        return _ko(f"inspection impossible ({exc})")


def write_autopart(passphrase=None, path=None, chiffrer=True, plan=None):
    """Ecrit le partitionnement qu'Anaconda %include, et le verrouille.

    Trois issues possibles, dans l'ordre de surete decroissante :
      - « interactif » : que des commentaires, Anaconda pose la question ;
      - « cohabitation » : on retrecit le voisin, on ne l'efface pas ;
      - « disque_entier » : l'ancien comportement, sur un disque vierge.

    0600, parce que pour la duree de l'installation ce fichier EST la cle du
    disque. Pas de `except OSError: pass` sur le chmod : un chmod avale en
    silence laissait la cle en 0644 sans que rien ne le dise.
    """
    path = path or AUTOPART_INCLUDE
    if plan is None:
        plan = plan_par_defaut()
    if not chiffrer:
        # Un appelant qui passerait les deux se contredit : on n'accepte pas
        # de phrase ici.
        passphrase = None
    write_private(path, render_partitionnement(plan, passphrase, chiffrer))
    return path


def enrol_machine(enroll_url, token, policy, post=_post_json,
                  new_uuid=None, os_version=None, disk_passphrase=""):
    """Register this machine and return the staged dict, or raise ProvisionError.

    The UUID is drawn here, not by the server: it is the machine's own handle on
    its record, and drawing it locally means a re-run of the same install (same
    staged file) rotates that record's token instead of piling up a second one.
    A reinstall draws a fresh one — the two records coexist and `last_seen` is
    what tells them apart.
    """
    machine_uuid = str((new_uuid or uuid.uuid4)())
    hostname = (policy.get("install", {}) or {}).get("hostname", "") or ""
    payload = {
        "machine_uuid": machine_uuid,
        "hostname": hostname,
        "os_version": image_version() if os_version is None else os_version,
    }
    if disk_passphrase:
        payload["disk_passphrase"] = disk_passphrase
    try:
        status, raw = post(enroll_url, payload, token)
    except Exception as exc:  # noqa: BLE001
        raise ProvisionError(f"enrolment request failed: {exc}") from exc
    if status != 200:
        raise ProvisionError(f"enrolment HTTP {status}")
    try:
        d = json.loads(raw)
    except ValueError as exc:
        raise ProvisionError(f"enrolment bad JSON: {exc}") from exc
    if not isinstance(d, dict) or not d.get("token") or not d.get("endpoint"):
        raise ProvisionError("enrolment response missing token/endpoint")
    return {
        "schema": "bf-machine/v1",
        "machine_uuid": d.get("machine_uuid") or machine_uuid,
        "machine_id": d.get("machine_id"),
        # Endpoint served by the org itself, so a tenant can move its policy
        # plane without us guessing the URL from the install-time one.
        "endpoint": d["endpoint"],
        "token": d["token"],
        "hostname": d.get("hostname") or hostname,
        "user": d.get("user", ""),
        "enrolled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        # Odoo's answer to "do you hold this passphrase?". ⚠️ The flag, never
        # the passphrase: this dict is what gets written to /etc/bluefox/
        # machine.json on the installed system, and the disk key has no
        # business surviving the install.
        "disk_escrowed": bool(d.get("disk_escrowed")),
        "disk_escrow_error": str(d.get("disk_escrow_error") or "")[:200],
    }


def fallback_policy(env=None):
    env = env if env is not None else os.environ
    return {
        "schema": "bf-policy/v2",
        "org": {"company": "", "domain": ""},
        "user": {"login": ""},
        "install": {
            "locale": env.get("BFOS_FALLBACK_LANG", "fr_CA.UTF-8"),
            "keymap": env.get("BFOS_FALLBACK_KEYMAP", "ca"),
            "timezone": env.get("BFOS_FALLBACK_TIMEZONE", "America/Montreal"),
            "hostname": "blue-fox-os",
            "root": "locked",
            "login": {"mode": "local", "ldap_uri": "", "ldap_base_dn": ""},
        },
        "policies": {
            "offline_login": {"enabled": True, "max_offline_days": 7},
            "mfa_required": True,
            "auto_lock_minutes": 15,
        },
        "session": {"accent_color": "#29ABE1", "wallpaper_url": "",
                    "mounts": [], "pwas": []},
        "fallback": True,
    }


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_SEUL_LF_RE = re.compile(r"(?<!\r)\n")


def _en_crlf(msg):
    """Termine chaque ligne par CRLF, sans doubler ceux qui en ont deja un."""
    return _SEUL_LF_RE.sub("\r\n", msg)


def _sans_ansi(msg):
    return _ANSI_RE.sub("", msg)


def _console_writer():
    """Ecrit sur la console d'installation, et une copie a plat dans le journal.

    La console est ouverte explicitement en UTF-8 avec errors="replace". La
    locale du %pre est souvent ASCII : un seul caractere de dessin suffisait
    alors a lever UnicodeEncodeError. Sur la console c'etait deja avale en
    silence, mais le `print` vers stderr, lui, etait HORS du try — l'exception
    remontait jusqu'au garde-fou general et faisait basculer toute
    l'installation sur la politique de repli. Autrement dit : un ecran plus
    joli qui empeche l'enrolement. Le journal passe par le tampon binaire pour
    la meme raison, et sans les sequences ANSI, qui n'ont rien a y faire.
    """
    try:
        fh = open(CONSOLE, "w", encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        fh = None

    def write(msg):
        if fh is not None:
            try:
                # ⚠️ CRLF, et ce n'est pas de la coquetterie (mesure
                # en VM). La console de l'installateur n'a pas
                # ONLCR : un \n y descend d'une ligne SANS ramener le curseur
                # a gauche. Chaque ligne repart donc la ou la precedente s'est
                # arretee — l'ecran part en escalier et le contenu, pourtant
                # exact, devient illisible. Le defaut existait avant le code
                # QR : l'ancienne banniere de six lignes courtes escaladait
                # deja, ca se voyait juste a peine. Vingt-quatre lignes le
                # rendent flagrant.
                fh.write(_en_crlf(msg))
                fh.flush()
            except Exception:  # noqa: BLE001
                pass
        plat = _sans_ansi(msg)
        try:
            tampon = getattr(sys.stderr, "buffer", None)
            if tampon is not None:
                tampon.write((plat + "\n").encode("utf-8", "replace"))
                tampon.flush()
            else:
                print(plat, file=sys.stderr)
        except Exception:  # noqa: BLE001
            pass

    return write


# =========================================================================
# Identifiants Nextcloud obtenus en arriere-plan
# =========================================================================
# Avant : au premier demarrage, l'agent ouvrait un navigateur, l'usager
# refaisait un SSO Nextcloud (Login Flow v2) et on en tirait un mot de passe
# d'application. Une deuxieme authentification pour la meme personne, a la
# meme minute, apres celle qui vient d'autoriser l'installation.
#
# Maintenant : pendant que le porteur de l'operateur est encore en main, on
# l'echange (RFC 8693) contre un jeton destine au fournisseur Nextcloud, et on
# s'en sert une fois pour frapper un mot de passe d'application durable. Le
# jeton echange vit une heure ; le mot de passe d'application, lui, survit et
# se revoque depuis Nextcloud comme n'importe quel autre appareil.
#
# Degradation, pas panne : sans oidc_client_id dans la politique, sans reseau,
# sur un refus d'Authentik ou de Nextcloud, on ne stage rien et l'agent
# retombe sur le parcours SSO d'avant. C'est une etape en moins, pas une
# etape dont tout depend.

NC_CREDENTIALS_JSON = "/tmp/bfos-nc-credentials.json"
_GRANT_ECHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
_TYPE_JETON_ACCES = "urn:ietf:params:oauth:token-type:access_token"


def _get_ocs(url, token, timeout=30):
    """GET sur l'API OCS de Nextcloud. L'en-tete OCS-APIRequest n'est pas
    decorative : sans elle Nextcloud refuse la requete."""
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")
    req.add_header("OCS-APIRequest", "true")
    req.add_header("User-Agent", "Blue Fox OS (poste de siege)")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode()


def echanger_jeton(token_url, client_id, jeton, audience, post=_post_form):
    """RFC 8693 : troque le jeton de l'installation contre un jeton destine a
    un autre fournisseur, sans rien redemander a personne.

    ⚠️ Authentik refuse l'echange (invalid_target / target_not_federated) tant
    que le fournisseur CIBLE ne declare pas federer avec le demandeur. Cote
    Authentik c'est jwt_federation_providers sur le fournisseur Nextcloud, pose
    par le plan nextcloud-bf-federation.yaml. La confiance se donne ; elle ne
    se suppose pas, et aucune variante de parametres ne la remplace.
    """
    try:
        status, raw = post(token_url, {
            "grant_type": _GRANT_ECHANGE,
            "client_id": client_id,
            "subject_token": jeton,
            "subject_token_type": _TYPE_JETON_ACCES,
            "audience": audience,
            "scope": SCOPE,
        })
    except Exception as exc:  # noqa: BLE001
        raise ProvisionError(f"token exchange request failed: {exc}") from exc
    if status != 200:
        raise ProvisionError(f"token exchange HTTP {status}")
    try:
        d = json.loads(raw)
    except ValueError as exc:
        raise ProvisionError(f"token exchange bad JSON: {exc}") from exc
    jeton_echange = d.get("access_token")
    if not jeton_echange:
        raise ProvisionError("token exchange returned no access_token")
    return jeton_echange


def _charge_ocs(raw):
    """Extrait `data` d'une reponse OCS apres avoir verifie son statut INTERNE.

    ⚠️ OCS rend un echec dans le corps avec un 200 HTTP tout autant qu'avec un
    401 : se fier au code HTTP seul, c'est prendre un refus pour un succes.
    """
    try:
        d = json.loads(raw)
    except ValueError as exc:
        raise ProvisionError(f"OCS bad JSON: {exc}") from exc
    meta = ((d.get("ocs") or {}).get("meta") or {})
    code = meta.get("statuscode")
    if code not in (100, 200):
        raise ProvisionError(f"OCS refused ({code}): {meta.get('message', '')}")
    return (d.get("ocs") or {}).get("data") or {}


def obtenir_identifiants_nextcloud(token, policy, env=None, out=None,
                                   post=_post_form, get=_get_ocs, path=None):
    """Frappe un mot de passe d'application Nextcloud pour le siege.

    Ne leve jamais : c'est un `after_policy`, et `run()` documente que rien
    n'y a le droit de remonter. Retourne le dict stage, ou None.
    """
    out = out or (lambda msg: None)
    try:
        return _obtenir_identifiants_nextcloud(
            token, policy, env=env, out=out, post=post, get=get, path=path)
    except Exception as exc:  # noqa: BLE001 — le contrat est : ne jamais lever
        out(f"[bfos] les identifiants Nextcloud n'ont pas pu etre obtenus "
            f"({exc}) ; l'agent d'accueil proposera le parcours SSO habituel.\n")
        return None


def _obtenir_identifiants_nextcloud(token, policy, env=None, out=None,
                                    post=_post_form, get=_get_ocs, path=None):
    """Le corps de obtenir_identifiants_nextcloud. A part, pour que le garde
    ci-dessus soit le seul chemin d'entree — comme pour l'enrolement."""
    env = env if env is not None else os.environ
    out = out or (lambda msg: None)
    path = path or NC_CREDENTIALS_JSON

    nc = ((policy.get("services") or {}).get("nextcloud") or {})
    nc_url = (nc.get("url") or "").rstrip("/")
    audience = nc.get("oidc_client_id") or ""
    token_url = env.get("BFOS_OIDC_TOKEN_URL", "")
    client_id = env.get("BFOS_OIDC_CLIENT_ID", "")

    if not (nc_url and audience and token_url and client_id):
        manquant = [nom for nom, val in (
            ("services.nextcloud.url", nc_url),
            ("services.nextcloud.oidc_client_id", audience),
            ("BFOS_OIDC_TOKEN_URL", token_url),
            ("BFOS_OIDC_CLIENT_ID", client_id)) if not val]
        out("[bfos] identifiants Nextcloud non demandes : "
            f"{', '.join(manquant)} absent(s).\n")
        return None

    jeton_nc = echanger_jeton(token_url, client_id, token, audience, post=post)

    # On demande d'abord QUI Nextcloud voit. Deux raisons : le porteur est
    # valide avant qu'on frappe quoi que ce soit, et l'identifiant Nextcloud
    # n'est pas celui de la politique — l'annuaire sert « Prenom » la ou la
    # politique porte « prenom@... ». Deviner la casse suffirait a ecrire un
    # rclone.conf qui ne monte rien.
    status, raw = get(f"{nc_url}/ocs/v2.php/cloud/user?format=json", jeton_nc)
    if status != 200:
        raise ProvisionError(f"Nextcloud refused the bearer (HTTP {status})")
    login = (_charge_ocs(raw) or {}).get("id") or ""
    if not login:
        raise ProvisionError("Nextcloud returned no user id")

    status, raw = get(f"{nc_url}/ocs/v2.php/core/getapppassword?format=json",
                      jeton_nc)
    if status != 200:
        raise ProvisionError(f"getapppassword HTTP {status}")
    mot_de_passe = (_charge_ocs(raw) or {}).get("apppassword") or ""
    if not mot_de_passe:
        raise ProvisionError("getapppassword returned no password")

    stage = {"login": login, "app_password": mot_de_passe, "url": nc_url}
    write_private(path, json.dumps(stage))
    out(f"[bfos] Nextcloud : mot de passe d'application obtenu pour {login} "
        "(aucun mot de passe n'a ete demande).\n")
    return stage


def run(env=None, post=_post_form, get=_get, sleep=time.sleep, out=None,
        after_policy=None):
    """Full device flow → policy fetch. Returns the policy dict or raises.

    `after_policy(access_token, policy)` runs while the operator's bearer is
    still valid — that is the enrolment window, and the only one. It must not
    raise: the policy is already in hand at that point, and nothing about it
    depends on the enrolment succeeding.
    """
    env = env if env is not None else os.environ
    out = out or _console_writer()
    device_url = env.get("BFOS_OIDC_DEVICE_URL", "")
    token_url = env.get("BFOS_OIDC_TOKEN_URL", "")
    client_id = env.get("BFOS_OIDC_CLIENT_ID", "")
    policy_url = env.get("BFOS_POLICY_URL", "")
    if not all([device_url, token_url, client_id, policy_url]):
        raise ProvisionError("missing BFOS_OIDC_* / BFOS_POLICY_URL config")
    d = device_authorize(device_url, client_id, post=post)
    announce(d, out)
    token = poll_token(
        token_url, client_id, d["device_code"],
        d.get("interval", 5), d.get("expires_in", 300),
        post=post, sleep=sleep, out=out)
    policy = fetch_policy(policy_url, token, get=get, sleep=sleep, out=out)
    if after_policy is not None:
        after_policy(token, policy)
    return policy


def stage_enrolment(token, policy, env=None, out=None, post=_post_json,
                    path=None, autopart_path=None, gen=None, decider=None,
                    marqueur_tpm=None, compte_path=None, plan=None):
    """Enrol this machine, stage the secret, and settle the disk passphrase.

    ⚠️ This is the `after_policy` seam, and `run()` documents that it must not
    raise. Before the disk escrow, that held by construction: every statement lived under
    a try. The escrow added three bare calls, and any of them raising would
    have travelled up through `run()` to `main()`, which treats one exception
    as total failure and throws away a policy that was already in hand — the
    machine would then install on org fallbacks, with no enrolment, over a
    successful fetch. The guard below restores the contract rather than trusting
    each new statement to be safe.

    Returns the staged dict, or None when enrolment was skipped or failed. A
    machine that fails to enrol installs exactly as before — it just won't
    follow later policy changes on its own, which is a degradation, not a
    breakage, and the console says so.

    The disk passphrase rides the same authenticated window, and the
    ORDER here is the whole safety argument: we draw a passphrase, we ask Odoo
    to keep it, and we only tell Anaconda to use it once Odoo has said yes.
    Every other outcome — escrow off, no key on the server, enrolment failed,
    deposit refused — leaves the include file exactly as the %pre wrote it, so
    Anaconda prompts and a human ends up holding the key, which is where we
    started. There is no path that seals a disk against everyone.
    """
    out = out or (lambda msg: None)
    try:
        return _stage_enrolment(token, policy, env=env, out=out, post=post,
                                path=path, autopart_path=autopart_path,
                                gen=gen, decider=decider,
                                marqueur_tpm=marqueur_tpm,
                                compte_path=compte_path, plan=plan)
    except Exception as exc:  # noqa: BLE001 — the contract is: never raise
        out(f"[bfos] the enrolment step failed ({exc}); the policy already "
            "fetched is kept and the install goes on. Anaconda will ask for "
            "a disk passphrase.\n")
        return None


def _stage_enrolment(token, policy, env=None, out=None, post=_post_json,
                     path=None, autopart_path=None, gen=None, decider=None,
                     marqueur_tpm=None, compte_path=None, plan=None):
    """The body of stage_enrolment. Kept apart so the guard above is the only
    way in, and so nothing added here can quietly break the no-raise contract.
    """
    env = env if env is not None else os.environ
    out = out or (lambda msg: None)
    path = path or MACHINE_JSON
    url = env.get("BFOS_ENROLL_URL", "") or enroll_url_from(
        env.get("BFOS_POLICY_URL", ""))
    if not url:
        out("[bfos] no enrolment endpoint; this machine will not follow later "
            "policy changes\n")
        return None

    # ⚠️ La decision de chiffrer se prend AVANT l'enrolement : la prendre apres
    # reviendrait a sequestrer une phrase pour un disque laisse en clair, et la
    # fiche machine d'Odoo mentirait sur ce que porte la machine.
    decider = decider or decider_chiffrement
    chiffrer, enroler_tpm = decider(out)

    # Le plan de partitionnement se decide ICI, une seule fois, et il commande
    # le sequestre. ⚠️ En mode « interactif » c'est ANACONDA qui demandera une
    # phrase a l'operateur : celle qu'on tirerait n'ouvrirait rien. Deposer
    # quand meme donnerait a Odoo une cle qui n'est pas celle du disque — pire
    # que pas de cle du tout, parce qu'on la croirait bonne le jour ou elle
    # servirait.
    plan = plan if plan is not None else plan_par_defaut()
    if plan.get("mode") == "interactif":
        out("[bfos] partitionnement laisse a l'operateur "
            f"({plan.get('raison', 'raison inconnue')}) ; aucune phrase de "
            "disque n'est tiree ni deposee : celle qu'Anaconda demandera sera "
            "la vraie, et elle n'appartiendra qu'a la personne presente.\n")
        write_autopart(path=autopart_path, chiffrer=chiffrer, plan=plan)
        chiffrer = False          # coupe le sequestre plus bas, sans le dupliquer
    elif not chiffrer:
        write_autopart(path=autopart_path, chiffrer=False, plan=plan)

    passphrase = ""
    if chiffrer and escrow_requested(policy):
        passphrase = generate_disk_passphrase(rng=gen)

    try:
        machine = enrol_machine(url, token, policy, post=post,
                                disk_passphrase=passphrase)
    except Exception as exc:  # noqa: BLE001 — never abort the install
        out(f"[bfos] enrolment failed ({exc}); this machine will not follow "
            "later policy changes\n")
        if passphrase:
            # ⚠️ Do NOT claim the deposit failed. We know we got no answer; we
            # do not know what the server did. Odoo commits the escrow before
            # it replies, so a lost return leg leaves a record saying this
            # machine is escrowed while the operator is about to seal the disk
            # with a passphrase of their own. The person reading this screen is
            # the only one placed to notice, and telling them "NOT deposited"
            # is precisely what would stop them looking.
            out("[bfos] no answer on the disk passphrase deposit: it may or "
                "may not have been recorded. Anaconda will ask for one, and "
                "that typed passphrase is the real one. If Odoo lists this "
                "machine as escrowed, that record is wrong — clear it.\n")
        return None

    if passphrase:
        if machine.get("disk_escrowed"):
            write_autopart(passphrase, path=autopart_path, plan=plan)
            out("[bfos] disk passphrase drawn and deposited in Odoo; this "
                "install will not ask for one\n")
            # Le compte de secours prend la MEME phrase. Ecrit ici et pas
            # ailleurs : seul ce point sait qu'Odoo la detient, donc qu'elle
            # sera revelable. Un compte ouvert sur une phrase perdue serait
            # une porte murée.
            if secours_voulu(policy):
                write_compte(passphrase, path=compte_path, out=out)
                out(f"[bfos] compte de secours {COMPTE_SECOURS} ouvert sur la "
                    "phrase du disque (revelable dans Odoo)\n")
            else:
                # La forme VERROUILLEE ecrite par le kickstart reste en place :
                # elle satisfait Anaconda, qui refuse de commencer quand root
                # est verrouille et qu'aucun usager n'existe. On n'y ecrit
                # surtout PAS la phrase du disque — inutile de la poser dans
                # /etc/shadow d'un compte que le %post va supprimer.
                out(f"[bfos] la politique refuse le compte de secours : "
                    f"{COMPTE_SECOURS} reste verrouille le temps de "
                    "l'installation, et sera supprime au %post\n")
            if enroler_tpm:
                # Le %post --nochroot lit ce marqueur ET la phrase dans
                # /tmp/bfos-autopart.ks. Un marqueur explicite plutot qu'une
                # deduction : « il y a un TPM et la ligne porte une phrase »
                # serait vrai aussi quand l'operateur a refuse l'enrolement.
                try:
                    write_private(marqueur_tpm or TPM_MARKER, "")
                    out("[bfos] TPM2 present : le disque sera enrole a "
                        "l'installation, sans invite au demarrage\n")
                except OSError as exc:
                    out(f"[bfos] marqueur TPM non ecrit ({exc}); le disque "
                        "demandera sa phrase a chaque demarrage\n")
        else:
            reason = machine.get("disk_escrow_error") or "reason unknown"
            out(f"[bfos] disk passphrase NOT deposited ({reason}); Anaconda "
                "will ask for one\n")
    try:
        # Le secret de la machine. 0600 AVANT tout, et jamais journalise —
        # ce que le commentaire promettait deja, mais que le chmod POSTERIEUR
        # ne rendait pas.
        write_private(path, json.dumps(machine))
    except OSError as exc:
        out(f"[bfos] could not stage the machine secret ({exc})\n")
        return None
    out(f"[bfos] machine enrolled as {machine['hostname']} "
        f"({machine['machine_uuid']})\n")
    return machine


def _apres_politique(token, policy, out):
    """Tout ce qui exige le porteur de l'operateur, pendant qu'il vaut encore.

    Les deux etapes sont independantes et aucune ne leve : un enrolement rate
    ne doit pas couter les identifiants Nextcloud, et l'inverse non plus.
    """
    stage_enrolment(token, policy, out=out)
    obtenir_identifiants_nextcloud(token, policy, out=out)


def assurer_plan_de_partitionnement(out=None, path=None, plan_fn=None):
    """Garantit qu'un plan de partitionnement est ecrit, quoi qu'il arrive.

    ⚠️ LE DEFAUT QUE CETTE FONCTION FERME (mesure en VM).
    Le plan etait ecrit dans _stage_enrolment, et DEUX chemins le sautaient :
      1. le flux d'appareil echoue — un code non autorise dans les dix minutes,
         cas NORMAL et non une panne — : main() part sur la politique de repli
         sans jamais appeler _stage_enrolment ;
      2. le flux reussit mais aucune adresse d'enrolement n'est derivable :
         _stage_enrolment sort par `return None` AVANT de calculer le plan.
    Dans les deux cas le filet du kickstart — un simple commentaire depuis
    qu'il ne peut plus effacer un disque — restait tel quel, et Anaconda
    affichait « Kickstart insufficient » sur un disque VIERGE.

    Or l'inspection des disques n'a besoin d'aucune autorisation : seule la
    phrase du disque en depend. On decide donc OU s'installer dans tous les
    cas ; sans phrase sequestree, Anaconda demandera la phrase, ce qui est le
    comportement documente quand le sequestre n'aboutit pas.

    N'ecrase JAMAIS un plan reel : si le fichier porte deja une directive
    active (celle du sequestre, avec sa phrase, ou un choix explicite de ne
    pas chiffrer), on n'y touche pas. Un plan « interactif » delibere ne porte
    que des commentaires ; le recalculer rend le meme resultat sur les memes
    disques, donc c'est sans effet. Ne leve jamais : main() ne doit pas
    avorter l'installation.
    """
    out = out or (lambda _m: None)
    path = path or AUTOPART_INCLUDE
    try:
        with open(path, encoding="utf-8") as fh:
            actives = [l for l in fh.read().splitlines()
                       if l.strip() and not l.lstrip().startswith("#")]
        if actives:
            return False
    except OSError:
        pass
    try:
        plan = (plan_fn or plan_par_defaut)()
        write_autopart(path=path, chiffrer=True, plan=plan)
        out(f"[bfos] partitionnement decide sans flux d'appareil abouti : "
            f"{plan.get('mode')}"
            + (f" ({plan.get('raison')})" if plan.get("raison") else "")
            + " ; la phrase du disque sera demandee a l'installation\n")
        return True
    except Exception as exc:  # noqa: BLE001 — ne jamais avorter l'installation
        out(f"[bfos] partitionnement non decide ({exc}) ; Anaconda "
            "demandera ou s'installer\n")
        return False


def main(argv=None):
    out = _console_writer()
    try:
        policy = run(out=out,
                     after_policy=lambda tok, pol: _apres_politique(
                         tok, pol, out=out))
        out("[bfos] policy received for user "
            f"{policy.get('user', {}).get('login', '?')}\n")
    except Exception as exc:  # noqa: BLE001 — never abort the install
        out(f"[bfos] provisioning failed ({exc}); using org fallback defaults\n")
        policy = fallback_policy()
    # The staged policy carries the operator login + LDAP endpoints (no token),
    # so keep it owner-only rather than the installer's default umask (0o644).
    write_private(STAGED_JSON, json.dumps(policy))
    # Toujours, reussite comme repli : voir assurer_plan_de_partitionnement.
    assurer_plan_de_partitionnement(out=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
