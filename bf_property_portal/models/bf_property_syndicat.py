"""Ce que le syndicat affiche de lui-même au portail.

Les coordonnées d'un syndicat ne sont pas celles d'une entreprise : il n'a
souvent ni bureau ni employé, et la personne à joindre est un administrateur
bénévole ou un gestionnaire externe. Le module ne devine rien et laisse le
syndicat écrire ce qu'il veut voir affiché.

⚠️ **Aucun annuaire des copropriétaires.** L'art. 1070 al. 1 met au registre le
nom et l'adresse de chaque copropriétaire, et n'y met les autres renseignements
personnels que si la personne y consent expressément. Un portail qui listerait
les coordonnées de tout le monde distribuerait ces renseignements sans ce
consentement, et l'art. 1070.1 réserve de toute façon la consultation du
registre à des conditions que le portail ne remplit pas. Le portail affiche donc
la personne à joindre au syndicat, et rien sur les autres occupants.
"""
from odoo import fields, models


class BfPropertySyndicat(models.Model):
    _inherit = "bf.property.syndicat"

    portal_contact_name = fields.Char(
        string="Personne à joindre",
        help="Administrateur, secrétaire ou gestionnaire. Affiché au portail.",
    )
    portal_contact_email = fields.Char(string="Courriel du portail")
    portal_contact_phone = fields.Char(string="Téléphone du portail")
    portal_contact_note = fields.Text(
        string="Précisions au portail",
        help="Heures de disponibilité, marche à suivre en cas d'urgence, "
             "modalités de consultation du registre prévues au règlement de "
             "l'immeuble (art. 1070.1 C.c.Q.).",
    )
    request_acknowledge_days = fields.Integer(
        string="Engagement de prise en charge (jours)",
        default=0,
        help="⚠️ Aucune disposition n'oblige le syndicat à répondre dans un "
             "nombre de jours : ce n'est pas un délai légal, c'est "
             "l'engagement que le syndicat se donne. À zéro, le module ne "
             "compte rien et n'affiche aucun retard.",
    )
    request_ids = fields.One2many(
        "bf.property.request", "syndicat_id", string="Demandes d'entretien"
    )

    announcement_ids = fields.One2many(
        "bf.property.announcement", "syndicat_id", string="Annonces"
    )
    portal_document_ids = fields.One2many(
        "bf.property.document", "syndicat_id", string="Documents du portail"
    )
    announcement_count = fields.Integer(compute="_compute_portal_counts")
    portal_document_count = fields.Integer(compute="_compute_portal_counts")

    def _compute_portal_counts(self):
        announcements = self.env["bf.property.announcement"]._read_group(
            [("syndicat_id", "in", self.ids)], ["syndicat_id"], ["__count"]
        )
        documents = self.env["bf.property.document"]._read_group(
            [("syndicat_id", "in", self.ids)], ["syndicat_id"], ["__count"]
        )
        by_announcement = {s.id: count for s, count in announcements}
        by_document = {s.id: count for s, count in documents}
        for syndicat in self:
            syndicat.announcement_count = by_announcement.get(syndicat.id, 0)
            syndicat.portal_document_count = by_document.get(syndicat.id, 0)

    def action_view_announcements(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Annonces",
            "res_model": "bf.property.announcement",
            "view_mode": "list,form",
            "domain": [("syndicat_id", "=", self.id)],
            "context": {"default_syndicat_id": self.id},
        }

    def action_view_portal_documents(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Documents du portail",
            "res_model": "bf.property.document",
            "view_mode": "list,form",
            "domain": [("syndicat_id", "=", self.id)],
            "context": {"default_syndicat_id": self.id},
        }
