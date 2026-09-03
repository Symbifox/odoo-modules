# -*- coding: utf-8 -*-
"""Inscription publique à une liste d'envoi, sans tiers et à double consentement.

Deux routes, et une seule idée : une page STATIQUE, qui n'a ni session Odoo ni
jeton CSRF, doit pouvoir inscrire quelqu'un sans qu'on puisse inscrire n'importe
qui à sa place. Le double consentement est ce qui remplace le jeton — le premier
appel n'écrit qu'une inscription désactivée et n'envoie qu'un courriel, et seule
la personne qui relève l'adresse peut la confirmer.
"""

import hmac
import logging
import re
import threading
import time
from collections import defaultdict
from datetime import date
from hashlib import sha256

import werkzeug.utils
from urllib.parse import quote

from odoo import fields, http
from odoo.http import request

_logger = logging.getLogger(__name__)

# La liste visée. Paramètre système plutôt que constante : le même module sert
# un autre locataire avec une autre liste, sans toucher au code.
ICP_LIST = "bf_mailing_signup.list_id"
ICP_REPLY_TO_FR = "bf_mailing_signup.reply_to_fr"
ICP_REPLY_TO_EN = "bf_mailing_signup.reply_to_en"

# Où retomber après un envoi. Ce sont des pages du site appelant, pas des vues
# d'ici : le module n'ajoute aucune vue, c'est tout son intérêt.
PAGES = {
    "fr": {"merci": "/infolettre-merci.html", "confirme": "/infolettre-confirme.html",
           "retour": "/#infolettre"},
    "en": {"merci": "/en/infolettre-merci.html", "confirme": "/en/infolettre-confirme.html",
           "retour": "/en/#infolettre"},
}

# Fenêtre de validité du lien de confirmation, en jours. Obtenue en réessayant
# les N derniers quantièmes plutôt qu'en stockant une date : rien à purger.
CONFIRM_DAYS = 7

# Seaux de limitation. L'inscription est serrée parce que chaque appel peut
# faire partir un courriel ; la confirmation l'est moins, parce qu'un lien
# rechargé deux fois est normal.
_SIGNUP_MAX, _SIGNUP_WINDOW = 5, 3600
_CONFIRM_MAX, _CONFIRM_WINDOW = 30, 3600

# Assez pour refuser une adresse manifestement fausse, sans prétendre valider
# une adresse courriel par expression régulière — seul l'envoi le prouve.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")
_EMAIL_MAX = 254  # RFC 5321

_bucket_lock = threading.Lock()
_bucket_data = defaultdict(list)
# Un flot venant d'adresses toutes différentes fait grossir ce dictionnaire une
# fois par adresse. Plafond franc, purge totale : perdre l'historique d'un seau
# est sans gravité, laisser la mémoire enfler ne l'est pas.
_MAX_TRACKED_IPS = 10000


def _client_ip():
    """L'adresse du client, pour la limitation de débit.

    ⚠️ `proxy_mode = True` est posé sur ce déploiement : werkzeug a déjà réécrit
    `remote_addr` avec l'adresse réelle, en ne faisant confiance qu'au nombre de
    sauts déclaré. Ne JAMAIS lire `X-Forwarded-For` ni `X-Real-IP` nous-mêmes —
    un client qui atteindrait le point d'entrée directement les choisit, et se
    donnerait un seau neuf à chaque requête. Même règle que `bf_appointment` et
    `bf_meeting`.
    """
    try:
        return request.httprequest.remote_addr or "inconnu"
    except Exception:
        return "inconnu"


def _rate_ok(bucket, max_hits, window):
    """Rend True si l'appel passe, et le compte."""
    ident = (bucket, _client_ip())
    now = time.monotonic()
    with _bucket_lock:
        if len(_bucket_data) > _MAX_TRACKED_IPS:
            _bucket_data.clear()
        cutoff = now - window
        hits = [t for t in _bucket_data[ident] if t > cutoff]
        if len(hits) >= max_hits:
            _bucket_data[ident] = hits
            return False
        hits.append(now)
        _bucket_data[ident] = hits
    return True


def _secret(env):
    return (env["ir.config_parameter"].sudo().get_param("database.secret") or "").encode()


def _token(env, list_id, email, day):
    payload = f"{list_id}:{email.strip().lower()}:{day}".encode()
    return hmac.new(_secret(env), payload, sha256).hexdigest()[:32]


def _token_valid(env, list_id, email, token):
    """Vrai si le jeton vaut pour l'un des CONFIRM_DAYS derniers quantièmes.

    ⚠️ `hmac.compare_digest` et non `==` : une comparaison qui s'arrête au
    premier octet différent laisse mesurer le bon jeton octet par octet.
    """
    today = date.today().toordinal()
    return any(hmac.compare_digest(_token(env, list_id, email, today - d), token or "")
               for d in range(CONFIRM_DAYS))


def _lang(raw):
    return "en" if (raw or "").strip().lower().startswith("en") else "fr"


def _list_id(env):
    raw = env["ir.config_parameter"].sudo().get_param(ICP_LIST)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


class BfMailingSignup(http.Controller):

    # ⚠️ `csrf=False` est obligatoire, pas commode : la page appelante est un
    # fichier statique servi par le proxy, sans session Odoo, donc aucun jeton
    # ne peut y être posé. Ce que ça ouvre est refermé par le double
    # consentement — un envoi non sollicité n'inscrit personne, il fait partir
    # un seul courriel vers l'adresse saisie — plus le pot de miel et le seau.
    @http.route("/infolettre", type="http", auth="public", methods=["POST"],
                csrf=False, website=False, sitemap=False)
    def signup(self, **post):
        lang = _lang(post.get("lang"))
        pages = PAGES[lang]
        # Réponse UNIQUE quoi qu'il arrive. Dire « déjà inscrit », « adresse
        # refusée » ou « trop de tentatives » donne à qui sonde le service
        # exactement ce qu'il cherche : savoir si une adresse est sur la liste.
        merci = werkzeug.utils.redirect(pages["merci"], 303)

        # Le pot de miel : un champ que la feuille de style sort de l'écran,
        # qu'aucun lecteur d'écran n'annonce et qu'aucune tabulation n'atteint.
        # Rempli, c'est un robot. On rend la page de remerciement quand même :
        # un refus visible lui apprendrait où est le champ.
        if (post.get("site_web") or "").strip():
            return merci
        if not _rate_ok("signup", _SIGNUP_MAX, _SIGNUP_WINDOW):
            return merci

        email = (post.get("courriel") or "").strip().lower()
        if not email or len(email) > _EMAIL_MAX or not _EMAIL_RE.match(email):
            return merci

        env = request.env
        list_id = _list_id(env)
        if not list_id or not env["mailing.list"].sudo().browse(list_id).exists():
            _logger.warning("bf_mailing_signup : %s ne pointe sur aucune liste", ICP_LIST)
            return merci

        try:
            self._ensure_pending(env, list_id, email)
            self._send_confirmation(env, list_id, email, lang)
        except Exception:               # noqa: BLE001 — la page ne doit jamais 500
            _logger.exception("bf_mailing_signup : échec d'inscription")
        return merci

    @http.route("/infolettre/confirmer", type="http", auth="public", methods=["GET"],
                csrf=False, website=False, sitemap=False)
    def confirm(self, e=None, j=None, lang=None, **kw):
        lang = _lang(lang)
        pages = PAGES[lang]
        email = (e or "").strip().lower()
        env = request.env
        list_id = _list_id(env)
        if (not _rate_ok("confirm", _CONFIRM_MAX, _CONFIRM_WINDOW)
                or not email or not list_id
                or not _token_valid(env, list_id, email, j)):
            # Lien périmé, tronqué ou fabriqué : on renvoie au formulaire, qui
            # est la seule chose utile à faire ensuite.
            return werkzeug.utils.redirect(pages["retour"], 303)
        try:
            self._activate(env, list_id, email)
        except Exception:               # noqa: BLE001
            _logger.exception("bf_mailing_signup : échec de confirmation")
        return werkzeug.utils.redirect(pages["confirme"], 303)

    # ------------------------------------------------------------------ interne

    def _ensure_pending(self, env, list_id, email):
        """Crée le contact et son inscription DÉSACTIVÉE, sans rien réactiver.

        ⚠️ Une adresse qui s'est désinscrite ne doit pas se réinscrire par une
        simple resoumission du formulaire : seul le lien de confirmation, qui ne
        parvient qu'à elle, remet `opt_out` à faux. C'est aussi ce qui empêche
        un tiers de la remettre sur la liste.
        """
        Contact = env["mailing.contact"].sudo()
        contact = Contact.search([("email_normalized", "=", email)], limit=1)
        if not contact:
            contact = Contact.create({"email": email})
        sub = env["mailing.subscription"].sudo().search(
            [("contact_id", "=", contact.id), ("list_id", "=", list_id)], limit=1)
        if not sub:
            env["mailing.subscription"].sudo().create({
                "contact_id": contact.id, "list_id": list_id, "opt_out": True})
        return contact

    def _send_confirmation(self, env, list_id, email, lang):
        """Un seul courriel, celui qui porte le lien. Jamais de contenu marketing.

        `email_from` n'est pas posé : `mail.mail` retombe sur l'adresse d'envoi
        par défaut de l'instance, qui est celle dont le domaine est aligné en
        SPF et DKIM. En poser une ici serait le meilleur moyen de faire classer
        la confirmation en pourriel.
        """
        icp = env["ir.config_parameter"].sudo()
        base = (icp.get_param("bf_mailing_signup.base_url") or "https://symbifox.com").rstrip("/")
        token = _token(env, list_id, email, date.today().toordinal())
        url = (f"{base}/infolettre/confirmer?e={quote(email)}"
               f"&j={token}&lang={lang}")
        reply_to = icp.get_param(
            ICP_REPLY_TO_EN if lang == "en" else ICP_REPLY_TO_FR) or ""
        if lang == "en":
            subject = "Confirm your Symbifox subscription"
            body = (
                "<p>Someone — we hope you — asked to receive the Symbifox updates.</p>"
                f'<p><a href="{url}">Confirm my subscription</a></p>'
                "<p>If it was not you, ignore this message. Nothing is sent to an "
                "address that has not confirmed, and this link expires in seven days.</p>")
        else:
            subject = "Confirmez votre inscription à Symbifox"
            body = (
                "<p>Quelqu'un — nous espérons que c'est vous — a demandé à recevoir "
                "les nouveautés Symbifox.</p>"
                f'<p><a href="{url}">Confirmer mon inscription</a></p>'
                "<p>Si ce n'est pas vous, ignorez ce message. Rien n'est envoyé à une "
                "adresse qui n'a pas confirmé, et ce lien expire dans sept jours.</p>")
        mail = env["mail.mail"].sudo().create({
            "subject": subject,
            "body_html": body,
            "email_to": email,
            "reply_to": reply_to or False,
            "auto_delete": True,
        })
        # Envoi immédiat, mais un SMTP lent ou fâché ne doit pas faire attendre
        # le visiteur ni lui rendre une erreur : en cas d'échec le message reste
        # en file et le cron d'Odoo reprend.
        try:
            mail.send(raise_exception=False)
        except Exception:               # noqa: BLE001
            _logger.exception("bf_mailing_signup : envoi différé de la confirmation")

    def _activate(self, env, list_id, email):
        """Lève l'`opt_out` et date le consentement dans le fil du contact."""
        contact = env["mailing.contact"].sudo().search(
            [("email_normalized", "=", email)], limit=1)
        if not contact:
            contact = env["mailing.contact"].sudo().create({"email": email})
        sub = env["mailing.subscription"].sudo().search(
            [("contact_id", "=", contact.id), ("list_id", "=", list_id)], limit=1)
        if sub:
            sub.write({"opt_out": False})
        else:
            env["mailing.subscription"].sudo().create({
                "contact_id": contact.id, "list_id": list_id, "opt_out": False})
        # La preuve du consentement exprès, datée, là où quelqu'un la cherchera.
        contact.message_post(body=(
            "Consentement exprès confirmé par lien courriel le "
            f"{fields.Datetime.now()} UTC "
            "(inscription publique symbifox.com, double consentement)."))
