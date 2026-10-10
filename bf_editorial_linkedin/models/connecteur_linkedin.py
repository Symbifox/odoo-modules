# -*- coding: utf-8 -*-
"""Le connecteur LinkedIn, sur l'API versionnée.

Trois pièges que ce fichier existe pour éviter :

1. **L'identifiant du billet ne revient pas dans le corps.** LinkedIn le rend
   dans l'en-tête ``x-restli-id``. Un connecteur qui lit ``response.json()``
   trouve un corps vide, conclut à un échec, et republie au prochain passage.

2. **La version d'API se périme.** L'en-tête ``LinkedIn-Version`` est un
   ``AAAAMM`` que LinkedIn retire au bout d'environ un an. Codée en dur, elle
   fait tomber la diffusion un matin sans que rien n'ait changé chez nous. Elle
   vit donc dans un paramètre.

3. **Le jeton dure 60 jours.** Il n'y a pas de rafraîchissement automatique
   hors des programmes approuvés. On ne peut pas éviter l'échéance, on peut
   seulement refuser de la découvrir le jour où elle tombe : la date est
   portée par le canal, et un travail quotidien prévient d'avance.
"""

import json
import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

try:
    import requests
except ImportError:                                     # pragma: no cover
    requests = None

API = "https://api.linkedin.com"
DELAI = 20

# 3 000 caractères pour le commentaire d'une publication.
LIMITE_TEXTE = 3000

# Une version connue pour fonctionner au moment d'écrire. Elle SE PÉRIME :
# le paramètre existe pour qu'on la bouge sans redéployer. 202608 est retirée
# le 17 août 2027 — table « API Migration Status » de la documentation LinkedIn.
VERSION_PAR_DEFAUT = "202608"
CLE_VERSION = "bf_editorial_linkedin.api_version"

# Combien de jours d'avance sur l'expiration du jeton.
PREAVIS_JOURS = 7


class LinkedInConnector(models.AbstractModel):
    _name = "bf.social.connector.linkedin"
    _inherit = "bf.social.connector"
    _description = "LinkedIn connector"

    _network_label = "LinkedIn"

    def _limits(self):
        return {"body_chars": LIMITE_TEXTE, "posts_per_hour": None}

    def _link_in_body(self):
        """Faux : LinkedIn fabrique une carte à partir de l'article joint.

        Écrire l'URL dans le texte donnerait les deux, le lien nu ET la carte.
        """
        return False

    # --- plomberie --------------------------------------------------------
    def _version(self):
        return (self.env["ir.config_parameter"].sudo().get_param(
            CLE_VERSION, VERSION_PAR_DEFAUT,
        ) or VERSION_PAR_DEFAUT).strip()

    def _entetes(self, channel, avec_version=True):
        jeton = channel._decrypt_secret()
        if not jeton:
            raise UserError(_(
                "Channel \"%s\": no access token. Paste the one the "
                "LinkedIn app issued.", channel.name,
            ))
        entetes = {
            "Authorization": "Bearer %s" % jeton,
            "X-Restli-Protocol-Version": "2.0.0",
            "Content-Type": "application/json",
        }
        if avec_version:
            entetes["LinkedIn-Version"] = self._version()
        return entetes

    def _appel(self, methode, chemin, channel, avec_version=True, **kwargs):
        """Un seul point de sortie vers le réseau.

        Tout passe par ici : c'est ce qui permet de mettre le connecteur à
        l'épreuve sans application LinkedIn, en ne remplaçant qu'une méthode.
        """
        if not requests:
            raise UserError(_("The \"requests\" library is not installed."))
        return requests.request(
            methode, "%s%s" % (API, chemin),
            headers=self._entetes(channel, avec_version=avec_version),
            timeout=DELAI, **kwargs
        )

    # --- identifiants -----------------------------------------------------
    def _validate_credentials(self, channel):
        try:
            r = self._appel("GET", "/v2/userinfo", channel, avec_version=False)
        except UserError as exc:
            return False, str(exc)
        except Exception as exc:                        # noqa: BLE001
            return False, _("Network error: %s", str(exc)[:180])

        if r.status_code == 401:
            return False, _(
                "LinkedIn rejects the token (401). A token lasts 60 days: "
                "this one has probably expired or been revoked."
            )
        if r.status_code == 403:
            return False, _(
                "LinkedIn accepts the token but refuses the scope (403). "
                "The app is missing \"openid\" and \"profile\"."
            )
        if r.status_code != 200:
            return False, _(
                "LinkedIn returned HTTP %(code)s: %(corps)s",
                code=r.status_code, corps=(r.text or "")[:200],
            )

        donnees = r.json() or {}
        sujet = donnees.get("sub")
        if not sujet:
            return False, _(
                "The response carries no member identifier. The "
                "\"openid\" scope is probably missing."
            )
        urn = "urn:li:person:%s" % sujet
        if channel.linkedin_member_urn != urn:
            channel.sudo().write({"linkedin_member_urn": urn})

        message = _("Valid token for %(nom)s (%(urn)s).",
                    nom=donnees.get("name") or "?", urn=urn)
        reste = channel._linkedin_days_left()
        if reste is not None:
            message = "%s %s" % (message, _(
                "Declared expiry in %s day(s).", reste,
            ))
        return True, message

    # --- diffusion --------------------------------------------------------
    def _corps_publication(self, post):
        canal = post.channel_id
        contenu = {
            "author": canal.linkedin_member_urn,
            "commentary": post.body or "",
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
        if post.link_url:
            entree = post.entry_id
            titre = entree.name or ""
            description = ""
            if entree.post_id:
                billet = entree.post_id.with_context(
                    lang=post.lang_id.code or "en_CA")
                titre = billet.name or titre
                description = (
                    billet.website_meta_description or billet.teaser or ""
                )
            contenu["content"] = {"article": {
                "source": post.link_url,
                "title": titre[:400],
                "description": description[:4086],
            }}
        return contenu

    def _publish(self, post):
        canal = post.channel_id
        if not canal.linkedin_member_urn:
            # Résoudre plutôt que refuser : la vérification des identifiants
            # le pose, mais personne ne devrait avoir à la lancer d'abord.
            ok, message = self._validate_credentials(canal)
            if not ok:
                raise UserError(_("LinkedIn: %s", message))
        texte = post.body or ""
        if len(texte) > LIMITE_TEXTE:
            raise UserError(_(
                "The text is %(n)s characters long; the limit is %(l)s.",
                n=len(texte), l=LIMITE_TEXTE,
            ))

        reponse = self._appel(
            "POST", "/rest/posts", canal,
            data=json.dumps(self._corps_publication(post)),
        )
        if reponse.status_code not in (200, 201):
            raise UserError(_(
                "LinkedIn rejects the post (HTTP %(code)s): %(corps)s",
                code=reponse.status_code, corps=(reponse.text or "")[:300],
            ))

        # ⚠️ L'identifiant vit dans l'en-tête, pas dans le corps. Le corps
        # d'une création réussie est vide.
        urn = (reponse.headers or {}).get("x-restli-id")
        if not urn:
            raise UserError(_(
                "LinkedIn accepted the post (HTTP %(code)s) without "
                "returning its identifier in the \"x-restli-id\" header. "
                "The post is probably online: check the feed before "
                "retrying, or you will publish it twice.",
                code=reponse.status_code,
            ))
        return {
            "remote_id": urn,
            "url": "https://www.linkedin.com/feed/update/%s/" % urn,
        }

    # --- mesures ----------------------------------------------------------
    def _fetch_metrics(self, post):
        """LinkedIn n'expose aucune mesure pour une publication de MEMBRE.

        Les statistiques demandent une page d'organisation et les portées qui
        vont avec. Rendre zéro serait un mensonge : le cadre lit un
        dictionnaire vide comme « ce réseau ne les donne pas ».
        """
        return {}
