"""Un catalogue de départ, pour que le module ne s'ouvre pas sur un écran vide.

Dix avantages courants au Québec, avec leurs règles. Les règles n'utilisent que
des critères disponibles partout (ancienneté, type d'emploi, ou rien) : elles ne
peuvent donc pas dépendre d'un département ou d'un poste qui n'existerait pas
chez le client.

Chargé par un bouton, et par les données de démonstration. La même méthode sert
aux deux, donc il n'y a qu'une seule liste à tenir à jour.
"""

from odoo import _, api, models

# (code, nom, catégorie, modèle de coût, montant, approbation, règles)
# Une règle est décrite par (nom, ancienneté en mois, type d'emploi).
STARTER = [
    ("ASSUR", "Assurance collective", "health", "per_employee_year", 2400.0, False,
     [("Permanents après trois mois", 3, "employee")]),
    ("REER", "REER collectif avec contribution de l'employeur", "retirement",
     "per_employee_year", 1800.0, False,
     [("Permanents après un an", 12, "employee")]),
    ("PAE", "Programme d'aide aux employés", "health", "per_employee_year", 180.0, False,
     [("Tout le personnel", 0, False)]),
    ("MOBILE", "Journées mobiles", "time_off", "none", 0.0, False,
     [("Permanents après trois mois", 3, "employee")]),
    ("TELE", "Télétravail et allocation de bureau à domicile", "equipment",
     "flat_year", 6000.0, False,
     [("Permanents après six mois", 6, "employee")]),
    ("FORM", "Budget annuel de formation", "learning", "per_use", 1500.0, True,
     [("Permanents après six mois", 6, "employee")]),
    ("CELL", "Forfait cellulaire", "equipment", "per_employee_year", 720.0, False,
     [("Tout le personnel", 0, False)]),
    ("STAT", "Stationnement ou titre de transport", "transport",
     "per_employee_year", 1200.0, False,
     [("Tout le personnel", 0, False)]),
    ("GYM", "Abonnement à un centre de conditionnement", "wellness", "per_use", 60.0, True,
     [("Permanents après trois mois", 3, "employee")]),
    ("FAM", "Congé pour obligations familiales rémunéré", "family", "none", 0.0, False,
     [("Permanents après un an", 12, "employee")]),
]


class BenefitStarter(models.Model):
    _inherit = "bf.ex.benefit"

    @api.model
    def _load_starter_catalogue(self, company=None):
        """Créer les avantages du catalogue de départ qui manquent.

        Idempotent : un avantage dont le code existe déjà dans la société est
        laissé tel quel, y compris s'il a été modifié. Le bouton peut donc se
        presser deux fois sans rien écraser.

        Renvoie les avantages créés.
        """
        company = company or self.env.company
        Rule = self.env["bf.ex.eligibility.rule"]
        created = self.browse()

        for code, name, category, cost_model, amount, approval, rules in STARTER:
            if self.with_context(active_test=False).search_count([
                ("code", "=", code), ("company_id", "=", company.id),
            ]):
                continue
            benefit = self.create({
                "code": code,
                "name": name,
                "category": category,
                "cost_model": cost_model,
                "cost_amount": amount,
                "approval_required": approval,
                "company_id": company.id,
            })
            for rule_name, months, employee_type in rules:
                Rule.create({
                    "name": rule_name,
                    "benefit_id": benefit.id,
                    "seniority_months_min": months,
                    "employee_type": employee_type or False,
                })
            created |= benefit
        return created

    @api.model
    def action_load_starter_catalogue(self):
        created = self._load_starter_catalogue()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success" if created else "warning",
                "message": (
                    _("%s avantage(s) ajouté(s) au catalogue.", len(created))
                    if created else
                    _("Le catalogue de départ est déjà chargé. Rien n'a été touché.")
                ),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
