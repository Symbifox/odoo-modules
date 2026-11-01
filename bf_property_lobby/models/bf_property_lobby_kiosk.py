"""La tablette de l'entrée et son journal d'appels.

🔴 **Page publique.** La tablette n'a pas de session : sa page se sert sans
compte, protégée par un jeton d'appareil. Seule l'EMPREINTE du jeton est
conservée (sha256) ; le jeton lui-même s'affiche une fois, à sa création.
Tout ce que la page peut faire passe par les méthodes privées ci-dessous,
appelées en sudo par le contrôleur après la vérification du jeton.

**Ce que la page voit** : les libellés que les résidents ont choisi de montrer,
et leur logement. Jamais un numéro de téléphone, jamais un nom qu'ils n'ont pas
consenti à afficher.
"""
import hashlib
import hmac
import re
import secrets
from datetime import timedelta

import psycopg2.errors

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.bf_property_core.models.mail_template import bf_mail_layout

# Plafonds contre l'abus d'une page publique.
CALLS_PER_MINUTE = 6
SAME_RESIDENT_WINDOW = timedelta(minutes=2)
# Une activité au responsable par tablette et par objet dans cette fenêtre.
ACTIVITY_WINDOW = timedelta(minutes=10)
# Indicatifs du plan nord-américain hors Canada et États-Unis (Caraïbes, etc.)
# et numéros surtaxés : la tablette ne compose jamais ces numéros, que le
# résident peut choisir lui-même au portail.
BLOCKED_AREA_CODES = {
    "900", "976", "242", "246", "264", "268", "284", "345", "441", "473", "649", "658",
    "664", "721", "758", "767", "784", "809", "829", "849", "868", "869", "876",
    # Indicatifs non géographiques (services, numéros personnels, interurbains).
    "456", "500", "533", "544", "566", "577", "588", "600", "622", "700", "710",
    "880", "881", "882",
}


def _digest(token):
    return hashlib.sha256((token or "").encode()).hexdigest()


class BfPropertyLobbyKiosk(models.Model):
    _name = "bf.property.lobby.kiosk"
    _description = "Portier sur tablette"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "building_id, name"

    name = fields.Char(string="Nom", required=True, tracking=True,
                       help="p. ex. Entrée nord.")
    active = fields.Boolean(default=True, tracking=True)
    building_id = fields.Many2one("bf.property.building", string="Immeuble", required=True,
                                  ondelete="cascade", index=True, tracking=True)
    organisation_id = fields.Many2one(related="building_id.organisation_id", store=True,
                                      string="Organisation")
    company_id = fields.Many2one(related="building_id.company_id", store=True, string="Société")
    user_id = fields.Many2one(
        "res.users", string="Responsable", required=True, default=lambda self: self.env.user,
        domain=[("share", "=", False)], tracking=True,
        help="Reçoit les livraisons annoncées et les appels qui n'ont pas pu sonner.")
    notify_by_email = fields.Boolean(
        string="Aussi par courriel", tracking=True,
        help="En plus de l'activité, le responsable reçoit un courriel à chaque livraison ou "
             "appel (le logement, jamais le nom du résident).")
    call_mode = fields.Selection(
        [("notify", "Prévenir le responsable"),
         ("phone", "Faire sonner le téléphone du résident")],
        string="Appel", required=True, default="notify", tracking=True,
        help="« Faire sonner » passe par la téléphonie Symbifox (Asterisk) : il faut "
             "le canal de la tablette et le contexte de composition.")
    sip_channel = fields.Char(
        string="Canal de la tablette", tracking=True,
        help="Le poste SIP de la tablette, qui sonne d'abord, p. ex. PJSIP/entree-nord.")
    dial_context = fields.Char(string="Contexte de composition", default="from-internal",
                               tracking=True)
    token_hash = fields.Char(string="Empreinte du jeton", readonly=True, copy=False,
                             groups="base.group_system")
    has_token = fields.Boolean(string="Appareil jumelé", compute="_compute_has_token")
    call_ids = fields.One2many("bf.property.lobby.call", "kiosk_id", string="Appels")
    throttle_stamp = fields.Datetime(string="Dernière demande", readonly=True, copy=False,
                                     help="Touché à chaque demande pour sérialiser les demandes "
                                          "concurrentes (plafonds).")

    @api.depends("token_hash")
    def _compute_has_token(self):
        for kiosk in self:
            kiosk.has_token = bool(kiosk.sudo().token_hash)

    @api.constrains("user_id")
    def _check_user(self):
        for kiosk in self:
            if not kiosk.user_id.has_group("bf_property_core.group_bf_property_user"):
                raise UserError(_("%s n'a pas l'accès Immobilier / Consultation.")
                                % kiosk.user_id.name)

    # ── Jumelage ──

    def action_pair(self):
        """Nouveau jeton : l'adresse s'affiche UNE fois ; l'ancienne cesse de marcher."""
        self.ensure_one()
        if not self.env.user.has_group("bf_property_core.group_bf_property_manager"):
            raise AccessError(_("Réservé aux gestionnaires."))
        self.check_access("write")
        token = secrets.token_urlsafe(32)
        self.sudo().token_hash = _digest(token)
        self.message_post(body=_("Appareil jumelé par %s (l'adresse précédente ne marche "
                                 "plus).") % self.env.user.name)
        base = self.get_base_url()
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {"title": _("Adresse de la tablette (affichée une seule fois)"),
                       "message": "%s/lobby/%d/%s" % (base, self.id, token),
                       "sticky": True, "type": "warning"},
        }

    def action_unpair(self):
        self.ensure_one()
        if not self.env.user.has_group("bf_property_core.group_bf_property_manager"):
            raise AccessError(_("Réservé aux gestionnaires."))
        self.check_access("write")
        self.sudo().token_hash = False
        self.message_post(body=_("Appareil dissocié par %s.") % self.env.user.name)

    @api.model
    def _bf_from_token(self, kiosk_id, token):
        """La tablette, si le jeton est le sien ; sinon rien (même temps de réponse)."""
        kiosk = self.sudo().browse(kiosk_id).exists()
        expected = kiosk.token_hash if kiosk and kiosk.active else _digest(secrets.token_hex(8))
        if not kiosk or not kiosk.active or not hmac.compare_digest(expected or "", _digest(token)):
            return self.browse()
        return kiosk

    # ── Ce que la page peut faire ──

    def _bf_directory(self):
        """Les résidents présents de l'immeuble qui consentent à paraître."""
        self.ensure_one()
        residents = self.env["bf.property.access.resident"].sudo().search([
            ("building_id", "=", self.building_id.id),
            ("state", "in", ("active", "departure_pending")),
            ("lobby_listed", "=", True), ("lobby_consent_date", "!=", False)])
        return [{"id": r.id, "label": r.lobby_label or _("Résident"),
                 "unit": r.unit_id.name or ""} for r in residents.sorted(
                     lambda r: ((r.unit_id.name or ""), (r.lobby_label or "")))]

    def _bf_throttled(self, resident=None):
        """🔴 Compter puis créer, sans verrou, laissait passer 9 appels pour 6.
        Un verrou seul ne suffit pas en lecture répétable (l'instantané précède
        l'attente) : on MET À JOUR la ligne de la tablette ; une requête
        concurrente échoue alors à sérialiser, et Odoo la rejoue sur des
        données fraîches, qui voient les appels déjà comptés."""
        self.ensure_one()
        # La requête concurrente qui perd la course répond « Un instant » : la
        # demande d'à côté vient de passer (avalé par le contrôleur,
        # le conflit faisait perdre la demande sans rien dire).
        try:
            with self.env.cr.savepoint(flush=False):
                self.env.cr.execute(
                    "UPDATE bf_property_lobby_kiosk SET throttle_stamp = (now() at time zone 'UTC') "
                    "WHERE id = %s", [self.id], log_exceptions=False)
        except (psycopg2.errors.SerializationFailure, psycopg2.errors.LockNotAvailable):
            return True
        Call = self.env["bf.property.lobby.call"].sudo()
        now = fields.Datetime.now()
        if Call.search_count([("kiosk_id", "=", self.id),
                              ("create_date", ">=", now - timedelta(minutes=1))]) >= CALLS_PER_MINUTE:
            return True
        if resident and Call.search_count([("kiosk_id", "=", self.id), ("resident_id", "=", resident.id),
                                           ("create_date", ">=", now - SAME_RESIDENT_WINDOW)]):
            return True
        return False

    def _bf_call(self, resident_id):
        """Appeler un résident de l'annuaire. Rend (issue, message pour la page)."""
        self.ensure_one()
        resident = self.env["bf.property.access.resident"].sudo().browse(int(resident_id)).exists()
        if (not resident or resident.building_id != self.building_id or not resident.lobby_listed
                or not resident.lobby_consent_date
                or resident.state not in ("active", "departure_pending")):
            return "refused", _("Cette personne n'est pas à l'annuaire.")
        if self._bf_throttled(resident):
            return "throttled", _("Un instant : un appel vient d'être fait.")
        Call = self.env["bf.property.lobby.call"].sudo()
        with self.env.cr.savepoint():
            return self._bf_call_now(resident, Call)

    def _bf_call_now(self, resident, Call):
        call = Call.create({"kiosk_id": self.id, "resident_id": resident.id, "kind": "call"})
        number = resident._bf_lobby_number()
        if self.call_mode == "phone" and number and self.sip_channel:
            ami = self.env.get("bf.softphone.ami")
            if ami is not None and ami.sudo().is_configured():
                try:
                    name = re.sub(r'["\r\n<>]', "", self.name or "")[:40]
                    ami.sudo().originate(
                        channel=self.sip_channel, dialplan_context=self.dial_context or "from-internal",
                        exten=number, callerid='"%s" <lobby>' % name)
                    call.outcome = "rang"
                    return "rang", _("Appel en cours…")
                except Exception as exc:  # noqa: BLE001 — la tablette ne plante pas
                    call.note = str(exc)[:200]
        call.outcome = "notified"
        # Pas de nom dans l'activité : marquée faite, elle devient un message
        # permanent de la tablette. Le logement suffit.
        self._bf_notify("call", resident.unit_id.name)
        return "notified", _("Le concierge est prévenu.")

    def _bf_delivery(self):
        self.ensure_one()
        if self._bf_throttled():
            return "throttled", _("Un instant : une demande vient d'être faite.")
        with self.env.cr.savepoint():
            self.env["bf.property.lobby.call"].sudo().create(
                {"kiosk_id": self.id, "kind": "delivery", "outcome": "notified"})
            self._bf_notify("delivery")
        return "notified", _("Le concierge est prévenu.")

    def _bf_notify(self, what, unit=None):
        """Une activité au responsable, rédigée dans SA langue (pas celle du
        navigateur de la tablette) ; la même, encore ouverte et récente, n'est pas
        répétée à chaque pression. Type d'activité propre à la tablette : la purge
        ne touche qu'à lui."""
        self.ensure_one()
        kiosk = self.sudo().with_context(lang=self.user_id.lang or self.env.lang)
        if what == "delivery":
            summary = kiosk.env._("Livraison à l'entrée « %s ».") % self.name
        elif unit:
            summary = kiosk.env._("Logement %(unit)s demandé à l'entrée « %(kiosk)s ».") % {
                "unit": unit, "kiosk": self.name}
        else:
            summary = kiosk.env._("Un résident est demandé à l'entrée « %s ».") % self.name
        recent = kiosk.activity_ids.filtered(
            lambda a: a.summary == summary
            and a.create_date >= fields.Datetime.now() - ACTIVITY_WINDOW)
        if recent:
            return
        kiosk.with_context(mail_activity_quick_update=True).activity_schedule(
            "bf_property_lobby.mail_activity_lobby", user_id=self.user_id.id, summary=summary)
        if self.notify_by_email and self.user_id.email:
            link = "%s/mail/view?model=%s&res_id=%s" % (self.get_base_url(), self._name, self.id)
            template = self.env.ref("bf_property_lobby.mail_template_lobby_notify").sudo()
            template.with_context(lang=self.user_id.lang, lobby_summary=summary, lobby_link=link).send_mail(
                self.id, email_layout_xmlid=bf_mail_layout(self.env),
                # Auteur : la société, jamais l'usager public de la tablette.
                email_values={"author_id": self.company_id.partner_id.id})


class BfPropertyLobbyCall(models.Model):
    _name = "bf.property.lobby.call"
    _description = "Appel du portier sur tablette"
    _order = "create_date desc, id desc"

    kiosk_id = fields.Many2one("bf.property.lobby.kiosk", string="Tablette", required=True,
                               ondelete="cascade", index=True)
    building_id = fields.Many2one(related="kiosk_id.building_id", store=True, string="Immeuble")
    organisation_id = fields.Many2one(related="kiosk_id.organisation_id", store=True,
                                      string="Organisation")
    company_id = fields.Many2one(related="kiosk_id.company_id", store=True, string="Société")
    resident_id = fields.Many2one("bf.property.access.resident", string="Résident appelé",
                                  ondelete="set null", index=True)
    kind = fields.Selection([("call", "Appel"), ("delivery", "Livraison")], string="Nature",
                            required=True, default="call")
    outcome = fields.Selection([("rang", "A sonné"), ("notified", "Responsable prévenu")],
                               string="Issue")
    note = fields.Char(string="Note")

    @api.model
    def _cron_purge(self):
        """Loi 25, art. 23 : le journal des appels suit le délai de l'organisation."""
        for org in self.env["bf.property.organisation"].sudo().search(
                [("access_log_retention_days", ">", 0)]):
            limit = fields.Datetime.now() - timedelta(days=org.access_log_retention_days)
            self.sudo().search([("organisation_id", "=", org.id),
                                ("create_date", "<", limit)]).unlink()
            # Les traces des appels dans la tablette elle-même : activités et
            # messages d'activités faites, au même délai (Loi 25, art. 23).
            kiosks = self.env["bf.property.lobby.kiosk"].sudo().with_context(
                active_test=False).search([("organisation_id", "=", org.id)])
            if kiosks:
                kind = self.env.ref("bf_property_lobby.mail_activity_lobby")
                self.env["mail.activity"].sudo().search([
                    ("res_model", "=", "bf.property.lobby.kiosk"), ("res_id", "in", kiosks.ids),
                    ("activity_type_id", "=", kind.id), ("create_date", "<", limit)]).unlink()
                self.env["mail.message"].sudo().search([
                    ("model", "=", "bf.property.lobby.kiosk"), ("res_id", "in", kiosks.ids),
                    ("mail_activity_type_id", "=", kind.id), ("date", "<", limit)]).unlink()


def _clean_number(raw):
    """Un numéro canadien ou américain ordinaire, sinon rien.

    Plan nord-américain strict (NXX-NXX-XXXX), sans les indicatifs des Caraïbes
    ni les numéros surtaxés : le résident choisit son numéro au portail, la
    tablette ne doit jamais composer un numéro qui coûte."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if not re.fullmatch(r"[2-9]\d{2}[2-9]\d{6}", digits):
        return ""
    if digits[:3] in BLOCKED_AREA_CODES or digits[3:6] in ("976",):
        return ""
    # Codes de service en x11 (911, 411…) : ni indicatif ni central valides.
    if digits[1:3] == "11" or digits[4:6] == "11":
        return ""
    # Central 555 : assistance-annuaire facturée (555-1212) ou numéros fictifs.
    if digits[3:6] == "555":
        return ""
    return digits
