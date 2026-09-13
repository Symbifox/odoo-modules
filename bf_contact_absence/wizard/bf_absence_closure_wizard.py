"""Semer les fermetures annoncées un an d'avance.

Les vacances de la construction ne se devinent pas dans un répondeur : la CCQ
les publie, elles tombent aux mêmes dates pour toute l'industrie, et un client
du secteur ferme donc à une date qu'on connaît avant lui.

Mesuré dans un corpus réel : trois personnes de la même
entreprise ont répondu la même fermeture en juillet 2026, dont une qui écrit
« nous serons fermés ». Personne ne s'en souvenait d'une année à l'autre.

Source des dates : CCQ, « Congés et vacances de la construction »
(https://www.ccq.org/fr-CA/avantages-sociaux/dates-conges-vacances). Elles sont
fixées par les conventions collectives, pas par un règlement, et la CCQ les
publie par année. ⚠️ Ce sont donc des DONNÉES, pas une règle à calculer : une
version du module les porte, et il faut les remettre à jour, pas les deviner.
"""

from datetime import date

from odoo import _, api, fields, models
from odoo.exceptions import UserError

# (clé, libellé, début, fin inclusive)
FERMETURES = [
    ("estival_2026", "Congé estival 2026", date(2026, 7, 19), date(2026, 8, 1)),
    ("hivernal_2026", "Congé hivernal 2026-2027", date(2026, 12, 20), date(2027, 1, 2)),
    ("estival_2027", "Congé estival 2027", date(2027, 7, 25), date(2027, 8, 7)),
    ("hivernal_2027", "Congé hivernal 2027-2028", date(2027, 12, 19), date(2028, 1, 1)),
]
PAR_CLE = {c: (lib, deb, fin) for c, lib, deb, fin in FERMETURES}


class BfAbsenceClosureWizard(models.TransientModel):
    _name = "bf.absence.closure.wizard"
    _description = "Semer une fermeture connue"

    closure = fields.Selection(
        selection=[(c, lib) for c, lib, _d, _f in FERMETURES],
        string="Fermeture",
        required=True,
        default=lambda self: self._default_closure(),
    )
    date_from = fields.Date(string="Du", compute="_compute_dates", store=True,
                            readonly=False)
    date_to = fields.Date(string="Au", compute="_compute_dates", store=True,
                          readonly=False,
                          help="Dernier jour de fermeture, inclus.")
    partner_ids = fields.Many2many(
        comodel_name="res.partner", string="Entreprises",
        domain="[('is_company', '=', True)]",
        help="Une fermeture se pose sur la SOCIÉTÉ : elle avertit ensuite "
             "pour chacun de ses contacts.")
    industry_id = fields.Many2one(
        comodel_name="res.partner.industry", string="Ou tout un secteur",
        help="Toutes les sociétés de ce secteur, en plus de celles nommées "
             "ci-dessus.")
    only_with_contacts = fields.Boolean(
        string="Seulement celles à qui on écrit", default=True,
        help="Écarte les sociétés qui n'ont ni contact rattaché ni courriel : "
             "un avertissement qui ne servira jamais est du bruit.")
    reminder = fields.Boolean(string="Rappel au retour", default=False,
                              help="Rarement voulu pour une fermeture "
                                   "d'entreprise : personne ne prend des "
                                   "nouvelles d'une société qui rouvre.")

    @api.model
    def _default_closure(self):
        """La prochaine fermeture qui n'est pas déjà passée."""
        today = fields.Date.context_today(self)
        for cle, _lib, _deb, fin in FERMETURES:
            if fin >= today:
                return cle
        return FERMETURES[-1][0]

    @api.depends("closure")
    def _compute_dates(self):
        for wiz in self:
            if wiz.closure in PAR_CLE:
                _lib, deb, fin = PAR_CLE[wiz.closure]
                wiz.date_from, wiz.date_to = deb, fin
            else:
                wiz.date_from = wiz.date_to = False

    def _cibles(self):
        self.ensure_one()
        cibles = self.partner_ids
        if self.industry_id:
            cibles |= self.env["res.partner"].search([
                ("is_company", "=", True),
                ("industry_id", "=", self.industry_id.id),
            ])
        if self.only_with_contacts:
            cibles = cibles.filtered(lambda p: p.child_ids or p.email)
        return cibles

    def action_seed(self):
        self.ensure_one()
        if not self.date_from or not self.date_to:
            raise UserError(_("Choisir une fermeture, ou saisir ses dates."))
        cibles = self._cibles()
        if not cibles:
            raise UserError(_(
                "Aucune entreprise visée. Nommer des sociétés, ou choisir un "
                "secteur."))
        Absence = self.env["bf.partner.absence"]
        poses, sautes = Absence, Absence
        for partner in cibles:
            # ⚠️ On ne double jamais une période : une absence déjà là, même
            # saisie à la main ou apprise d'un répondeur, gagne sur le semis.
            deja = Absence.search([
                ("partner_id", "=", partner.id),
                ("date_from", "<=", self.date_to),
                ("date_to", ">=", self.date_from),
            ], limit=1)
            if deja:
                sautes |= deja
                continue
            poses |= Absence.create({
                "partner_id": partner.id,
                "date_from": self.date_from,
                "date_to": self.date_to,
                "nature": "closure",
                "applies_to_children": True,
                "reminder": self.reminder,
                "source": "import",
                "note": PAR_CLE.get(self.closure, (self.closure,))[0],
            })
        message = _("%(poses)s fermeture(s) posée(s), %(sautes)s déjà connue(s).",
                    poses=len(poses), sautes=len(sautes))
        if not poses:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {"type": "warning", "message": message,
                           "sticky": False},
            }
        return {
            "type": "ir.actions.act_window",
            "name": _("Fermetures posées"),
            "res_model": "bf.partner.absence",
            "view_mode": "list,form",
            "domain": [("id", "in", poses.ids)],
        }
