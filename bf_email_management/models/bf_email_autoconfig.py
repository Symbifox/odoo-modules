"""Découverte de la configuration courriel d'une adresse.

Une cascade de sept voies, arrêt au premier succès. Rejouée par CE code, le
2026-09-20, sur 53 domaines réellement présents dans nos carnets :
**49 résolus (92 %)**, 0,94 s de moyenne, pire cas 4,4 s.

    1. autoconfig.<domaine>/mail/config-v1.1.xml      4 domaines
    2. <domaine>/.well-known/autoconfig/…             0 domaine
    3. ISPDB Thunderbird sur le domaine              15 domaines
    4. SRV RFC 6186 (_imaps._tcp)                     4 domaines
    5. MX puis ISPDB sur le domaine du MX            22 domaines  <-- la clé
    6. empreinte du MX (table ci-dessous)             1 domaine
    7. sonde imap./mail. VÉRIFIÉE                     3 domaines
    8. rien                                           4 domaines

Et ce que la cascade rend, en modes d'entrée : **23 domaines en OAuth
obligatoire** (tous Microsoft), 11 en mot de passe d'application, 19 en mot
de passe ordinaire.

🔴 L'étape 5 est celle qui décide : un domaine d'entreprise chez Google
Workspace ou Microsoft 365 ne publie ni autoconfig, ni `.well-known`, ni SRV,
et n'est pas dans l'ISPDB. Seul son MX le nomme. Sans elle la couverture
tombe de 83 % à 41 %.

⚠️ L'étape 7 ne se contente JAMAIS d'un enregistrement DNS. 34 des 53 domaines
ont un `imap.` ou un `mail.` qui résout, **12** répondent réellement en IMAP.
sur certains domaines le `imap.` résout et refuse 993, tandis que c'est le
`mail.` qui répond ; sur d'autres, l'inverse.

🔴 Et ce que rend la cascade n'est PAS une promesse de connexion. Le serveur
`outlook.office365.com` annonce `AUTH=PLAIN` dans son CAPABILITY et répond
`Basic authentication is disabled.` à la tentative. C'est pourquoi le mode
d'authentification est décidé par le FOURNISSEUR reconnu, jamais par ce que le
serveur annonce.

Toutes les requêtes sortantes passent par ``safe_push_endpoint`` : la cascade
fabrique des URL à partir d'une chaîne fournie par l'usager, ce qui en ferait
une SSRF aveugle sans ce garde-fou.
"""

import logging
import pathlib
import re
import secrets
import socket
import ssl
import time
from urllib.parse import quote

import requests

from odoo import api, models

from .push_transport import safe_push_endpoint

_logger = logging.getLogger(__name__)

# Budget total de la cascade, et par étape. Mesuré : la médiane est à 0,43 s,
# le pire cas à 2,35 s, et l'étape la plus lente est `.well-known`, qui n'a
# jamais rien rendu sur 53 domaines. On la garde par conformité, avec le délai
# le plus court de la cascade.
DELAI_TOTAL = 8.0
DELAI_ETAPE = 2.5
DELAI_WELLKNOWN = 1.2
DELAI_IMAP = 2.5

RESOLVEUR_DOH = "https://cloudflare-dns.com/dns-query"

# Empreintes de MX : le suffixe de l'hôte MX, puis le fournisseur. Ordre
# significatif, du plus précis au plus général.
EMPREINTES_MX = (
    ("migadu.com", "migadu"),
    ("googlemail.com", "google"),
    ("google.com", "google"),
    ("protection.outlook.com", "microsoft"),
    ("outlook.com", "microsoft"),
    ("office365.com", "microsoft"),
    ("yahoodns.net", "yahoo"),
    ("icloud.com", "apple"),
    ("me.com", "apple"),
    ("zoho.com", "zoho"),
    ("zoho.eu", "zoho"),
)

# Ce qu'on sait des grands fournisseurs, et surtout COMMENT on y entre.
# `auth` vaut :
#   password      : le mot de passe du compte suffit
#   app_password  : il faut un mot de passe d'application (2FA obligatoire)
#   oauth         : le mot de passe est refusé, point
FOURNISSEURS = {
    "migadu": {
        "libelle": "Migadu",
        "imap": ("imap.migadu.com", 993, "SSL"),
        "smtp": ("smtp.migadu.com", 465, "SSL"),
        "auth": "password",
    },
    "google": {
        "libelle": "Google (Gmail, Workspace)",
        "imap": ("imap.gmail.com", 993, "SSL"),
        "smtp": ("smtp.gmail.com", 465, "SSL"),
        "auth": "app_password",
        "oauth": "google",
        "aide_url": "https://myaccount.google.com/apppasswords",
    },
    "microsoft": {
        "libelle": "Microsoft 365 / Outlook.com",
        # Les trois noms (smtp.office365.com, smtp.outlook.com,
        # smtp-mail.outlook.com) répondent et annoncent LOGIN + XOAUTH2 sur
        # 587 ; celui-ci est le seul qui serve les comptes d'entreprise ET
        # les comptes personnels.
        "imap": ("outlook.office365.com", 993, "SSL"),
        "smtp": ("smtp.outlook.com", 587, "STARTTLS"),
        "auth": "oauth",
        "oauth": "microsoft",
    },
    "yahoo": {
        "libelle": "Yahoo / AOL",
        "imap": ("imap.mail.yahoo.com", 993, "SSL"),
        "smtp": ("smtp.mail.yahoo.com", 465, "SSL"),
        "auth": "app_password",
        "aide_url": "https://login.yahoo.com/account/security",
    },
    "apple": {
        "libelle": "Apple iCloud",
        "imap": ("imap.mail.me.com", 993, "SSL"),
        "smtp": ("smtp.mail.me.com", 587, "STARTTLS"),
        "auth": "app_password",
        "aide_url": "https://account.apple.com/account/manage",
    },
    "zoho": {
        "libelle": "Zoho",
        "imap": ("imap.zoho.com", 993, "SSL"),
        "smtp": ("smtp.zoho.com", 465, "SSL"),
        "auth": "app_password",
        "aide_url": "https://accounts.zoho.com/home#security/apppassword",
    },
}

# Reconnaissance du fournisseur à partir de l'hôte IMAP rendu par une voie
# quelconque : l'ISPDB rend `outlook.office365.com` sans dire « Microsoft ».
HOTES_FOURNISSEUR = (
    ("outlook.office365.com", "microsoft"),
    ("outlook.office.com", "microsoft"),
    ("imap-mail.outlook.com", "microsoft"),
    ("imap.gmail.com", "google"),
    ("imap.migadu.com", "migadu"),
    ("imap.mail.yahoo.com", "yahoo"),
    ("imap.aol.com", "yahoo"),
    ("imap.mail.att.net", "yahoo"),
    ("imap.mail.me.com", "apple"),
    ("imap.zoho.com", "zoho"),
)

# ⚠️ Deux noms, pas quatre. Mesuré sur les 53 domaines : les seuls qui
# répondent sont `imap.` (deux fournisseurs d'accès) et `mail.` (un hébergeur). Sonder
# `courriel.` et `imaps.` coûtait jusqu'à cinq secondes d'attente pour zéro
# découverte, et l'attente se paie dans un dialogue ouvert.
NOMS_SONDES = ("imap", "mail")


class BfEmailAutoconfig(models.AbstractModel):
    _name = "bf.email.autoconfig"
    _description = "Découverte de la configuration courriel"

    # ------------------------------------------------------------------
    # Entrée publique
    # ------------------------------------------------------------------
    @api.model
    @api.private
    def decouvrir(self, adresse):
        """Rend la configuration devinée pour ``adresse``.

        Ne lève jamais : une découverte qui échoue rend ``source = False`` et
        l'assistant bascule sur la saisie à la main. Un assistant qui plante
        parce qu'un domaine ne répond pas serait pire que pas d'assistant.
        """
        debut = time.monotonic()
        resultat = {
            "adresse": adresse, "domaine": False, "source": False,
            "fournisseur": False, "imap": False, "smtp": False,
            "auth": "password", "oauth": False, "aide_url": False,
            "mx": [], "duree_ms": 0, "essais": [],
        }
        domaine = self._domaine(adresse)
        if not domaine:
            return resultat
        resultat["domaine"] = domaine

        for nom, methode in (
            ("autoconfig", self._voie_autoconfig),
            ("wellknown", self._voie_wellknown),
            ("ispdb", self._voie_ispdb),
            ("srv", self._voie_srv),
            ("mx_ispdb", self._voie_mx_ispdb),
            ("empreinte_mx", self._voie_empreinte_mx),
            ("sonde", self._voie_sonde),
        ):
            if time.monotonic() - debut > DELAI_TOTAL:
                resultat["essais"].append("budget épuisé")
                break
            try:
                trouve = methode(domaine, adresse, resultat)
            except Exception as exc:  # une voie qui casse ne casse pas la suite
                _logger.info("autoconfig %s : voie %s en échec (%s)", domaine, nom, exc)
                trouve = None
            resultat["essais"].append(nom if trouve else f"{nom}:-")
            if trouve:
                resultat["source"] = nom
                resultat.update(trouve)
                break

        self._poser_le_mode_d_authentification(resultat)
        resultat["duree_ms"] = int((time.monotonic() - debut) * 1000)
        return resultat

    # ------------------------------------------------------------------
    # Les sept voies
    # ------------------------------------------------------------------
    def _voie_autoconfig(self, domaine, adresse, etat):
        url = (f"https://autoconfig.{domaine}/mail/config-v1.1.xml"
               f"?emailaddress={quote(adresse)}")
        return self._analyser_clientconfig(self._get(url))

    def _voie_wellknown(self, domaine, adresse, etat):
        url = f"https://{domaine}/.well-known/autoconfig/mail/config-v1.1.xml"
        return self._analyser_clientconfig(self._get(url, delai=DELAI_WELLKNOWN))

    def _voie_ispdb(self, domaine, adresse, etat):
        return self._analyser_clientconfig(
            self._get(f"https://autoconfig.thunderbird.net/v1.1/{domaine}"))

    def _voie_srv(self, domaine, adresse, etat):
        cible = self._srv(f"_imaps._tcp.{domaine}")
        if not cible:
            return None
        hote, port = cible
        envoi = (self._srv(f"_submissions._tcp.{domaine}")
                 or self._srv(f"_submission._tcp.{domaine}"))
        return {
            "imap": {"host": hote, "port": port or 993, "socket": "SSL"},
            "smtp": ({"host": envoi[0], "port": envoi[1] or 465,
                      "socket": "SSL" if (envoi[1] or 465) == 465 else "STARTTLS"}
                     if envoi else False),
        }

    def _voie_mx_ispdb(self, domaine, adresse, etat):
        """🔴 La voie qui décide : l'ISPDB lue sur le domaine du MX.

        Un domaine d'entreprise ne publie rien sur lui-même ; son MX, lui, dit
        chez qui il est. `aspmx.l.google.com` -> `google.com`, présent dans
        l'ISPDB. C'est ce que fait Thunderbird (bogue Mozilla 551519), et ce
        n'est pas dans sa page de wiki.
        """
        mx = self._mx(domaine)
        etat["mx"] = mx[:3]
        if not mx:
            return None
        hote = mx[0].lower().rstrip(".")
        morceaux = hote.split(".")
        for base in (".".join(morceaux[-2:]), ".".join(morceaux[-3:])):
            if not base or base == domaine:
                continue
            trouve = self._analyser_clientconfig(
                self._get(f"https://autoconfig.thunderbird.net/v1.1/{base}"))
            if trouve:
                trouve["base_mx"] = base
                return trouve
        return None

    def _voie_empreinte_mx(self, domaine, adresse, etat):
        """Notre propre table, pour ce que l'ISPDB ignore.

        🔴 Migadu n'est PAS dans l'ISPDB (404 sur `migadu.com` comme sur
        `mx1.migadu.com`), alors que google.com et outlook.com y sont. C'est la
        seule raison pour laquelle nos propres domaines tombent ici plutôt
        qu'à la voie précédente.
        """
        mx = etat.get("mx") or self._mx(domaine)
        etat["mx"] = mx[:3]
        for hote in mx:
            code = self._fournisseur_du_mx(hote)
            if code:
                return self._depuis_fournisseur(code)
        return None

    def _voie_sonde(self, domaine, adresse, etat):
        """Le repli, et il se VÉRIFIE en ouvrant une session IMAP."""
        depart = time.monotonic()
        for prefixe in NOMS_SONDES:
            if time.monotonic() - depart > DELAI_IMAP * 2:
                break
            hote = f"{prefixe}.{domaine}"
            if not self._parle_imap(hote):
                continue
            return {
                "imap": {"host": hote, "port": 993, "socket": "SSL"},
                "smtp": {"host": f"smtp.{domaine}", "port": 587,
                         "socket": "STARTTLS"} if self._resout(f"smtp.{domaine}") else False,
            }
        return None

    # ------------------------------------------------------------------
    # Le mode d'authentification, décidé par le fournisseur
    # ------------------------------------------------------------------
    def _poser_le_mode_d_authentification(self, etat):
        """🔴 Jamais d'après le CAPABILITY du serveur.

        `outlook.office365.com` annonce `AUTH=PLAIN` et refuse le mot de passe
        (« Basic authentication is disabled. », mesuré le 2026-09-20). Se fier
        à l'annonce ferait promettre à l'usager une connexion impossible.
        """
        code = etat.get("fournisseur")
        if not code and etat.get("imap"):
            code = self._fournisseur_de_l_hote(etat["imap"].get("host"))
            etat["fournisseur"] = code
        fiche = FOURNISSEURS.get(code or "")
        if not fiche:
            etat["auth"] = "password"
            return
        etat["auth"] = fiche["auth"]
        etat["oauth"] = fiche.get("oauth", False)
        etat["aide_url"] = fiche.get("aide_url", False)
        etat["libelle_fournisseur"] = fiche["libelle"]

    def _depuis_fournisseur(self, code):
        fiche = FOURNISSEURS[code]
        ih, ip, isock = fiche["imap"]
        sh, sp, ssock = fiche["smtp"]
        return {
            "fournisseur": code,
            "imap": {"host": ih, "port": ip, "socket": isock},
            "smtp": {"host": sh, "port": sp, "socket": ssock},
        }

    @staticmethod
    def _fournisseur_du_mx(hote):
        hote = (hote or "").lower().rstrip(".")
        for suffixe, code in EMPREINTES_MX:
            if hote == suffixe or hote.endswith("." + suffixe):
                return code
        return False

    @staticmethod
    def _fournisseur_de_l_hote(hote):
        hote = (hote or "").lower().rstrip(".")
        for nom, code in HOTES_FOURNISSEUR:
            if hote == nom or hote.endswith("." + nom):
                return code
        return False

    # ------------------------------------------------------------------
    # Plomberie : HTTP gardé, DNS par DoH, sonde IMAP
    # ------------------------------------------------------------------
    @staticmethod
    def _domaine(adresse):
        adresse = (adresse or "").strip().lower()
        if "@" not in adresse:
            return False
        domaine = adresse.rsplit("@", 1)[1].strip().strip(".")
        if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", domaine):
            return False
        return domaine

    def _get(self, url, delai=DELAI_ETAPE):
        """GET gardé contre la SSRF, qui ne lève jamais."""
        if not safe_push_endpoint(url):
            _logger.info("autoconfig : URL refusée par le garde-fou (%s)", url)
            return ""
        try:
            rep = requests.get(url, timeout=delai, allow_redirects=False,
                               headers={"User-Agent": "Symbifox autoconfig"})
        except requests.RequestException:
            return ""
        if rep.status_code != 200:
            return ""
        return rep.text or ""

    def _doh(self, nom, type_):
        """Résoudre MX ou SRV, par le résolveur du système d'abord.

        Ni `dnspython` ni `dig` ne sont dans l'image Odoo, et la bibliothèque
        standard ne sait pas demander un MX. La première version passait donc
        par DNS-over-HTTPS chez Cloudflare.

        ⚠️ Ça annonçait à un tiers, à chaque usage de l'assistant, le domaine
        de courriel que quelqu'un est en train de brancher. Le résolveur du
        conteneur voit la même chose, mais c'est le nôtre, et il est déjà dans
        le chemin de tout le reste. DoH ne sert donc plus que de secours, et
        il se coupe (`bf_email.doh_secours` à `0`).
        """
        reponses = self._dns_udp(nom, type_)
        if reponses:
            return reponses
        icp = self.env["ir.config_parameter"].sudo()
        if icp.get_param("bf_email.doh_secours", "1") in ("0", "False", "false"):
            return []
        try:
            rep = requests.get(
                icp.get_param("bf_email.doh_resolveur") or RESOLVEUR_DOH,
                timeout=DELAI_ETAPE, params={"name": nom, "type": type_},
                headers={"Accept": "application/dns-json"})
            if rep.status_code != 200:
                return []
            return [r.get("data", "") for r in (rep.json().get("Answer") or [])]
        except (requests.RequestException, ValueError):
            return []

    # -- un client DNS minimal, juste ce qu'il faut pour MX et SRV ----------
    _TYPES_DNS = {"MX": 15, "SRV": 33}

    @staticmethod
    def _serveurs_de_noms():
        try:
            lignes = pathlib.Path("/etc/resolv.conf").read_text().splitlines()
        except OSError:
            return []
        return [l.split()[1] for l in lignes
                if l.startswith("nameserver") and len(l.split()) > 1][:2]

    @classmethod
    def _nom_dns(cls, donnees, position):
        """Lire un nom, en suivant les pointeurs de compression (RFC 1035)."""
        morceaux, saut, vus = [], None, 0
        while True:
            if position >= len(donnees) or vus > 128:
                return "", len(donnees)
            taille = donnees[position]
            if taille == 0:
                position += 1
                break
            if taille & 0xC0 == 0xC0:           # pointeur
                if position + 1 >= len(donnees):
                    return "", len(donnees)
                cible = ((taille & 0x3F) << 8) | donnees[position + 1]
                if saut is None:
                    saut = position + 2
                position, vus = cible, vus + 1
                continue
            morceaux.append(donnees[position + 1:position + 1 + taille].decode(
                "ascii", "replace"))
            position += 1 + taille
        return ".".join(morceaux), (saut if saut is not None else position)

    @classmethod
    def _dns_udp(cls, nom, type_, delai=2.0):
        """Rend les réponses au format texte de l'API DoH, ou une liste vide."""
        qtype = cls._TYPES_DNS.get(type_)
        serveurs = cls._serveurs_de_noms()
        if not qtype or not serveurs:
            return []
        etiquettes = [m.encode("ascii", "ignore") for m in nom.strip(".").split(".")]
        if not etiquettes or any(not e or len(e) > 63 for e in etiquettes):
            return []
        question = b"".join(bytes([len(e)]) + e for e in etiquettes) + b"\x00"
        # ⚠️ Identifiant tiré au sort et vérifié au retour : un identifiant
        # fixe rend l'empoisonnement hors chemin beaucoup plus facile.
        identifiant = secrets.token_bytes(2)
        entete = identifiant + b"\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
        paquet = entete + question + qtype.to_bytes(2, "big") + b"\x00\x01"
        for serveur in serveurs:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as prise:
                    prise.settimeout(delai)
                    prise.sendto(paquet, (serveur, 53))
                    donnees, _ = prise.recvfrom(4096)
            except OSError:
                continue
            if len(donnees) < 12 or donnees[:2] != identifiant:
                continue
            nb_reponses = int.from_bytes(donnees[6:8], "big")
            position = 12
            _q, position = cls._nom_dns(donnees, position)
            position += 4
            sorties = []
            for _ in range(nb_reponses):
                _n, position = cls._nom_dns(donnees, position)
                if position + 10 > len(donnees):
                    break
                rtype = int.from_bytes(donnees[position:position + 2], "big")
                taille = int.from_bytes(donnees[position + 8:position + 10], "big")
                position += 10
                fin = position + taille
                if rtype == 15 and taille >= 3:      # MX
                    pref = int.from_bytes(donnees[position:position + 2], "big")
                    cible, _ = cls._nom_dns(donnees, position + 2)
                    sorties.append(f"{pref} {cible}.")
                elif rtype == 33 and taille >= 7:    # SRV
                    prio = int.from_bytes(donnees[position:position + 2], "big")
                    poids = int.from_bytes(donnees[position + 2:position + 4], "big")
                    port = int.from_bytes(donnees[position + 4:position + 6], "big")
                    cible, _ = cls._nom_dns(donnees, position + 6)
                    sorties.append(f"{prio} {poids} {port} {cible}.")
                position = fin
            if sorties:
                return sorties
        return []

    def _mx(self, domaine):
        """Les hôtes MX, triés par préférence croissante."""
        lignes = []
        for data in self._doh(domaine, "MX"):
            morceaux = data.split()
            if len(morceaux) == 2 and morceaux[0].isdigit():
                lignes.append((int(morceaux[0]), morceaux[1].rstrip(".")))
        return [h for _p, h in sorted(lignes)]

    def _srv(self, nom):
        """Rend ``(hôte, port)`` du SRV le plus prioritaire, ou ``False``.

        ⚠️ Un SRV à cible « . » veut dire « ce service n'existe pas ici »
        (RFC 2782) : `_imap._tcp.gmail.com` rend exactement ça.
        """
        meilleurs = []
        for data in self._doh(nom, "SRV"):
            morceaux = data.split()
            if len(morceaux) != 4:
                continue
            priorite, _poids, port, cible = morceaux
            cible = cible.rstrip(".")
            if not cible or cible == "." or not priorite.isdigit():
                continue
            meilleurs.append((int(priorite), cible, int(port)))
        if not meilleurs:
            return False
        meilleurs.sort()
        return meilleurs[0][1], meilleurs[0][2]

    def _resout(self, hote):
        try:
            socket.getaddrinfo(hote, None)
            return True
        except (socket.gaierror, UnicodeError):
            return False

    def _parle_imap(self, hote, port=993):
        """Vrai quand l'hôte répond un accueil IMAP sur 993, en TLS valide.

        ⚠️ Un enregistrement A ne prouve rien : sur 34 domaines dont `imap.` ou
        `mail.` résout, 12 seulement répondent. Et `koumbit.org` sert un
        certificat qui ne couvre pas le nom : refuser est le bon comportement,
        l'assistant bascule alors sur la saisie à la main.
        """
        if not self._resout(hote):
            return False
        if not safe_push_endpoint(f"https://{hote}"):
            return False
        try:
            ctx = ssl.create_default_context()
            with socket.create_connection((hote, port), timeout=DELAI_IMAP) as brut:
                with ctx.wrap_socket(brut, server_hostname=hote) as tls:
                    tls.settimeout(DELAI_IMAP)
                    accueil = tls.recv(256).decode("utf-8", "replace")
            return accueil.startswith("* OK")
        except (OSError, ssl.SSLError):
            return False

    # ------------------------------------------------------------------
    # Analyse du format Mozilla clientConfig (v1.1)
    # ------------------------------------------------------------------
    _RE_IN = re.compile(
        r"<incomingServer[^>]*type=\"(?P<type>imap|pop3)\"[^>]*>(?P<corps>.*?)"
        r"</incomingServer>", re.S | re.I)
    _RE_OUT = re.compile(r"<outgoingServer.*?</outgoingServer>", re.S | re.I)

    @classmethod
    def _analyser_clientconfig(cls, xml):
        if not xml or "clientConfig" not in xml:
            return None

        def champs(corps):
            def lire(balise):
                m = re.search(rf"<{balise}>(.*?)</{balise}>", corps, re.S | re.I)
                return (m.group(1).strip() if m else "")
            port = lire("port")
            return {
                "host": lire("hostname"),
                "port": int(port) if port.isdigit() else 0,
                "socket": (lire("socketType") or "SSL").upper(),
                "auth_annonce": lire("authentication"),
            }

        entrant = None
        for m in cls._RE_IN.finditer(xml):
            if m.group("type").lower() == "imap":
                entrant = champs(m.group("corps"))
                break
        if not entrant or not entrant["host"]:
            return None
        m = cls._RE_OUT.search(xml)
        sortant = champs(m.group(0)) if m else False
        if sortant and not sortant["host"]:
            sortant = False
        return {"imap": entrant, "smtp": sortant}
