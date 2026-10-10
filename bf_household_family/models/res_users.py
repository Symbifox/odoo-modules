"""Le rôle d'une personne du foyer : adulte, ou ado de 14 à 17 ans.

Le mois et l'année de naissance d'un ado (jamais le jour) ne sont lisibles
que par la gestion des droits : les autres membres voient le rôle, pas la naissance.
"""
from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import ValidationError

from .common import ADULT_AGE, MANAGER_GROUP, MONTHS, age_on


class ResUsers(models.Model):
    _inherit = "res.users"

    bf_household_role = fields.Selection(
        [("adult", "Adult"), ("teen", "Teen (14 to 17)")],
        string="Household role", default="adult", groups="base.group_erp_manager")
    bf_household_birth_month = fields.Selection(
        MONTHS, string="Birth month", groups="base.group_erp_manager")
    bf_household_birth_year = fields.Integer(string="Birth year", groups="base.group_erp_manager")

    def _household_check_all(self):
        """L'écran des groupes rejoue aussi cette garde (voir bf_household_base)."""
        super()._household_check_all()
        self._check_household_manager_is_adult()

    @api.constrains("groups_id", "bf_household_role")
    def _check_household_manager_is_adult(self):
        manager = self.env.ref(MANAGER_GROUP, raise_if_not_found=False)
        if not manager:
            return
        for user in self.sudo():
            if manager in user.groups_id and user.bf_household_role == "teen":
                raise ValidationError(_("A household manager is an adult, not a teen."))

    @api.model
    def _cron_household_teens_turn_adult(self):
        """Le mois des 18 ans, un ado devient adulte (le jour n'est pas connu)."""
        today = fields.Date.context_today(self)
        ados = self.sudo().with_context(active_test=False).search([("bf_household_role", "=", "teen")])
        for ado in ados:
            age = age_on(ado.bf_household_birth_year, ado.bf_household_birth_month, None, today)
            if age is not None and age >= ADULT_AGE:
                ado.bf_household_role = "adult"

    # ------------------------------------------------------------------
    # Départ
    # ------------------------------------------------------------------
    def _bf_household_before_leave(self):
        """Crochet des ponts, appelé avant qu'un compte quitte le foyer.

        Ici : l'enfant dont la personne est le parent principal passe au second
        parent (le pont Healthy Fox suit l'échange) ; celui dont elle est le second
        parent la perd. Un enfant sans second parent reste à elle, fermé : l'écran
        « Quitter le foyer » le dit avant le départ."""
        self.env["bf.household.child"]._bf_on_parents_leaving(self)
        return True

    def _bf_household_archive(self):
        """Archiver un compte, y compris le sien : Odoo refuse qu'on désactive le
        compte avec lequel on est connecté, d'où le superutilisateur. Ses sessions
        tombent à la requête suivante (jeton de session d'un compte inactif)."""
        self._bf_household_before_leave()
        self.with_user(SUPERUSER_ID).write({"active": False})
