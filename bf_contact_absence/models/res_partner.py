"""Ce que la fiche contact gagne : un onglet, et deux champs cherchables.

⚠️ Les deux champs sont calculés NON stockés, et ils portent chacun un
`search=`. Un champ calculé non stocké sans méthode de recherche voit son
critère **écarté en silence** : un filtre « absent aujourd'hui » rendrait alors
toute la base, et « pas absent » aussi. Un stockage serait pire : la valeur
dépend de la date du jour, donc elle serait fausse dès le lendemain sans que
rien ne la recalcule.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ResPartner(models.Model):
    _inherit = "res.partner"

    bf_absence_ids = fields.One2many(
        comodel_name="bf.partner.absence",
        inverse_name="partner_id",
        string="Absences",
    )
    bf_absence_count = fields.Integer(compute="_compute_bf_absence")
    bf_is_away = fields.Boolean(
        string="Absent aujourd'hui",
        compute="_compute_bf_absence",
        search="_search_bf_is_away",
    )
    bf_away_until = fields.Date(
        string="Absent jusqu'au",
        compute="_compute_bf_absence",
        search="_search_bf_away_until",
    )
    bf_absence_phrase = fields.Char(
        string="Absence",
        compute="_compute_bf_absence",
        help="La phrase affichée au moment d'écrire.",
    )

    @api.depends("bf_absence_ids.date_from", "bf_absence_ids.date_to",
                 "bf_absence_ids.active", "parent_id")
    def _compute_bf_absence(self):
        # ⚠️ Rien pour un utilisateur EXTERNE. La fiche contact est lisible au
        # portail, et savoir qui est en vacances chez nos clients est une
        # information interne. Vérifié à l'audit du 2026-09-13 : sans ce
        # garde-fou, un compte portail lisait la phrase sur sa propre fiche.
        if self.env.user.share:
            for partner in self:
                partner.bf_absence_count = 0
                partner.bf_is_away = False
                partner.bf_away_until = False
                partner.bf_absence_phrase = False
            return
        absences = self.env["bf.partner.absence"]._for_partners(self.ids)
        comptes = {}
        if self.ids:
            groupes = self.env["bf.partner.absence"].sudo()._read_group(
                [("partner_id", "in", self.ids)],
                groupby=["partner_id"], aggregates=["__count"])
            comptes = {partner.id: nombre for partner, nombre in groupes}
        for partner in self:
            absence = absences.get(partner.id)
            partner.bf_absence_count = comptes.get(partner.id, 0)
            partner.bf_is_away = bool(absence)
            partner.bf_away_until = absence.date_to if absence else False
            partner.bf_absence_phrase = (
                absence._phrase(for_partner=partner) if absence else False)

    @api.model
    def _search_bf_is_away(self, operator, value):
        if operator not in ("=", "!="):
            raise UserError(_(
                "« Absent aujourd'hui » ne se filtre qu'avec = ou !=."))
        ids = self.env["bf.partner.absence"]._partners_away_ids()
        positif = (operator == "=") == bool(value)
        return [("id", "in" if positif else "not in", list(ids))]

    @api.model
    def _search_bf_away_until(self, operator, value):
        if operator not in ("=", "!=", "<", "<=", ">", ">="):
            raise UserError(_(
                "« Absent jusqu'au » ne se filtre que par comparaison de date."))
        borne = fields.Date.to_date(value)
        absences = self.env["bf.partner.absence"].sudo().search(
            self.env["bf.partner.absence"]._applicable_domain())
        par_partenaire = {}
        for absence in absences:
            cibles = [absence.partner_id.id]
            if absence.applies_to_children and absence.partner_id.is_company:
                cibles += absence.partner_id.child_ids.ids
            for cible in cibles:
                garde = par_partenaire.get(cible)
                if not garde or absence.date_to > garde:
                    par_partenaire[cible] = absence.date_to
        operateurs = {
            "=": lambda d: d == borne,
            "!=": lambda d: d != borne,
            "<": lambda d: d < borne,
            "<=": lambda d: d <= borne,
            ">": lambda d: d > borne,
            ">=": lambda d: d >= borne,
        }
        test = operateurs[operator]
        retenus = [pid for pid, fin in par_partenaire.items() if test(fin)]
        return [("id", "in", retenus)]

    def action_bf_open_absences(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Absences de %s") % self.display_name,
            "res_model": "bf.partner.absence",
            "view_mode": "list,form",
            "domain": [("partner_id", "=", self.id)],
            "context": {"default_partner_id": self.id},
        }
