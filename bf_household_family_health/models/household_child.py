"""L'enfant du foyer, vu par Healthy Fox.

* Changer ses parents (nommer, retirer, échanger) change les parents de la
  personne à charge : le parent principal tient les fiches, le second parent les
  tient aussi. Healthy Fox réécrit lui-même l'auteur des fiches et détache le
  parent qui s'en va (``health.dependent._bf_changer_parents``).
* 🔴 La naissance que Healthy Fox suit décide du passage à 14 ans : Healthy Fox ne
  laisse que Blue Fox la corriger, pour qu'un parent ne repousse pas le passage.
  Tant que la santé de l'enfant est suivie, sa naissance ne change donc pas par la
  fiche du foyer non plus. Le jour de naissance, que Healthy Fox ne lit pas, reste
  libre.
"""
from odoo import _, api, fields, models
from odoo.exceptions import AccessError

SUIVI = ("suivi", "offert")


class HouseholdChild(models.Model):
    _inherit = "bf.household.child"

    health_dependent_ids = fields.One2many("health.dependent", "household_child_id", string="Healthy Fox")

    def _bf_dependants_suivis(self):
        return self.sudo().mapped("health_dependent_ids").filtered(lambda d: d.state in SUIVI)

    def write(self, vals):
        sync = self.env.context.get("bf_family_sync")
        if not self.env.su and not sync and {"birth_month", "birth_year"} & set(vals):
            for enfant in self:
                if enfant._bf_dependants_suivis():
                    raise AccessError(_(
                        "Healthy Fox follows this birth date to know when the child turns 14: only "
                        "Blue Fox corrects it."))
        res = super().write(vals)
        if sync:
            return res
        for enfant in self:
            lu = enfant.sudo()
            for dependant in enfant._bf_dependants_suivis():
                if {"primary_parent_id", "second_parent_id"} & set(vals):
                    dependant.with_context(bf_family_sync=True)._bf_changer_parents(
                        lu.primary_parent_id, lu.second_parent_id)
                if "name" in vals and dependant.name != lu.name:
                    dependant.with_context(bf_family_sync=True).write({"name": lu.name})
        return res
