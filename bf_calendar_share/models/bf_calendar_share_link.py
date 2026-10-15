# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Liens de partage de l'agenda vers l'extérieur (lot 5).

Trois formes, toutes en lecture et toutes ouvertes (aucun protocole fermé) :
abonnement iCalendar (RFC 5545, que lisent Outlook, Google, Apple et
Thunderbird), disponibilités en VFREEBUSY, et une page web. Le niveau se
filtre ICI, côté serveur : un lien « disponibilités » ne contient ni titre, ni
lieu, ni participant dans ce qu'il sert.

arbitrage du 2026-10-14 : un lien montre les événements « internes
seulement » au niveau choisi (le créer est la décision de les montrer à cette
personne) ; un événement privé reste « Occupé » sur tous les liens ; portée
choisie par lien ; expiration à 90 jours par défaut.

Ce que montre « tous les détails », et ce qu'aucun lien ne montre jamais :
les participants (des tiers, dont le courriel et la présence ne sont pas à
communiquer à l'extérieur, Loi 25) et le lien de visioconférence (il donne
accès à la rencontre). Les étiquettes non plus (un nom de client y vit
souvent) : seulement le type de plage.
"""

import secrets
from datetime import date, datetime, time, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import html2plaintext
from odoo.tools.misc import hmac

from . import ics
from .res_users import NIVEAUX

NIVEAUX_LIEN = [n for n in NIVEAUX if n[0] != "edit"]
JOURS_RAPPEL = 7
JOURS_AVENIR_HISTORIQUE = 3650  # « Tout l'historique » : dix ans devant


def _jeton():
    return secrets.token_urlsafe(32)


class BfCalendarShareLink(models.Model):
    _name = "bf.calendar.share.link"
    _description = "Calendar sharing link"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "active desc, date_expiry, id desc"

    name = fields.Char(
        string="Shared with", required=True, tracking=True,
        help="Who this link is for, for your own records (for example: accounting firm).")
    owner_id = fields.Many2one(
        "res.users", string="Calendar of", required=True, index=True,
        default=lambda self: self.env.user, ondelete="cascade", readonly=True)
    level = fields.Selection(
        NIVEAUX_LIEN, string="Level", required=True, default="freebusy", tracking=True,
        help="What the link shows. Private meetings always show as Busy.")
    token = fields.Char(required=True, readonly=True, copy=False, index="btree_not_null",
                        default=lambda self: _jeton())
    active = fields.Boolean(default=True, tracking=True)
    date_expiry = fields.Date(
        string="Expires on", tracking=True, required=True,
        default=lambda self: fields.Date.context_today(self) + timedelta(days=90),
        help="After this date the link no longer answers (90 days by default).")
    bf_rappel_pour = fields.Date(copy=False, readonly=True,
                                 help="Expiry date already reminded (one reminder per expiry date).")
    past_days = fields.Integer(string="Days in the past", default=30)
    future_days = fields.Integer(string="Days ahead", default=180)
    full_history = fields.Boolean(
        string="Full history",
        help="Every past meeting, with no limit, and the next ten years. The file gets large "
             "for a busy calendar, and anyone holding the address sees all of it.")
    url_ics = fields.Char(string="Subscription (iCalendar)", compute="_compute_urls")
    url_webcal = fields.Char(string="Subscription (webcal)", compute="_compute_urls")
    url_freebusy = fields.Char(string="Availability (free/busy)", compute="_compute_urls")
    url_page = fields.Char(string="Web page", compute="_compute_urls")
    access_ids = fields.One2many("bf.calendar.share.access", "link_id", string="Accesses")
    access_count = fields.Integer(compute="_compute_access", string="Accesses")
    last_access = fields.Datetime(compute="_compute_access", string="Last access")

    _sql_constraints = [
        ("token_uniq", "unique (token)", "Token collision, try again."),
        ("past_days_range", "CHECK (past_days BETWEEN 0 AND 366)", "Days in the past: 0 to 366."),
        ("future_days_range", "CHECK (future_days BETWEEN 0 AND 731)", "Days ahead: 0 to 731."),
    ]

    @api.depends("token")
    @api.depends_context("uid")
    def _compute_urls(self):
        # L'adresse publique des liens, si elle diffère de web.base.url : chez BF,
        # « www. » répond par une redirection 308 que des clients d'agenda suivent mal
        # (constaté à la pose du 2026-10-15).
        Param = self.env["ir.config_parameter"].sudo()
        base = (Param.get_param("bf_calendar_share.base_url") or Param.get_param("web.base.url", "")).rstrip("/")
        for link in self:
            # Les adresses ne se montrent qu'au propriétaire : l'inventaire des
            # administrateurs dit ce qui sort, il ne sert pas à l'ouvrir (second tour, m-5).
            if link.owner_id != self.env.user and not self.env.su:
                link.url_ics = link.url_webcal = link.url_freebusy = link.url_page = False
                continue
            racine = "%s/calendar/share/%s" % (base, link.token)
            link.url_ics = racine + ".ics"
            link.url_webcal = "webcal" + link.url_ics[link.url_ics.index(":"):] if ":" in base else ""
            link.url_freebusy = racine + "/freebusy.ifb"
            link.url_page = racine

    def _compute_access(self):
        Access = self.env["bf.calendar.share.access"].sudo()
        for link in self:
            acces = Access.search([("link_id", "=", link.id)], order="date desc")
            link.access_count = len(acces)
            link.last_access = acces[:1].date

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # Le jeton se tire ici, jamais par l'appelant (relecture adverse, B2 :
            # « cccc… » était accepté).
            vals.pop("token", None)
        return super().create(vals_list)

    def write(self, vals):
        # Relecture adverse, B2 : changer le propriétaire après coup publiait
        # l'agenda d'autrui ; écrire le jeton donnait des jetons faibles. Les deux
        # ne changent que par les boutons (sudo, après vérification d'accès).
        change = ("token" in vals and any(l.token != vals["token"] for l in self)) or \
            ("owner_id" in vals and any(l.owner_id.id != vals["owner_id"] for l in self))
        if change and not self.env.su:
            raise UserError(_("The calendar and the address of a link cannot be changed this way."))
        return super().write(vals)

    # ------------------------------------------------------------------
    # Gestes
    # ------------------------------------------------------------------

    def action_revoke(self):
        """Retirer : le lien ne répond plus, et ne répondra plus jamais (jeton changé)."""
        self.check_access("write")
        for link in self:
            link.sudo().write({"active": False, "token": _jeton()})
            link.message_post(body=_("Link revoked: its address no longer answers."))

    def action_new_address(self):
        """Nouvelle adresse : l'ancienne meurt, la personne doit recevoir la nouvelle."""
        self.check_access("write")
        for link in self:
            link.sudo().write({"token": _jeton()})
            link.message_post(body=_("New address: the previous one no longer answers."))

    def action_extend(self):
        for link in self:
            link.date_expiry = fields.Date.context_today(link) + timedelta(days=90)
            link.activity_ids.filtered(
                lambda a: a.activity_type_id == self.env.ref("mail.mail_activity_data_todo")
            ).action_feedback(feedback=_("Extended by 90 days."))

    # ------------------------------------------------------------------
    # Ce que le lien sert
    # ------------------------------------------------------------------

    @api.model
    def _bf_lien_valide(self, token):
        """Le lien de ce jeton s'il répond, sinon un recordset vide (même réponse pour tous les cas)."""
        if not token or len(token) < 20:
            return self.browse()
        link = self.sudo().search([("token", "=", token)], limit=1)
        if not link or not link.active:
            return self.browse()
        # Le jour du PROPRIÉTAIRE : appelé par l'usager public (UTC), le lien
        # expirait quatre heures trop tôt le soir (relecture adverse).
        if link.date_expiry and link.date_expiry < fields.Date.context_today(
                link.with_context(tz=link.owner_id.tz or "UTC")):
            return self.browse()
        if not link.owner_id.active:
            return self.browse()
        return link

    def _bf_domaine_portee(self):
        """Le domaine des événements que ce lien peut montrer (étendu par le pont Nextcloud)."""
        self.ensure_one()
        debut, fin = self._bf_fenetre()
        moi = self.owner_id.partner_id
        domaine = ["|", ("user_id", "=", self.owner_id.id), ("partner_ids", "in", moi.ids),
                   ("start", "<=", fin)]
        if debut:
            domaine.append(("stop", ">=", debut))
        return domaine

    def _bf_fenetre(self):
        """``(début, fin)`` de ce que le lien couvre ; début vide pour tout l'historique."""
        self.ensure_one()
        maintenant = fields.Datetime.now()
        if self.full_history:
            # Dix ans devant : un anniversaire ou une série sans fin irait sinon jusqu'en 2744.
            return None, maintenant + timedelta(days=JOURS_AVENIR_HISTORIQUE)
        return (maintenant - timedelta(days=max(self.past_days, 0)),
                maintenant + timedelta(days=max(self.future_days, 0)))

    def _bf_evenements(self):
        self.ensure_one()
        events = self.env["calendar.event"].sudo().search(self._bf_domaine_portee(), order="start")
        if "bf_event_status" in events._fields:
            events = events.filtered(lambda e: e.bf_event_status != "cancelled")
        moi = self.owner_id.partner_id
        # Une invitation refusée n'occupe pas l'agenda (relecture adverse).
        return events.filtered(lambda e: e.user_id == self.owner_id or not any(
            a.partner_id == moi and a.state == "declined" for a in e.attendee_ids))

    @staticmethod
    def _bf_confidentialite_effective(event):
        """Celle de l'événement, sinon le défaut de son organisateur (comme le cœur)."""
        return event.privacy or event.user_id.calendar_default_privacy or "public"

    def _bf_vue(self, event):
        """Ce que ce lien montre de ``event`` : un dict, ou None pour ne rien montrer."""
        self.ensure_one()
        libre = event.show_as == "free"
        if self.level == "freebusy" and libre:
            return None
        effective = self._bf_confidentialite_effective(event)
        if event.user_id == self.owner_id:
            # Ses propres rencontres : le privé reste « Occupé », « internes
            # seulement » se montre (créer le lien est la décision de le montrer).
            prive = effective == "private"
        else:
            # Celles d'autrui : selon la confidentialité que leur donne leur
            # organisateur ; seule une rencontre publique se montre.
            prive = effective != "public"
        vue = {"transparent": libre, "occupe": prive or self.level == "freebusy"}
        if vue["occupe"]:
            vue["resume"] = _("Busy")
            return vue
        vue["resume"] = event.name or ""
        vue["lieu"] = event.location or ""
        if "bf_block_type_id" in event._fields and event.bf_block_type_id:
            vue["categories"] = [event.bf_block_type_id.name]
        if self.level == "details":
            vue["description"] = html2plaintext(event.description or "").strip()
        return vue

    def _bf_uid(self, event):
        domaine = (self.env["ir.config_parameter"].sudo().get_param("web.base.url", "") or "symbifox")
        domaine = domaine.split("//")[-1].split("/")[0] or "symbifox"
        # Ni l'UID Nextcloud ni les ids : une empreinte propre au lien, stable
        # d'une lecture à l'autre (relecture adverse).
        empreinte = hmac(self.env(su=True), "bf_calendar_share.uid", "%s:%s" % (self.id, event.id))
        return "%s@%s" % (empreinte[:32], domaine)

    def _bf_ics(self):
        self.ensure_one()
        link = self.with_context(lang=self.owner_id.lang)
        maintenant = fields.Datetime.now()
        composants = []
        for event in link._bf_evenements():
            vue = link._bf_vue(event)
            if vue is None:
                continue
            if event.allday:
                debut = event.start_date or event.start.date()
                fin = event.stop_date or event.stop.date()
            else:
                debut, fin = event.start, event.stop
            composants += ics.lignes_evenement(
                link._bf_uid(event), debut, fin, event.allday, vue["resume"], maintenant,
                transparent=vue["transparent"], lieu=vue.get("lieu"),
                description=vue.get("description"), categories=vue.get("categories"),
                classe="PRIVATE" if vue["occupe"] and self.level != "freebusy" else None,
                # Un créneau que Symbifox a lui-même importé d'un autre agenda : un
                # autre Symbifox abonné à ce lien ne le réimporte pas (pas de boucle).
                extra=["X-SYMBIFOX-IMPORTED:1"]
                if "bf_subscription_id" in event._fields and event.bf_subscription_id else ())
        nom = _("Calendar of %s", self.owner_id.name)
        return ics.calendrier(nom, composants)

    def _bf_freebusy(self):
        """VFREEBUSY (RFC 5545 §3.6.4) : les périodes occupées, fusionnées."""
        self.ensure_one()
        maintenant = fields.Datetime.now()
        periodes = []
        tz = pytz.timezone(self.owner_id.tz or "UTC")
        for event in self._bf_evenements():
            if event.show_as == "free":
                continue
            if event.allday:
                d = tz.localize(datetime.combine(event.start_date or event.start.date(), time.min))
                f = tz.localize(datetime.combine((event.stop_date or event.stop.date())
                                                 + timedelta(days=1), time.min))
                periodes.append((d.astimezone(pytz.utc).replace(tzinfo=None),
                                 f.astimezone(pytz.utc).replace(tzinfo=None)))
            else:
                periodes.append((event.start, event.stop))
        periodes.sort()
        fusion = []
        for d, f in periodes:
            if fusion and d <= fusion[-1][1]:
                fusion[-1] = (fusion[-1][0], max(fusion[-1][1], f))
            else:
                fusion.append((d, f))
        debut, fin = self._bf_fenetre()
        debut = debut or (fusion[0][0] if fusion else maintenant)
        composant = ["BEGIN:VFREEBUSY", "UID:freebusy-%s@symbifox" % self.id,
                     "DTSTAMP:%s" % ics.instant(maintenant),
                     "DTSTART:%s" % ics.instant(debut), "DTEND:%s" % ics.instant(fin)]
        composant += ["FREEBUSY;FBTYPE=BUSY:%s/%s" % (ics.instant(d), ics.instant(f)) for d, f in fusion]
        composant.append("END:VFREEBUSY")
        return ics.calendrier(_("Availability of %s", self.owner_id.name), composant)

    def _bf_jours_pour_la_page(self, semaines=4):
        """``[(date, [(heure, vue)])]`` des semaines à venir, à l'heure de l'agenda partagé."""
        self.ensure_one()
        link = self.with_context(lang=self.owner_id.lang)
        tz = pytz.timezone(self.owner_id.tz or "UTC")
        aujourd_hui = datetime.now(tz).date()
        limite = aujourd_hui + timedelta(weeks=semaines)
        jours = {}
        for event in link._bf_evenements():
            vue = link._bf_vue(event)
            if vue is None:
                continue
            if event.allday:
                premier = event.start_date or event.start.date()
                dernier = event.stop_date or event.stop.date()
                heure = _("All day")
            else:
                local = pytz.utc.localize(event.start).astimezone(tz)
                local_fin = pytz.utc.localize(event.stop).astimezone(tz)
                premier, dernier = local.date(), (local_fin - timedelta(seconds=1)).date()
                heure = "%s – %s" % (local.strftime("%H:%M"), local_fin.strftime("%H:%M"))
            # Chaque jour couvert, à partir d'aujourd'hui (une rencontre commencée
            # hier et pas finie, une journée entière sur plusieurs jours).
            j = max(premier, aujourd_hui)
            while j <= dernier and j < limite:
                jours.setdefault(j, []).append((heure, vue))
                j += timedelta(days=1)
        return sorted(jours.items())

    def _bf_journaliser(self, forme, ip, agent):
        self.ensure_one()
        self.env["bf.calendar.share.access"].sudo().create({
            "link_id": self.id, "kind": forme, "ip": (ip or "")[:64],
            "user_agent": (agent or "")[:256]})

    # ------------------------------------------------------------------
    # Tâches planifiées
    # ------------------------------------------------------------------

    @api.model
    def _cron_bf_rappels_et_menage(self):
        """Rappel sept jours avant l'expiration, et journal gardé un an (réglable)."""
        aujourd_hui = fields.Date.context_today(self)
        todo = self.env.ref("mail.mail_activity_data_todo")
        for link in self.search([("active", "=", True), ("date_expiry", "!=", False),
                                 ("date_expiry", "<=", aujourd_hui + timedelta(days=JOURS_RAPPEL)),
                                 ("date_expiry", ">=", aujourd_hui)]):
            if link.bf_rappel_pour == link.date_expiry:
                continue
            link.bf_rappel_pour = link.date_expiry
            link.activity_schedule(
                "mail.mail_activity_data_todo", date_deadline=link.date_expiry,
                user_id=link.owner_id.id,
                summary=_("Calendar link for %s expires soon", link.name),
                note=_("Extend it if it is still in use, or let it expire."))
        # Conservation pour la traçabilité (arbitrage du 2026-10-14 :
        # adresse IP complète). Durée réglable, un an par défaut.
        try:
            jours = int(self.env["ir.config_parameter"].sudo().get_param(
                "bf_calendar_share.access_retention_days", "365") or 365)
        except ValueError:
            jours = 365  # une valeur illisible ne doit pas emporter les rappels
        self.env["bf.calendar.share.access"].sudo().search([
            ("date", "<", fields.Datetime.now() - timedelta(days=max(jours, 1)))]).unlink()


class BfCalendarShareAccess(models.Model):
    _name = "bf.calendar.share.access"
    _description = "Calendar sharing link access"
    _order = "date desc"
    _log_access = False

    link_id = fields.Many2one("bf.calendar.share.link", required=True, ondelete="cascade", index=True)
    date = fields.Datetime(default=fields.Datetime.now, required=True, index=True)
    kind = fields.Selection([("ics", "Subscription"), ("freebusy", "Availability"), ("page", "Web page")],
                            required=True)
    ip = fields.Char(string="Address")
    user_agent = fields.Char(string="Client")
