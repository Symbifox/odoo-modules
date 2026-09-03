# -*- coding: utf-8 -*-
"""Inscription publique à une liste d'envoi, sans tiers et à double consentement.

Deux routes, et une seule idée : une page STATIQUE, qui n'a ni session Odoo ni
jeton CSRF, doit pouvoir inscrire quelqu'un sans qu'on puisse inscrire n'importe
qui à sa place. Le double consentement est ce qui remplace le jeton — le premier
appel n'écrit qu'une inscription désactivée et n'envoie qu'un courriel, et seule
la personne qui relève l'adresse peut la confirmer.
"""

import hmac
import html
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
ICP_BASE_URL = "bf_mailing_signup.base_url"

# L'habillage du courriel. Mêmes raisons : la marque, son sigle et le logo de
# la société se règlent sans toucher au code, et un locataire qui n'en pose
# aucun reçoit un courriel sobre plutôt qu'un courriel cassé.
ICP_BRAND = "bf_mailing_signup.brand_name"
ICP_MARK = "bf_mailing_signup.brand_mark_url"
ICP_LOGO = "bf_mailing_signup.brand_logo_url"

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



# ---------------------------------------------------------------- habillage

# Palette relevée sur symbifox.com/styles.css, la même que celle des gabarits
# d'infolettre : la confirmation est le PREMIER courriel que la personne
# reçoit, et il doit ressembler à ceux qui suivront.
#
# ⚠️ Le cadre EXTÉRIEUR est CLAIR. Un cadre sombre a plus d'allure à l'écran,
# mais plusieurs clients jettent le `background-color` de la carte intérieure :
# le texte foncé se retrouve alors sur le fond foncé du cadre, et le message
# devient illisible. Les seuls aplats sombres sont posés en attribut `bgcolor`,
# que personne ne retire.
BLEU, MARINE, ENCRE = "#176CF2", "#071B4A", "#10213A"
GRIS, TRAIT, DOUX, GLACE, PAPIER = "#64748B", "#DCE6F3", "#EDF4FF", "#F6F9FF", "#FFFFFF"
CIEL = "#8EBBFA"

# Lexend ne se charge pas dans un client courriel : la pile de repli est
# nommée, sinon chaque plateforme choisit la sienne.
POLICE = ("Lexend,'Segoe UI',-apple-system,BlinkMacSystemFont,"
          "'Helvetica Neue',Arial,sans-serif")

# Le texte, au complet, dans les deux langues. Le sujet et les trois phrases du
# corps sont ceux d'avant l'habillage, mot pour mot : ils ont été écrits pour
# être lus par quelqu'un qui vient de remplir un formulaire, et l'habillage
# n'avait pas à les rouvrir.
TEXTES = {
    "fr": {
        "sujet": "Confirmez votre inscription à {marque}",
        "entete": "Un dernier geste, et c'est fait. Le lien expire dans sept jours.",
        "titre": "Confirmez votre inscription",
        "intro": "Quelqu'un — nous espérons que c'est vous — a demandé à recevoir "
                 "les nouveautés {marque}.",
        "bouton": "Confirmer mon inscription",
        "repli": "Si le bouton ne s'affiche pas, copiez ce lien dans votre navigateur :",
        "note": "Si ce n'est pas vous, ignorez ce message. Rien n'est envoyé à une "
                "adresse qui n'a pas confirmé, et ce lien expire dans sept jours.",
        "par": "par {societe}",
        "pourquoi": "Vous recevez ce message parce que cette adresse a été saisie sur "
                    "{site}. Rien d'autre ne partira tant qu'elle n'aura pas confirmé.",
    },
    "en": {
        "sujet": "Confirm your {marque} subscription",
        "entete": "One last step, and you are on the list. The link expires in seven days.",
        "titre": "Confirm your subscription",
        "intro": "Someone — we hope you — asked to receive the {marque} updates.",
        "bouton": "Confirm my subscription",
        "repli": "If the button does not show, copy this link into your browser:",
        "note": "If it was not you, ignore this message. Nothing is sent to an address "
                "that has not confirmed, and this link expires in seven days.",
        "par": "by {societe}",
        "pourquoi": "You are receiving this message because this address was entered on "
                    "{site}. Nothing else will be sent until it is confirmed.",
    },
}


def _ech(valeur):
    """Échappe du TEXTE. `quote=False` : dans un noeud texte, l'apostrophe n'a
    rien à protéger, et la changer en `&#x27;` rend le source illisible pour qui
    relit le courriel envoyé."""
    return html.escape(valeur or "", quote=False)


def _att(valeur):
    """Échappe une valeur d'ATTRIBUT — les guillemets y comptent, eux."""
    return html.escape(valeur or "", quote=True)


def _site(env):
    """Le nom d'hôte du site qui porte le formulaire, pour le pied de page."""
    icp = env["ir.config_parameter"].sudo()
    base = icp.get_param(ICP_BASE_URL) or icp.get_param("web.base.url") or ""
    return base.split("//")[-1].strip("/")


def _marque(env):
    """Ce que l'habillage doit savoir de la marque, pris aux paramètres système.

    ⚠️ Le sigle et le logo sont des URL absolues SANS valeur par défaut. Un
    module publié qui pointerait sur nos images à nous ferait relever les
    ouvertures des abonnés de qui l'installe par notre serveur à nous. Non
    réglés, l'en-tête garde son libellé et perd la vignette : rien ne casse.
    """
    icp = env["ir.config_parameter"].sudo()
    # ⚠️ `sudo()` n'est pas de la commodité. Ce code tourne pour l'usager
    # PUBLIC : il n'a le droit de lire ni la fiche partenaire de la société ni
    # le `res.country` dont dépend le format d'adresse. Sans ça, la mise en
    # forme lève, `signup` attrape, et la personne repart avec la page de
    # remerciement SANS avoir reçu de courriel — l'inscription devient
    # impossible, en silence.
    societe = env.company.sudo()
    adresse = societe.partner_id._display_address(without_company=True) or ""
    return {
        "marque": icp.get_param(ICP_BRAND) or societe.name,
        "societe": societe.name,
        "sigle": icp.get_param(ICP_MARK) or "",
        "logo": icp.get_param(ICP_LOGO) or "",
        # La LCAP veut une adresse postale dans un message qui demande un
        # consentement. `_display_address` la met au format du pays plutôt
        # qu'au nôtre, ce qui évite de bricoler province et code postal.
        "postale": ", ".join(l.strip() for l in adresse.splitlines() if l.strip()),
    }


def _habiller(env, url, lang):
    """Sujet et corps HTML du courriel de confirmation.

    Tout en tableaux `role="presentation"` et en style EN LIGNE : les clients
    courriel jettent les feuilles de style, ignorent `max-width` sur un div et
    ne connaissent ni flex ni grid. Les fonds sont explicites partout, sinon le
    mode sombre d'iOS et d'Outlook recolore le texte sans recolorer son fond.
    """
    b = _marque(env)
    t = {k: v.format(marque=b["marque"], societe=b["societe"], site=_site(env))
         for k, v in TEXTES[lang].items()}

    marque, societe = _att(b["marque"]), _att(b["societe"])
    lien_href = _att(url)
    lien = _ech(url)
    sujet = t["sujet"]

    vignette = ""
    if b["sigle"]:
        vignette = (
            f'<td width="40" style="padding-right:13px" valign="middle">'
            f'<img src="{_att(b["sigle"])}" alt="{marque}" width="40" height="40" '
            f'style="display:block;width:40px;height:40px;border:0;border-radius:9px"></td>')
    # « par Blue Fox » sous « Symbifox » situe le produit. Quand la marque EST
    # la société — un locataire qui n'a pas posé `brand_name` —, la ligne se
    # contente de répéter le titre : on la retire.
    signature = ""
    if b["marque"] != b["societe"]:
        signature = (
            f'<p style="margin:2px 0 0;font-family:{POLICE};font-size:12.5px;'
            f'color:{CIEL};line-height:1.2">{_ech(t["par"])}</p>')

    logo = ""
    if b["logo"]:
        logo = (
            f'<td width="60" valign="top" style="padding-right:16px">'
            f'<img src="{_att(b["logo"])}" alt="{societe}" width="60" height="60" '
            f'style="display:block;width:60px;height:60px;border:0"></td>')

    corps = f"""<!doctype html>
<html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<title>{_ech(sujet)}</title></head>
<body style="margin:0;padding:0;background-color:{GLACE}">
<div style="display:none;font-size:0;line-height:0;max-height:0;overflow:hidden;opacity:0">
{_ech(t["entete"])}</div>
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
       bgcolor="{GLACE}" style="background-color:{GLACE};margin:0;padding:0">
<tr><td align="center" style="padding:28px 12px">

<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600"
       style="width:100%;max-width:600px;background-color:{PAPIER};border-radius:16px;
              overflow:hidden;border:1px solid {TRAIT}">

  <tr><td bgcolor="{MARINE}" style="padding:24px 34px">
    <table role="presentation" cellpadding="0" cellspacing="0" border="0">
    <tr>{vignette}
      <td valign="middle">
        <p style="margin:0;font-family:{POLICE};font-size:21px;font-weight:600;
                  letter-spacing:-.01em;color:{PAPIER};line-height:1.15">{marque}</p>
        {signature}</td>
    </tr></table>
  </td></tr>

  <tr><td style="padding:34px 34px 30px">
    <h1 style="margin:0 0 18px;font-family:{POLICE};font-size:25px;line-height:1.3;
               font-weight:600;letter-spacing:-.015em;color:{ENCRE}">{_ech(t["titre"])}</h1>
    <p style="margin:0 0 16px;font-family:{POLICE};font-size:16px;line-height:1.65;
              color:{ENCRE}">{_ech(t["intro"])}</p>

    <table role="presentation" cellpadding="0" cellspacing="0" border="0"
           style="margin:28px 0 6px">
    <tr><td bgcolor="{BLEU}" style="border-radius:8px">
      <a href="{lien_href}" style="display:inline-block;padding:13px 26px;font-family:{POLICE};
         font-size:15px;font-weight:600;color:{PAPIER};text-decoration:none;
         border-radius:8px">{_ech(t["bouton"])}</a></td></tr></table>

    <p style="margin:26px 0 6px;font-family:{POLICE};font-size:13.5px;line-height:1.6;
              color:{GRIS}">{_ech(t["repli"])}</p>
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
    <tr><td bgcolor="{DOUX}" style="padding:12px 14px;border-radius:8px">
      <a href="{lien_href}" style="font-family:{POLICE};font-size:12.5px;line-height:1.5;
         color:{BLEU};text-decoration:none;word-break:break-all">{lien}</a>
    </td></tr></table>

    <p style="margin:26px 0 0;font-family:{POLICE};font-size:14.5px;line-height:1.65;
              color:{GRIS}">{_ech(t["note"])}</p>
  </td></tr>

  <tr><td bgcolor="{GLACE}" style="padding:22px 34px;border-top:1px solid {TRAIT}">
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
    <tr>{logo}
      <td valign="top">
        <p style="margin:0 0 7px;font-family:{POLICE};font-size:12.5px;line-height:1.6;
                  color:{GRIS}">{societe}<br>{_ech(b["postale"])}</p>
        <p style="margin:0;font-family:{POLICE};font-size:12.5px;line-height:1.6;
                  color:{GRIS}">{_ech(t["pourquoi"])}</p></td>
    </tr></table>
  </td></tr>

</table>
</td></tr></table>
</body></html>"""
    return sujet, corps


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
        la confirmation en pourriel. ⚠️ Habiller le courriel aux couleurs de la
        marque donne très envie d'y mettre une adresse assortie : c'est
        précisément ce qu'il ne faut pas faire. Le `reply_to`, lui, porte déjà
        l'adresse de la marque, et c'est le bon endroit pour ça.
        """
        icp = env["ir.config_parameter"].sudo()
        base = (icp.get_param(ICP_BASE_URL) or "https://symbifox.com").rstrip("/")
        token = _token(env, list_id, email, date.today().toordinal())
        url = (f"{base}/infolettre/confirmer?e={quote(email)}"
               f"&j={token}&lang={lang}")
        reply_to = icp.get_param(
            ICP_REPLY_TO_EN if lang == "en" else ICP_REPLY_TO_FR) or ""
        subject, body = _habiller(env, url, lang)
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
