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
from datetime import date, timedelta
from hashlib import sha256

import psycopg2.errors

import werkzeug.utils
from urllib.parse import quote

from odoo import SUPERUSER_ID, api, fields, http
from odoo.http import request
from odoo.service.model import PG_CONCURRENCY_EXCEPTIONS_TO_RETRY
from odoo.tools import config, mute_logger

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
# Le site de la SOCIÉTÉ (pas celui du produit), en lien dans le pied, à côté de
# son nom. Même règle que les images : aucune valeur par défaut.
ICP_COMPANY_URL = "bf_mailing_signup.company_url"

# Où retomber après un envoi. Ce sont des pages du site appelant, pas des vues
# d'ici : le module n'ajoute aucune vue, c'est tout son intérêt.
PAGES = {
    "fr": {"merci": "/infolettre-merci.html", "confirme": "/infolettre-confirme.html",
           "occupe": "/infolettre-occupe.html", "retour": "/#infolettre"},
    "en": {"merci": "/en/infolettre-merci.html", "confirme": "/en/infolettre-confirme.html",
           "occupe": "/en/infolettre-occupe.html", "retour": "/en/#infolettre"},
}

# Fenêtre de validité du lien de confirmation, en jours. Obtenue en réessayant
# les N derniers quantièmes plutôt qu'en stockant une date : rien à purger.
CONFIRM_DAYS = 7

# Seaux de limitation. L'inscription est serrée parce que chaque appel peut
# faire partir un courriel ; la confirmation l'est moins, parce qu'un lien
# rechargé deux fois est normal.
_SIGNUP_MAX, _SIGNUP_WINDOW = 5, 3600
_CONFIRM_MAX, _CONFIRM_WINDOW = 30, 3600

# ⚠️ Stricte, exprès : la valeur part dans `email_to`, où une virgule ou un
# point-virgule séparent DEUX destinataires, où chevrons et guillemets changent
# le sens de l'en-tête, et où `=?…?=` est un mot encodé RFC 2047 que le serveur
# décode en une AUTRE adresse. Ni `=` ni `?` dans la partie locale ; pas d'IDN :
# un refus vaut mieux qu'un courriel à la mauvaise boîte.
_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/^_`{|}~-]+"
    r"@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")
_EMAIL_MAX = 254  # RFC 5321

# Plafonds tenus en BASE (bf.mailing.signup.trace) : le seau par IP vit dans
# chaque worker et repart à zéro à chaque recyclage, il ne borne pas le total.
# Trois confirmations par BOÎTE et par jour : la boîte est normalisée (sans
# +suffixe, sans les points chez Gmail), sinon cible+0@ à cible+7@ en donnaient
# huit. Vingt par heure et cent par 24 heures en tout : au-delà, la page
# « occupé » et un seul avis interne par fenêtre.
_PAR_BOITE_MAX = 3
_GLOBAL_HEURE = 20
_GLOBAL_JOUR = 100
ICP_PLAFOND_AVISE = "bf_mailing_signup.plafond_avise"
_GMAIL = frozenset(("gmail.com", "googlemail.com"))


def _cle_verrou(nom):
    """Un entier signé de 64 bits, stable, pour `pg_advisory_xact_lock`."""
    return int.from_bytes(sha256(nom.encode()).digest()[:8], "big", signed=True)


# Le verrou consultatif qui sérialise le compte-puis-écrit des plafonds de
# l'infolettre. Un autre formulaire tient le sien à part.
VERROU_PLAFONDS = _cle_verrou("bf_mailing_signup:plafonds")

_bucket_lock = threading.Lock()
_bucket_data = defaultdict(list)
# Un flot venant d'adresses toutes différentes fait grossir ce dictionnaire une
# fois par adresse. Plafond franc, élagage par `_borner_seaux` : perdre
# l'historique d'un seau est sans gravité, laisser la mémoire enfler ne l'est pas.
_MAX_TRACKED_IPS = 10000
_bucket_window = {}  # seau -> fenêtre la plus longue vue, pour l'élagage

# La taille d'un envoi légitime tient en quelques centaines d'octets. Au-delà
# de 64 Kio, werkzeug refuse avant même de lire le corps (413).
MAX_CORPS = 64 * 1024

# Un jeton bien formé : 32 chiffres hexadécimaux. Tout le reste est refusé AVANT
# `hmac.compare_digest`, qui lève sur une chaîne non ASCII (erreur 500, et donc
# un oracle : la 500 ne sortait que pour un identifiant existant).
_JETON_RE = re.compile(r"^[0-9a-f]{32}$")

# Marque d'un courriel qui ne doit porter AUCUN pixel de suivi d'ouverture : la
# confirmation part vers une adresse qui n'a peut-être rien demandé. Retirée
# par `ir.mail_server.build_email` (models/ir_mail_server.py) avant l'envoi.
ENTETE_SANS_SUIVI = "X-BF-Sans-Suivi"
_proxy_sans_hote_signale = False


def _borner_seaux(maintenant):
    """Tenir `_bucket_data` sous `_MAX_TRACKED_IPS` clés sans relâcher un bloqué.

    ⚠️ La purge totale remettait à zéro la source même qu'on plafonnait :
    faire défiler assez d'adresses effaçait son propre blocage. On
    retire d'abord les clés échues (fenêtre de leur seau) ; si le flot est
    frais, celles qui pèsent le moins. Une clé bloquée sort en dernier.
    Appelée sous `_bucket_lock`.
    """
    if len(_bucket_data) <= _MAX_TRACKED_IPS:
        return
    for ident in [i for i, v in _bucket_data.items()
                  if not v or v[-1] <= maintenant - _bucket_window.get(i[0], 0)]:
        del _bucket_data[ident]
    if len(_bucket_data) > _MAX_TRACKED_IPS:
        cible = _MAX_TRACKED_IPS * 9 // 10
        ordre = sorted(_bucket_data,
                       key=lambda i: (len(_bucket_data[i]), _bucket_data[i][-1]))
        for ident in ordre[:len(_bucket_data) - cible]:
            del _bucket_data[ident]


def _client_ip():
    """L'adresse du client, pour la limitation de débit.

    ⚠️ `proxy_mode = True` est posé sur ce déploiement : werkzeug a déjà réécrit
    `remote_addr` avec l'adresse réelle, en ne faisant confiance qu'au nombre de
    sauts déclaré. Ne JAMAIS lire `X-Forwarded-For` ni `X-Real-IP` nous-mêmes —
    un client qui atteindrait le point d'entrée directement les choisit, et se
    donnerait un seau neuf à chaque requête. Même règle que `bf_appointment` et
    `bf_meeting`.
    """
    global _proxy_sans_hote_signale
    try:
        environ = request.httprequest.environ
        # 🔴 Odoo 18 n'applique ProxyFix que si `X-Forwarded-Host` arrive. Un
        # proxy qui ne l'envoie pas laisse `remote_addr` à SA propre adresse :
        # tous les visiteurs tombent dans le même seau, et cinq envois par
        # heure suffisent à fermer le formulaire pour tout le monde. Signalé
        # une fois par processus, au premier envoi.
        if (config.get("proxy_mode") and environ.get("HTTP_X_FORWARDED_FOR")
                and not environ.get("HTTP_X_FORWARDED_HOST")
                and not _proxy_sans_hote_signale):
            _proxy_sans_hote_signale = True
            _logger.warning(
                "bf_mailing_signup : proxy_mode actif, X-Forwarded-For reçu sans "
                "X-Forwarded-Host. Odoo n'applique pas ProxyFix : le seau par IP "
                "devient un seau unique pour tous les visiteurs. Ajouter "
                "« proxy_set_header X-Forwarded-Host » au bloc du proxy.")
        return request.httprequest.remote_addr or "inconnu"
    except Exception:
        return "inconnu"


def _rate_ok(bucket, max_hits, window):
    """Rend True si l'appel passe, et le compte."""
    ident = (bucket, _client_ip())
    now = time.monotonic()
    with _bucket_lock:
        _bucket_window[bucket] = max(window, _bucket_window.get(bucket, 0))
        _borner_seaux(now)
        cutoff = now - window
        hits = [t for t in _bucket_data[ident] if t > cutoff]
        if len(hits) >= max_hits:
            _bucket_data[ident] = hits
            _logger.warning("bf_mailing_signup : seau « %s » plein pour %s (%d en %d s), "
                            "envoi refusé", bucket, ident[1], max_hits, window)
            return False
        hits.append(now)
        _bucket_data[ident] = hits
    return True


def _boite(email):
    """La boîte qui reçoit vraiment : sans +suffixe, et sans les points chez Gmail."""
    local, _sep, domaine = (email or "").strip().lower().rpartition("@")
    local = local.split("+", 1)[0]
    if domaine in _GMAIL:
        local, domaine = local.replace(".", ""), "gmail.com"
    return f"{local}@{domaine}"


def _cle_boite(env, email, espace="bf_mailing_signup"):
    """HMAC de la boîte normalisée : ce que la base garde, jamais l'adresse."""
    charge = f"{espace}:boite:{_boite(email)}".encode()
    return hmac.new(_secret(env), charge, sha256).hexdigest()


def _origine_attendue(env):
    """`schéma://hôte[:port]` du site qui porte les formulaires (`base_url`)."""
    icp = env["ir.config_parameter"].sudo()
    base = (icp.get_param(ICP_BASE_URL) or icp.get_param("web.base.url") or "").strip()
    m = re.match(r"^(https?://[^/?#]+)", base, re.IGNORECASE)
    return m.group(1).lower() if m else ""


def _origine_refusee(env):
    """Vrai si le POST vient manifestement d'une autre page que celles du site.

    La route s'ouvre sans jeton CSRF : sans ce contrôle, une page tierce fait
    poster les navigateurs de ses visiteurs et remplit les plafonds à leur place.
    Refusé : un `Sec-Fetch-Site: cross-site`, ou un `Origin` PRÉSENT qui n'est pas
    celui du site (« null » compris). Un client sans navigateur n'envoie ni l'un
    ni l'autre : il reste borné par les plafonds.
    """
    entetes = request.httprequest.headers
    if (entetes.get("Sec-Fetch-Site") or "").strip().lower() == "cross-site":
        return True
    origine = entetes.get("Origin")
    if origine is None:
        return False
    attendue = _origine_attendue(env)
    return not attendue or origine.strip().rstrip("/").lower() != attendue


def _refus_origine():
    _logger.warning("bf_mailing_signup : envoi refusé, Origin ou Sec-Fetch-Site étranger")
    return request.make_response("Origine refusée.", status=403,
                                 headers=[("Content-Type", "text/plain; charset=utf-8")])


def _compter_sous_verrou(env, cle, compter):
    """Prendre le verrou `cle`, puis rendre `compter(env_frais)`, lu sur une transaction NEUVE.

    🔴 Le compte puis l'écriture d'un plafond doivent être sérialisés : sans
    verrou, 24 envois simultanés comptaient tous 19 et écrivaient tous (23 lignes
    pour 20), et le perdant d'une course sur la boîte
    laissait son INSERT au journal de Postgres.
    🔴 Le verrou SEUL ne suffit pas. Odoo travaille en REPEATABLE READ :
    l'instantané de la requête date de sa première instruction, donc d'AVANT la
    prise du verrou, et un compte fait sur `env.cr` ne voit pas la ligne que le
    détenteur précédent vient de valider. Le compte se fait donc sur un curseur
    ouvert APRÈS la prise du verrou, et fermé avant toute écriture de la requête.
    Le verrou, lui, tient jusqu'à la fin de la transaction de la requête : la
    ligne qu'elle écrit est visible du suivant quand il l'obtient.
    """
    env.cr.execute("SELECT pg_advisory_xact_lock(%s)", [cle])
    with env.registry.cursor() as cr:
        return compter(api.Environment(cr, SUPERUSER_ID, {}))


def _reveiller_la_file(env):
    """La tâche « Email Queue Manager », tout de suite plutôt qu'à son heure."""
    cron = env.ref("mail.ir_cron_mail_scheduler_action", raise_if_not_found=False)
    if cron:
        cron.sudo()._trigger()


def _aviser_plafond(env, cle_icp, fenetre, destinataires, objet, texte):
    """Un seul avis interne par fenêtre de plafond, même entre plusieurs workers.

    La ligne du paramètre `cle_icp` (créée par les données du module) est prise
    sous verrou : le second worker attend, lève une erreur de sérialisation quand
    le premier valide, et Odoo rejoue sa requête, qui trouve la fenêtre déjà
    avisée. ⚠️ `set_param` vide les caches de tous les workers : une fois par
    fenêtre au plus.
    """
    with mute_logger("odoo.sql_db"):
        env.cr.execute("SELECT value FROM ir_config_parameter WHERE key = %s FOR UPDATE",
                       [cle_icp])
    ligne = env.cr.fetchone()
    if ligne and ligne[0] == fenetre:
        return False
    env["ir.config_parameter"].sudo().set_param(cle_icp, fenetre)
    _logger.warning("bf_mailing_signup : %s", objet)
    if destinataires:
        env["mail.mail"].sudo().create({
            "subject": objet,
            "body_html": f'<p style="font-family:{POLICE}">{_ech(texte)}</p>',
            "email_to": destinataires,
            "auto_delete": True,
            "headers": repr({ENTETE_SANS_SUIVI: "1"}),
        })
        _reveiller_la_file(env)
    return True


def _fenetres(maintenant):
    return (f"heure {maintenant:%Y-%m-%dT%H}", f"jour {maintenant:%Y-%m-%d}")


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
    if not isinstance(token, str) or not _JETON_RE.match(token):
        return False
    today = date.today().toordinal()
    return any(hmac.compare_digest(_token(env, list_id, email, today - d), token)
               for d in range(CONFIRM_DAYS))


def _texte(valeur):
    """Une valeur de formulaire en texte, ou "" si elle n'en est pas une.

    ⚠️ Un champ envoyé en `multipart/form-data` comme FICHIER arrive en
    `FileStorage`, pas en `str` : `.strip()` levait, et la route rendait une 500.
    """
    return valeur if isinstance(valeur, str) else ""


def _lang(raw):
    return "en" if _texte(raw).strip().lower().startswith("en") else "fr"


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
    # Seuls http et https passent : la valeur finit dans un `href`, et un
    # `javascript:` ou un `data:` n'a rien à faire dans un courriel.
    site_societe = (icp.get_param(ICP_COMPANY_URL) or "").strip()
    if not re.match(r"^https?://[^\s/]+", site_societe, re.IGNORECASE):
        site_societe = ""
    return {
        "marque": icp.get_param(ICP_BRAND) or societe.name,
        "societe": societe.name,
        "sigle": icp.get_param(ICP_MARK) or "",
        "logo": icp.get_param(ICP_LOGO) or "",
        "site_societe": site_societe,
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

    # Le site de la société, à côté de son nom. L'adresse postale reste dessous,
    # plus petite mais dans le même gris : la LCAP la veut, présentée clairement,
    # dans tout message qui demande un consentement, et un lien ne la remplace pas.
    site_societe = ""
    if b["site_societe"]:
        hote = b["site_societe"].split("//", 1)[-1].rstrip("/")
        site_societe = (
            f' · <a href="{_att(b["site_societe"])}" style="color:{GRIS};'
            f'text-decoration:underline">{_ech(hote)}</a>')

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
                  color:{GRIS}">{societe}{site_societe}<br>
        <span style="font-size:11px;line-height:1.5">{_ech(b["postale"])}</span></p>
        <p style="margin:0;font-family:{POLICE};font-size:12.5px;line-height:1.6;
                  color:{GRIS}">{_ech(t["pourquoi"])}</p></td>
    </tr></table>
  </td></tr>

</table>
</td></tr></table>
</body></html>"""
    return sujet, corps


# ------------------------------------------------------------------- avis

# À qui dire qu'une inscription vient d'arriver. Une ou plusieurs adresses
# séparées par des virgules. Non réglé, aucun avis ne part : c'est le défaut,
# et c'est ce qui permet de publier le module sans qu'un locataire hérite d'une
# adresse qui n'est pas la sienne.
ICP_NOTIFY = "bf_mailing_signup.notify_email"

MOMENTS = {
    "demande": (
        "Infolettre : demande d'inscription de {email}",
        "Demande reçue, en attente de confirmation",
        "Le courriel de confirmation vient de partir. Tant que le lien n'est pas "
        "suivi, l'inscription reste désactivée et rien d'autre ne sera envoyé à "
        "cette adresse.",
    ),
    "confirmation": (
        "Infolettre : inscription confirmée de {email}",
        "Consentement exprès confirmé",
        "Le lien a été suivi depuis cette adresse. Le consentement est daté au "
        "fil du contact, et l'adresse recevra les prochains envois.",
    ),
}


def _avis_html(env, moment, email, lang, contact):
    b = _marque(env)
    sujet, etat, contexte = MOMENTS[moment]
    icp = env["ir.config_parameter"].sudo()
    base = (icp.get_param("web.base.url") or "").rstrip("/")
    liste = env["mailing.list"].sudo().browse(_list_id(env))

    fiche = _ech(contact.email if contact else email)
    if contact and base:
        cible = _att(f"{base}/odoo/m-mailing.contact/{contact.id}")
        fiche = (f'<a href="{cible}" style="color:{BLEU};text-decoration:none">'
                 f'{_ech(contact.email or email)}</a>')

    lignes = [
        ("Adresse", fiche),
        ("Langue", "Français" if lang == "fr" else "English"),
        ("État", _ech(etat)),
        ("Liste", _ech(liste.name if liste.exists() else "(aucune)")),
        ("Reçu le", _ech(fields.Datetime.to_string(fields.Datetime.now()) + " UTC")),
    ]
    corps_lignes = "".join(
        f'<tr>'
        f'<td style="padding:7px 16px 7px 0;font-family:{POLICE};font-size:13.5px;'
        f'color:{GRIS};white-space:nowrap;vertical-align:top">{_ech(cle)}</td>'
        f'<td style="padding:7px 0;font-family:{POLICE};font-size:14.5px;'
        f'color:{ENCRE};vertical-align:top">{valeur}</td></tr>'
        for cle, valeur in lignes)

    marque = _att(b["marque"])
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<title>{_ech(sujet.format(email=email))}</title></head>
<body style="margin:0;padding:0;background-color:{GLACE}">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
       bgcolor="{GLACE}" style="background-color:{GLACE};margin:0;padding:0">
<tr><td align="center" style="padding:24px 12px">

<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="560"
       style="width:100%;max-width:560px;background-color:{PAPIER};border-radius:14px;
              overflow:hidden;border:1px solid {TRAIT}">

  <tr><td bgcolor="{MARINE}" style="padding:16px 26px">
    <p style="margin:0;font-family:{POLICE};font-size:15px;font-weight:600;
              color:{PAPIER};line-height:1.3">{marque} &#183; infolettre</p></td></tr>

  <tr><td style="padding:24px 26px 20px">
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
      {corps_lignes}
    </table>
    <p style="margin:20px 0 0;font-family:{POLICE};font-size:13.5px;line-height:1.6;
              color:{GRIS}">{_ech(contexte)}</p>
  </td></tr>

</table>
</td></tr></table>
</body></html>"""


def _aviser(env, moment, email, lang, contact=None):
    """Prévenir l'interne qu'une inscription bouge. Ne peut RIEN faire échouer.

    ⚠️ Tout est sous `try` jusqu'au bout. Cet avis est un confort d'exploitation
    posé APRÈS le travail utile : s'il lève, la personne qui vient de s'inscrire
    ne doit pas en payer le prix, ni voir une erreur, ni perdre son inscription.
    ⚠️ Et sous `savepoint` : une erreur SQL attrapée sans lui laisserait la
    transaction en échec, et la requête perdrait l'inscription en validant.
    """
    try:
        with env.cr.savepoint():
            destinataires = (env["ir.config_parameter"].sudo()
                             .get_param(ICP_NOTIFY) or "").strip()
            if not destinataires:
                return
            sujet = MOMENTS[moment][0].format(email=email)
            # `email_from` reste absent, pour la même raison que sur la confirmation :
            # l'adresse d'envoi par défaut est la seule alignée en SPF et DKIM.
            env["mail.mail"].sudo().create({
                "subject": sujet,
                "body_html": _avis_html(env, moment, email, lang, contact),
                "email_to": destinataires,
                "auto_delete": True,
                # Il nomme l'adresse saisie : ni pixel, et relevé effacé à sept jours.
                "headers": repr({ENTETE_SANS_SUIVI: "1"}),
            }).send(raise_exception=False)
    except Exception:               # noqa: BLE001
        _logger.exception("bf_mailing_signup : avis interne non envoyé (%s)", moment)



def _page_bouton(env, email, jeton, lang):
    """La page que le lien de confirmation ouvre : un bouton, rien d'autre.

    ⚠️ Pas de script qui soumet tout seul : un robot d'analyse qui exécute le
    JavaScript confirmerait encore. Consentir est un geste de la personne.
    """
    b = _marque(env)
    t = {k: v.format(marque=b["marque"], societe=b["societe"], site=_site(env))
         for k, v in TEXTES[lang].items()}
    return f"""<!doctype html>
<html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>{_ech(t["titre"])}</title></head>
<body style="margin:0;background:#FFFFFF;color:#2E3132;font-family:{POLICE}">
<main style="max-width:32rem;margin:10vh auto;padding:0 16px">
<h1 style="font-size:1.5rem">{_ech(t["titre"])}</h1>
<p>{_ech(t["intro"])}</p>
<form method="post" action="/infolettre/confirmer">
<input type="hidden" name="e" value="{_att(email)}">
<input type="hidden" name="j" value="{_att(jeton)}">
<input type="hidden" name="lang" value="{_att(lang)}">
<button type="submit" style="font:inherit;font-weight:600;padding:12px 22px;border:0;border-radius:8px;background:#2E3132;color:#FFFFFF;cursor:pointer">{_ech(t["bouton"])}</button>
</form>
<p style="font-size:.9rem;color:#5A5F61">{_ech(t["note"])}</p>
</main></body></html>"""

class BfMailingSignup(http.Controller):

    # ⚠️ `csrf=False` est obligatoire, pas commode : la page appelante est un
    # fichier statique servi par le proxy, sans session Odoo, donc aucun jeton
    # ne peut y être posé. Ce que ça ouvre est refermé par le double
    # consentement — un envoi non sollicité n'inscrit personne, il fait partir
    # un seul courriel vers l'adresse saisie — plus le pot de miel et le seau.
    @http.route("/infolettre", type="http", auth="public", methods=["POST"],
                csrf=False, website=False, sitemap=False, max_content_length=MAX_CORPS)
    def signup(self, **post):
        lang = _lang(post.get("lang"))
        pages = PAGES[lang]
        if _origine_refusee(request.env):
            return _refus_origine()
        # Réponse UNIQUE quoi qu'il arrive. Dire « déjà inscrit », « adresse
        # refusée » ou « trop de tentatives » donne à qui sonde le service
        # exactement ce qu'il cherche : savoir si une adresse est sur la liste.
        merci = werkzeug.utils.redirect(pages["merci"], 303)

        # Le pot de miel : un champ que la feuille de style sort de l'écran,
        # qu'aucun lecteur d'écran n'annonce et qu'aucune tabulation n'atteint.
        # Rempli, c'est un robot. On rend la page de remerciement quand même :
        # un refus visible lui apprendrait où est le champ.
        # Un pot de miel envoyé comme fichier est aussi un robot.
        site_web = post.get("site_web")
        if site_web is not None and (not isinstance(site_web, str) or site_web.strip()):
            return merci
        if not _rate_ok("signup", _SIGNUP_MAX, _SIGNUP_WINDOW):
            return merci

        email = _texte(post.get("courriel")).strip().lower()
        if not email or len(email) > _EMAIL_MAX or not _EMAIL_RE.match(email):
            return merci

        env = request.env
        list_id = _list_id(env)
        if not list_id or not env["mailing.list"].sudo().browse(list_id).exists():
            _logger.warning("bf_mailing_signup : %s ne pointe sur aucune liste", ICP_LIST)
            return merci

        try:
            with env.cr.savepoint():
                plafond = self._plafonds(env, email)
                if plafond:
                    return werkzeug.utils.redirect(pages["occupe"], 303)
                if plafond is None:
                    contact = self._ensure_pending(env, list_id, email)
                    self._send_confirmation(env, list_id, email, lang)
                    _aviser(env, "demande", email, lang, contact)
        except PG_CONCURRENCY_EXCEPTIONS_TO_RETRY:
            raise                       # Odoo rejoue la requête
        except psycopg2.errors.UniqueViolation:
            # Le perdant d'une course sur le plafond par boîte : la contrainte a
            # levé avant tout envoi. Rien n'est écrit.
            _logger.warning("bf_mailing_signup : plafond par boîte atteint en concurrence, "
                            "envoi refusé")
        except Exception:               # noqa: BLE001 — la page ne doit jamais 500
            _logger.exception("bf_mailing_signup : échec d'inscription")
        return merci

    # 🔴 Ouvrir le lien ne confirme RIEN. Les passerelles de
    # courriel (Safe Links, Proofpoint, Mimecast…) et les aperçus ouvrent chaque
    # lien d'un message avant la personne, en GET ou en HEAD : tant que le GET
    # activait, un tiers inscrivait l'adresse d'une entreprise dont la
    # passerelle analyse les liens, et le double consentement se confirmait
    # tout seul. Le GET rend un bouton ; seul le POST de ce bouton active.
    @http.route("/infolettre/confirmer", type="http", auth="public",
                methods=["GET", "POST"], csrf=False, website=False, sitemap=False,
                max_content_length=MAX_CORPS)
    def confirm(self, e=None, j=None, lang=None, **kw):
        lang = _lang(lang)
        pages = PAGES[lang]
        email = _texte(e).strip().lower()
        env = request.env
        list_id = _list_id(env)
        if (not _rate_ok("confirm", _CONFIRM_MAX, _CONFIRM_WINDOW)
                or not email or not list_id
                or not _token_valid(env, list_id, email, j)):
            # Lien périmé, tronqué ou fabriqué : on renvoie au formulaire, qui
            # est la seule chose utile à faire ensuite.
            return werkzeug.utils.redirect(pages["retour"], 303)
        if request.httprequest.method != "POST":
            return request.make_response(
                _page_bouton(env, email, j, lang),
                headers=[("Content-Type", "text/html; charset=utf-8"),
                         ("Cache-Control", "no-store"),
                         ("X-Robots-Tag", "noindex"),
                         ("Referrer-Policy", "no-referrer")])
        try:
            self._activate(env, list_id, email, lang)
        except Exception:               # noqa: BLE001
            _logger.exception("bf_mailing_signup : échec de confirmation")
        return werkzeug.utils.redirect(pages["confirme"], 303)

    # ------------------------------------------------------------------ interne

    def _plafonds(self, env, email):
        """Rend True (plafond global : page « occupé »), False (plafond de la
        boîte : « merci » sans envoi) ou None (la confirmation peut partir, sa
        trace est écrite). Les comptes se font sous le verrou des plafonds, sur
        une transaction neuve (`_compter_sous_verrou`) : deux envois simultanés
        ne voient plus le même compte. Une course perdue malgré tout (seconde
        ligne) lève `UniqueViolation`."""
        Trace = env["bf.mailing.signup.trace"].sudo()
        maintenant = fields.Datetime.now()
        heure, jour_ = _fenetres(maintenant)
        bornes = ((_GLOBAL_HEURE, maintenant - timedelta(hours=1), heure),
                  (_GLOBAL_JOUR, maintenant - timedelta(days=1), jour_))
        cle = _cle_boite(env, email)
        jour = fields.Date.today()

        def compter(frais):
            Lues = frais["bf.mailing.signup.trace"]
            return ([Lues.search_count([("create_date", ">=", depuis)])
                     for _limite, depuis, _fenetre in bornes],
                    Lues.search_count([("key", "=", cle), ("day", "=", jour)]))

        globaux, deja = _compter_sous_verrou(env, VERROU_PLAFONDS, compter)
        for (limite, _depuis, fenetre), n in zip(bornes, globaux):
            if n >= limite:
                _aviser_plafond(
                    env, ICP_PLAFOND_AVISE, fenetre,
                    (env["ir.config_parameter"].sudo().get_param(ICP_NOTIFY) or "").strip(),
                    f"Infolettre : plafond global atteint ({limite} confirmations, {fenetre})",
                    "Le formulaire d'infolettre répond « occupé » jusqu'à la fin de la "
                    "fenêtre. Un pic légitime, ou un envoi en masse : le journal d'Odoo "
                    "dit d'où viennent les refus du seau.")
                return True
        if deja >= _PAR_BOITE_MAX:
            _logger.warning("bf_mailing_signup : plafond de %d confirmations par boîte et par "
                            "jour atteint, envoi refusé", _PAR_BOITE_MAX)
            return False
        # `mute_logger` : le perdant d'une course écrirait sinon la requête
        # INSERT au journal en ERROR, provoqué de l'extérieur.
        with mute_logger("odoo.sql_db"):
            Trace.create({"key": cle, "day": jour, "rank": deja + 1}).flush_recordset()
        return None

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
            "headers": repr({ENTETE_SANS_SUIVI: "1"}),
        })
        # Envoi immédiat, mais un SMTP lent ou fâché ne doit pas faire attendre
        # le visiteur ni lui rendre une erreur : en cas d'échec le message reste
        # en file et le cron d'Odoo reprend.
        try:
            mail.send(raise_exception=False)
        except Exception:               # noqa: BLE001
            _logger.exception("bf_mailing_signup : envoi différé de la confirmation")

    def _activate(self, env, list_id, email, lang="fr"):
        """Lève l'`opt_out` et date le consentement dans le fil du contact."""
        contact = env["mailing.contact"].sudo().search(
            [("email_normalized", "=", email)], limit=1)
        if not contact:
            contact = env["mailing.contact"].sudo().create({"email": email})
        sub = env["mailing.subscription"].sudo().search(
            [("contact_id", "=", contact.id), ("list_id", "=", list_id)], limit=1)
        if sub:
            nouvelle = sub.opt_out
            sub.write({"opt_out": False})
        else:
            nouvelle = True
            env["mailing.subscription"].sudo().create({
                "contact_id": contact.id, "list_id": list_id, "opt_out": False})
        # La preuve du consentement exprès, datée, là où quelqu'un la cherchera.
        # Elle est posée à CHAQUE passage, même si le lien est rechargé : chaque
        # clic est un acte de consentement de plus, et une trace en double coûte
        # moins cher qu'une trace manquante le jour où on la cherche.
        contact.message_post(body=(
            "Consentement exprès confirmé par lien courriel le "
            f"{fields.Datetime.now()} UTC "
            "(inscription publique symbifox.com, double consentement)."))
        # L'avis, lui, ne part QUE si l'état a changé. Un lien rechargé deux
        # fois n'est pas une deuxième inscription, et prévenir deux fois pour la
        # même personne apprend à ignorer l'avis.
        if nouvelle:
            _aviser(env, "confirmation", email, lang, contact)
