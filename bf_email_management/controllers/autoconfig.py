# Part of bf_email_management. See LICENSE file for full copyright and licensing details.
"""L'autoconfiguration du courrier, servie par Odoo.

Thunderbird 160 interroge, par ordre de priorité (`FindConfig.sys.mjs:63`) :

    1. `isp/<domaine>.xml`, dans son dossier d'installation
    2. `https://autoconfig.<domaine>/mail/config-v1.1.xml`, puis
       `https://<domaine>/.well-known/autoconfig/mail/config-v1.1.xml`
    3. l'ISPDB de Mozilla
    4. le MX, puis l'ISPDB sur le domaine du MX

Cette route sert la seconde URL de l'étape 2, et elle existe parce que la première est morte :
`autoconfig.migadu.com` coupe la poignée de main TLS dès que le SNI n'est pas le sien, donc le
CNAME proxifié rend 525 et le CNAME nu fait échouer la vérification du nom. Constaté sur une
série de domaines : plus aucun ne publiait de configuration lisible, et l'ISPDB rend 404 sur
`migadu.com` aussi.

Ce que la route ne fait pas, volontairement :

* ⛔ Elle ne répond QUE pour les domaines nommés dans `bf_email_management.autoconfig_domaines`,
  et le module n'en livre AUCUN par défaut : un locataire qui ne pose pas le paramètre ne publie
  rien. Publier une configuration sur un domaine dont le courrier est ailleurs est pire que ne
  rien publier (un domaine peut servir son propre IMAP, un autre n'avoir qu'un
  renvoi).
* ⚠️ Elle ne recopie JAMAIS le `?emailaddress=` envoyé par le client. Le fichier porte
  `%EMAILADDRESS%`, que le client remplace lui-même.

⚠️ Le type MIME n'est pas cosmétique : Thunderbird n'analyse la réponse que si son `Content-Type`
commence par `text/xml`, `application/xml` ou `text/rdf` (`FetchHTTP.sys.mjs:176`). Servi en
`text/html`, le fichier serait ignoré en silence, et la cascade tomberait sur l'ISPDB.
"""
import logging
import re

from lxml import etree
from odoo import http
from odoo.http import request

from ..models.bf_email_autoconfig import FOURNISSEURS

_logger = logging.getLogger(__name__)

PARAM_DOMAINES = "bf_email_management.autoconfig_domaines"
PARAM_FOURNISSEUR = "bf_email_management.autoconfig_fournisseur"
PARAM_NOM = "bf_email_management.autoconfig_nom"

# Un nom d'hôte, et rien d'autre : une valeur de travers dans le paramètre ne doit pas pouvoir
# entrer dans le XML servi.
NOM_HOTE = re.compile(
    r"^(?=.{4,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
    r"(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+\Z"
)

# Les trois seules valeurs de <socketType> que Thunderbird connaisse
# (`readFromXML.sys.mjs:131`). Une fiche de fournisseur qui en porterait une autre ferait
# refuser tout le fichier par le client : mieux vaut ne rien servir.
SOCKETS = ("SSL", "STARTTLS", "plain")

# Le champ `auth` des fiches de FOURNISSEURS, traduit en <authentication> du format de Mozilla.
# Un mot de passe d'application reste, pour le client, un mot de passe en clair.
AUTHENTIFICATION = {
    "password": "password-cleartext",
    "app_password": "password-cleartext",
    "oauth": "OAuth2",
}

# ⚠️ Dix minutes, pas une heure : un domaine qui change de fournisseur, ou une entrée fautive, doit
# pouvoir être retiré sans qu'un cache continue de servir une configuration fausse. `Vary` parce
# que la réponse dépend entièrement de l'hôte demandé.
# (`X-Content-Type-Options: nosniff` n'est pas ici : Odoo le pose sur toute reponse.)
EN_TETES = [
    ("Content-Type", "application/xml; charset=utf-8"),
    ("Cache-Control", "public, max-age=600"),
    ("Vary", "Host"),
]

# Un 404 ici veut dire « ce domaine n'est pas publié » : une réponse à ne pas garder, sinon un
# domaine fraîchement publié reste masqué par un refus en cache.
EN_TETES_REFUS = [("Content-Type", "text/plain"), ("Cache-Control", "no-store")]


def _hote():
    """Le domaine demandé : sans port, en minuscules, sans le `www.` de tête.

    ⚠️ La valeur rendue est validée ici, et pas seulement comparée aux clés de la carte : la
    sûreté doit être locale. Sans ça, un jour où l'appariement deviendrait un joker ou une
    comparaison par suffixe, un `Host` portant un octet nul, un retour de ligne ou 3 000
    caractères entrerait dans le fichier servi.
    """
    hote = (request.httprequest.host or "").split(":")[0].strip().lower().rstrip(".")
    hote = hote.removeprefix("www.")
    return hote if NOM_HOTE.match(hote) else ""


def _carte():
    """Les domaines servis, lus au paramètre : `domaine`, ou `domaine=fournisseur`."""
    param = request.env["ir.config_parameter"].sudo()
    brut = param.get_param(PARAM_DOMAINES, "") or ""
    defaut = (param.get_param(PARAM_FOURNISSEUR, "") or "migadu").strip().lower()
    carte = {}
    for entree in re.split(r"[,;\s]+", brut):
        if not entree:
            continue
        domaine, _separateur, fournisseur = entree.partition("=")
        # Le même `www.` qu'enlève `_hote()` : sinon une entrée `www.exemple.com` au paramètre
        # ne sert personne, ni `exemple.com` ni `www.exemple.com`, et sans un mot au journal.
        domaine = domaine.strip().lower().rstrip(".").removeprefix("www.")
        fournisseur = fournisseur.strip().lower() or defaut
        if not NOM_HOTE.match(domaine):
            _logger.warning("autoconfig : domaine ignoré, nom d'hôte invalide : %r", domaine)
            continue
        if fournisseur not in FOURNISSEURS:
            _logger.warning("autoconfig : domaine %s ignoré, fournisseur inconnu : %r",
                            domaine, fournisseur)
            continue
        carte[domaine] = fournisseur
    return carte


def _nom_affiche(domaine):
    """Ce que le client affichera : le paramètre, sinon le domaine lui-même.

    ⛔ Pas la première société de la base : sur un locataire multi-société, rien ne rattache
    la société n° 1 au domaine demandé, et le défaut doit être exact plutôt que devine. Le
    client, lui, affiche de toute façon l'identifiant du fichier quand le nom manque.

    ⛔ Ce paramètre vaut pour TOUT le locataire : ne le poser que si ce locataire publie
    un seul domaine de courriel. Autrement, le nom d'une marque s'afficherait aux usagers
    d'une autre, et le défaut (le domaine lui-même) est toujours exact.
    """
    nom = (request.env["ir.config_parameter"].sudo().get_param(PARAM_NOM, "") or "").strip()
    return nom or domaine


def _fiche(fournisseur):
    """La fiche du fournisseur, relue en entier avant d'en servir quoi que ce soit.

    ⚠️ La table `FOURNISSEURS` est tenue pour la cascade de découverte : qui y ajoute
    un fournisseur n'a aucune raison de savoir qu'une route HTTP publique la lit aussi. Une
    fiche de travers doit donner un refus, jamais une trace de 500 chez tous les locataires, et
    jamais un fichier que le client rejettera.
    """
    fiche = FOURNISSEURS[fournisseur]
    if not isinstance(fiche, dict):
        raise TypeError(f"fournisseur {fournisseur} : fiche {type(fiche).__name__}, pas un dict")
    if fiche.get("auth") not in AUTHENTIFICATION:
        raise ValueError(f"fournisseur {fournisseur} : auth {fiche.get('auth')!r} sans traduction")
    for genre in ("imap", "smtp"):
        hote, port, prise = fiche[genre]
        if not (isinstance(hote, str) and NOM_HOTE.match(hote)):
            raise ValueError(f"fournisseur {fournisseur} : hôte {genre} {hote!r} invalide")
        if not (isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= 65535):
            raise ValueError(f"fournisseur {fournisseur} : port {genre} {port!r} invalide")
        if prise not in SOCKETS:
            raise ValueError(
                f"fournisseur {fournisseur} : <socketType> {prise!r} inconnu de Thunderbird")
    return fiche


def _xml(domaine, fournisseur, nom):
    fiche = _fiche(fournisseur)
    racine = etree.Element("clientConfig", version="1.1")
    bloc = etree.SubElement(racine, "emailProvider", id=domaine)
    etree.SubElement(bloc, "domain").text = domaine
    etree.SubElement(bloc, "displayName").text = nom
    etree.SubElement(bloc, "displayShortName").text = domaine
    for genre, balise, type_ in (("imap", "incomingServer", "imap"),
                                 ("smtp", "outgoingServer", "smtp")):
        hote, port, prise = fiche[genre]
        serveur = etree.SubElement(bloc, balise, type=type_)
        etree.SubElement(serveur, "hostname").text = hote
        etree.SubElement(serveur, "port").text = str(port)
        etree.SubElement(serveur, "socketType").text = prise
        etree.SubElement(serveur, "authentication").text = AUTHENTIFICATION[fiche["auth"]]
        # Le client remplace lui-même %EMAILADDRESS% : ne jamais recopier ici ce qu'il a envoyé.
        etree.SubElement(serveur, "username").text = "%EMAILADDRESS%"
    return etree.tostring(racine, xml_declaration=True, encoding="UTF-8", pretty_print=True)


class BfEmailAutoconfigServie(http.Controller):

    @http.route(["/.well-known/autoconfig/mail/config-v1.1.xml"], type="http", auth="none",
                methods=["GET"], csrf=False, save_session=False)
    def autoconfig_mail(self, **kw):
        # ⚠️ Odoo ajoute HEAD et OPTIONS a toute route : `methods=["GET"]` veut dire
        # GET, HEAD et OPTIONS, et refuse le reste en 405. Et `readonly` vaut deja
        # `auth == "none"` par defaut (Odoo 18), donc le curseur est en lecture seule
        # sans le declarer : ne pas « corriger » ca en `readonly=False`.
        try:
            domaine = _hote()
            fournisseur = _carte().get(domaine) if domaine else None
            if not fournisseur:
                return request.make_response(b"", headers=EN_TETES_REFUS, status=404)
            corps = _xml(domaine, fournisseur, _nom_affiche(domaine))
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            # ⛔ Les trois familles, pas seulement ValueError : une fiche de fournisseur sans
            # sa clé `smtp` lève KeyError, et un port absent lève TypeError. Le contrat du
            # fichier est « mieux vaut ne rien servir », donc 404, jamais 500.
            _logger.warning("autoconfig : rien servi : %r", exc)
            return request.make_response(b"", headers=EN_TETES_REFUS, status=404)
        return request.make_response(corps, headers=EN_TETES)
