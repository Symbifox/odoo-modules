"""Un enfant du foyer : une fiche, un ou deux parents nommés.

La fiche nomme ses parents, un ou
deux comptes. Les deux tiennent sa santé et ses papiers ; les autres adultes du
foyer voient son prénom et son anniversaire, pour le calendrier et Célébrations.

* Lecture : tout le foyer (aucune règle de lecture). La fiche ne porte rien de
  privé : ni santé, ni papier, ni numéro.
* Écriture et suppression : ses parents seulement (règle).
* Le parent principal (celui qui crée la fiche) est celui qui tient ses fiches
  dans Healthy Fox. Il ne se change pas par une écriture : « Échanger les
  parents » passe la main au second parent, et le pont Healthy Fox suit.
* Un contact est créé pour l'enfant (classe partagée) : c'est lui qu'on invite à
  un événement du calendrier et dont Célébrations souligne l'anniversaire.
* La naissance : l'année et le mois toujours ; le jour seulement si un parent le
  donne (l'anniversaire en a besoin, Healthy Fox n'en lit que le mois et l'année).
"""
from datetime import date

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .common import ADULT_AGE, MONTHS, TEEN_MIN_AGE, age_on, is_household_member, neutral_env

#: Les champs qu'une écriture ordinaire ne touche jamais : ils suivent les gestes.
PARENT_FIELDS = {"primary_parent_id", "second_parent_id"}


class HouseholdChild(models.Model):
    _name = "bf.household.child"
    _description = "Child of the household"
    _order = "birth_year desc, birth_month desc, name, id"

    name = fields.Char(string="First name", required=True)
    active = fields.Boolean(string="Active", default=True)
    birth_year = fields.Integer(string="Birth year", required=True)
    birth_month = fields.Selection(MONTHS, string="Birth month", required=True)
    birth_day = fields.Integer(
        string="Birth day", help="Optional. Needed for the birthday in the calendar and Celebrations.")
    birthday_label = fields.Char(string="Birthday", compute="_compute_birthday_label")
    age_band = fields.Selection(
        [("child", "Under 14"), ("teen", "14 to 17"), ("adult", "18 or older")],
        string="Age", compute="_compute_age_band")

    primary_parent_id = fields.Many2one(
        "res.users", string="Parent", required=True, readonly=True, index=True, ondelete="restrict",
        default=lambda self: self.env.user,
        help="The parent who created this record. They hold the child's records in Healthy Fox.")
    second_parent_id = fields.Many2one(
        "res.users", string="Second parent", index=True, ondelete="set null",
        domain="[('share', '=', False), ('id', '!=', primary_parent_id)]")
    partner_id = fields.Many2one(
        "res.partner", string="Contact", readonly=True, copy=False, ondelete="restrict",
        help="The contact used to invite the child to an event and for their birthday.")
    user_id = fields.Many2one(
        "res.users", string="Own account", copy=False, ondelete="set null",
        help="From 14, the teen's own account in the household.")

    is_parent = fields.Boolean(string="I am a parent", compute="_compute_is_parent")
    is_primary_parent = fields.Boolean(compute="_compute_is_parent")

    _sql_constraints = [
        ("bf_household_child_user_unique", "unique(user_id)",
         "This account is already linked to another child."),
    ]

    # ------------------------------------------------------------------
    # Calculs
    # ------------------------------------------------------------------
    @api.depends("birth_year", "birth_month", "birth_day")
    def _compute_birthday_label(self):
        mois = dict(self._fields["birth_month"]._description_selection(self.env))
        for rec in self:
            if not rec.birth_month:
                rec.birthday_label = False
            elif rec.birth_day:
                rec.birthday_label = f"{rec.birth_day} {mois[rec.birth_month]}"
            else:
                rec.birthday_label = f"{mois[rec.birth_month]} {rec.birth_year}"

    def _compute_age_band(self):
        today = fields.Date.context_today(self)
        for rec in self:
            age = age_on(rec.birth_year, rec.birth_month, rec.birth_day, today)
            if age is None:
                rec.age_band = False
            elif age < TEEN_MIN_AGE:
                rec.age_band = "child"
            elif age < ADULT_AGE:
                rec.age_band = "teen"
            else:
                rec.age_band = "adult"

    @api.depends_context("uid")
    @api.depends("primary_parent_id", "second_parent_id")
    def _compute_is_parent(self):
        for rec in self:
            lu = rec.sudo()
            rec.is_primary_parent = lu.primary_parent_id.id == self.env.uid
            rec.is_parent = self.env.uid in (lu.primary_parent_id.id, lu.second_parent_id.id)

    def _bf_parents(self):
        """Les parents (un ou deux comptes)."""
        self.ensure_one()
        lu = self.sudo()
        return lu.primary_parent_id | lu.second_parent_id

    def next_birthday(self, today):
        """La prochaine date d'anniversaire, ou False sans jour de naissance. Un 29
        février tombe le 28 les années ordinaires."""
        self.ensure_one()
        if not self.birth_day or not self.birth_month:
            return False
        mois = int(self.birth_month)
        for annee in (today.year, today.year + 1):
            jour = self.birth_day
            while jour > 28:
                try:
                    candidat = date(annee, mois, jour)
                    break
                except ValueError:
                    jour -= 1
            else:
                candidat = date(annee, mois, jour)
            if candidat >= today:
                return candidat
        return False

    # ------------------------------------------------------------------
    # Contraintes
    # ------------------------------------------------------------------
    @api.constrains("birth_year", "birth_month", "birth_day")
    def _check_birth(self):
        today = fields.Date.context_today(self)
        for rec in self:
            if rec.birth_year < 1900 or (rec.birth_year, int(rec.birth_month)) > (today.year, today.month):
                raise ValidationError(_("The birth month and year cannot be in the future."))
            if rec.birth_day:
                try:
                    born = date(rec.birth_year, int(rec.birth_month), rec.birth_day)
                except ValueError:
                    raise ValidationError(_("This birth day does not exist in that month.")) from None
                if born > today:
                    raise ValidationError(_("The birth date cannot be in the future."))

    @api.constrains("primary_parent_id", "second_parent_id")
    def _check_parents(self):
        for rec in self.sudo():
            parents = rec.primary_parent_id | rec.second_parent_id
            if rec.second_parent_id and rec.second_parent_id == rec.primary_parent_id:
                raise ValidationError(_("The two parents must be two different people."))
            for parent in parents:
                if parent.id == SUPERUSER_ID or not is_household_member(parent):
                    raise ValidationError(_("A parent is an active member of the household."))
                if parent.bf_household_role == "teen":
                    raise ValidationError(_("A parent is an adult member of the household."))
                if rec.user_id and parent == rec.user_id:
                    raise ValidationError(_("The child's own account cannot be their parent."))

    @api.constrains("user_id")
    def _check_own_account(self):
        for rec in self.sudo():
            if rec.user_id and (not is_household_member(rec.user_id) or rec.user_id.bf_household_role != "teen"):
                raise ValidationError(_("The child's own account is a teen account of the household."))

    # ------------------------------------------------------------------
    # ORM
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            if not is_household_member(self.env.user):
                raise AccessError(_("Only a member of the household adds a child."))
            for vals in vals_list:
                # Le parent principal est celui qui crée la fiche : on ne crée pas
                # l'enfant d'un autre (il en serait le parent sans l'avoir voulu).
                vals["primary_parent_id"] = self.env.uid
        children = super().create(vals_list)
        children._bf_sync_partner()
        return children

    def write(self, vals):
        if not self.env.su and set(vals) & PARENT_FIELDS:
            for rec in self:
                lu = rec.sudo()
                if "primary_parent_id" in vals and vals["primary_parent_id"] != lu.primary_parent_id.id:
                    raise AccessError(_("Use « Swap parents » to hand the child's records to the second parent."))
                if "second_parent_id" in vals:
                    nouveau = vals["second_parent_id"] or False
                    par_le_principal = lu.primary_parent_id.id == self.env.uid
                    se_retire = lu.second_parent_id.id == self.env.uid and not nouveau
                    if nouveau != lu.second_parent_id.id and not (par_le_principal or se_retire):
                        raise AccessError(_("Only the parent who holds the child's records names the second parent."))
        res = super().write(vals)
        if "name" in vals:
            self._bf_sync_partner()
        return res

    def unlink(self):
        partenaires = self.sudo().mapped("partner_id")
        res = super().unlink()
        partenaires.with_env(neutral_env(self.env)).write({"active": False})
        return res

    def action_archive(self):
        res = super().action_archive()
        self.sudo().mapped("partner_id").write({"active": False})
        return res

    def action_unarchive(self):
        res = super().action_unarchive()
        self.sudo().mapped("partner_id").write({"active": True})
        return res

    def _bf_sync_partner(self):
        """Le contact de l'enfant, créé ou renommé. Partagé avec le foyer comme
        tout contact : il ne porte que le prénom."""
        env = neutral_env(self.env)
        for rec in self:
            lu = rec.with_env(env)
            if lu.partner_id:
                if lu.partner_id.name != lu.name:
                    lu.partner_id.name = lu.name
            else:
                lu.partner_id = env["res.partner"].create({"name": lu.name, "type": "contact"})

    # ------------------------------------------------------------------
    # Gestes
    # ------------------------------------------------------------------
    def action_swap_parents(self):
        """Le parent principal passe la main au second parent : celui-ci tient
        désormais les fiches de l'enfant, et l'autre devient second parent."""
        for rec in self:
            lu = rec.sudo()
            if not self.env.su and lu.primary_parent_id.id != self.env.uid:
                raise AccessError(_("Only the parent who holds the child's records hands them over."))
            if not lu.second_parent_id:
                raise UserError(_("Name a second parent first."))
            lu._bf_swap_parents()
        return True

    def _bf_swap_parents(self):
        """Mécanique, en superutilisateur. Les ponts suivent (Healthy Fox)."""
        for rec in self.sudo():
            ancien, nouveau = rec.primary_parent_id, rec.second_parent_id
            rec.write({"primary_parent_id": nouveau.id, "second_parent_id": ancien.id})

    @api.model
    def _bf_on_parents_leaving(self, users):
        Child = self.sudo().with_context(active_test=False)
        for child in Child.search([("primary_parent_id", "in", users.ids), ("second_parent_id", "!=", False),
                                   ("second_parent_id", "not in", users.ids)]):
            child._bf_swap_parents()
        Child.search([("second_parent_id", "in", users.ids)]).write({"second_parent_id": False})

    @api.model
    def _bf_held_alone_by(self, user):
        """Les enfants dont ``user`` est le seul parent : ils resteront fermés s'il part."""
        return self.sudo().search([("primary_parent_id", "=", user.id), ("second_parent_id", "=", False)])
