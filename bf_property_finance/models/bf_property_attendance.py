"""Le pont entre les impayés et l'art. 1094 C.c.Q.

Art. 1094 : « Le copropriétaire qui, depuis plus de trois mois, n'a pas acquitté
sa quote-part des charges communes, est privé de son droit de vote. Il peut à
nouveau exercer ce droit dès qu'il acquitte la totalité des charges communes
qu'il doit. » (1991, c. 64, a. 1094 ; 2019, c. 28, a. 51.)

La gouvernance porte depuis P4.0 une case `voting_deprived` cochée à la main,
avec ce commentaire : « le module ne connaît pas encore l'état des charges, qui
vit dans le volet financier ». Le volet financier existe maintenant, et c'est
lui qui vient au-devant : `bf_property_finance` dépend de la gouvernance, jamais
l'inverse. Un champ typé posé dans l'autre sens rendrait la gouvernance
ininstallable seule.

⚠️ La case reste cochée à la main, et c'est voulu. Le module calcule le fait —
une contribution échue depuis plus de trois mois et non acquittée — et le
propose. Il ne prive personne : la privation se constate à l'assemblée, sur un
registre que le président tient, et un encaissement reçu la veille et pas encore
saisi suffit à la faire tomber.

⚠️ Trois lectures s'arrêtent ici, portées à P2.3 :

1. **La privation frappe la personne, pas la fraction.** « Le copropriétaire
   [...] est privé de son droit de vote » : celui qui détient trois fractions et
   n'en paie qu'une perd ses voix, toutes. Le module propose donc la privation
   sur toutes les lignes de cette personne.
2. **Ce que couvre « sa quote-part des charges communes ».** L'art. 1072 range
   les sommes à verser au fonds de prévoyance et au fonds d'auto assurance dans
   la contribution aux charges communes ; le module les compte donc.
3. **La restauration est plus exigeante que la privation.** L'alinéa 2 rend le
   droit « dès qu'il acquitte la totalité des charges communes qu'il doit », ce
   qui n'est pas la simple disparition d'un retard de plus de trois mois. Suivre
   cet état d'une assemblée à l'autre supposerait de le mémoriser ; le module
   affiche les deux faits et laisse trancher.
"""
from odoo import _, api, fields, models

from .bf_property_syndicat import DEPRIVATION_MONTHS


class BfPropertyAssemblyAttendance(models.Model):
    _inherit = "bf.property.assembly.attendance"

    charges_overdue_amount = fields.Monetary(
        string="Charges échues impayées",
        currency_field="currency_id",
        compute="_compute_charges_state",
        help="Capital des contributions échues depuis plus de trois mois et "
             "non acquittées, pour toutes les fractions de ce copropriétaire "
             "dans ce syndicat.",
    )
    charges_overdue_since = fields.Date(
        string="Impayé depuis", compute="_compute_charges_state"
    )
    charges_total_due = fields.Monetary(
        string="Total dû au syndicat",
        currency_field="currency_id",
        compute="_compute_charges_state",
        help="Art. 1094 al. 2 C.c.Q. : le droit de vote revient « dès qu'il "
             "acquitte la totalité des charges communes qu'il doit ». Ce "
             "total-ci porte tout ce qui reste dû, échu depuis plus de trois "
             "mois ou non.",
    )
    deprivation_suggested = fields.Boolean(
        string="Art. 1094 : privation constatable",
        compute="_compute_charges_state",
        help="Le registre montre une contribution échue depuis plus de trois "
             "mois et non acquittée. Le module le signale ; il ne coche rien.",
    )
    currency_id = fields.Many2one(
        related="company_id.currency_id", string="Devise"
    )

    # ⚠️ Ces `depends` sont INCOMPLETS, et volontairement. Le fait se mesure
    # contre le registre des impayés, que rien ne relie à une ligne de présence :
    # un encaissement saisi sur une fraction change l'état de toutes les lignes
    # de son propriétaire, à toutes les assemblées, sans qu'aucune chaîne de
    # dépendances puisse le voir. Le champ n'est donc pas stocké — un champ
    # stocké garderait la photo du jour de la saisie — et les écrivains
    # invalident explicitement (voir `bf_property_payment.py`). Sans cela, lire,
    # encaisser, relire dans la même transaction rend la valeur d'AVANT.
    @api.depends("partner_id", "assembly_id.date", "assembly_id.syndicat_id")
    def _compute_charges_state(self):
        # Un passage par assemblée : `_owners_in_default` interroge tout le
        # registre, et le refaire ligne par ligne multiplierait la recherche par
        # le nombre de copropriétaires.
        for assembly in self.mapped("assembly_id"):
            lines = self.filtered(lambda l, a=assembly: l.assembly_id == a)
            syndicat = assembly.syndicat_id
            if not syndicat or not assembly.date:
                lines._clear_charges_state()
                continue
            in_default = syndicat._owners_in_default(
                assembly.date.date(), DEPRIVATION_MONTHS
            )
            owed = {}
            for unit in syndicat.unit_ids:
                for partner in unit.owner_ids:
                    owed[partner.id] = owed.get(partner.id, 0.0) + unit.overdue_total
            for line in lines:
                entry = in_default.get(line.partner_id.id)
                line.charges_overdue_amount = entry["amount"] if entry else 0.0
                line.charges_overdue_since = entry["since"] if entry else False
                line.deprivation_suggested = bool(entry)
                line.charges_total_due = owed.get(line.partner_id.id, 0.0)
        self.filtered(lambda l: not l.assembly_id)._clear_charges_state()

    def _clear_charges_state(self):
        for line in self:
            line.charges_overdue_amount = 0.0
            line.charges_overdue_since = False
            line.charges_total_due = 0.0
            line.deprivation_suggested = False


class BfPropertyAssembly(models.Model):
    _inherit = "bf.property.assembly"

    deprivation_candidate_count = fields.Integer(
        string="Privations constatables (art. 1094)",
        compute="_compute_deprivation_candidates",
    )

    @api.depends(
        "attendance_ids.deprivation_suggested", "attendance_ids.voting_deprived"
    )
    def _compute_deprivation_candidates(self):
        for assembly in self:
            assembly.deprivation_candidate_count = len(
                assembly.attendance_ids.filtered(
                    lambda a: a.deprivation_suggested and not a.voting_deprived
                )
            )

    def action_apply_deprivation(self):
        """Coche l'art. 1094 sur les lignes que le registre désigne.

        Un geste explicite, jamais un automatisme : l'art. 1094 se constate à
        l'assemblée, et il retire des voix, ce que l'art. 1103 rend attaquable
        90 jours durant. Le chatter garde qui a appliqué quoi, et sur la foi de
        quel impayé.
        """
        for assembly in self:
            candidates = assembly.attendance_ids.filtered(
                lambda a: a.deprivation_suggested and not a.voting_deprived
            )
            if not candidates:
                continue
            candidates.voting_deprived = True
            detail = "".join(
                _(
                    "<li>%(owner)s — %(unit)s : %(amount).2f dus, échus depuis "
                    "le %(since)s</li>"
                )
                % {
                    "owner": line.partner_id.name,
                    "unit": line.unit_id.display_name,
                    "amount": line.charges_overdue_amount,
                    "since": line.charges_overdue_since,
                }
                for line in candidates
            )
            assembly.message_post(
                body=_(
                    "<p>Art. 1094 C.c.Q. appliqué à %(count)d ligne(s) de "
                    "présence, sur la foi du registre des impayés : une "
                    "contribution échue depuis plus de trois mois n'est pas "
                    "acquittée.</p><ul>%(detail)s</ul><p>Art. 1099 : les voix "
                    "ainsi retirées viennent en diminution du total des voix du "
                    "syndicat. Art. 1094 al. 2 : le droit revient dès que la "
                    "totalité des charges dues est acquittée.</p>"
                )
                % {"count": len(candidates), "detail": detail}
            )
        return True
