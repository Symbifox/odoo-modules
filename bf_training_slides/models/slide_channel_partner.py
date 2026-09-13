import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class SlideChannelPartner(models.Model):
    """La complétion d'un cours écrit une ligne au registre.

    🔴 Le natif ne garde **aucune date de complétion** : ni
    `slide.channel.partner` ni `slide.slide.partner` n'en portent une, et la
    ligne de CV que produit `hr_skills_slides` est datée du jour du recalcul.
    Ici, la réalisation est écrite **au moment où la complétion se produit** :
    c'est la seule façon d'avoir une date qui veut dire quelque chose.
    """

    _inherit = "slide.channel.partner"

    def _recompute_completion(self):
        avant = {rec.id: rec.member_status for rec in self}
        resultat = super()._recompute_completion()
        acheves = self.filtered(
            lambda r: r.member_status == "completed" and avant.get(r.id) != "completed")
        if acheves:
            acheves._ecrire_au_registre()
        self.env["bf.training.assignment"].sudo().search([
            ("channel_partner_id", "in", self.ids)])._sync_completion()
        return resultat

    def _ecrire_au_registre(self):
        """Crée la réalisation correspondant à cette complétion."""
        Activite = self.env["bf.training.activity"].sudo()
        Realisation = self.env["bf.training.record"].sudo()
        Employe = self.env["hr.employee"].sudo()
        creees = Realisation.browse()
        for inscription in self:
            activite = Activite.search(
                [("slide_channel_id", "=", inscription.channel_id.id)], limit=1)
            if not activite:
                continue
            employe = Employe.search([
                "|", ("work_contact_id", "=", inscription.partner_id.id),
                ("user_id.partner_id", "=", inscription.partner_id.id),
            ], limit=1)
            if not employe:
                # Une personne sans fiche d'employé n'a pas de dossier de
                # formation : on ne fabrique pas d'employé pour la loger.
                _logger.info(
                    "Registre de formation : complétion du cours %s par %s, "
                    "sans fiche d'employé ; rien écrit au registre.",
                    inscription.channel_id.id, inscription.partner_id.id)
                continue
            jour = fields.Date.context_today(self)
            deja = Realisation.search([
                ("employee_id", "=", employe.id),
                ("activity_id", "=", activite.id),
                ("content_version", "=", activite.content_version),
            ], limit=1)
            if deja:
                continue
            creees |= Realisation.create({
                "employee_id": employe.id,
                "activity_id": activite.id,
                "date_done": jour,
                "hours": activite.slide_channel_id.total_time or 0.0,
                "mode": "elearning",
                "state": "confirmed",
                "support_provided": activite.requires_support and activite.support_default,
                "plan_id": activite.default_plan_id.id if activite.default_plan_id else False,
                "note": _("Écrite automatiquement à la complétion du cours en ligne."),
            })
        return creees
