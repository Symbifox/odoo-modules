import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class BfTrainingAssignment(models.Model):
    """Ce que l'assignation gagne quand l'activité est adossée à un cours."""

    _inherit = "bf.training.assignment"

    slide_channel_id = fields.Many2one(
        related="activity_id.slide_channel_id", string="Cours en ligne", store=True)
    channel_partner_id = fields.Many2one(
        "slide.channel.partner", string="Inscription",
        compute="_compute_channel_partner_id", store=True)
    member_status = fields.Selection(
        related="channel_partner_id.member_status", string="Statut au cours")

    @api.depends("partner_id", "slide_channel_id")
    def _compute_channel_partner_id(self):
        for rec in self:
            if not rec.partner_id or not rec.slide_channel_id:
                rec.channel_partner_id = False
                continue
            rec.channel_partner_id = self.env["slide.channel.partner"].sudo().search([
                ("channel_id", "=", rec.slide_channel_id.id),
                ("partner_id", "=", rec.partner_id.id),
            ], limit=1)

    # ------------------------------------------------------------------
    # Le compte portail
    # ------------------------------------------------------------------
    def _auto_grant_portal(self):
        """Fabrique-t-on un compte portail quand la personne n'en a pas ?"""
        return self.env["ir.config_parameter"].sudo().get_param(
            "bf_training.auto_grant_portal", "1") in ("1", "True", "true")

    def _ensure_portal_user(self):
        """Le cours n'enregistre la complétion que pour une personne connectée.

        Sans compte, l'inscription tient mais rien ne revient : la personne suit
        le cours et le registre n'en saura rien. On fabrique donc un compte
        portail quand c'est permis et qu'il y a une adresse.
        """
        self.ensure_one()
        partenaire = self.partner_id
        if partenaire.user_ids:
            return partenaire.user_ids[0]
        if not (self._auto_grant_portal() and partenaire.email):
            return False
        groupe = self.env.ref("base.group_portal", raise_if_not_found=False)
        if not groupe:
            return False
        try:
            return self.env["res.users"].sudo().with_context(
                no_reset_password=True).create({
                    "name": partenaire.name or partenaire.email,
                    "login": partenaire.email,
                    "email": partenaire.email,
                    "partner_id": partenaire.id,
                    "groups_id": [(6, 0, [groupe.id])],
                })
        except Exception as exc:  # noqa: BLE001 - collision de login, etc.
            _logger.warning(
                "Registre de formation : compte portail impossible pour le "
                "partenaire %s : %s", partenaire.id, exc)
            return False

    def action_enroll(self):
        """Inscrire la personne au cours adossé à l'activité."""
        for rec in self:
            if not rec.slide_channel_id:
                continue
            utilisateur = rec._ensure_portal_user()
            statut = "joined" if utilisateur else "invited"
            rec.slide_channel_id.sudo()._action_add_members(
                rec.partner_id, member_status=statut)
            rec._compute_channel_partner_id()
            if rec.state == "pending":
                rec.state = "in_progress"
        return True

    def action_open_course(self):
        self.ensure_one()
        if not self.slide_channel_id:
            return False
        return {
            "type": "ir.actions.act_url",
            "url": self.slide_channel_id.website_url,
            "target": "new",
        }

    def _sync_completion(self):
        """Reprendre l'avancement du cours, sans rien inventer."""
        for rec in self:
            inscription = rec.channel_partner_id
            if not inscription:
                continue
            rec.completion = inscription.completion
            if inscription.member_status == "completed" and rec.state != "done":
                rec.state = "done"
            elif inscription.member_status == "ongoing" and rec.state == "pending":
                rec.state = "in_progress"
        return True
