"""Qui accède au portail, et par quelle porte.

Deux portes, et elles ne donnent pas sur la même chose :

- **copropriétaire** : une inscription courante au registre de propriété
  (`bf.property.ownership`), art. 1070 al. 1.
- **occupant** : le locataire porté à la fraction (`unit.occupant_id`), qui
  figure au registre par son nom et son adresse sans que cela lui ouvre le
  registre lui-même (art. 1070.1).

Une même personne peut être les deux, pour des fractions différentes. Le calcul
rend donc un couple de vrais ou de faux, jamais un rôle unique.

🔴 **La recherche passe par le registre, jamais par `owner_ids`.** Ce champ est
un many2many calculé et stocké : lire est juste, CHERCHER rend l'état d'avant
dans la même transaction, et le module s'est déjà fait prendre une fois avec
l'ancien propriétaire. Voir `bf_property_finance` et sa fiche.
"""
from odoo import api, fields, models


class BfPropertyUnit(models.Model):
    _inherit = "bf.property.unit"

    @api.model
    def _portal_units_for(self, partner):
        """Les fractions qu'une personne peut voir, et à quel titre.

        Rend `(fractions_possédées, fractions_occupées)`.
        """
        if not partner:
            empty = self.browse()
            return empty, empty
        ownerships = self.env["bf.property.ownership"].search(
            [("partner_id", "=", partner.id), ("is_current", "=", True)]
        )
        owned = ownerships.mapped("unit_id").filtered("active")
        occupied = self.search(
            [("occupant_id", "=", partner.id), ("is_rented", "=", True)]
        )
        return owned, occupied

    @api.model
    def _portal_syndicats_for(self, partner):
        """Les syndicats qu'une personne touche, avec le rôle qu'elle y a.

        Rend `{syndicat: {"owner": bool, "occupant": bool}}`. Le rôle est par
        syndicat : être copropriétaire ici n'ouvre rien là-bas.
        """
        owned, occupied = self._portal_units_for(partner)
        roles = {}
        for unit in owned:
            roles.setdefault(unit.syndicat_id, {"owner": False, "occupant": False})
            roles[unit.syndicat_id]["owner"] = True
        for unit in occupied:
            roles.setdefault(unit.syndicat_id, {"owner": False, "occupant": False})
            roles[unit.syndicat_id]["occupant"] = True
        roles.pop(self.env["bf.property.syndicat"], None)
        return roles

    @api.model
    def _portal_audiences_for(self, partner):
        """Les auditoires qu'une personne peut lire, syndicat par syndicat.

        Rend `{syndicat_id: set_des_auditoires}`. « all » suit dès qu'un rôle
        existe ; « owners » n'est jamais servi à un simple occupant.
        """
        audiences = {}
        for syndicat, role in self._portal_syndicats_for(partner).items():
            allowed = {"all"}
            if role["owner"]:
                allowed.add("owners")
            if role["occupant"]:
                allowed.add("occupants")
            audiences[syndicat.id] = allowed
        return audiences
