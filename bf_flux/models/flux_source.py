# -*- coding: utf-8 -*-
"""Une source : un flux RSS ou Atom, et sa relève.

Un flux ne garde que ses derniers éléments (vingt chez GlobeNewswire) et il n'y
a pas d'archive publique à rattraper. La cadence se règle donc flux par flux,
sur la vitesse à laquelle sa fenêtre tourne, pas par prudence.
"""
import html
import ipaddress
import logging
import re
import socket
import time
from urllib.parse import urljoin, urlsplit
from datetime import timedelta, timezone
from email.utils import parsedate_to_datetime

import requests
from lxml import etree

from odoo import _, api, fields, models
from odoo.modules import module as odoo_module
from odoo.exceptions import AccessError, ValidationError

_logger = logging.getLogger(__name__)

# ⚠️ Deux refus mesurés le 26 septembre 2026, tous deux muets (la requête pend
# jusqu'au délai) : GlobeNewswire pour un en-tête qui imite Chrome, et CBC dès
# que l'en-tête porte une adresse web (« +https://… »). Cette forme passe chez
# les deux, et chez la BBC.
UA = "Mozilla/5.0 (compatible; SymbifoxFlux/1.0)"
TIMEOUT = 30
MAX_BYTES = 2 * 1024 * 1024
REDIRECTIONS_MAX = 5
PAUSE = 1.5  # s entre deux pages d'un même passage, par politesse envers l'hôte
# Un échec passager qui se répète n'est plus passager. Au sixième de suite (une
# demi-journée à la cadence par défaut), la source prévient, une seule fois.
SEUIL_ALERTE = 6

# On frappe le même hôte plusieurs fois d'affilée : un 429, un 403 temporaire ou
# un 5xx sont les refus les plus probables. Les traiter comme définitifs figerait
# un élément sans texte pour toujours. Tout autre 4xx est définitif.
STATUTS_PASSAGERS = {403, 408, 425, 429}

# Analyseur fermé : un flux vient de l'extérieur. Aucune entité externe, aucun
# accès réseau depuis le XML, pas d'arbre géant.
_PARSER = etree.XMLParser(
    resolve_entities=False, no_network=True, huge_tree=False, recover=True)


class FluxErreur(Exception):
    """Échec de téléchargement, avec son caractère passager ou définitif."""

    def __init__(self, message, passager):
        super().__init__(message)
        self.passager = passager


def adresse_publique(url):
    """Vrai si l'hôte de l'URL ne résout que vers des adresses publiques.

    Le lien d'un article vient du flux, donc de l'extérieur : sans ce contrôle,
    un article pointant vers localhost, le réseau privé ou l'adresse de
    métadonnées d'un nuage ferait lire au serveur une page interne, dont le
    texte serait ensuite montré au personnel et déposé au Nextcloud.
    """
    try:
        hote = urlsplit(url).hostname
        if not hote:
            return False
        infos = socket.getaddrinfo(hote, None)
    except (ValueError, OSError):
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        # « is_global » et non une liste de cas : 100.64.0.0/10 (espace partagé,
        # celui de Tailscale) n'est ni privé ni réservé pour Python, et passait
        # (relevé par la contre-vérification adverse).
        if not ip.is_global or ip.is_multicast:
            return False
    return True


def telecharger(url, accept="application/rss+xml, application/atom+xml, */*",
                publique_seulement=False):
    """Télécharge une ressource. Lève FluxErreur, jamais une exception brute.

    `publique_seulement` : pour une adresse venue d'un flux (la page d'un
    article). Chaque saut de redirection est revérifié, sans quoi un site
    public n'aurait qu'à rediriger vers une adresse interne.
    """
    try:
        for _saut in range(REDIRECTIONS_MAX + 1):
            if publique_seulement and not adresse_publique(url):
                raise FluxErreur("Adresse non publique refusée", passager=False)
            rep = requests.get(
                url, timeout=TIMEOUT, stream=True, allow_redirects=False,
                headers={"User-Agent": UA, "Accept": accept})
            if rep.is_redirect and rep.headers.get("Location"):
                url = urljoin(url, rep.headers["Location"])
                rep.close()
                if not re.match(r"^https?://", url, re.I):
                    raise FluxErreur("Redirection hors http(s) refusée", passager=False)
                continue
            break
        else:
            raise FluxErreur("Trop de redirections", passager=False)
    except (requests.Timeout, requests.ConnectionError) as exc:
        raise FluxErreur(str(exc), passager=True) from exc
    except requests.RequestException as exc:
        raise FluxErreur(str(exc), passager=False) from exc
    with rep:
        code = rep.status_code
        if code >= 400:
            passager = code in STATUTS_PASSAGERS or code >= 500
            raise FluxErreur(f"HTTP {code}", passager=passager)
        brut = rep.raw.read(MAX_BYTES, decode_content=True)
        return brut, rep.headers.get("Content-Type", "")


_IMG_RE = re.compile(r"""<img[^>]+src=["']([^"']+)["']""", re.I)


def _local(tag):
    return tag.split("}")[-1] if isinstance(tag, str) else ""


def a_plat(h):
    """Chapeau d'un flux : du HTML, souvent en CDATA, remis à plat."""
    h = re.sub(r"(?i)<br\s*/?>", "\n", h or "")
    h = re.sub(r"(?i)</(p|div|li|h[1-6])>", "\n", h)
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    h = html.unescape(h).replace("\xa0", " ")
    return "\n".join(
        re.sub(r"[ \t]{2,}", " ", ln).strip() for ln in h.splitlines()
    ).strip()


def _date(val):
    if not val:
        return False
    try:
        dt = parsedate_to_datetime(val)
    except (TypeError, ValueError):
        dt = None
    if dt is None:
        try:
            dt = fields.Datetime.to_datetime(val[:19].replace("T", " "))
            return dt
        except ValueError:
            return False
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def lien_normalise(lien):
    lien = (lien or "").strip()
    lien = re.sub(r"[?#].*$", "", lien) if "utm_" in lien else lien
    return lien.rstrip("/")


def analyser(brut):
    """Éléments d'un flux RSS 2.0, RSS 1.0 ou Atom, en dictionnaires normalisés.

    La clé est l'identifiant le plus stable offert par le flux : dc:identifier,
    puis guid (ou id en Atom), puis le lien normalisé. Jamais le titre.
    """
    try:
        racine = etree.fromstring(brut, parser=_PARSER)
    except etree.XMLSyntaxError as exc:
        raise FluxErreur(f"Flux illisible : {exc}", passager=False) from exc
    if racine is None or _local(racine.tag) not in ("rss", "RDF", "feed"):
        raise FluxErreur("Ce n'est pas un flux RSS ou Atom.", passager=False)
    langue_flux = ""
    for n in racine.iter():
        if _local(n.tag) == "language" and n.text:
            langue_flux = n.text.strip().lower()
            break
    sortie = []
    for noeud in racine.iter():
        if _local(noeud.tag) not in ("item", "entry"):
            continue
        rec = {"sujets": [], "identifiant": "", "guid": ""}
        for ch in noeud:
            nom, val = _local(ch.tag), (ch.text or "").strip()
            if nom == "title":
                rec["titre"] = html.unescape(val)
            elif nom == "link":
                href = ch.get("href")
                if href:
                    if ch.get("rel", "alternate") == "alternate":
                        rec["lien"] = href.strip()
                else:
                    rec["lien"] = val
            elif nom in ("thumbnail", "content", "enclosure") and ch.get("url"):
                # media:thumbnail, media:content et enclosure : une image si
                # le flux le dit. Un balado ou une vidéo ne s'affiche pas.
                genre = (ch.get("type") or "").lower()
                if (nom == "thumbnail" or ch.get("medium") == "image"
                        or genre.startswith("image/")):
                    rec.setdefault("image", ch.get("url"))
            elif nom in ("description", "summary", "content", "encoded"):
                image = _IMG_RE.search(ch.text or "")
                if image:
                    rec.setdefault("image", html.unescape(image.group(1)))
                if not rec.get("resume") or nom == "description":
                    rec["resume"] = a_plat(ch.text or "")
            elif nom in ("pubDate", "published", "date"):
                rec["date"] = _date(val)
            elif nom == "updated" and not rec.get("date"):
                rec["date"] = _date(val)
            elif nom == "identifier":
                rec["identifiant"] = val
            elif nom in ("guid", "id"):
                rec["guid"] = val
            elif nom == "language":
                rec["langue"] = val.lower()
            elif nom in ("contributor", "creator"):
                rec["emetteur"] = html.unescape(val)
            elif nom == "source":
                # Un agrégateur (Google News) nomme ici le vrai diffuseur, et
                # son adresse : le lien de l'élément, lui, reste chez Google.
                if val:
                    rec.setdefault("emetteur", html.unescape(val))
                if re.match(r"^https?://", ch.get("url") or "", re.I):
                    rec["source_url"] = ch.get("url").strip()[:500]
            elif nom == "author":
                nom_auteur = next(
                    (a.text for a in ch if _local(a.tag) == "name"), None)
                rec["emetteur"] = html.unescape(nom_auteur or val)
            elif nom in ("subject", "category"):
                # Une catégorie RSS dotée d'un domaine est une classification
                # du diffuseur (code ISIN, symbole boursier), pas un sujet.
                if nom == "category" and ch.get("domain"):
                    continue
                terme = val or ch.get("term") or ""
                if terme:
                    rec["sujets"].append(html.unescape(terme))
        lien = rec.get("lien") or ""
        # 🔴 Un lien vient de l'extérieur et finit dans un href : seul http(s)
        # entre. Un « javascript: » posé par un flux piégé s'exécuterait sur le
        # domaine d'Odoo au clic (relecture adverse avant publication).
        if not re.match(r"^https?://", lien, re.I):
            continue
        cle = rec.pop("identifiant") or rec.pop("guid", "") or lien_normalise(lien)
        rec.pop("guid", None)
        if not cle or not lien:
            continue
        rec["cle"] = cle[:512]
        if not re.match(r"^https?://", rec.get("image") or "", re.I):
            rec.pop("image", None)
        rec.setdefault("titre", "(sans titre)")
        rec.setdefault("langue", langue_flux)
        sortie.append(rec)
    return sortie


class FluxSource(models.Model):
    _name = "bf.flux.source"
    _description = "Source de flux"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "name"
    _check_company_auto = True

    name = fields.Char("Nom", required=True, tracking=True)
    url = fields.Char("Adresse du flux", required=True, tracking=True)
    active = fields.Boolean(default=True, tracking=True)
    company_id = fields.Many2one(
        "res.company", string="Société", default=lambda s: s.env.company)
    cadence_minutes = fields.Integer(
        "Relever toutes les (min)", default=120, required=True, tracking=True,
        help="Réglez-la sur la vitesse à laquelle le flux renouvelle sa "
             "fenêtre : ce qui en sort entre deux relèves est perdu. La relève "
             "passe aux 15 minutes : une cadence plus courte n'y changerait rien.")
    langue_preferee = fields.Selection(
        [("fr", "Français"), ("en", "Anglais")], string="Langue préférée",
        default="fr",
        help="Quand un élément paraît en deux langues sous le même "
             "identifiant, cette version l'emporte, même si elle arrive après.")
    texte_complet = fields.Boolean(
        "Récupérer le texte complet",
        help="Va chercher la page de chaque élément. Le chapeau du flux ne "
             "suffit pas toujours à juger ni à lire.")
    coupures = fields.Text(
        "Couper le texte à partir de",
        help="Un début de ligne par ligne, sans égard à la casse. Le texte "
             "complet s'arrête à la première ligne qui commence ainsi : pièces "
             "jointes, « à lire aussi », mentions du diffuseur.")
    note = fields.Text("Note")
    rattrapage_du = fields.Boolean(
        "Première relève à venir", readonly=True,
        help="La prochaine relève réussie ramènera l'arriéré de la recherche : "
             "retenu, jamais diffusé ni alerté. Reposé à chaque changement "
             "d'adresse et à chaque réactivation.")
    sujet_id = fields.Many2one(
        "bf.flux.sujet", string="Sujet surveillé", readonly=True, index=True,
        ondelete="cascade",
        help="Source de recherche créée et tenue par un sujet surveillé : "
             "elle suit le sujet, ne se règle pas à la main.")

    derniere_releve = fields.Datetime("Dernière relève", readonly=True)
    prochaine_releve = fields.Datetime(
        "Prochaine relève", readonly=True, default=fields.Datetime.now)
    dernier_etat = fields.Selection(
        [("ok", "Relevé"), ("passager", "Échec passager"),
         ("definitif", "Échec définitif")],
        string="État", readonly=True)
    dernier_message = fields.Char("Dernier message", readonly=True)
    echecs_consecutifs = fields.Integer("Échecs consécutifs", readonly=True)
    derniers_vus = fields.Integer(
        "Éléments au dernier passage", readonly=True,
        help="Si ce nombre égale la taille de la fenêtre du flux et que tous "
             "sont nouveaux, la cadence est trop lente : des éléments ont pu "
             "sortir de la fenêtre sans être vus.")
    derniers_nouveaux = fields.Integer("Nouveaux au dernier passage", readonly=True)
    element_ids = fields.Many2many(
        "bf.flux.element", "bf_flux_element_source_rel", "source_id",
        "element_id", string="Éléments", readonly=True)
    element_count = fields.Integer(compute="_compute_element_count")
    liste_ids = fields.Many2many(
        "bf.flux.liste", "bf_flux_liste_source_rel", "source_id", "liste_id",
        string="Listes")

    _sql_constraints = [
        ("cadence_positive", "CHECK(cadence_minutes >= 15)",
         "La cadence doit être d'au moins 15 minutes : c'est le pas de la relève."),
    ]

    @api.depends("element_ids")
    def _compute_element_count(self):
        for src in self:
            src.element_count = len(src.element_ids)

    # ------------------------------------------------------------------ relève

    def _flux_telecharger(self, url, **kw):
        """Point d'accroche des essais : tout accès réseau passe par ici."""
        return telecharger(url, **kw)

    def _flux_verrouiller(self):
        """Verrou de relève : deux passages simultanés (cron et bouton) ne se
        marchent pas dessus. La source déjà prise est simplement sautée."""
        self.env.cr.execute(
            "SELECT id FROM bf_flux_source WHERE id = %s FOR UPDATE SKIP LOCKED",
            (self.id,))
        return bool(self.env.cr.fetchone())

    def action_relever(self):
        # Relever va chercher une adresse sur le réseau et écrit la source :
        # un geste de la gestion, pas un appel ouvert à tout le personnel.
        if not (self.env.su or self.env.user.has_group("bf_flux.group_flux_gestion")):
            raise AccessError(_("Relever une source est réservé à la gestion des flux."))
        for src in self:
            # Relever sans trier perdait les éléments : ils entraient sans
            # retenue et n'étaient plus jamais « touchés » ensuite.
            src._flux_relever()._flux_trier_et_diffuser()
        return True

    def _flux_relever(self):
        """Relève une source. Rend les éléments nouveaux ou remplacés."""
        self.ensure_one()
        maintenant = fields.Datetime.now()
        if not self._flux_verrouiller():
            return self.env["bf.flux.element"]
        vals = {
            "derniere_releve": maintenant,
            "prochaine_releve": maintenant + timedelta(minutes=self.cadence_minutes),
        }
        try:
            brut, _type = self._flux_telecharger(self.url)
            recs = analyser(brut)
        except FluxErreur as exc:
            vals.update(
                dernier_etat="passager" if exc.passager else "definitif",
                dernier_message=str(exc)[:250],
                echecs_consecutifs=self.echecs_consecutifs + 1)
            if exc.passager:
                # On revient vite : la fenêtre du flux, elle, continue de tourner.
                vals["prochaine_releve"] = maintenant + timedelta(
                    minutes=min(30, self.cadence_minutes))
            self.write(vals)
            _logger.info("Flux %s : %s", self.name, exc)
            if self.echecs_consecutifs == SEUIL_ALERTE:
                self._flux_alerter(str(exc))
            return self.env["bf.flux.element"]
        touches, nouveaux = self.env["bf.flux.element"]._flux_integrer(self, recs)
        vals.update(
            dernier_etat="ok", dernier_message=False, echecs_consecutifs=0,
            derniers_vus=len(recs), derniers_nouveaux=len(nouveaux))
        arriere = self.rattrapage_du
        if arriere:
            # Seule une relève RÉUSSIE solde l'arriéré : un 429 à la première
            # tentative ne doit pas faire passer la semaine suivante pour du neuf.
            vals["rattrapage_du"] = False
        self.write(vals)
        return touches.with_context(flux_rattrapage=True) if arriere else touches

    def _flux_alerter(self, message):
        """Une panne qui dure se dit : sans ça, « repris au prochain passage »
        peut durer des semaines sans que personne le voie."""
        self.ensure_one()
        texte = _(
            "%(n)s relèves de suite sans rien lire (%(msg)s). Ce qui sort de la "
            "fenêtre du flux pendant la panne est perdu : vérifiez l'adresse, "
            "ou si l'hôte refuse nos requêtes.",
            n=self.echecs_consecutifs, msg=message)
        self.message_post(body=texte, subtype_xmlid="mail.mt_comment")
        self.activity_schedule(
            "mail.mail_activity_data_todo",
            summary=_("Flux en panne : %s", self.name), note=texte,
            user_id=self.create_uid.id if self.create_uid.active else self.env.uid)

    @api.model
    def _cron_releve(self, limite=50):
        """Relève les sources dues, puis trie et diffuse ce qui est entré."""
        dues = self.search([
            ("active", "=", True),
            ("prochaine_releve", "<=", fields.Datetime.now()),
        ], order="prochaine_releve", limit=limite)
        en_essai = bool(odoo_module.current_test)
        for src in dues:
            try:
                src._flux_relever()._flux_trier_et_diffuser()
            except Exception:
                _logger.exception("Relève du flux %s", src.name)
                if not en_essai:
                    self.env.cr.rollback()
                continue
            if not en_essai:
                # Une source relevée est acquise, même si la suivante échoue.
                self.env.cr.commit()
        self.env["bf.flux.element"]._cron_texte_complet()
        return True

    def action_voir_elements(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Éléments de %s", self.name),
            "res_model": "bf.flux.element",
            "view_mode": "list,form",
            "domain": [("source_ids", "in", self.ids)],
        }

    @api.constrains("url")
    def _check_url(self):
        for src in self:
            if not re.match(r"^https?://", src.url or "", re.I):
                raise ValidationError(_("L'adresse du flux doit commencer par http:// ou https://."))

    @staticmethod
    def _pause():
        time.sleep(PAUSE)
