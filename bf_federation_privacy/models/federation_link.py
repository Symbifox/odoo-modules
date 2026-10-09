"""Les liens d'un avis de violation ne se posent qu'en superutilisateur.

Le socle ouvre la création et l'écriture des liens au gestionnaire de projet, sans garde sur
l'objet visé. Pour un avis, un lien posé à la main transmettrait son fil à un pair choisi et
ouvrirait la porte à un accusé venu de lui. Tous les chemins légitimes (partage, réception,
archivage) tournent déjà en sudo.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import SQL

MODEL = "privacy.breach.notice"


class FederationLink(models.Model):
    _inherit = "federation.link"

    # La société du lien est celle de l'AVIS, pas celle du pair : un pair sans société ouvrait ses
    # liens d'avis aux lecteurs de toutes les sociétés. Une règle ne joint pas `res_id` à l'avis ;
    # ce champ le fait par sa recherche, en SQL.
    breach_in_company = fields.Boolean(
        compute="_compute_breach_in_company", search="_search_breach_in_company",
        string="Avis dans mes sociétés")

    def _breach_in_company_ids(self):
        # Les sociétés cochées, bornées à celles de l'utilisateur : en sudo, le contexte n'est pas vérifié.
        company_ids = tuple((self.env.companies & self.env.user.company_ids).ids) or (0,)
        self.env["federation.link"].flush_model(["res_model", "res_id"])  # le SQL lit la base
        self.env[MODEL].flush_model(["company_id"])
        self.env.cr.execute(SQL(
            "SELECT l.id FROM federation_link l JOIN privacy_breach_notice n ON n.id = l.res_id "
            "WHERE l.res_model = %s AND n.company_id IN %s", MODEL, company_ids))
        return [row[0] for row in self.env.cr.fetchall()]

    @api.depends_context("uid", "allowed_company_ids")
    def _compute_breach_in_company(self):
        ids = set(self._breach_in_company_ids())
        for link in self:
            link.breach_in_company = link.id in ids

    def _search_breach_in_company(self, operator, value):
        if operator not in ("=", "!=") or not isinstance(value, bool):
            raise UserError(_("Recherche non prise en charge."))
        positive = (operator == "=") == value
        return [("id", "in" if positive else "not in", self._breach_in_company_ids())]

    def _breach_link_refusal(self):
        # Littéral dans `_()` : l'extracteur des traductions ne lit que les littéraux.
        return UserError(_("Les liens d'un avis de violation se posent seuls, au partage ou à la réception."))

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and any(v.get("res_model") == MODEL for v in vals_list):
            raise self._breach_link_refusal()
        links = super().create(vals_list)
        if not self.env.su and links.filtered(lambda l: l.res_model == MODEL):
            raise self._breach_link_refusal()  # par un défaut du contexte
        return links

    def write(self, vals):
        if not self.env.su and (vals.get("res_model") == MODEL or self.filtered(lambda l: l.res_model == MODEL)):
            raise self._breach_link_refusal()
        return super().write(vals)

    def unlink(self):
        if not self.env.su and self.filtered(lambda l: l.res_model == MODEL):
            raise self._breach_link_refusal()
        return super().unlink()

    def _apply_message(self, data):
        """Un message venu du pair sur un avis n'est pas retenu (voir mail_message.py)."""
        if self.res_model == MODEL:
            return None
        return super()._apply_message(data)
