"""Ce que le téléphone a le droit de savoir d'un événement, et rien de plus.

La collecte vit ici plutôt que dans le contrôleur, pour la même raison que
les autres surfaces mobiles de la maison : elle passe par les droits de
l'appelant et s'éprouve sans jeton.

⚠️ Deux pièges commandent la forme des charges utiles.

1. **Un identifiant d'occurrence ne survit pas à la synchro.** Dès qu'un ``.ics``
   réimporté porte un ``RRULE``, ``calendar_nextcloud_sync`` rase la récurrence
   et recrée toutes ses occurrences avec des ``id`` neufs. Un téléphone qui a
   gardé l'``id`` d'hier reporterait donc un rappel qui n'existe plus. Chaque
   événement part avec sa ``key`` stable (celle de
   ``bf.calendar.reminder.ack``), et le contrôleur accepte cette clé partout où
   il accepte un ``id``.

2. **Les heures partent en UTC, jamais en local.** L'usager et le parc ne sont
   pas toujours dans le même fuseau : formater côté serveur reviendrait à
   choisir un des deux pour lui. Le téléphone reçoit des instants et affiche
   dans SON fuseau.
"""

import logging
from datetime import datetime, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from . import odoo_palette

_logger = logging.getLogger(__name__)

# Fenêtre maximale d'une requête. La grille demande une semaine à la fois ;
# le plafond est là pour qu'un client fautif ne demande pas dix ans, ce qui
# est loin d'être théorique : un anniversaire récurrent porte des occurrences
# à plusieurs siècles.
MAX_RANGE_DAYS = 62

# Plafond de sécurité sur le nombre d'événements rendus.
MAX_EVENTS = 500


def iso(value):
    """Instant UTC en ISO, ou None. Le « Z » est explicite, pas déduit."""
    if not value:
        return None
    if isinstance(value, str):
        value = fields.Datetime.from_string(value)
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


class CalendarEvent(models.Model):
    _inherit = "calendar.event"

    # ------------------------------------------------------------------
    # Portée
    # ------------------------------------------------------------------

    @api.model
    def _mobile_domain(self):
        """« Mon agenda » : ce dont je suis participant, ou que j'organise.

        Pas de repli sur la société : un agenda mobile qui montrerait les
        rencontres des autres serait une fuite, pas une commodité.
        """
        partner = self.env.user.partner_id
        return ["|", ("partner_ids", "in", partner.ids), ("user_id", "=", self.env.uid)]

    @api.model
    def _mobile_window(self, date_from, date_to):
        """Borne la fenêtre demandée, en refusant plutôt qu'en rognant.

        Rogner silencieusement rendrait une semaine incomplète que l'app
        afficherait comme complète.
        """
        start = fields.Datetime.from_string(date_from) if date_from else None
        stop = fields.Datetime.from_string(date_to) if date_to else None
        if not start or not stop:
            start = fields.Datetime.now() - timedelta(days=1)
            stop = start + timedelta(days=8)
        if stop <= start:
            raise UserError("La fin de la fenêtre précède son début.")
        if (stop - start) > timedelta(days=MAX_RANGE_DAYS):
            raise UserError("Fenêtre trop large (maximum %s jours)." % MAX_RANGE_DAYS)
        return start, stop

    # ------------------------------------------------------------------
    # Clé stable d'occurrence
    # ------------------------------------------------------------------

    def _mobile_key(self):
        self.ensure_one()
        return self.env["bf.calendar.reminder.ack"]._bf_reminder_key(self)

    @api.model
    def _mobile_resolve(self, event_id=None, key=None):
        """Retrouver l'événement, par identifiant sinon par clé stable.

        L'ordre compte : l'identifiant est exact quand il est encore valide, et
        la clé sert justement de rattrapage quand il ne l'est plus.

        ⚠️ Toujours DANS « mon agenda » (`_mobile_domain`), comme la liste :
        la fiche rend la description, les courriels des participants et le
        compte rendu, et un identifiant deviné ne doit pas ouvrir la rencontre
        d'un collègue que la règle d'accès d'Odoo laisserait lire.
        """
        mien = self._mobile_domain()
        if event_id:
            event = self.browse(int(event_id)).exists().filtered_domain(mien)
            if event:
                return event
        if not key:
            return self.browse()
        if key.startswith("odoo:"):
            return self.browse(int(key[5:])).exists().filtered_domain(mien)
        if not key.startswith("nc:") or "@" not in key:
            return self.browse()
        # ⚠️ Coupé au DERNIER « @ », pas au premier : un UID iCalendar en
        # contient un (``dd1558aa-…@odoo.example.com``), et couper au premier
        # rendait un début de la forme « domaine@2026-10-05 12:00:00 », donc une
        # recherche qui ne trouvait jamais rien.
        uid, _sep, start = key[3:].rpartition("@")
        if not uid or not start:
            return self.browse()
        if "x_nc_uid" not in self._fields:
            return self.browse()
        # L'occurrence générée ne porte pas l'UID : seule la base de la
        # récurrence l'a. On cherche donc des deux côtés.
        return self.search(
            mien + [
                ("start", "=", start),
                "|",
                ("x_nc_uid", "=", uid),
                ("recurrence_id.base_event_id.x_nc_uid", "=", uid),
            ],
            limit=1,
        )

    # ------------------------------------------------------------------
    # Charges utiles
    # ------------------------------------------------------------------

    def _mobile_attendee(self):
        """La fiche participant de l'usager courant, ou un ensemble vide."""
        self.ensure_one()
        partner = self.env.user.partner_id
        return self.attendee_ids.filtered(lambda a: a.partner_id == partner)[:1]

    def _mobile_alarms(self):
        """Le rappel configuré, et s'il a déjà sonné.

        La fiche offrait « reporter » et « vu » à toute rencontre, y compris à
        celle de la semaine prochaine dont le rappel n'a pas encore sonné : ces
        deux gestes n'ont de sens qu'une fois le rappel parti. Le téléphone
        reçoit donc ce qui est configuré et l'heure à laquelle chaque rappel
        sonne, plus un verdict calculé ICI, avec l'horloge du serveur, pour que
        l'écart de fuseau entre le compte et l'appareil ne fasse pas mentir la
        fiche.

        ⚠️ Type « notification » seulement : un rappel par courriel part une
        fois et ne se reporte pas d'un geste, il n'a rien à faire sur la fiche.
        """
        self.ensure_one()
        now = fields.Datetime.now()
        alarms = []
        fired = False
        for alarm in self.alarm_ids.filtered(lambda a: a.alarm_type == "notification"):
            notify_at = None
            if self.start:
                notify_at = self.start - timedelta(minutes=alarm.duration_minutes or 0)
                if notify_at <= now:
                    fired = True
            alarms.append({
                "name": alarm.name or "",
                "minutes": alarm.duration_minutes or 0,
                "notify_at": iso(notify_at),
            })
        alarms.sort(key=lambda a: -a["minutes"])
        return {"alarms": alarms, "reminder_fired": fired}

    def _mobile_payload(self):
        """Ce qui suffit à dessiner une case de la grille."""
        self.ensure_one()
        mine = self._mobile_attendee()
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url") or ""
        data = {
            "id": self.id,
            "key": self._mobile_key(),
            "name": self.display_name or "",
            "start": iso(self.start),
            "stop": iso(self.stop),
            "allday": bool(self.allday),
            "duration": self.duration or 0.0,
            "location": self.location or "",
            "videocall": self.videocall_location or "",
            "show_as": self.show_as or "",
            "recurring": bool(self.recurrency),
            "attendees": len(self.attendee_ids),
            "my_state": mine.state if mine else "",
            "snoozed_until": iso(mine.bf_snoozed_until) if mine else None,
            "dismissed_at": iso(mine.bf_dismissed_at) if mine else None,
            "url": "%s/odoo/calendar/%s" % (base, self.id) if base else "",
        }
        data.update(self._mobile_alarms())
        # La couleur de la grille, calculée avec la règle d'Odoo pour que le
        # téléphone peigne exactement ce que la vue Calendrier peint. La clé est
        # le champ `color` de l'événement, un par calendrier.
        cle = self.color if "color" in self._fields else 0
        data["color"] = odoo_palette.couleur(cle)
        data["color_soft"] = odoo_palette.couleur_douce(cle)
        data["color_index"] = cle or 0
        if "x_nc_calendar_id" in self._fields and self.x_nc_calendar_id:
            data["calendar"] = self.x_nc_calendar_id.display_name or ""
        else:
            data["calendar"] = ""
        # Les deux exclusions de `bf_meeting`, distinctes : l'une dit « pas
        # d'OdJ formel », l'autre « hors du tableau de bord ». Les confondre
        # ferait disparaître du suivi une rencontre qu'on voulait seulement
        # dispenser d'ordre du jour.
        if "bf_skip_agenda" in self._fields:
            data["skip_agenda"] = bool(self.bf_skip_agenda)
            data["skip_dashboard"] = bool(self.bf_skip_dashboard)
        # Pastilles Symbifox : lues seulement si `bf_meeting` est là.
        if "bf_agenda_state" in self._fields:
            data["agenda_state"] = self.bf_agenda_state or "none"
            data["minutes_state"] = self.bf_minutes_state or "none"
        return data

    @api.model
    def mobile_range(self, date_from=None, date_to=None):
        """Les événements de l'usager dans une fenêtre, triés par début."""
        start, stop = self._mobile_window(date_from, date_to)
        domain = self._mobile_domain() + [("start", "<", stop), ("stop", ">", start)]
        events = self.search(domain, order="start asc", limit=MAX_EVENTS + 1)
        truncated = len(events) > MAX_EVENTS
        events = events[:MAX_EVENTS]
        return {
            "ok": True,
            "from": iso(start),
            "to": iso(stop),
            "truncated": truncated,
            "events": [event._mobile_payload() for event in events],
        }

    def mobile_detail(self):
        """La fiche d'un événement, avec ce que Symbifox y ajoute."""
        self.ensure_one()
        data = self._mobile_payload()
        data["description"] = self.description or ""
        data["organizer"] = self.user_id.display_name or ""
        data["attendee_list"] = [
            {
                "name": att.common_name or att.partner_id.display_name or "",
                "state": att.state or "",
                "is_me": att.partner_id == self.env.user.partner_id,
                "partner_id": att.partner_id.id or 0,
                "email": att.email or att.partner_id.email or "",
            }
            for att in self.attendee_ids
        ]
        # L'organisateur ne se retire pas de sa propre rencontre, et le
        # téléphone doit le savoir AVANT d'offrir le geste : un bouton qui
        # échoue au serveur se lit comme une panne, pas comme une règle.
        data["organizer_partner_id"] = self.user_id.partner_id.id or 0
        try:
            self.check_access("write")
            data["can_edit_attendees"] = True
        except AccessError:
            data["can_edit_attendees"] = False
        data["agenda"] = self._mobile_agenda()
        data["minutes"] = self._mobile_minutes()
        return data

    def _mobile_agenda(self):
        """L'ordre du jour lié, s'il y en a un et si `bf_meeting` est là."""
        self.ensure_one()
        if "meeting.agenda" not in self.env:
            return None
        agenda = self.env["meeting.agenda"].search(
            [("calendar_event_id", "=", self.id)], limit=1)
        if not agenda:
            return None
        return {
            "id": agenda.id,
            "name": agenda.name or "",
            "state": agenda.state or "",
            "sent_date": iso(agenda.sent_date),
            "topics": [t.display_name for t in agenda.topic_ids[:20]],
        }

    def _mobile_minutes(self):
        """Le compte rendu lié : le résumé et les décisions, jamais le verbatim.

        Le verbatim et les notes de révision restent au bureau. Un téléphone
        égaré ne doit pas porter la transcription d'une rencontre client, et
        ``bf_meeting_portal`` a déjà tranché ce périmètre pour les clients.
        """
        self.ensure_one()
        if "meeting.record" not in self.env:
            return None
        record = self.env["meeting.record"].search(
            [("calendar_event_id", "=", self.id)], limit=1)
        if not record:
            return None
        return {
            "id": record.id,
            "name": record.name or "",
            "report_state": record.report_state or "",
            "summary": (record.summary or "")[:2000],
            "decisions": [d.display_name for d in record.decision_ids[:20]],
        }

    # ------------------------------------------------------------------
    # Écrire
    # ------------------------------------------------------------------

    # Les seuls champs qu'un téléphone peut poser à la création. Écrit en
    # liste blanche, jamais en liste noire : un champ ajouté à `calendar.event`
    # demain ne doit pas devenir écrivable par l'app sans qu'on l'ait voulu.
    _MOBILE_CREATE_FIELDS = (
        "name", "start", "stop", "allday", "location", "videocall_location",
        "description", "show_as",
    )

    @api.model
    def mobile_create(self, vals):
        """Créer une rencontre depuis le téléphone.

        ⚠️ Le calendrier de destination est celui que la personne choisit, et à
        défaut celui de sa configuration par défaut. Poser un événement sans
        `x_nc_calendar_id` le laisserait dans Odoo SEUL : la synchro ne pousse
        que ce qui porte une configuration, donc il n'apparaîtrait jamais sur
        le téléphone via CalDAV ni sur le poste de travail.

        ⚠️ L'organisateur est ajouté aux participants explicitement. Odoo le
        fait pour la vue, pas pour un `create` par RPC, et sans lui l'événement
        n'entre pas dans « mon agenda » — il serait créé puis invisible.
        """
        vals = {k: v for k, v in (vals or {}).items() if k in self._MOBILE_CREATE_FIELDS
                or k == "calendar_config_id"}
        if not vals.get("name"):
            raise UserError(_("Une rencontre a besoin d'un titre."))
        if not vals.get("start") or not vals.get("stop"):
            raise UserError(_("Une rencontre a besoin d'un début et d'une fin."))
        config_id = vals.pop("calendar_config_id", None)
        partner = self.env.user.partner_id
        vals["partner_ids"] = [(6, 0, partner.ids)]
        vals["user_id"] = self.env.uid
        if config_id and "x_nc_calendar_id" in self._fields:
            config = self.env["nextcloud.calendar.sync.config"].sudo().browse(
                int(config_id)).exists().filtered_domain(self._mobile_calendar_domain())
            if not config:
                raise UserError(_("Ce calendrier n'est pas le vôtre."))
            if config.exists():
                vals["x_nc_calendar_id"] = config.id
                # La couleur SUIT le calendrier, comme partout ailleurs sur
                # cette base : la choisir à la main ferait un événement qui ne
                # ressemble à aucun de ses voisins.
                if "color" in self._fields and config.id:
                    voisin = self.search(
                        [("x_nc_calendar_id", "=", config.id), ("color", "!=", 0)],
                        limit=1, order="id desc")
                    if voisin:
                        vals["color"] = voisin.color
        event = self.create(vals)
        return {"ok": True, "event": event._mobile_payload()}

    # ------------------------------------------------------------------
    # Participants
    # ------------------------------------------------------------------

    @api.model
    def mobile_partners(self, query, limit=20):
        """Qui inviter : les contacts que la personne a le droit de voir.

        Deux caractères au moins, sinon on rendrait le carnet entier à chaque
        frappe ; et par le nom OU le courriel, parce qu'on connaît souvent
        l'adresse d'une personne avant l'orthographe de son nom.
        """
        query = (query or "").strip()
        if len(query) < 2:
            return []
        partners = self.env["res.partner"].search(
            ["|", ("name", "ilike", query), ("email", "ilike", query)],
            limit=max(1, min(int(limit or 20), 50)), order="name asc")
        return [
            {"id": p.id, "name": p.display_name or "", "email": p.email or ""}
            for p in partners
        ]

    def mobile_set_attendees(self, add_ids=None, remove_ids=None, notify=False):
        """Ajouter ou retirer des participants depuis le téléphone.

        ⚠️ Odoo envoie l'invitation à tout participant ajouté, en `force_send`,
        et ce module ne poste aucun courriel sans qu'on le lui ait demandé
        (voir l'en-tête du contrôleur). L'invitation ne part donc que si le
        téléphone le dit explicitement (`notify`), sinon `no_mail_to_attendees`
        la retient — c'est le même levier que la case « Envoyer par courriel »
        du bureau.

        L'organisateur ne se retire pas : sans lui la rencontre sortirait de
        « mon agenda » et deviendrait introuvable depuis l'app qui vient de
        la modifier.
        """
        self.ensure_one()
        Partner = self.env["res.partner"]
        add = Partner.browse([int(i) for i in (add_ids or [])]).exists()
        remove = Partner.browse([int(i) for i in (remove_ids or [])]).exists()
        organizer = self.user_id.partner_id
        if organizer and organizer in remove:
            raise UserError(_("L'organisateur reste sur sa rencontre."))
        commands = [(4, p.id) for p in add if p not in self.partner_ids]
        commands += [(3, p.id) for p in remove if p in self.partner_ids]
        if commands:
            # ⚠️ Posé dans les DEUX sens, jamais « laissé tel quel » : un
            # enregistrement hérite du contexte de qui l'a créé, et un
            # `no_mail_to_attendees` reçu en amont aurait avalé l'invitation
            # demandée. Vu au banc, où l'événement est créé sans courriel.
            self.with_context(no_mail_to_attendees=not notify).write(
                {"partner_ids": commands})
            ajoutes = ", ".join(p.display_name for p in add if p not in remove)
            retires = ", ".join(p.display_name for p in remove)
            morceaux = []
            if ajoutes:
                morceaux.append(_("a ajouté %s", ajoutes))
            if retires:
                morceaux.append(_("a retiré %s", retires))
            self.message_post(
                body=_("%(who)s %(what)s depuis l'application mobile%(mail)s.",
                       who=self.env.user.display_name,
                       what=" ; ".join(morceaux),
                       mail=_(" (invitation envoyée)") if notify and ajoutes else ""),
                subtype_xmlid="mail.mt_note",
            )
        return {"ok": True, "event": self.mobile_detail()}

    @api.model
    def _mobile_calendar_domain(self):
        """Les configurations où la personne a le droit d'écrire : les siennes,
        et celles sans propriétaire. Poser une rencontre dans le calendrier
        CalDAV d'un collègue serait écrire dans son agenda."""
        return ["|", ("calendar_owner_id", "=", self.env.uid),
                ("calendar_owner_id", "=", False)]

    @api.model
    def mobile_calendars(self):
        """Les calendriers où la personne peut poser une rencontre."""
        if "nextcloud.calendar.sync.config" not in self.env:
            return []
        configs = self.env["nextcloud.calendar.sync.config"].sudo().search(
            self._mobile_calendar_domain())
        sorties = []
        for config in configs:
            voisin = self.search(
                [("x_nc_calendar_id", "=", config.id), ("color", "!=", 0)],
                limit=1, order="id desc")
            cle = voisin.color if voisin else 0
            sorties.append({
                "id": config.id,
                "name": config.display_name or "",
                "color": odoo_palette.couleur(cle),
                "color_soft": odoo_palette.couleur_douce(cle),
            })
        return sorties

    def mobile_set_flags(self, skip_agenda=None, skip_dashboard=None):
        """Poser ou retirer les exclusions de `bf_meeting`.

        Rendre la fiche complète plutôt qu'un accusé : l'app réaffiche sans
        avoir à deviner ce que le serveur a retenu.
        """
        self.ensure_one()
        if "bf_skip_agenda" not in self._fields:
            raise UserError(_("Les exclusions demandent le module Rencontres."))
        vals = {}
        if skip_agenda is not None:
            vals["bf_skip_agenda"] = bool(skip_agenda)
        if skip_dashboard is not None:
            vals["bf_skip_dashboard"] = bool(skip_dashboard)
        if vals:
            self.write(vals)
        return {"ok": True, "event": self._mobile_payload()}
