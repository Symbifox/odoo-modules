"""La personne à charge de Healthy Fox est un enfant du foyer.

* Créée depuis Healthy Fox sans enfant choisi, elle fait naître l'enfant du foyer
  (même prénom, même naissance) : une seule fiche par enfant.
* Créée pour un enfant du foyer, elle en reprend le prénom, la naissance et le
  second parent. Seul le parent principal de l'enfant la crée : c'est lui qui
  tient les fiches (``create_uid``), comme dans Healthy Fox.
* Un changement fait dans Healthy Fox (prénom, naissance corrigée par Blue Fox,
  second parent) revient à l'enfant du foyer ; l'inverse passe par
  ``household_child.py``.
"""
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.bf_household_family.models.common import is_household_member


class HealthDependent(models.Model):
    _inherit = "health.dependent"

    household_child_id = fields.Many2one(
        "bf.household.child", string="Child of the household", index=True, ondelete="restrict", copy=False,
        domain="[('primary_parent_id', '=', uid)]",
        help="Empty: the child of the household is created with this record.")

    _sql_constraints = [
        ("bf_health_dependent_child_unique", "unique(household_child_id)",
         "This child is already followed in Healthy Fox."),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            Child = self.env["bf.household.child"]
            for vals in vals_list:
                if vals.get("household_child_id"):
                    enfant = Child.browse(vals["household_child_id"]).exists()
                    if not enfant or enfant.sudo().primary_parent_id.id != self.env.uid:
                        raise AccessError(_(
                            "Only the child's parent who holds their records starts following their health."))
                    lu = enfant.sudo()
                    vals.update(name=lu.name, birth_month=lu.birth_month, birth_year=lu.birth_year,
                                coparent_id=lu.second_parent_id.id or False)
                elif is_household_member(self.env.user):
                    # Hors du foyer (une instance de travail, un compte qui n'en est
                    # pas), la personne à charge reste seule, comme sans ce pont.
                    if not vals.get("name") or not vals.get("birth_month") or not vals.get("birth_year"):
                        raise UserError(_("First name, birth month and birth year are needed."))
                    enfant = Child.create({
                        "name": vals["name"],
                        "birth_month": vals["birth_month"],
                        "birth_year": vals["birth_year"],
                        "second_parent_id": vals.get("coparent_id") or False,
                    })
                    vals["household_child_id"] = enfant.id
        return super().create(vals_list)

    def write(self, vals):
        res = super().write(vals)
        if self.env.context.get("bf_family_sync"):
            return res
        a_suivre = {k: vals[k] for k in ("name", "birth_month", "birth_year") if k in vals}
        if "coparent_id" in vals:
            a_suivre["second_parent_id"] = vals["coparent_id"] or False
        if a_suivre:
            enfants = self.sudo().mapped("household_child_id")
            enfants.with_context(bf_family_sync=True).write(a_suivre)
        return res

    def _bf_transferer(self):
        """Au passage à 14 ans, le compte de l'ado devient celui de l'enfant du
        foyer, si c'est un compte d'ado."""
        nombre = super()._bf_transferer()
        for dependant in self.sudo():
            enfant, ado = dependant.household_child_id, dependant.ado_id
            if enfant and ado and not enfant.user_id and ado.bf_household_role == "teen":
                enfant.with_context(bf_family_sync=True).write({"user_id": ado.id})
        return nombre
