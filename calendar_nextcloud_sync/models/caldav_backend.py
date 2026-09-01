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
from datetime import timedelta

import requests

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Au-delà, un serveur CalDAV lent vaut mieux qu'une requête HTTP qui pend :
# l'appelant journalise et le cron de reprise repassera.
CALDAV_TIMEOUT = 30

# VTIMEZONE sérialisés, par nom IANA. Les règles d'un fuseau ne changent qu'à
# la mise à jour de la base tzdata, donc au redémarrage du processus : un cache
# de processus n'a pas besoin d'invalidation. `None` mémorise un fuseau dont la
# sérialisation a échoué, pour ne pas la retenter à chaque poussée.
_VTIMEZONE_CACHE = {}


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
                return href, href
            base = (config.nextcloud_base_url or "").rstrip("/")
            return base + href, href
        name = self._resource_name(event)
        if not name or not config.caldav_url:
            return None, None
        url = config.caldav_url.rstrip("/") + "/" + name
        return url, None

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
        password = config.nextcloud_app_password
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
            headers={"Content-Type": "text/calendar; charset=utf-8"},
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

        response = requests.delete(url, auth=auth, timeout=CALDAV_TIMEOUT)
        if response.status_code == 404:
            _logger.info("CalDAV: %s déjà absent côté Nextcloud", url)
            return True
        response.raise_for_status()
        return True
