# -*- coding: utf-8 -*-
"""Le connecteur Bluesky, sur le protocole AT.

Deux pièges que ce fichier existe pour éviter :

1. **Les positions sont en OCTETS UTF-8**, pas en caractères. Un billet
   français contient des accents ; compter en caractères décale chaque lien
   et chaque mot-clic de la ligne. C'est la source d'erreur classique.

2. **La session expire.** Le jeton d'accès dure environ deux heures. Un
   travail périodique qui garde le même jeton toute la journée échoue en
   silence à partir de la troisième heure : on ouvre donc une session par
   opération plutôt que d'en mettre une en cache.
"""

import json
import logging
import re

from odoo import _, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

PDS = "https://bsky.social"
API = "https://public.api.bsky.app"
DELAI = 20

# Le réseau compte en graphèmes ; on reste prudent avec la même valeur.
LIMITE_TEXTE = 300

_RE_URL = re.compile(r"https?://[^\s<>\"]+[^\s<>\".,;:!?)\]]", re.U)
_RE_TAG = re.compile(r"(?<![\w#])#([A-Za-zÀ-ÿ0-9_]{1,64})", re.U)


class BlueskyConnector(models.AbstractModel):
    _name = "bf.social.connector.bluesky"
    _inherit = "bf.social.connector"
    _description = "Connecteur Bluesky"

    _network_label = "Bluesky"

    def _limits(self):
        return {"body_chars": LIMITE_TEXTE, "posts_per_hour": 1666}

    # --- session ----------------------------------------------------------
    def _session(self, channel):
        """Ouvrir une session. Jamais mise en cache : le jeton expire vite."""
        if not requests:
            raise UserError(_("La bibliothèque « requests » n'est pas installée."))
        secret = channel._decrypt_secret()
        if not (channel.login and secret):
            raise UserError(_(
                "Canal « %s » : identifiant ou mot de passe d'application manquant.",
                channel.name,
            ))
        r = requests.post(
            f"{PDS}/xrpc/com.atproto.server.createSession",
            json={"identifier": channel.login, "password": secret},
            timeout=DELAI,
        )
        if r.status_code != 200:
            raise UserError(_(
                "Bluesky refuse la session (HTTP %(code)s) : %(corps)s",
                code=r.status_code, corps=r.text[:200],
            ))
        return r.json()

    def _validate_credentials(self, channel):
        try:
            s = self._session(channel)
        except UserError as exc:
            return False, str(exc)
        except Exception as exc:            # noqa: BLE001
            return False, _("Erreur réseau : %s", str(exc)[:180])
        pseudo = s.get("handle") or ""
        if channel.handle and pseudo.lower() != channel.handle.lower():
            return False, _(
                "La session ouvre sur « %(reel)s » alors que le canal déclare"
                " « %(attendu)s ».", reel=pseudo, attendu=channel.handle)
        return True, _("Session ouverte sur @%s.", pseudo)

    # --- balisage ---------------------------------------------------------
    def _facets(self, texte):
        """Positions des liens et mots-clics, EN OCTETS UTF-8.

        Compter en caractères est le piège : « Récupérez » fait 10 caractères
        et 11 octets, et tout ce qui suit se décale d'un cran.
        """
        octets = texte.encode("utf-8")
        facettes = []

        def bornes(m):
            debut = len(texte[:m.start()].encode("utf-8"))
            return debut, debut + len(m.group(0).encode("utf-8"))

        for m in _RE_URL.finditer(texte):
            d, f = bornes(m)
            facettes.append({
                "index": {"byteStart": d, "byteEnd": f},
                "features": [{"$type": "app.bsky.richtext.facet#link",
                              "uri": m.group(0)}],
            })
        for m in _RE_TAG.finditer(texte):
            d, f = bornes(m)
            facettes.append({
                "index": {"byteStart": d, "byteEnd": f},
                "features": [{"$type": "app.bsky.richtext.facet#tag",
                              "tag": m.group(1)}],
            })
        assert all(0 <= x["index"]["byteStart"] < x["index"]["byteEnd"] <= len(octets)
                   for x in facettes), "positions hors bornes"
        return facettes

    def _embed_card(self, post):
        """La carte de lien. Sans elle, l'URL s'affiche nue dans le fil."""
        if not post.link_url:
            return None
        entree = post.entry_id
        titre = (entree.name or "")[:300]
        desc = ""
        if entree.post_id:
            billet = entree.post_id.with_context(lang=post.lang_id.code or "en_CA")
            titre = (billet.name or titre)[:300]
            desc = (billet.website_meta_description or billet.teaser or "")[:1000]
        return {
            "$type": "app.bsky.embed.external",
            "external": {"uri": post.link_url, "title": titre, "description": desc},
        }

    # --- diffusion --------------------------------------------------------
    def _publish(self, post):
        from datetime import datetime, timezone
        canal = post.channel_id
        session = self._session(canal)
        texte = post.body or ""
        if len(texte) > LIMITE_TEXTE:
            raise UserError(_(
                "Texte de %(n)s caractères pour une limite de %(l)s.",
                n=len(texte), l=LIMITE_TEXTE))

        record = {
            "$type": "app.bsky.feed.post",
            "text": texte,
            "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "langs": [(post.lang_id.code or "en").split("_")[0]],
        }
        facettes = self._facets(texte)
        if facettes:
            record["facets"] = facettes
        carte = self._embed_card(post)
        if carte:
            record["embed"] = carte

        r = requests.post(
            f"{PDS}/xrpc/com.atproto.repo.createRecord",
            headers={"Authorization": "Bearer %s" % session["accessJwt"]},
            json={"repo": session["did"], "collection": "app.bsky.feed.post",
                  "record": record},
            timeout=DELAI,
        )
        if r.status_code != 200:
            raise UserError(_(
                "Bluesky refuse le billet (HTTP %(code)s) : %(corps)s",
                code=r.status_code, corps=r.text[:300]))
        d = r.json()
        uri = d.get("uri", "")
        rkey = uri.rsplit("/", 1)[-1] if uri else ""
        return {
            "remote_id": uri,
            "url": "https://bsky.app/profile/%s/post/%s" % (
                session.get("handle") or session["did"], rkey),
        }

    # --- mesures ----------------------------------------------------------
    def _fetch_metrics(self, post):
        """Bluesky ne publie PAS d'affichages : la clé reste absente."""
        if not (requests and post.remote_id):
            return {}
        r = requests.get(
            f"{API}/xrpc/app.bsky.feed.getPosts",
            params={"uris": post.remote_id}, timeout=DELAI,
        )
        if r.status_code != 200:
            _logger.warning("Bluesky : mesures indisponibles (HTTP %s)", r.status_code)
            return {}
        billets = r.json().get("posts") or []
        if not billets:
            return {}
        b = billets[0]
        return {
            "likes": b.get("likeCount") or 0,
            "reposts": (b.get("repostCount") or 0) + (b.get("quoteCount") or 0),
            "replies": b.get("replyCount") or 0,
        }
