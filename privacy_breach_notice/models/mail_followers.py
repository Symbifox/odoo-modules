"""Les abonnés d'un avis ou d'une fiche du registre se lisent dans la société de la fiche.

Une règle ne peut pas joindre `res_id` à la société de la fiche suivie : ce champ non stocké le
fait par sa recherche, en SQL. Défini à l'identique dans privacy_breach_notice et privacy_incident
(chacun peut être installé seul) ; les deux règles qui s'en servent ont le même domaine.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import SQL

TABLES = (("privacy.breach.notice", "privacy_breach_notice"), ("privacy.incident", "privacy_incident"))


class MailFollowers(models.Model):
    _inherit = "mail.followers"

    privacy_in_company = fields.Boolean(
        compute="_compute_privacy_in_company", search="_search_privacy_in_company",
        string="Fiche Vie privée dans mes sociétés")

    @api.depends_context("uid", "allowed_company_ids")
    def _compute_privacy_in_company(self):
        ids = set(self._privacy_in_company_ids())
        for follower in self:
            follower.privacy_in_company = follower.id in ids

    def _privacy_in_company_ids(self):
        # Les sociétés cochées, bornées à celles de l'utilisateur : en sudo, le contexte n'est pas vérifié.
        company_ids = tuple((self.env.companies & self.env.user.company_ids).ids) or (0,)
        self.env["mail.followers"].flush_model(["res_model", "res_id"])  # le SQL lit la base
        ids = []
        for model, table in TABLES:
            self.env.cr.execute(SQL("SELECT to_regclass(%s)", table))
            if not self.env.cr.fetchone()[0]:
                continue
            if model in self.env:
                self.env[model].flush_model(["company_id"])
            self.env.cr.execute(SQL(
                "SELECT f.id FROM mail_followers f JOIN %s r ON r.id = f.res_id "
                "WHERE f.res_model = %s AND r.company_id IN %s",
                SQL.identifier(table), model, company_ids))
            ids += [row[0] for row in self.env.cr.fetchall()]
        return ids

    def _search_privacy_in_company(self, operator, value):
        if operator not in ("=", "!=") or not isinstance(value, bool):
            raise UserError(_("Recherche non prise en charge."))
        positive = (operator == "=") == value
        return [("id", "in" if positive else "not in", self._privacy_in_company_ids())]
