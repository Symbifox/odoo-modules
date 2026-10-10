"""La fiche d'urgence, nourrie par Healthy Fox quand la personne coche la case.

Ce qui sort de Healthy Fox : le nom et le dosage des médicaments actifs, le nom
des conditions actives ou gérées. Rien d'autre (ni humeur, ni signes vitaux, ni
notes). Healthy Fox n'a pas de fiche d'allergie : les allergies s'écrivent sur la
carte. Pour un enfant, ce sont les fiches que ses parents tiennent pour lui.

🔴 La case ouvre ces lignes à tout le foyer et aux liens de la gardienne : c'est
le choix de la personne (ou du parent), et de personne d'autre. Seuls ceux qui
tiennent la carte la cochent (règle d'écriture de la carte).
"""
from odoo import _, models

SUIVI = ("suivi", "offert")


class HouseholdEmergencyCard(models.Model):
    _inherit = "bf.household.emergency.card"

    def _compute_health_summary(self):
        super()._compute_health_summary()
        for carte in self:
            carte.health_available = True
            lu = carte.sudo()
            if not lu.include_health:
                carte.health_summary = False
                continue
            domaine = lu._bf_health_domain()
            if domaine is None:
                carte.health_summary = False
                continue
            env = lu.env
            medicaments = env["health.medication"].search(domaine + [("state", "=", "active")], order="name")
            conditions = env["health.condition"].search(
                domaine + [("state", "in", ("active", "managed"))], order="name")
            lignes = []
            if medicaments:
                lignes.append(_("Medications: %s", "; ".join(
                    f"{m.name} ({m.dosage})" if m.dosage else m.name for m in medicaments)))
            if conditions:
                lignes.append(_("Conditions: %s", "; ".join(conditions.mapped("name"))))
            carte.health_summary = "\n".join(lignes) or _("Nothing active in Healthy Fox.")

    def _bf_health_domain(self):
        """Les fiches santé de la personne de la carte, ou None s'il n'y en a pas."""
        self.ensure_one()
        lu = self.sudo()
        if lu.child_id:
            dependants = lu.child_id.health_dependent_ids.filtered(lambda d: d.state in SUIVI)
            return [("dependent_id", "in", dependants.ids)] if dependants else None
        return [("create_uid", "=", lu.holder_user_id.id), ("dependent_id", "=", False)]
