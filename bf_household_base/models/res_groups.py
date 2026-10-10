"""L'autre chemin vers les groupes d'un compte : l'écran des groupes.

Paramètres › Groupes › onglet Utilisateurs écrit ``res.groups.users`` (ou ``implied_ids``)
sans passer par ``res.users.write`` : Odoo n'y revalide que les comptes qui reçoivent une
rangée de groupe impliqué NEUVE. Sans ce rejeu, on pouvait y donner « Droits d'accès » à une
personne du foyer, ou faire entrer un onzième compte. Seul un gestionnaire des droits
(Blue Fox) écrit ce modèle ; aucune personne du foyer n'atteint ce chemin.
"""
from odoo import api, models


def _touche_les_membres(vals):
    return "users" in vals or "implied_ids" in vals


class ResGroups(models.Model):
    _inherit = "res.groups"

    @api.model_create_multi
    def create(self, vals_list):
        touche = any(_touche_les_membres(v) for v in vals_list)
        avant = self.env["res.users"]._household_counting_ids() if touche else None
        groups = super().create(vals_list)
        if touche:
            groups._household_revalidate(avant)
        return groups

    def write(self, vals):
        touche = _touche_les_membres(vals)
        avant = self.env["res.users"]._household_counting_ids() if touche else None
        res = super().write(vals)
        if touche:
            self._household_revalidate(avant)
        return res

    def _household_revalidate(self, avant):
        # Les membres du groupe écrit reçoivent ses groupes impliqués : ce sont eux qui
        # changent, que la relation ait bougé côté membres ou côté implications.
        membres = self.sudo().with_context(active_test=False).users
        membres.invalidate_recordset(["groups_id"])
        membres._household_check_all()
        self.env["res.users"]._household_check_seats(avant)
