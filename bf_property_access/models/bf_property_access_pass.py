"""Accès temporaire : un visiteur, un entrepreneur, une entrée au logement.

Contrairement au résident, l'accès temporaire se retire **à heure fixe** : son
titulaire n'est ni locataire ni occupant, le bail ne le protège pas (l'art. 1941
C.c.Q. ne joue pas). À la confirmation, un ordre d'ajout part pour chaque point,
avec la plage ; à la fin de la plage, le cron du quart d'heure ordonne le retrait.

**Entrée au logement (art. 1930 à 1933 C.c.Q.).** Le locateur a le droit de
vérifier l'état du logement, d'y faire des travaux et de le faire visiter à un
acquéreur ou à un locataire éventuel, mais il doit l'exercer de façon raisonnable
(art. 1857) :

- hors urgence, un préavis de 24 heures (art. 1931) ;
- le locataire peut refuser une vérification ou une visite avant 9 h ou après 21 h
  (art. 1932), et des travaux avant 7 h ou après 19 h (art. 1933) ;
- l'urgence dispense du préavis, et se motive.

Le module refuse de confirmer une entrée qui ne respecte pas ces bornes, sauf
accord du locataire consigné. Il ne décide pas pour personne : il oblige à écrire
ce qui a été convenu.
"""
from datetime import timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import format_datetime

from .bf_property_access_point import CREDENTIAL_KINDS

PASS_KINDS = [
    ("visitor", "Visiteur"),
    ("contractor", "Entrepreneur ou ouvrier"),
    ("staff", "Personnel"),
    ("unit_entry", "Entrée au logement"),
]

ENTRY_REASONS = [
    ("verification", "Vérifier l'état du logement"),
    ("works", "Travaux"),
    ("showing_buyer", "Visite d'un acquéreur éventuel"),
    ("showing_tenant", "Visite d'un locataire éventuel"),
    ("vacant", "Logement inoccupé"),
    ("urgent", "Urgence"),
]

# Bornes horaires du locataire (heure de l'immeuble), par motif : (début, fin).
ENTRY_HOURS = {"verification": (9, 21), "showing_buyer": (9, 21), "showing_tenant": (9, 21),
               "works": (7, 19)}
# Art. 1922 : travaux majeurs non urgents, avis écrit de 10 jours, ou de 3 mois si
# le locataire doit évacuer plus d'une semaine.
MAJOR_NOTICE = timedelta(days=10)
MAJOR_EVACUATION_NOTICE = timedelta(days=90)
# 🔴 L'art. 1931 n'exige le préavis de 24 h que pour la vérification, les travaux
# et la visite d'un ACQUÉREUR éventuel : la visite d'un locataire éventuel n'en
# demande pas. Les heures de l'art. 1932 valent pour les deux.
NOTICE_REASONS = ("verification", "works", "showing_buyer")
NOTICE = timedelta(hours=24)
DEFAULT_TZ = "America/Toronto"


class BfPropertyAccessPass(models.Model):
    _name = "bf.property.access.pass"
    _description = "Accès temporaire"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date_from desc, id desc"

    kind = fields.Selection(PASS_KINDS, string="Nature", required=True, default="contractor",
                            tracking=True)
    holder_name = fields.Char(string="Titulaire", required=True, tracking=True,
                              help="Le nom suffit : un visiteur n'a rien consenti, et la "
                                   "trace se purge.")
    partner_id = fields.Many2one("res.partner", string="Contact", tracking=True,
                                 help="Facultatif : un entrepreneur déjà au carnet.")
    holder_company = fields.Char(string="Entreprise", tracking=True)
    building_id = fields.Many2one("bf.property.building", string="Immeuble", required=True,
                                  ondelete="cascade", index=True, tracking=True)
    organisation_id = fields.Many2one(related="building_id.organisation_id", store=True,
                                      string="Organisation")
    company_id = fields.Many2one(related="building_id.company_id", store=True,
                                 string="Société")
    unit_id = fields.Many2one("bf.property.unit", string="Fraction",
                              domain="[('building_id', '=', building_id)]", tracking=True)
    point_ids = fields.Many2many("bf.property.access.point", string="Points d'accès",
                                 domain="[('building_id', '=', building_id)]")
    credential_kind = fields.Selection(CREDENTIAL_KINDS, string="Support", default="pin",
                                       required=True)
    date_from = fields.Datetime(string="Du", required=True, tracking=True)
    date_to = fields.Datetime(string="Au", required=True, tracking=True)
    state = fields.Selection(
        [("draft", "Brouillon"), ("confirmed", "Confirmé"), ("ended", "Terminé"),
         ("cancelled", "Annulé")],
        string="État", default="draft", required=True, tracking=True, copy=False, index=True,
    )
    grant_ids = fields.One2many("bf.property.access.grant", "pass_id", string="Droits")
    note = fields.Text(string="Note")

    # ── Entrée au logement ──
    entry_reason = fields.Selection(ENTRY_REASONS, string="Motif de l'entrée", tracking=True)
    notice_given_on = fields.Datetime(string="Préavis donné le", tracking=True)
    notice_method = fields.Selection([("written", "Écrit"), ("verbal", "Verbal")],
                                     string="Mode du préavis", tracking=True)
    tenant_agreed_outside_hours = fields.Boolean(
        string="Accord du locataire hors des heures", tracking=True,
        help="Le locataire peut refuser avant 9 h ou après 21 h (visite, vérification) "
             "et avant 7 h ou après 19 h (travaux) : son accord se consigne.")
    tenant_agreement_note = fields.Char(string="Accord consigné (qui, quand, comment)",
                                        tracking=True)
    urgent_reason = fields.Text(string="Motif de l'urgence")
    tenant_notice_received_on = fields.Date(
        string="Avis du locataire reçu le", tracking=True,
        help="Visite d'un locataire éventuel sans préavis : seulement après que le "
             "locataire a avisé qu'il ne reconduit pas son bail ou le résilie (art. "
             "1930-1931 C.c.Q.). Sans cette date, le préavis de 24 h s'applique.")
    works_major = fields.Boolean(string="Travaux majeurs", tracking=True,
                                 help="Art. 1922 : avis écrit de 10 jours.")
    evacuation_over_week = fields.Boolean(string="Évacuation de plus d'une semaine",
                                          tracking=True, help="Art. 1922 : avis de 3 mois.")
    accompanied_by = fields.Char(
        string="Accompagné par", tracking=True,
        help="Le locataire peut refuser une visite si le locateur ou son représentant "
             "ne peut être présent (art. 1932 al. 2).")

    _sql_constraints = [
        ("window", "CHECK(date_to > date_from)", "Un accès temporaire finit après avoir commencé."),
    ]

    # ── Calculs, contraintes ──

    def _bf_holder_label(self):
        self.ensure_one()
        name = self.partner_id.name or self.holder_name or ""
        return f"{name} ({self.holder_company})" if self.holder_company else name

    @api.depends("holder_name", "holder_company", "kind", "partner_id.name")
    def _compute_display_name(self):
        kinds = dict(self._fields["kind"]._description_selection(self.env))
        for temp in self:
            temp.display_name = f"{temp._bf_holder_label()} · {kinds.get(temp.kind, '')}"

    @api.constrains("point_ids", "building_id", "unit_id", "kind")
    def _check_scope(self):
        for temp in self:
            if temp.point_ids.filtered(lambda p, t=temp: p.building_id != t.building_id):
                raise ValidationError(_("Les points d'accès sont ceux de l'immeuble."))
            if temp.unit_id and temp.unit_id.building_id != temp.building_id:
                raise ValidationError(_("La fraction n'est pas dans l'immeuble."))
            if temp.kind == "unit_entry" and not temp.unit_id:
                raise ValidationError(_("Une entrée au logement nomme le logement."))
            # Un point attitré à une fraction ne sert un accès temporaire que
            # pour une entrée dans CE logement.
            other_units = temp.point_ids.filtered(
                lambda p, t=temp: p.unit_id and (t.kind != "unit_entry" or p.unit_id != t.unit_id))
            if other_units:
                raise ValidationError(_(
                    "%s sert une fraction : seule une entrée dans ce logement y donne "
                    "accès.") % other_units[0].display_name)

    @api.onchange("kind", "unit_id", "building_id")
    def _onchange_default_points(self):
        if self.state != "draft" or not self.building_id:
            return
        common = self.env["bf.property.access.point"].search(
            [("building_id", "=", self.building_id.id), ("unit_id", "=", False),
             ("point_type", "in", ("entrance", "elevator"))])
        door = self.env["bf.property.access.point"]
        if self.kind == "unit_entry" and self.unit_id:
            door = self.env["bf.property.access.point"].search(
                [("unit_id", "=", self.unit_id.id)])
        self.point_ids = common | door

    # ── Écriture ──

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            for vals in vals_list:
                vals.pop("state", None)
        return super().create(vals_list)

    def write(self, vals):
        if "state" in vals and not self.env.su:
            raise UserError(_("L'état d'un accès temporaire change par ses boutons."))
        locked = {"building_id", "unit_id", "point_ids", "date_from", "date_to", "kind",
                  "credential_kind", "entry_reason", "notice_given_on", "notice_method",
                  "tenant_agreed_outside_hours", "tenant_agreement_note", "urgent_reason",
                  "holder_name", "partner_id", "holder_company", "accompanied_by",
                  "tenant_notice_received_on", "works_major", "evacuation_over_week",
                  "organisation_id", "company_id"}
        if not self.env.su and locked & set(vals) and self.filtered(lambda t: t.state != "draft"):
            raise UserError(_("Un accès confirmé ne se modifie plus : annulez-le et "
                              "créez-en un autre."))
        return super().write(vals)

    def _bf_set(self, vals):
        return self.sudo().write(vals)

    # ── Règles de l'entrée au logement ──

    def _bf_tz(self):
        """🔴 Le fuseau de l'IMMEUBLE, jamais celui de la personne connectée : un
        gestionnaire en Nouvelle-Zélande faisait passer des travaux de 19 h à 21 h
        (heure de Montréal) sans l'accord du locataire."""
        self.ensure_one()
        org = self.building_id.organisation_id
        return (org.partner_id.tz or self.building_id.company_id.partner_id.tz
                or DEFAULT_TZ)

    def _bf_local(self, dt):
        return pytz.utc.localize(dt).astimezone(pytz.timezone(self._bf_tz()))

    def _bf_fmt(self, dt, dt_format="short"):
        return format_datetime(self.env, dt, tz=self._bf_tz(), dt_format=dt_format)

    def _bf_check_unit_entry(self):
        for temp in self.filtered(lambda t: t.kind == "unit_entry"):
            if not temp.entry_reason:
                raise UserError(_("Une entrée au logement donne son motif."))
            if temp.entry_reason == "urgent":
                if not (temp.urgent_reason or "").strip():
                    raise UserError(_("L'urgence dispense du préavis, et se motive : "
                                      "écrivez pourquoi."))
                continue
            if temp.entry_reason == "vacant":
                present = self.env["bf.property.access.resident"].sudo().search_count([
                    ("unit_id", "=", temp.unit_id.id),
                    ("state", "in", ("active", "departure_pending"))])
                if present:
                    raise UserError(_("Le logement n'est pas inoccupé : quelqu'un y réside. "
                                      "Choisissez le motif de l'entrée."))
                continue
            needs_notice = temp.entry_reason in NOTICE_REASONS or (
                temp.entry_reason == "showing_tenant" and not (
                    temp.tenant_notice_received_on
                    and temp.tenant_notice_received_on <= fields.Date.context_today(temp)))
            if needs_notice:
                if not temp.notice_given_on:
                    raise UserError(_("Hors urgence, le locateur donne un préavis de 24 "
                                      "heures (art. 1931 C.c.Q.) : indiquez quand il l'a été."))
                if temp.notice_given_on > fields.Datetime.now():
                    raise UserError(_("Le préavis se consigne une fois donné, pas d'avance."))
                if temp.entry_reason == "works" and temp.works_major:
                    delay = MAJOR_EVACUATION_NOTICE if temp.evacuation_over_week else MAJOR_NOTICE
                    if temp.notice_method != "written" or temp.notice_given_on > temp.date_from - delay:
                        raise UserError(_(
                            "Travaux majeurs : avis écrit de %(days)d jours avant le début "
                            "(art. 1922 C.c.Q.).") % {"days": delay.days})
                if temp.notice_given_on > temp.date_from - NOTICE:
                    raise UserError(_(
                        "Préavis de 24 heures (art. 1931 C.c.Q.) : donné le %(notice)s, "
                        "l'entrée est prévue le %(start)s.") % {
                            "notice": temp._bf_fmt(temp.notice_given_on),
                            "start": temp._bf_fmt(temp.date_from)})
            start, end = temp._bf_local(temp.date_from), temp._bf_local(temp.date_to)
            first, last = ENTRY_HOURS[temp.entry_reason]
            same_day = start.date() == end.date()
            inside = (same_day and start.hour >= first
                      and (end.hour, end.minute) <= (last, 0))
            if not inside:
                if not temp.tenant_agreed_outside_hours or not (temp.tenant_agreement_note or "").strip():
                    article = "1933" if temp.entry_reason == "works" else "1932"
                    hint = "" if same_day else _(
                        " Pour des travaux sur plusieurs jours, créez un accès par jour.")
                    raise UserError(_(
                        "Le locataire peut refuser cette entrée hors de %(first)d h à "
                        "%(last)d h, heure de l'immeuble (art. %(article)s C.c.Q.) : "
                        "consignez son accord, ou changez la plage.") % {
                            "first": first, "last": last, "article": article} + hint)

    # ── Gestes ──

    def _bf_check_manager(self):
        if not self.env.user.has_group("bf_property_core.group_bf_property_manager"):
            raise AccessError(_("Réservé aux gestionnaires."))

    def action_confirm(self):
        self._bf_check_manager()
        self._bf_confirm()

    def _bf_confirm(self):
        """Confirmer : ordres d'ajout pour chaque point, avec la plage."""
        now = fields.Datetime.now()
        for temp in self:
            if temp.state != "draft":
                raise UserError(_("%s n'est pas en brouillon.") % temp.display_name)
            if temp.date_to <= now:
                raise UserError(_("La plage de %s est déjà passée.") % temp.display_name)
            if not temp.point_ids:
                raise UserError(_("%s n'ouvre aucun point d'accès.") % temp.display_name)
        self._bf_check_unit_entry()
        for temp in self:
            temp._bf_set({"state": "confirmed"})
            temp._bf_ensure_grants()
            temp.message_post(body=_("Confirmé : accès du %(start)s au %(end)s.") % {
                "start": temp._bf_fmt(temp.date_from), "end": temp._bf_fmt(temp.date_to)})
            if (temp.kind == "unit_entry" and temp.entry_reason in ("showing_buyer", "showing_tenant")
                    and not (temp.accompanied_by or "").strip()):
                temp.message_post(body=_(
                    "Sans accompagnement du locateur ou de son représentant, le locataire "
                    "peut refuser la visite (art. 1932 al. 2 C.c.Q.)."))

    def _bf_serves_point(self, point):
        """La seule règle : ce point sert-il cet accès ? (droits créés, retirés,
        recréés à la réactivation d'un point : tous passent par ici)."""
        self.ensure_one()
        if not point.active or point not in self.point_ids:
            return False
        if point.building_id != self.building_id:
            return False
        # Un visiteur n'a que des portes communes (entrée, ascenseur) : un point
        # devenu garage ou boîte à colis ne le sert plus.
        if self.kind == "visitor" and point.point_type not in ("entrance", "elevator"):
            return False
        # Même règle que _check_scope : un point attitré à une fraction ne sert
        # qu'une entrée dans CE logement.
        return not point.unit_id or (self.kind == "unit_entry" and point.unit_id == self.unit_id)

    def _bf_ensure_grants(self, points=None):
        Grant = self.env["bf.property.access.grant"].sudo()
        for temp in self.filtered(lambda t: t.state == "confirmed"):
            held = temp.grant_ids.filtered(
                lambda g: g.state in ("awaiting_consent", "to_issue", "issued")).point_id
            for point in (points if points is not None else temp.point_ids):
                if point not in held and temp._bf_serves_point(point):
                    Grant.create({"pass_id": temp.id, "point_id": point.id})

    def action_end(self):
        """Terminer maintenant (le visiteur est reparti, les travaux sont finis)."""
        self._bf_check_manager()
        self._bf_end(_("Terminé à la main par %s.") % self.env.user.name)

    def _bf_end(self, why):
        for temp in self.filtered(lambda t: t.state == "confirmed"):
            temp._bf_set({"state": "ended"})
            temp.grant_ids._bf_revoke("expired")
            temp.message_post(body=why)

    def action_cancel(self):
        self._bf_check_manager()
        self._bf_cancel(_("Annulé par %s.") % self.env.user.name)

    def _bf_cancel(self, why):
        for temp in self.filtered(lambda t: t.state in ("draft", "confirmed")):
            temp._bf_set({"state": "cancelled"})
            temp.grant_ids._bf_revoke("other")
            temp.message_post(body=why)

    @api.model
    def _cron_expire(self):
        """Toutes les quinze minutes : la plage échue ordonne le retrait."""
        due = self.sudo().search([("state", "=", "confirmed"),
                                  ("date_to", "<=", fields.Datetime.now())])
        due._bf_end(_("Plage échue : retrait ordonné."))
        return len(due)

    # ── Résumé d'un ordre ──

    def _bf_order_summary(self, order, kinds):
        self.ensure_one()
        kind = kinds.get(order.grant_id.credential_kind, "")
        who = self._bf_holder_label()
        if order.action == "add":
            return _("Donner à %(who)s accès à %(point)s : %(kind)s, du %(start)s au %(end)s") % {
                "who": who, "point": order.point_id.display_name, "kind": kind,
                "start": self._bf_fmt(self.date_from, "short"),
                "end": self._bf_fmt(self.date_to, "short")}
        return _("Retirer l'accès temporaire de %(who)s à %(point)s : %(kind)s") % {
            "who": who, "point": order.point_id.display_name, "kind": kind}
