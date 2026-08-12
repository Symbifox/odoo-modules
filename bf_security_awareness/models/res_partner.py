from odoo import _, fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    security_profile_id = fields.Many2one(
        "bf.security.profile", string="Profil de risque",
        compute="_compute_security_profile_id", search="_search_security_profile",
    )
    security_risk_level = fields.Selection(
        related="security_profile_id.risk_level", string="Niveau de risque",
        store=False,
    )
    phishing_result_ids = fields.One2many(
        "bf.phishing.result", "partner_id", string="Résultats d'hameçonnage")
    training_assignment_ids = fields.One2many(
        "bf.training.assignment", "partner_id", string="Formations assignées")
    phishing_result_count = fields.Integer(
        compute="_compute_secaware_counts", string="Tests d'hameçonnage")
    training_assignment_count = fields.Integer(
        compute="_compute_secaware_counts", string="Formations")

    def _compute_security_profile_id(self):
        Profile = self.env["bf.security.profile"]
        for partner in self:
            partner.security_profile_id = Profile.search(
                [("partner_id", "=", partner.id)], limit=1)

    def _search_security_profile(self, operator, value):
        profiles = self.env["bf.security.profile"].search(
            [("id", operator, value)])
        return [("id", "in", profiles.mapped("partner_id").ids)]

    def _compute_secaware_counts(self):
        for partner in self:
            partner.phishing_result_count = len(partner.phishing_result_ids)
            partner.training_assignment_count = len(
                partner.training_assignment_ids)

    def action_view_phishing_results(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Résultats d'hameçonnage"),
            "res_model": "bf.phishing.result",
            "view_mode": "list,form",
            "domain": [("partner_id", "=", self.id)],
        }

    def action_view_training_assignments(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Formations assignées"),
            "res_model": "bf.training.assignment",
            "view_mode": "list,form",
            "domain": [("partner_id", "=", self.id)],
        }
