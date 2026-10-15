# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Agendas externes abonnés (lot 6).

La moitié « Symbifox voit Outlook » du lien bidirectionnel, en protocoles
ouverts seulement (arbitrage du 2026-10-14). Outlook, Google ou
Apple publient un agenda en iCalendar ; Symbifox le relit à intervalle, le
montre en couche en lecture dans la grille, et ses créneaux occupés ferment
la prise de rendez-vous (resource_booking compte « occupé » tout événement dont
la personne est l'organisatrice).

Les événements importés ne repartent jamais nulle part : ni vers Nextcloud
(aucun agenda Nextcloud, `skip_nc_sync`), ni par les liens de partage autrement
que « Occupé » (ils sont privés par défaut), ni en invitation.
"""

import logging
import socket
import threading
import time as time_module
from datetime import date, datetime, time, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from . import fetch

_logger = logging.getLogger(__name__)

try:
    import vobject
except ImportError:  # pragma: no cover
    vobject = None

MAX_INSTANCES = 5000
CTX_SYNCHRO = {
    "bf_subscription_sync": True, "skip_nc_sync": True, "no_mail_to_attendees": True,
    "tracking_disable": True, "mail_create_nolog": True, "dont_notify": True,
    "mail_notrack": True,
}


def _utc_naif(valeur, tz_defaut):
    """Une date-heure de l'agenda en UTC naïf (heure « flottante » = fuseau de l'usager)."""
    if valeur.tzinfo is None:
        valeur = tz_defaut.localize(valeur)
    return valeur.astimezone(pytz.utc).replace(tzinfo=None)


def _cle_instant(valeur, tz_defaut):
    if isinstance(valeur, datetime):
        return _utc_naif(valeur, tz_defaut).strftime("%Y%m%dT%H%M%SZ")
    return valeur.strftime("%Y%m%d")


FREQUENCES_REFUSEES = ("FREQ=SECONDLY", "FREQ=MINUTELY")
# Une règle à la minute ou à la seconde se cache aussi derrière BYMINUTE/BYSECOND
# (« HOURLY;BYMINUTE=0,…,59 ») ; aucun agenda de rencontres n'en a besoin.
PARTIES_REFUSEES = FREQUENCES_REFUSEES + ("BYMINUTE=", "BYSECOND=")
DUREE_DEPLIAGE = 30  # secondes pour tout un agenda (relecture adverse, second tour, A)
# Ce qu'un lien de partage de Symbifox a lui-même importé : ne pas le réimporter,
# sinon deux Symbifox abonnés l'un à l'autre se renvoient leurs créneaux sans fin.
MARQUE_IMPORTE = "x-symbifox-imported"


def _regle_refusee(ev):
    """Une règle trop fine (ou une EXRULE, dépréciée) que dateutil déplierait sans fin."""
    if "exrule" in ev.contents:
        return True
    for ligne in ev.contents.get("rrule", []):
        valeur = (ligne.value or "").upper()
        if any(p in valeur for p in PARTIES_REFUSEES):
            return True
    return False


def instances(texte, debut, fin, tz_defaut):
    """Les occurrences de l'agenda entre ``debut`` et ``fin`` (UTC naïfs).

    Rend ``(occurrences, ignores, tronque)``. Chaque occurrence est un dict :
    key, journee, start, stop (UTC naïfs ; dates pour une journée entière, fin
    INCLUSE), summary, location, transparent, serie.

    Relecture adverse du lot 6 : chaque VEVENT est lu à part (un seul fautif
    gelait tout l'abonnement, M5) ; la clé d'une exception est son instant
    D'ORIGINE, et une clé déjà vue reçoit un suffixe (M4) ; les répétitions à la
    seconde ou à la minute sont refusées (dateutil les construit toutes depuis
    leur début, M3) ; le plafond vaut pour tout et se dit.
    """
    if vobject is None:
        raise UserError(_("The vobject library is missing."))
    try:
        cal = vobject.readOne(texte)
    except Exception as exc:  # noqa: BLE001 (fichier tiers)
        raise UserError(_("This address does not return a readable calendar.")) from exc
    if getattr(cal, "name", "").upper() != "VCALENDAR":
        # Un VCARD ou une page vide effaçait tout l'agenda importé.
        raise UserError(_("This address does not return a calendar."))
    evenements = cal.contents.get("vevent", [])
    remplacees = {}
    for ev in evenements:
        try:
            if "recurrence-id" in ev.contents and "uid" in ev.contents:
                remplacees[(ev.uid.value, _cle_instant(ev.recurrence_id.value, tz_defaut))] = ev
        except Exception:  # noqa: BLE001
            continue
    sortie, vues = [], {}
    etat = {"ignores": 0, "tronque": False}
    echeance = time_module.monotonic() + DUREE_DEPLIAGE

    def a_bout():
        if len(sortie) >= MAX_INSTANCES or time_module.monotonic() > echeance:
            etat["tronque"] = True
        return etat["tronque"]

    def ajouter(ev, depart, cle, serie):
        if len(sortie) >= MAX_INSTANCES:
            etat["tronque"] = True
            return
        statut = (ev.status.value.upper() if "status" in ev.contents else "")
        if statut == "CANCELLED":
            return
        dtstart = ev.dtstart.value
        if "dtend" in ev.contents:
            dtend = ev.dtend.value
            if isinstance(dtend, datetime) != isinstance(dtstart, datetime):
                dtend = dtstart  # date et date-heure mêlées : durée nulle
            duree = dtend - dtstart
        elif "duration" in ev.contents:
            duree = ev.duration.value
        else:
            duree = timedelta(days=1) if not isinstance(dtstart, datetime) else timedelta(0)
        duree = max(duree, timedelta(0))
        journee = not isinstance(depart, datetime) or not isinstance(dtstart, datetime)
        transp = (ev.transp.value.upper() if "transp" in ev.contents else "OPAQUE")
        if journee:
            d = depart.date() if isinstance(depart, datetime) else depart
            jours = max(int(duree.days), 1)
            vals = {"journee": True, "start": d, "stop": d + timedelta(days=jours - 1)}
            borne_debut, borne_fin = (datetime.combine(d, time.min),
                                      datetime.combine(vals["stop"], time.max))
        else:
            s = _utc_naif(depart, tz_defaut)
            vals = {"journee": False, "start": s, "stop": s + duree}
            borne_debut, borne_fin = vals["start"], vals["stop"]
        if borne_fin < debut or borne_debut > fin:
            return
        n = vues.get(cle, 0)
        vues[cle] = n + 1
        vals.update({
            "key": cle if not n else "%s#%d" % (cle, n),
            "summary": ev.summary.value if "summary" in ev.contents else "",
            "location": ev.location.value if "location" in ev.contents else "",
            "transparent": transp == "TRANSPARENT",
            "serie": serie,
        })
        sortie.append(vals)

    for ev in evenements:
        if a_bout():
            break
        try:
            if "dtstart" not in ev.contents or "recurrence-id" in ev.contents:
                continue
            if MARQUE_IMPORTE in ev.contents:
                continue
            uid = ev.uid.value if "uid" in ev.contents else ""
            if "rrule" not in ev.contents and "rdate" not in ev.contents:
                ajouter(ev, ev.dtstart.value, "%s|%s" % (uid, _cle_instant(ev.dtstart.value, tz_defaut)), False)
                continue
            if _regle_refusee(ev):
                etat["ignores"] += 1
                continue
            regle = ev.getrruleset(addRDate=True)
            dtstart = ev.dtstart.value
            if isinstance(dtstart, datetime) and dtstart.tzinfo is not None:
                a = pytz.utc.localize(debut - timedelta(days=1))
                b = pytz.utc.localize(fin + timedelta(days=1))
            else:
                a, b = debut - timedelta(days=1), fin + timedelta(days=1)
            for depart in regle.xafter(a, inc=True) if hasattr(regle, "xafter") else regle.between(a, b, inc=True):
                if depart > b or a_bout():
                    break
                if not isinstance(dtstart, datetime):
                    depart = depart.date()
                cle = "%s|%s" % (uid, _cle_instant(depart, tz_defaut))
                remplacante = remplacees.pop((uid, _cle_instant(depart, tz_defaut)), None)
                if remplacante is not None:
                    ajouter(remplacante, remplacante.dtstart.value, cle, True)
                else:
                    ajouter(ev, depart, cle, True)
        except Exception:  # noqa: BLE001 (VEVENT tiers mal formé : sauté, compté)
            etat["ignores"] += 1
    # Les exceptions dont l'occurrence d'origine tombe hors de l'horizon.
    for (uid, origine), ev in remplacees.items():
        if a_bout():
            break
        if MARQUE_IMPORTE in ev.contents:
            continue
        try:
            ajouter(ev, ev.dtstart.value, "%s|%s" % (uid, origine), True)
        except Exception:  # noqa: BLE001
            etat["ignores"] += 1
    return sortie, etat["ignores"], etat["tronque"]


class BfCalendarSubscription(models.Model):
    _name = "bf.calendar.subscription"
    _description = "External calendar subscription"
    _inherit = ["bf.color.mixin"]
    _order = "name"

    name = fields.Char(required=True, help="For example: Main client (Outlook).")
    owner_id = fields.Many2one(
        "res.users", string="Shown in the calendar of", required=True, index=True,
        default=lambda self: self.env.user, ondelete="cascade", readonly=True)
    url = fields.Char(
        string="Published address", required=True,
        help="The iCalendar address the other calendar publishes (https:// or webcal://). "
             "In Outlook: Settings > Calendar > Shared calendars > Publish a calendar, "
             "then copy the ICS link. Keep it private: it gives read access to that calendar.")
    active = fields.Boolean(default=True)
    privacy = fields.Selection(
        [("private", "Private"), ("confidential", "Only internal users"), ("public", "Public")],
        string="Privacy of imported events", default="private", required=True,
        help="Private: colleagues and sharing links only see Busy.")
    import_free = fields.Boolean(
        string="Also import free events",
        help="By default only busy events are imported. Busy imported events close "
             "online booking slots; free ones do not.")
    import_location = fields.Boolean(string="Import locations", default=False)
    past_days = fields.Integer(default=7)
    future_days = fields.Integer(default=180)
    refresh_minutes = fields.Integer(string="Refresh every (minutes)", default=60)
    last_fetch = fields.Datetime(readonly=True)
    state = fields.Selection([("new", "Never read"), ("ok", "Up to date"), ("error", "Error")],
                             default="new", readonly=True)
    last_error = fields.Char(readonly=True)
    http_etag = fields.Char(readonly=True)
    http_last_modified = fields.Char(readonly=True)
    event_count = fields.Integer(compute="_compute_event_count")

    def _compute_event_count(self):
        Event = self.env["calendar.event"].sudo()
        for sub in self:
            sub.event_count = Event.search_count([("bf_subscription_id", "=", sub.id)])

    @api.constrains("url")
    def _check_url(self):
        for sub in self:
            try:
                fetch.normaliser(sub.url)
            except fetch.LectureRefusee as exc:
                raise UserError(str(exc)) from exc

    OPTIONS_RELECTURE = {"url", "privacy", "import_free", "import_location", "past_days", "future_days"}

    def write(self, vals):
        # Relecture adverse, M1 : la règle d'accès se vérifie AVANT l'écriture ;
        # changer ensuite le propriétaire posait des créneaux occupés chez autrui.
        if "owner_id" in vals and any(s.owner_id.id != vals["owner_id"] for s in self) \
                and not self.env.su:
            raise UserError(_("A subscription cannot be moved to someone else's calendar."))
        # Une option changée doit se voir à la lecture suivante, même si l'agenda
        # distant répond « inchangé » (304).
        if self.OPTIONS_RELECTURE & set(vals):
            vals = dict(vals, http_etag=False, http_last_modified=False)
        if "url" in vals:
            vals["state"] = "new"
        if vals.get("active") is True:
            vals = dict(vals, http_etag=False, http_last_modified=False)
        res = super().write(vals)
        if vals.get("active") is False:
            # Archivé, il ne ferme plus de créneaux (relecture adverse, second tour).
            self.env["calendar.event"].sudo().with_context(**CTX_SYNCHRO).search(
                [("bf_subscription_id", "in", self.ids)]).write({"active": False})
        return res

    def unlink(self):
        self.env["calendar.event"].sudo().with_context(**CTX_SYNCHRO).search(
            [("bf_subscription_id", "in", self.ids)]).unlink()
        return super().unlink()

    def action_view_events(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window", "res_model": "calendar.event",
            "name": self.name, "view_mode": "list,form",
            "domain": [("bf_subscription_id", "=", self.id)],
        }

    def action_refresh(self):
        for sub in self:
            sub._bf_synchroniser(force=True)

    # ------------------------------------------------------------------
    # Synchronisation
    # ------------------------------------------------------------------

    def _bf_synchroniser(self, force=False):
        self.ensure_one()
        sub = self.sudo()
        hotes, ips = self._bf_adresses_propres()
        try:
            statut, entetes, corps = fetch.lire(
                sub.url, None if force else sub.http_etag, None if force else sub.http_last_modified,
                hotes_interdits=hotes, ips_interdites=ips)
            if statut == 304:
                sub.write({"last_fetch": fields.Datetime.now(), "state": "ok", "last_error": False})
                return
            if statut != 200:
                raise UserError(_("The calendar server answered %s.", statut))
            texte = corps.decode("utf-8", errors="replace")
            tz = pytz.timezone(sub.owner_id.tz or "UTC")
            maintenant = fields.Datetime.now()
            lues, ignores, tronque = instances(
                texte, maintenant - timedelta(days=min(max(sub.past_days, 0), 366)),
                maintenant + timedelta(days=min(max(sub.future_days, 0), 731)), tz)
            # Tout ou rien : une application à moitié laissait des périmés et
            # n'ajoutait pas les nouveaux (relecture adverse, M5).
            with self.env.cr.savepoint():
                sub._bf_appliquer(lues)
            remarque = []
            if ignores:
                remarque.append(_("%s unreadable or unsupported events were skipped.", ignores))
            if tronque:
                remarque.append(_("Only the first %s occurrences were imported.", MAX_INSTANCES))
            sub.write({
                "last_fetch": maintenant, "state": "ok", "last_error": " ".join(remarque) or False,
                "http_etag": entetes.get("ETag") or entetes.get("etag") or False,
                "http_last_modified": entetes.get("Last-Modified") or entetes.get("last-modified") or False,
            })
        except (fetch.LectureRefusee, UserError) as exc:
            sub.write({"last_fetch": fields.Datetime.now(), "state": "error", "last_error": str(exc)[:250]})
        except Exception as exc:  # noqa: BLE001 (réseau, TLS, serveur tiers)
            _logger.warning("Abonnement %s : lecture en échec : %s", sub.id, exc)
            sub.write({"last_fetch": fields.Datetime.now(), "state": "error",
                       "last_error": _("The calendar could not be read (%s).", type(exc).__name__)})

    def _bf_appliquer(self, lues):
        self.ensure_one()
        Event = self.env["calendar.event"].sudo().with_context(**CTX_SYNCHRO)
        existants, doublons = {}, Event.browse()
        for e in Event.with_context(active_test=False).search(
                [("bf_subscription_id", "=", self.id)], order="id"):
            if e.bf_subscription_key in existants:
                doublons |= e  # relecture adverse, M4 : une clé, un événement
            else:
                existants[e.bf_subscription_key] = e
        if doublons:
            doublons.unlink()
        vus = set()
        moi = self.owner_id.partner_id
        for lue in lues:
            if lue["transparent"] and not self.import_free:
                continue
            vus.add(lue["key"])
            vals = {
                "name": lue["summary"] or _("Busy"),
                "allday": lue["journee"],
                "show_as": "free" if lue["transparent"] else "busy",
                "privacy": self.privacy,
                "location": lue["location"] if self.import_location else False,
                "bf_subscription_serie": lue["serie"],
            }
            if lue["journee"]:
                # Comparées en dates : une journée entière garde les heures que le
                # cœur lui donne (8 h à 18 h), qu'il ne faut pas réécrire à chaque passage.
                vals.update({"start_date": lue["start"], "stop_date": lue["stop"]})
            else:
                vals.update({"start": lue["start"], "stop": lue["stop"]})
            event = existants.get(lue["key"])
            if event:
                ecart = {k: v for k, v in vals.items() if (event[k] or False) != (v or False)}
                if not event.active:
                    ecart["active"] = True
                if ecart:
                    event.write(ecart)
                continue
            if lue["journee"]:
                vals.update({"start": datetime.combine(lue["start"], time(8)),
                             "stop": datetime.combine(lue["stop"], time(18))})
            vals.update({
                "user_id": self.owner_id.id,
                "partner_ids": [(6, 0, moi.ids)],
                "alarm_ids": [(5, 0, 0)],
                "bf_subscription_id": self.id,
                "bf_subscription_key": lue["key"],
            })
            # Rien à préparer pour un événement d'un autre agenda : ni ordre du
            # jour ni tableau de bord des rencontres (liens mous vers bf_meeting).
            for champ in ("bf_skip_agenda", "bf_skip_dashboard"):
                if champ in Event._fields:
                    vals[champ] = True
            cree = Event.create(vals)
            self._bf_hors_nextcloud(cree)
        # Relecture adverse, B1 : la synchro Nextcloud rattache tout événement neuf
        # à l'agenda par défaut ; un événement importé n'a rien à y faire.
        self._bf_hors_nextcloud(Event.browse([e.id for e in existants.values()]))
        partis = [e for cle, e in existants.items() if cle not in vus]
        if partis:
            Event.browse([e.id for e in partis]).unlink()

    @api.model
    def _bf_adresses_propres(self):
        """Les noms et adresses IP de ce serveur, refusés comme source.

        Par le nom seul, un autre domaine du même site (sans « www ») passait
        (relecture adverse, second tour, B).
        """
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "") or ""
        hotes = {base.split("//")[-1].split("/")[0].split(":")[0]}
        if "website" in self.env:
            for domaine in self.env["website"].sudo().search([]).mapped("domain"):
                if domaine:
                    hotes.add(domaine.split("//")[-1].split("/")[0].split(":")[0])
        hotes = {h.lower() for h in hotes if h}
        ips = set()
        for hote in hotes:
            try:
                ips |= {info[4][0] for info in socket.getaddrinfo(hote, 443, proto=socket.IPPROTO_TCP)}
            except (OSError, UnicodeError):
                continue
        return hotes, ips

    @api.model
    def _bf_hors_nextcloud(self, events):
        champs = {"x_nc_calendar_id": False, "x_nc_uid": False, "x_sync_source": False}
        champs = {k: v for k, v in champs.items() if k in events._fields}
        if champs:
            a_nettoyer = events.filtered(lambda e: any(e[k] for k in champs))
            if a_nettoyer:
                a_nettoyer.with_context(**CTX_SYNCHRO).write(champs)

    @api.model
    def _cron_bf_synchroniser(self):
        """Le plus ancien d'abord, et une transaction par abonnement (relecture adverse, M3).

        En une seule transaction, un abonnement lent faisait tuer tout le cron
        (limit_time_real) et repassait en premier au tour suivant.
        """
        maintenant = fields.Datetime.now()
        enregistrer = not getattr(threading.current_thread(), "testing", False)
        abonnements = self.sudo().search([("active", "=", True), ("owner_id.active", "=", True)])
        for sub in abonnements.sorted(lambda s: s.last_fetch or datetime.min):
            if sub.last_fetch and sub.last_fetch > maintenant - timedelta(minutes=max(sub.refresh_minutes, 15)):
                continue
            # Réservé AVANT de lire : si le cron est tué pendant cette lecture,
            # l'abonnement passe en dernier au tour suivant au lieu d'affamer
            # tous les autres (relecture adverse, second tour, A).
            sub.write({"last_fetch": fields.Datetime.now()})
            if enregistrer:
                self.env.cr.commit()
            try:
                with self.env.cr.savepoint():
                    sub._bf_synchroniser()
            except Exception as exc:  # noqa: BLE001
                _logger.error("Abonnement %s en échec : %s", sub.id, exc)
            if enregistrer:
                self.env.cr.commit()
