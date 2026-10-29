"""Chacun pose ses propres pronoms depuis ses préférences, et change son
propre nom sans laisser l'ancien au fil de sa fiche d'employé.

Les pronoms d'une personne de la maison se saisissent par elle, pas par un
collègue qui les devine : les deux champs sont ouverts en écriture sur son
propre usager (`SELF_WRITEABLE_FIELDS`). Le nom légal n'y est pas : il reste
au groupe « Noms légaux ».

Quand le module RH est là, il recopie
le nom de l'usager vers ses employés, avec suivi (`hr/models/res_users.py`).
Changer son nom dans ses préférences laissait donc « ancien → nouveau » au fil
de sa fiche d'employé, alors que celui de sa fiche contact n'en gardait rien.
"""

from odoo import fields, models

from .res_partner import forget_name_tracking


class ResUsers(models.Model):
    _inherit = "res.users"

    bf_pronouns = fields.Char(related="partner_id.bf_pronouns", readonly=False)
    bf_grammatical_gender = fields.Selection(
        related="partner_id.bf_grammatical_gender", readonly=False)

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + ["bf_pronouns", "bf_grammatical_gender"]

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + ["bf_pronouns", "bf_grammatical_gender"]

    def write(self, vals):
        res = super().write(vals)
        if "name" in vals and "hr.employee" in self.env:
            own = self & self.env.user
            if own:
                own._bf_rename_own_employees_untracked(vals["name"])
        return res

    def _bf_rename_own_employees_untracked(self, name):
        """Renomme sans suivi les employés que le module RH va renommer.

        Le module RH ne dépend pas de celui-ci : selon l'ordre de chargement,
        sa recopie du nom vers l'employé passe AVANT ce code (dans `super()`)
        ou APRÈS (au retour d'ici). On couvre les deux :

        - le nom est retiré du suivi en attente de ces employés (recopie déjà
          faite, ou écriture suivie plus tôt dans la transaction) ;
        - puis il est écrit sans suivi : une recopie qui vient après ne change
          plus rien, et le suivi qu'elle relève ne voit pas l'ancien nom.

        Mêmes employés que la recopie du module RH (ceux de cet usager dans la
        société courante), en sudo comme elle : rien d'autre n'est renommé.
        Les autres champs de l'employé restent suivis.
        """
        Employee = self.env["hr.employee"].sudo()
        employees = Employee.search([
            *Employee._check_company_domain(self.env.company),
            ("user_id", "in", self.ids),
        ])
        if not employees:
            return
        forget_name_tracking(employees)
        employees.filtered(lambda e: e.name != name).with_context(
            mail_notrack=True).write({"name": name})
