"""Les anniversaires des enfants du foyer, posés par le cron de Célébrations.

Le cron de Célébrations ne connaît que les profils d'employés. Ce pont lui ajoute
les enfants du foyer dont un parent a donné le jour de naissance : une occasion
« Anniversaire » sur le contact de l'enfant, à l'horizon du cron, une seule par
date. La personne qui organise est le parent qui tient ses fiches. Aucune
occasion ne porte d'âge (le module n'en calcule jamais).
"""
from odoo import api, models


class CelebrationOccasion(models.Model):
    _inherit = "bf.celebration.occasion"

    @api.model
    def _generer_anniversaires(self, aujourdhui, limite):
        crees = super()._generer_anniversaires(aujourdhui, limite)
        return crees + self._generer_anniversaires_enfants(aujourdhui, limite)

    @api.model
    def _generer_anniversaires_enfants(self, aujourdhui, limite):
        enfants = self.env["bf.household.child"].sudo().search([
            ("celebrate_birthday", "=", True), ("birth_day", "!=", False), ("partner_id", "!=", False),
        ])
        valeurs, organisateurs = [], []
        for enfant in enfants:
            date = enfant.next_birthday(aujourdhui)
            if not date or date > limite:
                continue
            if self.sudo().search_count([
                    ("partner_id", "=", enfant.partner_id.id), ("occasion_type", "=", "birthday"),
                    ("date", "=", date)]):
                continue
            valeurs.append({
                "occasion_type": "birthday",
                "date": date,
                "partner_id": enfant.partner_id.id,
                "company_id": self.env.company.id,
                "allow_board": True,
            })
            organisateurs.append(enfant.primary_parent_id)
        if not valeurs:
            return 0
        occasions = self.sudo().create(valeurs)
        for occasion, organisateur in zip(occasions, organisateurs):
            occasion.organizer_id = organisateur.id if organisateur.active else False
        occasions._poser_evenement_agenda()
        return len(occasions)
