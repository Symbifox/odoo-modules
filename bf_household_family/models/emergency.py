"""Les fiches d'urgence : qui appeler, quoi savoir, pour chaque personne du foyer.

Les deux formes à la fois. La fiche est
visible des personnes du foyer, s'imprime en PDF, ET se partage avec une gardienne
par un lien à jeton qui expire.

* Lecture : tout le foyer. Écriture : la personne elle-même, ou les parents de
  l'enfant. Une fiche ne porte aucun numéro de papier.
* Healthy Fox : ses médicaments et ses conditions n'y entrent que si la personne
  (ou le parent, pour l'enfant) coche la case. Le pont Healthy Fox les lit ; ce
  module n'en sait rien.

🔴 Le lien de la gardienne est un PORTEUR : quiconque le reçoit (courriel
transféré, aperçu de messagerie, historique) ouvre la fiche. D'où :

* un jeton de 32 octets tirés au hasard ; la base n'en garde que l'empreinte
  SHA-256, et le lien ne se montre qu'une fois, à sa création ;
* une durée courte (24 h par défaut, 7 jours au plus), et un retrait d'un clic ;
* une page qui ne montre que l'urgence, jamais un numéro, servie en ``noindex``,
  sans cache ni référent ;
* un journal des ouvertures que les titulaires de la fiche lisent ;
* la même page « introuvable » pour un jeton inconnu, expiré ou retiré : aucune
  réponse ne dit lequel des trois.
"""
import hashlib
import secrets
from datetime import timedelta

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

from .common import is_household_member, neutral_env
from .document import text_holds_sin

#: Les textes libres d'une fiche d'urgence : lus du ménage ET des liens de gardienne.
CARD_TEXTS = ("allergies", "medications", "conditions", "doctor_name", "doctor_phone", "notes")

BLOOD_TYPES = [
    ("a_pos", "A+"), ("a_neg", "A-"), ("b_pos", "B+"), ("b_neg", "B-"),
    ("ab_pos", "AB+"), ("ab_neg", "AB-"), ("o_pos", "O+"), ("o_neg", "O-"),
]
LINK_MAX_HOURS = 168
LINK_ROUTE = "/family/emergency/"


def token_hash(token):
    return hashlib.sha256((token or "").encode()).hexdigest()


class HouseholdEmergencyCard(models.Model):
    _name = "bf.household.emergency.card"
    _description = "Emergency card"
    _order = "name, id"

    name = fields.Char(string="Person", compute="_compute_name", store=True)
    holder_user_id = fields.Many2one(
        "res.users", string="Member", index=True, ondelete="cascade",
        default=lambda self: self.env.user, domain="[('share', '=', False)]")
    child_id = fields.Many2one("bf.household.child", string="Child", index=True, ondelete="cascade")

    blood_type = fields.Selection(BLOOD_TYPES, string="Blood type")
    allergies = fields.Text(string="Allergies")
    medications = fields.Text(string="Medications")
    conditions = fields.Text(string="Conditions")
    doctor_name = fields.Char(string="Doctor")
    doctor_phone = fields.Char(string="Doctor's phone")
    notes = fields.Text(string="Notes for whoever helps")
    contact_ids = fields.One2many("bf.household.emergency.contact", "card_id", string="Who to call")

    include_health = fields.Boolean(
        string="Add the medications and conditions from Healthy Fox",
        help="Only what is ticked here leaves Healthy Fox, and only onto this card: the household "
             "and the babysitter's links then see it.")
    health_available = fields.Boolean(compute="_compute_health_summary")
    health_summary = fields.Text(string="From Healthy Fox", compute="_compute_health_summary")

    link_ids = fields.One2many("bf.household.emergency.link", "card_id", string="Babysitter links")
    is_writer = fields.Boolean(string="I keep this card", compute="_compute_is_writer")

    _sql_constraints = [
        ("bf_household_card_holder_unique", "unique(holder_user_id)", "This member already has an emergency card."),
        ("bf_household_card_child_unique", "unique(child_id)", "This child already has an emergency card."),
    ]

    @api.depends("holder_user_id.name", "child_id.name")
    def _compute_name(self):
        for rec in self:
            lu = rec.sudo()
            rec.name = lu.child_id.name or lu.holder_user_id.name

    def _compute_health_summary(self):
        """Vide sans le pont Healthy Fox, qui le remplit."""
        for rec in self:
            rec.health_available = False
            rec.health_summary = False

    @api.depends_context("uid")
    @api.depends("holder_user_id", "child_id")
    def _compute_is_writer(self):
        for rec in self:
            rec.is_writer = self.env.uid in rec.sudo()._bf_writers().ids

    def _bf_writers(self):
        self.ensure_one()
        lu = self.sudo()
        if lu.child_id:
            return lu.child_id.primary_parent_id | lu.child_id.second_parent_id
        return lu.holder_user_id

    @api.constrains("holder_user_id", "child_id")
    def _check_holder(self):
        for rec in self.sudo():
            if bool(rec.holder_user_id) == bool(rec.child_id):
                raise ValidationError(_("A card belongs either to a member of the household or to a child."))
            if rec.holder_user_id and not is_household_member(rec.holder_user_id):
                raise ValidationError(_("A card belongs to an active member of the household."))

    @api.constrains(*CARD_TEXTS)
    def _check_no_sin(self):
        """Aucun NAS sur une fiche que le ménage et une gardienne lisent."""
        for rec in self:
            if any(text_holds_sin(rec[champ]) for champ in CARD_TEXTS):
                raise ValidationError(_(
                    "This looks like a social insurance number. Symbifox does not keep it: "
                    "Service Canada advises keeping it locked away and giving it only when the law "
                    "requires it."))

    def copy(self, default=None):
        """Dupliquer une fiche, c'est en publier le contenu : celle qu'on tient seulement."""
        self._bf_check_is_writer()
        return super().copy(default)

    def _bf_check_is_writer(self):
        if self.env.su:
            return
        for rec in self:
            if self.env.uid not in rec._bf_writers().ids:
                raise AccessError(_("You keep only your own card, or your own child's."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # Le formulaire envoie aussi le titulaire, champ invisible, à sa valeur par
            # défaut (soi) : un enfant choisi l'emporte toujours (vu au navigateur).
            if vals.get("child_id"):
                vals["holder_user_id"] = False
        recs = super().create(vals_list)
        recs._bf_check_is_writer()
        return recs

    def write(self, vals):
        if vals.get("child_id"):
            vals["holder_user_id"] = False
        res = super().write(vals)
        if {"holder_user_id", "child_id"} & set(vals):
            self._bf_check_is_writer()
        return res

    def action_share_link(self):
        self.ensure_one()
        self._bf_check_is_writer()
        return {
            "type": "ir.actions.act_window",
            "name": _("Share with a babysitter"),
            "res_model": "bf.household.emergency.link.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_card_id": self.id},
        }

    def action_print(self):
        return self.env.ref("bf_household_family.action_report_emergency_card").report_action(self)

    def _bf_public_values(self):
        """Ce que la page de la gardienne montre : l'urgence, rien d'autre."""
        self.ensure_one()
        lu = self.sudo()
        sang = dict(self._fields["blood_type"]._description_selection(self.env))
        return {
            "card": lu,
            "name": lu.name,
            "blood_type": sang.get(lu.blood_type),
            "allergies": lu.allergies,
            "medications": lu.medications,
            "conditions": lu.conditions,
            "health_summary": lu.health_summary if lu.include_health else False,
            "doctor_name": lu.doctor_name,
            "doctor_phone": lu.doctor_phone,
            "notes": lu.notes,
            "contacts": lu.contact_ids,
        }


class HouseholdEmergencyContact(models.Model):
    _name = "bf.household.emergency.contact"
    _description = "Emergency contact"
    _order = "sequence, id"

    card_id = fields.Many2one(
        "bf.household.emergency.card", string="Card", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(string="Name", required=True)
    relation = fields.Char(string="Relation")
    phone = fields.Char(string="Phone", required=True)

    def _bf_check_card(self):
        if self.env.su:
            return
        for rec in self:
            if self.env.uid not in rec.card_id._bf_writers().ids:
                raise AccessError(_("You keep only your own card, or your own child's."))

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        recs._bf_check_card()
        return recs

    def write(self, vals):
        res = super().write(vals)
        if "card_id" in vals:
            self._bf_check_card()
        return res


class HouseholdEmergencyLink(models.Model):
    _name = "bf.household.emergency.link"
    _description = "Emergency card link for a babysitter"
    _order = "create_date desc, id desc"

    card_id = fields.Many2one(
        "bf.household.emergency.card", string="Card", required=True, ondelete="cascade", index=True,
        readonly=True)
    label = fields.Char(string="For", help="Who received it, for your own memory: « Babysitter, Saturday ».")
    token_hash = fields.Char(required=True, index=True, readonly=True, copy=False)
    expires_at = fields.Datetime(string="Expires", required=True, readonly=True)
    revoked = fields.Boolean(string="Withdrawn", readonly=True, copy=False)
    revoked_at = fields.Datetime(string="Withdrawn on", readonly=True, copy=False)
    open_count = fields.Integer(string="Opened", readonly=True, copy=False)
    last_opened_at = fields.Datetime(string="Last opened", readonly=True, copy=False)
    open_ids = fields.One2many("bf.household.emergency.link.open", "link_id", string="Openings")
    state = fields.Selection(
        [("active", "Active"), ("expired", "Expired"), ("revoked", "Withdrawn")],
        string="State", compute="_compute_state")

    _sql_constraints = [
        ("bf_household_link_token_unique", "unique(token_hash)", "This link already exists."),
    ]

    def _compute_state(self):
        maintenant = fields.Datetime.now()
        for rec in self:
            if rec.revoked:
                rec.state = "revoked"
            elif rec.expires_at <= maintenant:
                rec.state = "expired"
            else:
                rec.state = "active"

    @api.depends("label", "card_id")
    @api.depends_context("uid")
    def _compute_display_name(self):
        uid = self.env.uid
        for rec, lu in zip(self, self.sudo()):
            if uid == SUPERUSER_ID or uid in lu.card_id._bf_writers().ids:
                rec.display_name = lu.label or lu.card_id.name
            else:
                rec.display_name = self.env._("Private link")

    @api.constrains("expires_at")
    def _check_expiry(self):
        for rec in self:
            debut = rec.create_date or fields.Datetime.now()
            if rec.expires_at > debut + timedelta(hours=LINK_MAX_HOURS, minutes=5):
                raise ValidationError(_("A babysitter link lasts seven days at most."))

    def write(self, vals):
        """Seul le retrait se fait à la main ; le reste suit les ouvertures."""
        if not self.env.su and set(vals) - {"label"}:
            raise AccessError(_("A link is not edited: withdraw it and create another one."))
        return super().write(vals)

    def action_revoke(self):
        for rec in self:
            if self.env.uid not in rec.card_id._bf_writers().ids and not self.env.su:
                raise AccessError(_("Only whoever keeps the card withdraws its links."))
        self.sudo().write({"revoked": True, "revoked_at": fields.Datetime.now()})
        return True

    @api.model
    def _bf_create_for(self, card, label, hours):
        """Un lien neuf : rend (lien, jeton). Le jeton n'est gardé nulle part.

        La carte est relue sous l'identité de l'APPELANT : une carte reçue d'un autre
        environnement (celui de sa titulaire) passait sinon le contrôle sous SON nom
        (trouvé par l'essai ``test_links_are_private_to_whoever_keeps_the_card``)."""
        card = self.env["bf.household.emergency.card"].browse(card.id)
        card._bf_check_is_writer()
        heures = max(1, min(int(hours), LINK_MAX_HOURS))
        jeton = secrets.token_urlsafe(32)
        lien = neutral_env(self.env)[self._name].create({
            "card_id": card.id,
            "label": label,
            "token_hash": token_hash(jeton),
            "expires_at": fields.Datetime.now() + timedelta(hours=heures),
        })
        return lien.with_env(self.env), jeton

    @api.model
    def _bf_find_valid(self, token):
        """Le lien d'un jeton s'il est encore bon, sinon un ensemble vide."""
        if not token or len(token) > 128:
            return self.browse()
        return self.sudo().search([
            ("token_hash", "=", token_hash(token)), ("revoked", "=", False),
            ("expires_at", ">", fields.Datetime.now()),
        ], limit=1)

    def _bf_record_opening(self):
        self.ensure_one()
        lu = self.sudo()
        maintenant = fields.Datetime.now()
        lu.env["bf.household.emergency.link.open"].create({"link_id": lu.id, "opened_at": maintenant})
        lu.write({"open_count": lu.open_count + 1, "last_opened_at": maintenant})


class HouseholdEmergencyLinkOpen(models.Model):
    _name = "bf.household.emergency.link.open"
    _description = "Opening of a babysitter link"
    _order = "opened_at desc, id desc"

    link_id = fields.Many2one(
        "bf.household.emergency.link", string="Link", required=True, ondelete="cascade", index=True)
    opened_at = fields.Datetime(string="Opened on", required=True)
