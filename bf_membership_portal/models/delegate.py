from odoo import _, fields, models
from odoo.exceptions import UserError


class MembershipDelegate(models.Model):
    _inherit = "bf.membership.delegate"

    in_office = fields.Boolean(
        string="En fonction aujourd'hui", compute="_compute_in_office", search="_search_in_office",
        help="Le mandat couvre la date du jour.",
    )

    def _compute_in_office(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.in_office = rec._in_office(today)

    def _search_in_office(self, operator, value):
        """Cherchable, pour que la règle du portail tienne compte de la date.

        🔴 Une règle d'enregistrement ne peut pas comparer à la date du jour :
        Odoo met en cache le domaine évalué de chaque règle, par usager, jusqu'à
        la prochaine invalidation. `time.strftime(...)` écrit dans la règle
        serait figé au jour de son premier calcul, et un délégué remplacé
        verrait encore l'adhésion de l'organisation des semaines plus tard.
        Ce champ, lui, calcule la date au moment de la recherche.
        """
        if operator not in ("=", "!=") or not isinstance(value, bool):
            raise UserError(_("Recherche non prise en charge."))
        today = fields.Date.context_today(self)
        domain = [
            "|", ("date_from", "=", False), ("date_from", "<=", today),
            "|", ("date_to", "=", False), ("date_to", ">=", today),
        ]
        if (operator == "=") == value:
            return domain
        return [("id", "not in", self._search(domain))]
