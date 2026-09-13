"""Lire un calendrier tenu à la main, et en proposer les absences.

Avant d'avoir un module, on note les vacances de ses clients quelque part. Le
calendrier réel qui a servi de modèle porte six entrées sous deux formes, et
c'est de là que viennent toutes les règles d'ici :

    🌴 Prénom - Société                 un marqueur seul, au jour du départ
    Prénom (Société) - vacances         idem
    Vacances - Prénom Nom               un départ...
    Retour de vacances Prénom           ...et son retour, quelques jours plus loin
    Vacances - Société - Lieu           un départ, avec une précision de plus
    Retour de vacances Société          son retour

⚠️ Le module ne CRÉE aucun contact et n'écrit rien dans le calendrier. Une
entrée dont le nom ne correspond à personne est rapportée telle quelle :
appeler « David » le mauvais David enverrait le courrier d'un client à un autre.
"""

import logging
import re
import unicodedata
from datetime import date, timedelta

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Ce qui marque un RETOUR plutôt qu'un départ.
RX_RETOUR = re.compile(r"^\s*(retour(\s+de\s+vacances)?|de\s+retour|back)\b", re.I)
# Les préfixes qui n'appartiennent pas au nom de la personne.
RX_PREFIXE = re.compile(r"^\s*(vacances|conges?|absence|absent[e]?|fermeture|holidays?)\s*[-:]?\s*", re.I)
# Une entrée peut commencer par une frimousse : elle n'est pas un nom.
RX_ORNEMENT = re.compile(r"^[\W_]+", re.UNICODE)
# 🔴 Le mot qui dit la nature peut être en SUFFIXE autant qu'en préfixe :
# « MMDB - vacances » autant que « Vacances - MMDB ». Sans ça, « vacances »
# devient un indice de société et empêche l'appariement. Vu en lisant le vrai
# calendrier, pas en imaginant ses formes.
NATURES = {
    "vacances": "vacation", "vacance": "vacation", "conge": "leave",
    "conges": "leave", "absence": "other", "absent": "other",
    "absente": "other", "fermeture": "closure", "formation": "training",
    "congres": "training", "holidays": "vacation", "holiday": "vacation",
}
# 🔴 Au-delà de cette distance, un retour n'appartient plus au départ qu'il
# suit : c'est un autre voyage. Sans cette borne, un unique « Retour de
# vacances X » fermait TROIS départs du même raccourci, dont un vieux de
# quatorze mois. Vu en apprenant un raccourci au module, pas avant.
JOURS_MAX_ENTRE_DEPART_ET_RETOUR = 70
# La société notée entre parenthèses : « Prénom (Société) ».
RX_PARENTHESE = re.compile(r"^(?P<nom>[^(]+)\((?P<societe>[^)]+)\)\s*$")


def _sansaccent(texte):
    texte = unicodedata.normalize("NFD", texte or "")
    return "".join(c for c in texte if unicodedata.category(c) != "Mn").lower().strip()


class BfAbsenceCalendarSource(models.Model):
    _name = "bf.absence.calendar.source"
    _description = "Calendrier d'absences à lire"
    _order = "sequence, id"

    name = fields.Char(string="Nom", required=True,
                       help="Pour vous. Par exemple « Vacances des clients ».")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    config_key = fields.Selection(
        selection="_selection_config",
        string="Connexion Nextcloud",
        # ⚠️ Pas obligatoire à la création : on doit pouvoir poser le
        # calendrier et ses raccourcis avant d'avoir choisi la connexion. Le
        # manque est dit clairement au moment de lire, pas au moment d'écrire.
        help="La configuration de synchronisation déjà en place fournit "
             "l'adresse, le compte et le mot de passe d'application. Aucun "
             "identifiant neuf n'est demandé.")
    calendar_slug = fields.Char(
        string="Identifiant du calendrier",
        required=True,
        help="Le dernier morceau de l'adresse du calendrier, par exemple "
             "« migrated-vacances-cts ».")
    days_back = fields.Integer(
        string="Remonter de (jours)", default=120, required=True,
        help="Une absence notée il y a plus de quatre mois est déjà finie.")
    days_ahead = fields.Integer(
        string="Aller jusqu'à (jours)", default=400, required=True)
    nature = fields.Selection(
        selection=[
            ("vacation", "Vacances"),
            ("leave", "Congé"),
            ("closure", "Fermeture"),
            ("training", "Formation ou congrès"),
            ("other", "Autre"),
        ],
        string="Nature par défaut", default="vacation", required=True)
    alias_ids = fields.One2many(
        comodel_name="bf.absence.calendar.alias",
        inverse_name="source_id",
        string="Raccourcis",
        help="Ce qu'on écrit dans le calendrier et qui n'est le nom de "
             "personne : des initiales, un surnom, le nom d'un dossier.")
    last_run = fields.Datetime(string="Dernière lecture", readonly=True)
    last_message = fields.Char(string="Résultat", readonly=True)

    # ------------------------------------------------------------------
    # La connexion, retrouvée sans en dépendre
    # ------------------------------------------------------------------
    MODELE_CONFIG = "nextcloud.calendar.sync.config"

    @api.model
    def _configs(self):
        """Les connexions Nextcloud disponibles, ou rien.

        🔴 Le modèle est cherché dans le registre plutôt qu'exigé au manifeste :
        le module qui le porte réclame `googleapiclient`, que toutes les images
        ne portent pas.
        """
        if self.MODELE_CONFIG not in self.env:
            return self.env["base"].browse()
        return self.env[self.MODELE_CONFIG].sudo().search([])

    @api.model
    def _selection_config(self):
        try:
            return [(str(c.id), "%s (%s)" % (c.name or c.id, c.nextcloud_user or "?"))
                    for c in self._configs()]
        except Exception:  # noqa: BLE001 - une liste vide vaut mieux qu'un écran mort
            return []

    def _config(self):
        self.ensure_one()
        if self.MODELE_CONFIG not in self.env:
            raise UserError(_(
                "La synchronisation de calendrier Nextcloud n'est pas "
                "installée : c'est elle qui porte l'adresse du serveur et le "
                "mot de passe d'application."))
        config = self.env[self.MODELE_CONFIG].sudo().browse(
            int(self.config_key or 0)).exists()
        if not self.config_key:
            raise UserError(_(
                "Choisir la connexion Nextcloud avant de lire le calendrier."))
        if not config:
            raise UserError(_("La connexion Nextcloud choisie n'existe plus."))
        return config

    # ------------------------------------------------------------------
    # La lecture CalDAV
    # ------------------------------------------------------------------
    def _url(self):
        self.ensure_one()
        config = self._config()
        base = (config.nextcloud_base_url or "").rstrip("/")
        user = config.nextcloud_user or ""
        if not base or not user:
            raise UserError(_(
                "La connexion Nextcloud n'a ni adresse ni compte."))
        return "%s/remote.php/dav/calendars/%s/%s/" % (
            base, user, (self.calendar_slug or "").strip("/"))

    def _fetch(self):
        """Rend le texte des VEVENT du calendrier sur la fenêtre demandée.

        ⚠️ Lecture seule, et une seule requête : un REPORT CalDAV borné aux
        dates. Rien n'est écrit, et le calendrier n'entre pas dans l'agenda
        d'Odoo.
        """
        self.ensure_one()
        config = self._config()
        mdp = config._decrypt_value(config.nextcloud_app_password_encrypted)
        if not mdp:
            raise UserError(_(
                "La connexion Nextcloud n'a pas de mot de passe d'application."))
        today = fields.Date.context_today(self)
        debut = (today - timedelta(days=max(0, self.days_back))).strftime("%Y%m%dT000000Z")
        fin = (today + timedelta(days=max(1, self.days_ahead))).strftime("%Y%m%dT235959Z")
        corps = (
            '<?xml version="1.0" encoding="utf-8" ?>'
            '<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
            '<d:prop><d:getetag/><c:calendar-data/></d:prop>'
            '<c:filter><c:comp-filter name="VCALENDAR">'
            '<c:comp-filter name="VEVENT">'
            '<c:time-range start="%s" end="%s"/>'
            '</c:comp-filter></c:comp-filter></c:filter>'
            '</c:calendar-query>' % (debut, fin)
        )
        rep = requests.request(
            "REPORT", self._url(),
            data=corps.encode("utf-8"),
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
            auth=(config.nextcloud_user, mdp),
            timeout=60,
        )
        if rep.status_code >= 300:
            raise UserError(_(
                "Le calendrier a répondu %(code)s. Vérifier l'identifiant du "
                "calendrier et la connexion.", code=rep.status_code))
        return rep.text

    @api.model
    def _parse_ics(self, texte):
        """Les entrées du calendrier : (uid, jour, titre), toutes journées."""
        sorties = []
        for bloc in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", texte or "", re.S):
            # ⚠️ Une ligne ICS se replie sur la suivante avec une espace en
            # tête : sans ce recollage, un titre long est tronqué en silence.
            plat = re.sub(r"\r?\n[ \t]", "", bloc)
            uid = re.search(r"^UID:(.+)$", plat, re.M)
            titre = re.search(r"^SUMMARY[^:]*:(.+)$", plat, re.M)
            debut = re.search(r"^DTSTART[^:]*:(\d{8})", plat, re.M)
            if not (uid and titre and debut):
                continue
            brut = debut.group(1)
            try:
                jour = date(int(brut[:4]), int(brut[4:6]), int(brut[6:8]))
            except ValueError:
                continue
            propre = titre.group(1).replace("\\,", ",").replace("\\;", ";").strip()
            sorties.append((uid.group(1).strip(), jour, propre))
        return sorted(sorties, key=lambda t: t[1])

    # ------------------------------------------------------------------
    # La lecture des titres
    # ------------------------------------------------------------------
    @api.model
    def _cle_personne(self, titre):
        """(clé de la personne, indices, est_un_retour) tirés d'un titre."""
        propre = RX_ORNEMENT.sub("", titre or "").strip()
        retour = bool(RX_RETOUR.match(propre))
        if retour:
            propre = RX_RETOUR.sub("", propre, count=1).strip(" -:")
        else:
            propre = RX_PREFIXE.sub("", propre, count=1).strip(" -:")
        morceaux = [m.strip() for m in re.split(r"\s+-\s+", propre) if m.strip()]
        # Les mots de nature, où qu'ils soient, ne sont pas des indices.
        morceaux = [m for m in morceaux if _sansaccent(m) not in NATURES]
        if not morceaux:
            return "", [], retour
        tete, indices = morceaux[0], morceaux[1:]
        entre = RX_PARENTHESE.match(tete)
        if entre:
            tete = entre.group("nom").strip()
            indices = [entre.group("societe").strip()] + indices
        return tete, indices, retour

    @api.model
    def _nature_du_titre(self, titre, defaut):
        """La nature nommée dans le titre, sinon celle de la source."""
        mots = re.split(r"[\s\-:()]+", _sansaccent(titre))
        for mot in mots:
            if mot in NATURES:
                return NATURES[mot]
        return defaut

    def _apparier(self, cle, indices):
        """Le contact qui porte ce nom, ou rien. Ne crée jamais personne.

        ⚠️ On refuse une correspondance ambiguë. « David » tout seul peut
        désigner trois personnes, et se tromper enverrait le courrier d'un
        client à un autre.

        🔴 Les raccourcis appris passent EN PREMIER. Un sigle ne ressemble à
        aucun nom : ce sont des initiales, et rapprocher des initiales d'un nom
        qui commence pareil serait exactement l'erreur qu'on refuse ailleurs.
        """
        Partner = self.env["res.partner"]
        cle_n = _sansaccent(cle)
        if not cle_n:
            return Partner
        for alias in self.alias_ids:
            if _sansaccent(alias.label) == cle_n:
                return alias.partner_id
        exact = Partner.search([("name", "=ilike", cle)], limit=2)
        if len(exact) == 1:
            return exact
        candidats = Partner.search([("name", "ilike", cle)], limit=20)
        candidats = candidats.filtered(
            lambda p: _sansaccent(p.name).split() and
            _sansaccent(p.name).startswith(cle_n.split()[0]))
        if indices:
            indice_n = _sansaccent(indices[0])
            serres = candidats.filtered(
                lambda p: indice_n in _sansaccent(p.parent_id.name or "")
                or indice_n in _sansaccent(p.name))
            if len(serres) == 1:
                return serres
            candidats = serres or candidats
        return candidats if len(candidats) == 1 else Partner

    # ------------------------------------------------------------------
    # La passe
    # ------------------------------------------------------------------
    def action_read(self):
        """Lit le calendrier et propose ce qu'il y trouve."""
        Suggestion = self.env["bf.partner.absence.suggestion"]
        total_poses = 0
        for source in self:
            entrees = source._parse_ics(source._fetch())
            departs, retours = [], []
            for uid, jour, titre in entrees:
                cle, indices, est_retour = source._cle_personne(titre)
                if not cle:
                    continue
                if est_retour:
                    retours.append((_sansaccent(cle), jour))
                else:
                    departs.append((uid, jour, titre, cle, indices))

            poses, sans_contact, deja = 0, [], 0
            # 🔴 Un retour se consomme UNE fois. Il ferme le départ qu'il suit
            # de plus près, pas tous ceux d'avant. Les départs sont donc
            # parcourus dans l'ordre, et le retour retenu est retiré du lot.
            departs.sort(key=lambda d: d[1])
            restants = list(retours)
            for uid, jour, titre, cle, indices in departs:
                if Suggestion.search_count([("calendar_uid", "=", uid)]):
                    deja += 1
                    continue
                partner = source._apparier(cle, indices)
                if not partner:
                    sans_contact.append(titre)
                    continue
                # 🔴 Le retour le plus proche APRÈS le départ, apparié sur un
                # PRÉFIXE : « Retour de vacances François » ne répète pas le
                # nom de famille de « Vacances - François Béland ». L'appariement
                # à l'identique laissait la période sans fin.
                depart_n = _sansaccent(cle)
                candidats = [
                    (k, d) for k, d in restants
                    if d > jour
                    and (d - jour).days <= JOURS_MAX_ENTRE_DEPART_ET_RETOUR
                    and (depart_n.startswith(k) or k.startswith(depart_n))
                ]
                fin = False
                if candidats:
                    retenu = min(candidats, key=lambda kd: kd[1])
                    restants.remove(retenu)
                    fin = retenu[1] - timedelta(days=1)
                if self.env["bf.partner.absence"].search_count([
                    ("partner_id", "=", partner.id),
                    ("date_from", "<=", fin or jour),
                    ("date_to", ">=", jour),
                ]):
                    deja += 1
                    continue
                Suggestion.create({
                    "partner_id": partner.id,
                    "date_from": jour,
                    "date_to": fin or False,
                    "nature": source._nature_du_titre(titre, source.nature),
                    "detected_by": "calendrier",
                    "read_by": "calendrier" if fin else False,
                    "calendar_uid": uid,
                    "calendar_label": titre[:120],
                })
                poses += 1
            total_poses += poses
            message = _(
                "%(poses)s proposée(s), %(deja)s déjà connue(s), "
                "%(sans)s sans contact reconnu.",
                poses=poses, deja=deja, sans=len(sans_contact))
            if sans_contact:
                message += " " + _("Non reconnus : %s") % ", ".join(sans_contact[:5])
            source.write({"last_run": fields.Datetime.now(),
                          "last_message": message[:250]})
            _logger.info("bf_contact_absence_calendar: %s -> %s",
                         source.name, message)
        if not total_poses:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {"type": "info", "sticky": False,
                           "message": self[:1].last_message or _("Rien de neuf.")},
            }
        return {
            "type": "ir.actions.act_window",
            "name": _("Absences proposées"),
            "res_model": "bf.partner.absence.suggestion",
            "view_mode": "list,form",
            "domain": [("calendar_uid", "!=", False), ("state", "=", "pending")],
        }

    @api.model
    def _cron_read_all(self):
        sources = self.search([])
        if sources:
            sources.action_read()
        return len(sources)
