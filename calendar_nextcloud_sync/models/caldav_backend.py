# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Direct CalDAV push backend for Nextcloud calendars.

Historique. Le sens Nextcloud -> Odoo a toujours parlé CalDAV en direct
(``PROPFIND`` pour le jeton de synchro, ``REPORT`` pour les VEVENT, cf.
``nextcloud_sync_config``). Seul le sens Odoo -> Nextcloud passait par un
webhook n8n, qui construisait l'ICS et faisait le ``PUT`` à notre place. Ce
maillon s'est révélé le point faible de la chaîne :

* il est muet quand il échoue à moitié. Le 2026-08-19, un ``DELETE`` a reçu un
  2xx de n8n alors que l'événement était toujours dans Nextcloud ; le resync
  suivant l'a donc ressuscité ;
* il ne renvoie ni ``href`` ni ``ETag``, donc un événement fraîchement poussé
  restait sans ``x_caldav_href`` jusqu'au prochain pull. Cette fenêtre est
  exactement ce qui a permis au balayage d'orphelins de détruire des
  réservations confirmées ;
* il fait dépendre l'agenda d'un locataire d'un conteneur qui vit dans la pile
  d'un AUTRE locataire.

Ce backend fait le ``PUT``/``DELETE`` depuis Odoo, avec les mêmes identifiants
que le pull. Il est calqué sur ``calendar.google.backend``, qui rend déjà le
couple ``(id distant, etag)`` à ses appelants.

⚠️ Idempotence. ``push_create`` et ``push_update`` font le MÊME ``PUT`` sans
en-tête conditionnel. C'est voulu : la ressource est nommée d'après l'UID, donc
rejouer une création écrase proprement au lieu d'échouer en 412. Le filet
``_retry_failed_pushes`` rejoue justement des créations sur des événements qui
peuvent déjà exister côté serveur.
"""

import logging
import re
from datetime import timedelta

import pytz
import requests

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Au-delà, un serveur CalDAV lent vaut mieux qu'une requête HTTP qui pend :
# l'appelant journalise et le cron de reprise repassera.
CALDAV_TIMEOUT = 30

# Nextcloud ne poste aucun courriel de planification (iMIP) pour une écriture
# qui porte cet en-tête : `Schedule\Plugin::calendarObjectChange` et
# `beforeUnbind` sortent tout de suite (apps/dav, NC 34.0.4, comparaison
# stricte à la chaîne « false »). Sans lui, chaque rencontre créée dans Odoo
# partait DEUX fois chez ses invités : l'invitation d'Odoo, puis celle du
# greffon de Nextcloud une seconde plus tard (constaté en production). Même
# décision que le dos Google, qui écrit en `sendUpdates="none"` : Odoo est le
# seul à parler aux invités.
NO_SCHEDULING_HEADERS = {"x-nc-scheduling": "false"}

# VTIMEZONE sérialisés, par nom IANA. Les règles d'un fuseau ne changent qu'à
# la mise à jour de la base tzdata, donc au redémarrage du processus : un cache
# de processus n'a pas besoin d'invalidation. `None` mémorise un fuseau dont la
# sérialisation a échoué, pour ne pas la retenter à chaque poussée.
_VTIMEZONE_CACHE = {}

# ----------------------------------------------------------------------
# Retouche d'un .ics déjà dans Nextcloud
#
# Un événement né dans Nextcloud porte ce qu'Odoo ne modélise pas : alarmes
# posées au téléphone, propriétés X- des clients, CREATED, pièces jointes,
# exceptions d'une série. Le réécrire depuis les champs d'Odoo (`build_ics`)
# les effacerait. Une retouche relit donc la ressource, ne remplace QUE les
# propriétés qu'elle vise et garde tout le reste, octet pour octet à part le
# pliage des lignes.
# ----------------------------------------------------------------------

# Ce qu'une retouche réécrit dans un VEVENT. DURATION y est parce qu'un DTEND
# posé à côté d'un DURATION resté en place rendrait le VEVENT invalide
# (RFC 5545 §3.6.1 : l'un OU l'autre).
RETOUCHE_PROPS = ("DTSTART", "DTEND", "DURATION", "SUMMARY", "STATUS",
                  "TRANSP", "SEQUENCE", "DTSTAMP", "LAST-MODIFIED")

# Ce qu'une exception ne recopie pas de sa série : une instance surchargée n'a
# pas de règle (RFC 5545 §3.8.5.3), et son RECURRENCE-ID est le sien.
PROPS_DE_SERIE = ("RRULE", "RDATE", "EXDATE", "EXRULE", "RECURRENCE-ID")


# Confidentialité d'Odoo -> CLASS du VEVENT (RFC 5545 §3.8.1.3). La lecture
# fait l'inverse dans `calendar.event._bf_nc_vals_classe`.
CLASSE_DE_CONFIDENTIALITE = {
    "public": "PUBLIC", "private": "PRIVATE", "confidential": "CONFIDENTIAL"}


def decouper_liste(valeur):
    """Les éléments d'une valeur TEXT multiple (``CATEGORIES:a,b\\,c``).

    RFC 5545 §3.3.11 : la virgule sépare, ``\\,`` en est une dans un élément.
    Couper sur toutes les virgules faisait deux étiquettes de « Suivi, client ».
    Chaque élément sort déséchappé et sans espaces autour ; les vides tombent.
    """
    elements, courant, i = [], [], 0
    valeur = valeur or ""
    while i < len(valeur):
        car = valeur[i]
        if car == "\\" and i + 1 < len(valeur):
            suivant = valeur[i + 1]
            courant.append("\n" if suivant in "nN" else suivant)
            i += 2
            continue
        if car == ",":
            elements.append("".join(courant))
            courant = []
        else:
            courant.append(car)
        i += 1
    elements.append("".join(courant))
    return [e.strip() for e in elements if e.strip()]


def deplier(texte):
    """Lignes logiques d'un .ics (RFC 5545 §3.1), sans fin de ligne.

    ⚠️ Coupé sur CRLF/LF seulement, jamais par ``splitlines()`` : celui-ci coupe
    aussi sur U+2028, U+2029 et U+0085, qu'une description collée depuis un
    traitement de texte peut porter. La retouche réécrivait alors la moitié de
    la description en propriété parasite, ou la faisait refuser (relecture
    adverse de #essai, M1). Les lignes vides, qui ne sont pas des lignes de
    contenu, sont écartées.
    """
    texte = re.sub(r"\r?\n[ \t]", "", texte or "")
    return [ligne for ligne in re.split(r"\r?\n", texte) if ligne]


def _separateur(ligne):
    """Index du « : » qui sépare la tête de la valeur, ou -1.

    Le premier hors guillemets : une valeur de paramètre entre guillemets peut
    en porter (``SUMMARY;ALTREP="http://…":Titre``), et couper au premier
    cassait la ligne réécrite.

    Un guillemet n'ouvre une valeur que juste après « = » ou « , » (RFC 5545
    §3.2) ; ailleurs (``X-NOTE=a"b``, ``CN="O\"Hara"``) il n'est qu'un caractère.
    Sans « : » hors guillemets, le premier « : » : une ligne mal formée se lit
    comme avant, au lieu d'être sautée.
    """
    entre = False
    for i, car in enumerate(ligne):
        if entre:
            if car == '"':
                entre = False
        elif car == '"' and i and ligne[i - 1] in "=,":
            entre = True
        elif car == ":":
            return i
    return ligne.find(":")


def nom_prop(ligne):
    i = _separateur(ligne)
    tete = ligne[:i] if i >= 0 else ligne
    return tete.split(";", 1)[0].strip().upper()


def params_prop(ligne):
    i = _separateur(ligne)
    tete = ligne[:i] if i >= 0 else ligne
    return tete[len(tete.split(";", 1)[0]):]


def valeur_prop(ligne):
    i = _separateur(ligne)
    return ligne[i + 1:] if i >= 0 else ""


def blocs_vevent(lignes):
    """``[(début, fin)]`` des VEVENT de premier niveau, bornes comprises.

    Un VALARM imbriqué porte lui aussi des BEGIN/END : seule la profondeur dit
    lequel des END ferme le VEVENT.
    """
    blocs, debut, profondeur = [], None, 0
    for i, ligne in enumerate(lignes):
        s = ligne.strip().upper()
        if debut is None:
            if s == "BEGIN:VEVENT":
                debut, profondeur = i, 0
            continue
        if s.startswith("BEGIN:"):
            profondeur += 1
        elif s.startswith("END:"):
            if profondeur == 0:
                if s == "END:VEVENT":
                    blocs.append((debut, i))
                debut = None
            else:
                profondeur -= 1
    return blocs


def props_de_tete(bloc):
    """``[(index, ligne)]`` des propriétés du VEVENT lui-même, hors VALARM."""
    out, profondeur = [], 0
    for i, ligne in enumerate(bloc[1:-1], start=1):
        s = ligne.strip().upper()
        if s.startswith("BEGIN:"):
            profondeur += 1
        elif s.startswith("END:"):
            profondeur = max(0, profondeur - 1)
        elif profondeur == 0:
            out.append((i, ligne))
    return out


def retoucher_bloc(bloc, nouvelles, retirer):
    """Le VEVENT ``bloc`` sans ses propriétés ``retirer``, plus ``nouvelles``.

    Les nouvelles lignes se placent juste après UID (sinon après BEGIN) ; les
    composants imbriqués et toutes les autres propriétés restent où ils sont.
    """
    retirer = {n.upper() for n in retirer}
    a_retirer = {i for i, ligne in props_de_tete(bloc) if nom_prop(ligne) in retirer}
    garde = [ligne for i, ligne in enumerate(bloc) if i not in a_retirer]
    position = 1
    for i, ligne in props_de_tete(garde):
        if nom_prop(ligne) == "UID":
            position = i + 1
            break
    return garde[:position] + list(nouvelles) + garde[position:]


class RessourceInattendue(Exception):
    """La ressource Nextcloud n'a pas la forme qu'Odoo lui prête.

    Une série là où Odoo voit un événement simple, un événement simple là où
    il voit une occurrence, une ressource sans maître. Rien n'est écrit, et ce
    n'est PAS « rien à changer » : l'appelant le dit à l'usager (relecture
    adverse de #essai, M5 : ces cas passaient pour des succès).
    """


def garder_params(bloc, nouvelles, noms=("SUMMARY",)):
    """Reporte sur les lignes neuves les paramètres des lignes qu'elles remplacent.

    Un SUMMARY;LANGUAGE=fr-CA réécrit sans son paramètre perdait sa langue.
    """
    anciens = {nom_prop(ligne): params_prop(ligne) for _i, ligne in props_de_tete(bloc)
               if nom_prop(ligne) in noms}
    sortie = []
    for ligne in nouvelles:
        nom = nom_prop(ligne)
        if nom in anciens and anciens[nom] and not params_prop(ligne):
            ligne = "%s%s:%s" % (nom, anciens[nom], valeur_prop(ligne))
        sortie.append(ligne)
    return sortie


def journee_de(params):
    """La série est-elle en dates entières (DTSTART;VALUE=DATE) ?"""
    return "VALUE=DATE" in (params or "").upper() and "VALUE=DATE-TIME" not in (params or "").upper()


class CalDavBackend(models.AbstractModel):
    """Poussée directe d'un calendar.event vers une collection CalDAV."""

    _name = "calendar.caldav.backend"
    _description = "Nextcloud CalDAV Push Backend"

    # ------------------------------------------------------------------
    # Adressage de la ressource
    # ------------------------------------------------------------------

    @api.model
    def _resource_name(self, event):
        """Nom de fichier .ics de l'événement dans la collection.

        On garde la convention posée par n8n (partie locale de l'UID + .ics)
        pour que les événements déjà en place restent adressables après la
        bascule : sinon un update créerait un DOUBLON à côté de l'original.
        """
        if not event.x_nc_uid:
            return None
        return event.x_nc_uid.split("@")[0] + ".ics"

    @api.model
    def _resource_url(self, config, event):
        """(url absolue, href) de la ressource, ou (None, None)."""
        href = event.x_caldav_href
        if href:
            if href.startswith("http"):
                url = href
            else:
                url = (config.nextcloud_base_url or "").rstrip("/") + href
            return (url, href) if self._url_de_la_collection(config, url) else (None, None)
        name = self._resource_name(event)
        if not name or not config.caldav_url:
            return None, None
        url = config.caldav_url.rstrip("/") + "/" + name
        return (url, None) if self._url_de_la_collection(config, url) else (None, None)

    @api.model
    def _url_de_la_collection(self, config, url):
        """L'URL désigne-t-elle une ressource de la collection de ``config`` ?

        🔴 `x_caldav_href` et `x_nc_uid` s'écrivent par tout usager interne, et
        chaque requête vers cette URL porte le mot de passe d'application de la
        configuration. Une adresse absolue vers un autre hôte le livrait à qui
        l'avait posée. Même hôte, même schéma, sous
        le chemin de la collection, sans segment « . » ni « .. ».
        """
        from urllib.parse import unquote, urlsplit

        base = urlsplit((config.nextcloud_base_url or "").rstrip("/"))
        cible = urlsplit(url or "")
        # Comparés décodés : Nextcloud rend les href encodés (%20, %C3%A9), la
        # configuration garde le chemin tel qu'on l'a saisi. Et sans la casse :
        # Nextcloud rend celle de la requête, et changer la casse de
        # `caldav_path` aurait fait refuser tous les href déjà stockés. L'hôte
        # est la vraie frontière : le mot de passe vaut pour tout le compte.
        collection = unquote(base.path.rstrip("/") + "/" + (config.caldav_path or "").strip("/") + "/").lower()
        chemin = unquote(cible.path).lower()
        segments = [unquote(s) for s in cible.path.split("/")]
        sure = (
            bool(base.netloc)
            and (cible.scheme.lower(), cible.netloc.lower()) == (base.scheme.lower(), base.netloc.lower())
            and chemin.startswith(collection)
            and len(chemin) > len(collection)
            and not cible.query and not cible.fragment
            and not any(s in (".", "..") or "/" in s or "\\" in s for s in segments)
        )
        if not sure:
            _logger.warning(
                "Config %s : adresse de ressource hors de la collection refusée (%s)",
                config.display_name, url)
        return sure

    # ------------------------------------------------------------------
    # Fabrication du VEVENT
    # ------------------------------------------------------------------

    @api.model
    def _escape_ics(self, value):
        """Échappement texte RFC 5545 §3.3.11.

        L'inverse de ``_unescape_ics`` côté lecture. La barre oblique inverse
        se traite EN PREMIER, sinon on ré-échapperait les barres qu'on vient
        d'introduire.
        """
        if not value:
            return ""
        return (
            str(value)
            .replace("\\", "\\\\")
            .replace(";", "\\;")
            .replace(",", "\\,")
            .replace("\r\n", "\\n")
            .replace("\n", "\\n")
            .replace("\r", "\\n")
        )

    @api.model
    def _fold(self, line):
        """Pliage RFC 5545 §3.1 à 75 octets, en coupant sur les OCTETS.

        Compter en caractères couperait un caractère accentué en deux au
        mauvais endroit, et le déplieur d'en face recollerait des octets
        invalides. On découpe donc l'encodage UTF-8 en veillant à ne pas
        scinder une séquence multi-octets.
        """
        raw = line.encode("utf-8")
        if len(raw) <= 75:
            return line
        chunks = []
        start = 0
        limit = 75
        while start < len(raw):
            end = min(start + limit, len(raw))
            # Ne jamais couper au milieu d'une séquence UTF-8 : les octets de
            # continuation valent 0b10xxxxxx.
            while end > start and end < len(raw) and (raw[end] & 0xC0) == 0x80:
                end -= 1
            chunks.append(raw[start:end].decode("utf-8"))
            start = end
            limit = 74  # les lignes suivantes portent une espace d'indentation
        return "\r\n ".join(chunks)

    @api.model
    def _ics_datetime(self, value):
        """Datetime naïf-UTC d'Odoo -> forme UTC de l'ICS."""
        return fields.Datetime.to_datetime(value).strftime("%Y%m%dT%H%M%SZ")

    # ------------------------------------------------------------------
    # Fuseau de l'événement
    # ------------------------------------------------------------------

    @api.model
    def _ics_tzname(self, event):
        """Nom IANA dans lequel écrire DTSTART/DTEND, ou ``None`` pour l'UTC.

        Un ``DTSTART:…Z`` est JUSTE — l'instant est le bon, et tout client
        l'affiche à l'heure locale du lecteur. Mais il ne porte aucun fuseau,
        et Nextcloud titre alors la fiche « 21:00 UTC » en reléguant l'heure
        réelle sur une seconde ligne en gris. Personne ne raisonne en UTC :
        l'agenda annonce une heure à laquelle la rencontre n'a pas lieu.

        Le fuseau qui a du sens est celui dans lequel la rencontre a été
        convenue. Sur une réservation, c'est celui où la personne a CHOISI son
        créneau (``resource.booking`` sait le calculer); sinon celui de
        l'organisateur, puis le défaut de l'instance.

        ⚠️ Lien MOU vers ``bf_appointment`` : on interroge la réservation par
        ``hasattr`` plutôt que par un import. Ce module se déploie sur des
        locataires qui n'ont pas la prise de rendez-vous, et un import dur y
        casserait la synchro d'agenda entière.
        """
        candidates = []
        if "resource_booking_ids" in event._fields:
            booking = event.sudo().resource_booking_ids[:1]
            if booking and hasattr(booking, "_get_booker_display_tz"):
                try:
                    candidates.append(booking._get_booker_display_tz())
                except Exception:  # pragma: no cover - fuseau illisible
                    _logger.warning(
                        "Fuseau du demandeur illisible sur la réservation %s",
                        booking.id, exc_info=True,
                    )
        candidates.append(event.user_id.tz)
        candidates.append(self.env.user.tz)
        tzname = self.env["bf.timezone"].resolve(candidates)
        # UTC n'a pas de VTIMEZONE utile : on retombe alors sur la forme
        # `…Z`, qui dit exactement la même chose en plus court.
        return tzname if tzname and tzname != "UTC" else None

    @api.model
    def _vtimezone_lines(self, tzname):
        """Lignes du VTIMEZONE d'un fuseau, ou ``[]`` si on ne sait pas le faire.

        ⚠️ Un ``TZID`` sans VTIMEZONE correspondant vaut, au sens de la RFC 5545
        §3.2.19, une heure FLOTTANTE : le rendez-vous se met alors à l'heure du
        lecteur au lieu de rester au même instant. Les deux vont donc ensemble,
        et l'absence de l'un annule l'autre — d'où le repli en UTC chez
        l'appelant plutôt qu'un TZID orphelin.
        """
        if tzname in _VTIMEZONE_CACHE:
            return list(_VTIMEZONE_CACHE[tzname] or [])
        lines = None
        try:
            import pytz
            import vobject

            block = vobject.icalendar.TimezoneComponent(
                tzinfo=pytz.timezone(tzname)
            ).serialize()
            lines = [ln for ln in block.replace("\r\n", "\n").split("\n") if ln]
            if not lines or not lines[0].startswith("BEGIN:VTIMEZONE"):
                lines = None
        except Exception:  # pragma: no cover - fuseau inconnu / lib absente
            _logger.warning(
                "VTIMEZONE non sérialisable pour %s, repli en UTC",
                tzname, exc_info=True,
            )
            lines = None
        _VTIMEZONE_CACHE[tzname] = lines
        return list(lines or [])

    @api.model
    def build_ics(self, event, payload=None):
        """VEVENT complet d'un événement non récurrent.

        ⚠️ Les données viennent de ``_get_sync_payload`` et NON des champs du
        record. Cette méthode porte des règles métier qu'on ne doit surtout pas
        redémontrer ici : la liste de participants est délibérément VIDE pour
        un événement issu d'une réservation (sans quoi le greffon de
        planification de Nextcloud émet une SECONDE invitation iMIP au même
        client), le lien de visioconférence retombe dans ``LOCATION``, et la
        description Html est aplatie en texte brut. Une seule source de vérité,
        que le transport soit CalDAV ou le webhook.

        L'heure part avec son fuseau (``DTSTART;TZID=…`` + VTIMEZONE) plutôt
        qu'en UTC : voir ``_ics_tzname``. Le repli UTC subsiste dès qu'on ne
        sait pas produire le VTIMEZONE correspondant.
        """
        event.ensure_one()
        data = (payload or event._get_sync_payload("update"))["event"]

        # Le fuseau se résout AVANT l'en-tête : son VTIMEZONE se place entre
        # les propriétés du VCALENDAR et le VEVENT qui s'y réfère (RFC 5545
        # §3.6 — un composant ne peut pas citer un TZID déclaré après lui).
        tzname = None
        tzlines = []
        if not data.get("allday"):
            tzname = self._ics_tzname(event)
            if tzname:
                tzlines = self._vtimezone_lines(tzname)
                if not tzlines:
                    tzname = None

        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//Blue Fox Inc//Odoo Calendar Sync//FR",
            "CALSCALE:GREGORIAN",
        ]
        lines += tzlines
        lines += [
            "BEGIN:VEVENT",
            "UID:%s" % event.x_nc_uid,
            "DTSTAMP:%s" % fields.Datetime.now().strftime("%Y%m%dT%H%M%SZ"),
            "SUMMARY:%s" % self._escape_ics(data.get("name")),
        ]

        # 🔴 RFC 5545 §3.8.7.4. Ce VEVENT n'a longtemps porte AUCUN `SEQUENCE`,
        # alors que le `.ics` des courriels en porte un depuis le 2026-09-09.
        # Deux revisions de la meme rencontre arrivaient donc chez le serveur
        # distant sans qu'aucune ne se declare plus recente que l'autre, et un
        # client d'agenda qui les recoit dans le desordre n'a aucun moyen de
        # dire laquelle garder.
        #
        # ⚠️ Lien MOU, comme `bf_event_status` plus bas : `bf_ics_sequence`
        # appartient a `bf_calendar_invite`, et ce module tourne aussi chez des
        # locataires qui ne l'ont pas. Absent, on n'ecrit rien plutot que
        # d'ecrire un zero, parce qu'un `SEQUENCE:0` pose sur une rencontre
        # deja revisee la ferait passer pour l'originale.
        if "bf_ics_sequence" in event._fields:
            lines.append("SEQUENCE:%d" % (event.bf_ics_sequence or 0))

        if data.get("allday"):
            # DTEND est EXCLUSIF en RFC 5545 alors que `stop_date` d'Odoo est le
            # dernier jour INCLUS : sans le +1 jour, un « toute la journée » se
            # rend amputé de sa dernière journée.
            start_date = fields.Date.to_date(event.start_date or event.start)
            stop_date = fields.Date.to_date(event.stop_date or event.stop)
            lines.append("DTSTART;VALUE=DATE:%s" % start_date.strftime("%Y%m%d"))
            lines.append(
                "DTEND;VALUE=DATE:%s"
                % (stop_date + timedelta(days=1)).strftime("%Y%m%d")
            )
        elif tzname:
            import pytz

            tz = pytz.timezone(tzname)
            for prop, value in (("DTSTART", event.start), ("DTEND", event.stop)):
                local = pytz.utc.localize(
                    fields.Datetime.to_datetime(value)
                ).astimezone(tz)
                lines.append(
                    "%s;TZID=%s:%s"
                    % (prop, tzname, local.strftime("%Y%m%dT%H%M%S"))
                )
        else:
            lines.append("DTSTART:%s" % self._ics_datetime(event.start))
            lines.append("DTEND:%s" % self._ics_datetime(event.stop))

        if data.get("location"):
            lines.append("LOCATION:%s" % self._escape_ics(data["location"]))
        if data.get("description"):
            lines.append("DESCRIPTION:%s" % self._escape_ics(data["description"]))

        lines.append(
            "TRANSP:%s"
            % ("TRANSPARENT" if data.get("show_as") == "free" else "OPAQUE")
        )
        privacy = (data.get("privacy") or "").upper()
        if privacy in ("PUBLIC", "PRIVATE", "CONFIDENTIAL"):
            lines.append("CLASS:%s" % privacy)
        if data.get("categories"):
            lines.append(self._ligne_categories(data["categories"]))

        # STATUS n'est écrit que si Odoo en a un à dire. Un VEVENT sans STATUS
        # est « non précisé » en RFC 5545, ce qui est exactement l'état d'une
        # rencontre créée avant que ce champ existe ; écrire CONFIRMED d'office
        # affirmerait une confirmation que personne n'a donnée, et le ferait sur
        # tout l'historique à la première repoussée.
        if data.get("status") in ("TENTATIVE", "CONFIRMED", "CANCELLED"):
            lines.append("STATUS:%s" % data["status"])

        organizer = data.get("organizer") or {}
        if organizer.get("email"):
            lines.append(
                "ORGANIZER;CN=%s:mailto:%s"
                % (self._escape_ics(organizer.get("name")), organizer["email"])
            )
        for attendee in data.get("attendees") or []:
            if not attendee.get("email"):
                continue
            lines.append(
                "ATTENDEE;CN=%s;PARTSTAT=%s:mailto:%s"
                % (
                    self._escape_ics(attendee.get("name")),
                    "ACCEPTED" if attendee.get("status") == "accepted" else "NEEDS-ACTION",
                    attendee["email"],
                )
            )

        lines += self._build_valarms(event)
        lines += ["END:VEVENT", "END:VCALENDAR"]
        return "\r\n".join(self._fold(line) for line in lines) + "\r\n"

    @api.model
    def _build_valarms(self, event):
        """VALARM des alarmes Odoo de l'événement.

        ⚠️ Sans ça, une poussée AMPUTE l'événement de ses rappels côté
        Nextcloud : le PUT remplace l'objet entier, donc tout VALARM absent du
        corps envoyé disparaît du serveur. Constaté au QA du 2026-08-19, où un
        événement portant une alarme Odoo arrivait nu dans Nextcloud.

        ⚠️ Seules les alarmes ``notification`` sont sérialisées, en
        ``ACTION:DISPLAY``. Les alarmes ``email`` d'Odoo sont volontairement
        omises : Odoo envoie déjà ces courriels lui-même, et les confier en
        plus à Nextcloud ferait partir deux avis pour un seul rappel. C'est la
        même règle que pour les participants d'une réservation, et la même
        raison.

        L'UID de chaque VALARM est dérivé de l'UID de l'événement et de l'id de
        l'alarme, donc stable d'une poussée à l'autre : un client conforme à la
        RFC 9074 peut y accrocher son état d'acquittement sans qu'une nouvelle
        poussée le lui arrache.
        """
        event.ensure_one()
        if "alarm_ids" not in event._fields:
            return []
        base_uid = (event.x_nc_uid or "").split("@")[0] or str(event.id)
        lines = []
        for alarm in event.alarm_ids:
            if alarm.alarm_type != "notification":
                continue
            lines += [
                "BEGIN:VALARM",
                "UID:%s-alarm%s" % (base_uid, alarm.id),
                "ACTION:DISPLAY",
                "TRIGGER:-PT%dM" % int(alarm.duration_minutes or 0),
                "DESCRIPTION:%s" % self._escape_ics(event.name or alarm.name),
                "END:VALARM",
            ]
        return lines

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    @api.model
    def _auth(self, config):
        password = config._get_app_password()
        if not password or not config.nextcloud_user:
            return None
        return (config.nextcloud_user, password)

    @api.model
    def push(self, config, event, payload=None):
        """PUT du VEVENT. Rend ``(href, etag)``.

        Lève sur échec : l'appelant journalise et ``_retry_failed_pushes``
        repassera. C'est toute la différence avec le webhook, qui pouvait
        répondre 2xx sans avoir rien écrit.
        """
        auth = self._auth(config)
        if not auth:
            raise ValueError(
                "Config %s: identifiants Nextcloud absents, poussée CalDAV "
                "impossible." % config.display_name
            )
        url, href = self._resource_url(config, event)
        if not url:
            raise ValueError(
                "Config %s: impossible de calculer l'URL de la ressource pour "
                "l'événement %s (UID ou caldav_url manquant)."
                % (config.display_name, event.id)
            )

        body = self.build_ics(event, payload=payload)
        response = requests.put(
            url,
            data=body.encode("utf-8"),
            headers={
                "Content-Type": "text/calendar; charset=utf-8",
                **NO_SCHEDULING_HEADERS,
            },
            auth=auth,
            timeout=CALDAV_TIMEOUT,
        )
        response.raise_for_status()

        if not href:
            # Le href est le CHEMIN, pas l'URL absolue : c'est sous cette forme
            # que le REPORT du pull le renvoie, et l'appariement des deux
            # côtés se fait sur cette valeur.
            from urllib.parse import urlparse

            href = urlparse(url).path

        # Tous les serveurs ne rendent pas l'ETag sur un PUT. Ce n'est pas un
        # échec : le pull suivant le posera. Le href, lui, est acquis.
        etag = (response.headers.get("ETag") or "").strip() or False
        return href, etag

    @api.model
    def push_delete(self, config, event=None, url=None):
        """DELETE de la ressource. Un 404 vaut succès : la cible n'est plus là.

        Accepte soit l'événement (avant son unlink), soit une URL déjà
        calculée, parce que le chemin de suppression peut appeler après que le
        record ait perdu ses champs.
        """
        auth = self._auth(config)
        if not auth:
            raise ValueError(
                "Config %s: identifiants Nextcloud absents, suppression CalDAV "
                "impossible." % config.display_name
            )
        if not url:
            url, _href = self._resource_url(config, event)
        if not url:
            raise ValueError(
                "Config %s: impossible de calculer l'URL de la ressource à "
                "supprimer." % config.display_name
            )

        # Une suppression qui part d'Odoo ne doit pas non plus déclencher le
        # METHOD:CANCEL de Nextcloud : l'avis d'annulation d'Odoo est décoché
        # par défaut, et le greffon passait outre cette case.
        response = requests.delete(
            url, headers=dict(NO_SCHEDULING_HEADERS), auth=auth,
            timeout=CALDAV_TIMEOUT,
        )
        if response.status_code == 404:
            _logger.info("CalDAV: %s déjà absent côté Nextcloud", url)
            return True
        response.raise_for_status()
        return True

    # ------------------------------------------------------------------
    # Retouche d'une ressource existante
    # ------------------------------------------------------------------

    @api.model
    def _fetch(self, config, url):
        """GET de la ressource : ``(texte, etag)``, ou ``(None, None)`` si absente.

        L'ETag est rendu tel que le serveur l'écrit, guillemets compris : c'est
        la forme que ``If-Match`` attend (RFC 9110 §13.1.1).
        """
        if not url:
            # `_resource_url` a refusé l'adresse, ou n'en a pas : rien ne part.
            raise RessourceInattendue(
                "Config %s : adresse de la ressource absente ou hors de la "
                "collection." % config.display_name)
        auth = self._auth(config)
        if not auth:
            raise ValueError(
                "Config %s: identifiants Nextcloud absents, lecture CalDAV "
                "impossible." % config.display_name
            )
        response = requests.get(url, auth=auth, timeout=CALDAV_TIMEOUT)
        if response.status_code == 404:
            return None, None
        response.raise_for_status()
        response.encoding = "utf-8"
        return response.text, (response.headers.get("ETag") or "").strip() or False

    @api.model
    def _retoucher(self, config, url, transformer):
        """Lire, transformer, réécrire sous condition. Rend l'ETag neuf, ou False.

        ``transformer(texte)`` rend le texte neuf, ou ``None`` quand il n'y a
        rien à changer : la ressource n'est alors pas réécrite.

        ⚠️ ``If-Match`` et non le ``PUT`` nu de ``push``. Entre notre lecture
        et notre écriture, un téléphone ou un autre client a pu modifier la
        ressource ; sans condition, notre version écraserait la sienne en
        silence. Le serveur refuse alors en 412 : on relit et on rejoue UNE
        fois, sur la version fraîche. Un second 412 lève, et l'appelant le dit.
        """
        for essai in (1, 2):
            texte, etag = self._fetch(config, url)
            if texte is None:
                raise LookupError("CalDAV : %s absent côté Nextcloud" % url)
            neuf = transformer(texte)
            if neuf is None or neuf == texte:
                return False
            headers = {
                "Content-Type": "text/calendar; charset=utf-8",
                **NO_SCHEDULING_HEADERS,
            }
            if etag:
                headers["If-Match"] = etag
            response = requests.put(
                url, data=neuf.encode("utf-8"), headers=headers,
                auth=self._auth(config), timeout=CALDAV_TIMEOUT,
            )
            if response.status_code == 412 and essai == 1:
                _logger.info("CalDAV : %s modifié entre lecture et écriture, relecture", url)
                continue
            response.raise_for_status()
            return (response.headers.get("ETag") or "").strip() or False
        return False

    @api.model
    def _assembler(self, lignes):
        return "\r\n".join(self._fold(ligne) for ligne in lignes) + "\r\n"

    @api.model
    def _ligne_date(self, prop, params_serie, valeur, journee, vtimezones=None):
        """``PROP…:valeur`` écrite comme l'écrit le DTSTART de la série.

        Une heure locale dans le TZID de la série, et non de l'UTC : c'est la
        forme qu'un client d'agenda attend pour un RECURRENCE-ID (même type de
        valeur que DTSTART, RFC 5545 §3.8.4.4), et la VTIMEZONE est déjà dans
        le fichier. Un TZID qu'on ne sait pas résoudre retombe sur l'UTC, que
        Nextcloud compare à l'instant près.
        """
        if journee:
            jour = fields.Date.to_date(valeur)
            return "%s;VALUE=DATE:%s" % (prop, jour.strftime("%Y%m%d"))
        valeur = fields.Datetime.to_datetime(valeur)
        trouve = re.search(r'TZID=("[^"]*"|[^;:]+)', params_serie or "", re.IGNORECASE)
        if trouve:
            brut = trouve.group(1)
            nom = self.env["nextcloud.calendar.sync.config"]._normalize_timezone(
                brut.strip('"'))
            tz = None
            try:
                tz = pytz.timezone(nom)
            except (pytz.UnknownTimeZoneError, AttributeError):
                tz = (vtimezones or {}).get(nom) or (vtimezones or {}).get(brut.strip('"'))
            if tz is not None:
                local = pytz.utc.localize(valeur).astimezone(tz)
                return "%s;TZID=%s:%s" % (prop, brut, local.strftime("%Y%m%dT%H%M%S"))
        return "%s:%s" % (prop, valeur.strftime("%Y%m%dT%H%M%SZ"))

    @api.model
    def _lignes_retouche(self, event, params_serie, vtimezones, sequence, props):
        """Les propriétés que l'écriture d'Odoo a touchées, et elles seules.

        ``props`` ⊂ {"DATES", "SUMMARY", "STATUS", "TRANSP", "CLASS", "CATEGORIES"} : ce que les champs
        écrits disent. Réécrire aussi ce qu'on n'a pas touché ramènerait dans
        Nextcloud la vue d'Odoo, peut-être périmée : une annulation faite au
        téléphone depuis la dernière lecture serait défaite par un simple
        renommage dans Odoo.

        STATUS n'est écrit que si Odoo en a un (``bf_event_status``, lien mou).
        SEQUENCE monte d'un cran sur celui du VEVENT, ou rejoint celui des
        courriels d'Odoo s'il est plus haut.
        """
        maintenant = fields.Datetime.now().strftime("%Y%m%dT%H%M%SZ")
        lignes = []
        if "DATES" in props and event.allday:
            debut = event.start_date or event.start
            fin = fields.Date.to_date(event.stop_date or event.stop) + timedelta(days=1)
            lignes += [
                self._ligne_date("DTSTART", params_serie, debut, True),
                self._ligne_date("DTEND", params_serie, fin, True),
            ]
        elif "DATES" in props:
            lignes += [
                self._ligne_date("DTSTART", params_serie, event.start, False, vtimezones),
                self._ligne_date("DTEND", params_serie, event.stop, False, vtimezones),
            ]
        if "SUMMARY" in props:
            lignes.append("SUMMARY:%s" % self._escape_ics(event.name or ""))
        statut = event._bf_ics_status() if "bf_event_status" in event._fields else False
        if "STATUS" in props and statut:
            lignes.append("STATUS:%s" % statut)
        if "TRANSP" in props:
            lignes.append("TRANSP:%s" % ("TRANSPARENT" if event.show_as == "free" else "OPAQUE"))
        # CLASS et CATEGORIES : sans valeur dans Odoo, la propriété est RETIRÉE
        # (voir `_a_retirer`), pas laissée à son ancienne valeur. Une étiquette
        # enlevée dans Odoo restait sinon dans Nextcloud, et revenait à la
        # lecture suivante.
        if "CLASS" in props and event.privacy in CLASSE_DE_CONFIDENTIALITE:
            lignes.append("CLASS:%s" % CLASSE_DE_CONFIDENTIALITE[event.privacy])
        if "CATEGORIES" in props and event.categ_ids:
            lignes.append(self._ligne_categories(event.categ_ids.mapped("name")))
        sequence = (sequence or 0) + 1
        if "bf_ics_sequence" in event._fields:
            sequence = max(sequence, event.bf_ics_sequence or 0)
        lignes += [
            "SEQUENCE:%d" % sequence,
            "DTSTAMP:%s" % maintenant,
            "LAST-MODIFIED:%s" % maintenant,
        ]
        return lignes

    @staticmethod
    def _a_retirer(nouvelles, props=()):
        """Les propriétés remplacées : celles qu'on écrit, plus DURATION avec DTEND.

        Plus CLASS et CATEGORIES dès qu'Odoo les a touchées, même sans ligne
        neuve : c'est ainsi qu'une confidentialité ou une étiquette retirée
        dans Odoo disparaît aussi de Nextcloud. Toutes les lignes CATEGORIES
        partent (RFC 5545 en permet plusieurs), pour n'en garder qu'une.
        """
        noms = {nom_prop(ligne) for ligne in nouvelles}
        if "DTEND" in noms:
            noms.add("DURATION")
        noms |= set(props) & {"CLASS", "CATEGORIES"}
        return noms

    def _ligne_categories(self, noms):
        """``CATEGORIES:`` d'une liste de noms, chacun échappé (``\\,`` compris)."""
        return "CATEGORIES:%s" % ",".join(self._escape_ics(nom) for nom in noms if nom)

    @staticmethod
    def _sequence(bloc):
        for _i, ligne in props_de_tete(bloc):
            if nom_prop(ligne) == "SEQUENCE":
                try:
                    return int(valeur_prop(ligne).strip())
                except ValueError:
                    return 0
        return 0

    @staticmethod
    def _equivalents(avant, apres):
        """Même contenu, à l'horodatage et au numéro de révision près."""
        volatiles = {"DTSTAMP", "LAST-MODIFIED", "SEQUENCE"}

        def cle(bloc):
            return sorted(ligne for _i, ligne in props_de_tete(bloc)
                          if nom_prop(ligne) not in volatiles)
        return cle(avant) == cle(apres)

    @api.model
    def _meme_instant(self, ligne, cible, journee, vtimezones):
        """La ligne de date ``ligne`` désigne-t-elle l'instant ``cible`` ?"""
        dt, jour = self.env["nextcloud.calendar.sync.config"]._parse_ics_datetime(
            valeur_prop(ligne), params_prop(ligne), vtimezones)
        if not dt:
            return False
        cible = fields.Datetime.to_datetime(cible)
        if journee or jour:
            return dt.date() == cible.date()
        return abs((dt - cible).total_seconds()) < 60

    @api.model
    def _decouper(self, texte):
        """``(lignes, maître, exceptions)`` : les VEVENT d'une ressource.

        Le maître est le VEVENT sans RECURRENCE-ID ; les exceptions sont
        ``[(début, fin, ligne RECURRENCE-ID)]``.
        """
        lignes = deplier(texte)
        maitre, exceptions = None, []
        for debut, fin in blocs_vevent(lignes):
            tete = props_de_tete(lignes[debut:fin + 1])
            rid = next((ligne for _i, ligne in tete if nom_prop(ligne) == "RECURRENCE-ID"), None)
            if rid is None:
                if maitre is None:
                    maitre = (debut, fin)
            else:
                exceptions.append((debut, fin, rid))
        return lignes, maitre, exceptions

    @api.model
    def _retoucher_evenement(self, config, url, event, props):
        """Réécrit, dans un événement simple, les propriétés ``props`` seulement."""
        sync_config = self.env["nextcloud.calendar.sync.config"]

        def transformer(texte):
            lignes, maitre, _exceptions = self._decouper(texte)
            if maitre is None:
                raise RessourceInattendue("ressource sans VEVENT maître")
            debut, fin = maitre
            bloc = lignes[debut:fin + 1]
            tete = props_de_tete(bloc)
            # Une série ne passe pas par ici : la retoucher comme un événement
            # simple déplacerait TOUTES ses occurrences.
            if any(nom_prop(ligne) == "RRULE" for _i, ligne in tete):
                raise RessourceInattendue("une série là où Odoo voit un événement simple")
            params = next((params_prop(ligne) for _i, ligne in tete
                           if nom_prop(ligne) == "DTSTART"), "")
            nouvelles = garder_params(bloc, self._lignes_retouche(
                event, params, sync_config._ics_vtimezones(texte), self._sequence(bloc), props))
            neuf = retoucher_bloc(bloc, nouvelles, self._a_retirer(nouvelles, props))
            if self._equivalents(bloc, neuf):
                return None
            return self._assembler(lignes[:debut] + neuf + lignes[fin + 1:])

        return self._retoucher(config, url, transformer)

    @api.model
    def _retoucher_occurrence(self, config, url, event, origine, journee, props):
        """Pose ou met à jour l'exception RECURRENCE-ID d'une occurrence.

        ``origine`` est l'heure d'origine de l'occurrence (UTC naïf), celle que
        la règle de la série lui donne : c'est elle qui l'identifie, pas son
        heure actuelle. Une exception neuve part d'une copie du maître (alarmes
        et participants compris) sans ses propriétés de série.
        """
        sync_config = self.env["nextcloud.calendar.sync.config"]

        def transformer(texte):
            lignes, maitre, exceptions = self._decouper(texte)
            if maitre is None:
                raise RessourceInattendue("ressource sans VEVENT maître")
            vtz = sync_config._ics_vtimezones(texte)
            m_debut, m_fin = maitre
            m_bloc = lignes[m_debut:m_fin + 1]
            m_tete = props_de_tete(m_bloc)
            if not any(nom_prop(ligne) == "RRULE" for _i, ligne in m_tete):
                raise RessourceInattendue("pas de série là où Odoo voit une occurrence")
            params = next((params_prop(ligne) for _i, ligne in m_tete
                           if nom_prop(ligne) == "DTSTART"), "")
            # Le type du RECURRENCE-ID est celui du DTSTART de la série
            # (RFC 5545 §3.8.4.4), pas celui de l'occurrence telle qu'Odoo la
            # voit APRÈS l'écriture (passée en « toute la journée », par exemple).
            serie_en_jours = journee_de(params)
            # une exception sans CLASS
            # hérite de celle du maître à la lecture. Retirer la confidentialité
            # d'une seule occurrence sous un maître qui n'est pas PUBLIC se dit
            # donc par un CLASS:PUBLIC explicite.
            classe_maitre = next((valeur_prop(ligne).strip().upper() for _i, ligne in m_tete
                                  if nom_prop(ligne) == "CLASS"), "")

            def public_explicite(lignes_neuves):
                if "CLASS" in props and not event.privacy and classe_maitre \
                        and classe_maitre != "PUBLIC":
                    return list(lignes_neuves) + ["CLASS:PUBLIC"]
                return lignes_neuves

            for debut, fin, rid in exceptions:
                if self._meme_instant(rid, origine, serie_en_jours, vtz):
                    bloc = lignes[debut:fin + 1]
                    nouvelles = public_explicite(garder_params(bloc, self._lignes_retouche(
                        event, params, vtz, self._sequence(bloc), props)))
                    neuf = retoucher_bloc(bloc, nouvelles, self._a_retirer(nouvelles, props))
                    if self._equivalents(bloc, neuf):
                        return None
                    return self._assembler(lignes[:debut] + neuf + lignes[fin + 1:])
            rid_ligne = self._ligne_date("RECURRENCE-ID", params, origine, serie_en_jours, vtz)
            copie = retoucher_bloc(m_bloc, [rid_ligne], PROPS_DE_SERIE)
            # Une exception neuve porte toujours SA date : celle copiée du
            # maître serait la première de la série.
            nouvelles = public_explicite(garder_params(m_bloc, self._lignes_retouche(
                event, params, vtz, self._sequence(m_bloc), set(props) | {"DATES"})))
            neuf = retoucher_bloc(copie, nouvelles, self._a_retirer(nouvelles, props))
            dernier = max([m_fin] + [fin for _d, fin, _r in exceptions])
            return self._assembler(lignes[:dernier + 1] + neuf + lignes[dernier + 1:])

        return self._retoucher(config, url, transformer)

    @api.model
    def _retoucher_serie_classe(self, config, url, event, props):
        """CLASS et CATEGORIES sur TOUS les VEVENT d'une série : maître et exceptions.

        Le seul changement de série entière qu'Odoo renvoie : rendre privée une série « pour tous les
        événements » ne partait pas, et la confidentialité se perdait à la
        recréation suivante depuis Nextcloud. Les heures, la règle et le reste
        ne bougent pas : seules ces deux propriétés sont posées ou retirées,
        dans chaque VEVENT, parce que « tous les événements » vaut aussi pour
        les occurrences déjà déplacées.
        """
        props = set(props) & {"CLASS", "CATEGORIES"}
        sync_config = self.env["nextcloud.calendar.sync.config"]

        def transformer(texte):
            lignes, maitre, exceptions = self._decouper(texte)
            if maitre is None:
                raise RessourceInattendue("ressource sans VEVENT maître")
            vtz = sync_config._ics_vtimezones(texte)
            sortie = list(lignes)
            change = False
            # De la fin vers le début : les index des blocs précédents restent bons.
            blocs = sorted([maitre] + [(d, f) for d, f, _r in exceptions], reverse=True)
            for debut, fin in blocs:
                bloc = sortie[debut:fin + 1]
                params = next((params_prop(ligne) for _i, ligne in props_de_tete(bloc)
                               if nom_prop(ligne) == "DTSTART"), "")
                nouvelles = self._lignes_retouche(event, params, vtz, self._sequence(bloc), props)
                neuf = retoucher_bloc(bloc, nouvelles, self._a_retirer(nouvelles, props))
                if not self._equivalents(bloc, neuf):
                    sortie[debut:fin + 1] = neuf
                    change = True
            return self._assembler(sortie) if change else None

        return self._retoucher(config, url, transformer)

    @api.model
    def _exclure_occurrence(self, config, url, origines, journee):
        """Ajoute l'EXDATE d'occurrences supprimées, et retire leurs exceptions.

        Toutes les occurrences d'une même série en UNE retouche : une lecture,
        une écriture, au lieu d'une par occurrence.
        """
        sync_config = self.env["nextcloud.calendar.sync.config"]

        def transformer(texte):
            lignes, maitre, exceptions = self._decouper(texte)
            if maitre is None:
                raise RessourceInattendue("ressource sans VEVENT maître")
            vtz = sync_config._ics_vtimezones(texte)
            m_debut, m_fin = maitre
            m_bloc = lignes[m_debut:m_fin + 1]
            m_tete = props_de_tete(m_bloc)
            if not any(nom_prop(ligne) == "RRULE" for _i, ligne in m_tete):
                raise RessourceInattendue("pas de série là où Odoo voit une occurrence")
            params = next((params_prop(ligne) for _i, ligne in m_tete
                           if nom_prop(ligne) == "DTSTART"), "")
            journee = journee_de(params)
            existantes = []
            for _i, ligne in m_tete:
                if nom_prop(ligne) == "EXDATE":
                    existantes += ["EXDATE%s:%s" % (params_prop(ligne), v.strip())
                                   for v in valeur_prop(ligne).split(",") if v.strip()]
            neuves = [o for o in origines if not any(
                self._meme_instant(ex, o, journee, vtz) for ex in existantes)]
            a_jeter = {(d, f) for d, f, rid in exceptions
                       if any(self._meme_instant(rid, o, journee, vtz) for o in origines)}
            if not neuves and not a_jeter:
                return None
            sortie = [ligne for i, ligne in enumerate(lignes)
                      if not any(d <= i <= f for d, f in a_jeter)]
            # Les exceptions jetées sont avant ou après le maître, jamais dedans :
            # seules celles d'avant décalent ses bornes.
            decalage = sum(f - d + 1 for d, f in a_jeter if f < m_debut)
            debut, fin = m_debut - decalage, m_fin - decalage
            bloc = sortie[debut:fin + 1]
            if neuves:
                bloc = retoucher_bloc(
                    bloc,
                    [self._ligne_date("EXDATE", params, o, journee, vtz) for o in neuves],
                    (),
                )
            return self._assembler(sortie[:debut] + bloc + sortie[fin + 1:])

        return self._retoucher(config, url, transformer)
