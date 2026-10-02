from odoo import _, api, fields, models
from odoo.exceptions import UserError

LINK_FIELD = "bf_assembly_proposal_id"
# Ce que la résolution reprend de l'assemblée : il dit ce que les membres ont
# adopté, et ne se réécrit pas au registre.
BRIDGED_FIELDS = {
    "name", "resolution_type", "meeting_type", "meeting_date", "resolved_text",
    "vote_for", "vote_against", "vote_abstain", "unanimously_adopted",
    "mover_id", "seconder_id", "company_id",
}


class CorporateResolution(models.Model):
    """La résolution sait de quelle proposition d'assemblée elle vient.

    🔴 Le lien s'écrit de CE côté seulement ; la proposition le lit par
    l'inverse. Une proposition est verrouillée dès la clôture de son
    assemblée, et c'est après la clôture qu'on inscrit la résolution : écrire
    le lien sur la proposition obligerait à percer ce verrou. Un seul champ
    écrit, c'est aussi un lien qui ne peut pas se contredire.

    La contrainte d'unicité est le filet contre le doublon : deux clics
    simultanés passent tous deux le contrôle en Python, pas celui de la base.

    🔴 Le lien ne s'écrit qu'en superutilisateur, c'est-à-dire par le pont,
    ni à la création (valeurs ou valeur par défaut du contexte) ni ensuite.
    Une personne gestionnaire du registre, même sans rôle Membres, lierait
    sinon une résolution de son cru à une proposition : le pont la croirait
    déjà inscrite et refuserait l'inscription de la résolution adoptée.
    """

    _inherit = "corporate.resolution"

    bf_assembly_proposal_id = fields.Many2one(
        "bf.membership.assembly.proposal", string="Proposition d'assemblée des membres",
        readonly=True, copy=False, index=True, ondelete="set null",
    )
    bf_assembly_id = fields.Many2one(
        related="bf_assembly_proposal_id.assembly_id", string="Assemblée des membres",
    )

    _sql_constraints = [
        ("bf_assembly_proposal_uniq", "unique(bf_assembly_proposal_id)",
         "Cette proposition a déjà sa résolution au registre corporatif."),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            if any(vals.get(LINK_FIELD) for vals in vals_list):
                raise UserError(_(
                    "Le lien vers une proposition d'assemblée des membres se pose par "
                    "l'inscription au registre depuis la proposition adoptée."))
            # Une valeur par défaut du contexte s'ajouterait après ce contrôle.
            self = self.with_context({
                key: value for key, value in self.env.context.items() if key != "default_" + LINK_FIELD})
        return super().create(vals_list)

    def _check_bridged_kept(self, vals):
        """🔴 Une résolution inscrite par le pont garde ce que l'assemblée a
        adopté : son titre, son texte, ses totaux, sa séance.

        Réécrits au registre, ils feraient dire au livre des minutes autre
        chose que le procès-verbal de l'assemblée. Son statut ne change plus
        que pour la marquer remplacée, et ses signataires (la présidence et le
        secrétariat d'assemblée) sont figés : voir le modèle des signataires.
        Le reste de la fiche (date d'entrée en vigueur, préambule, notes,
        documents) suit la vie du registre.
        """
        if not self.env.su and "status" in vals:
            for rec in self.filtered(LINK_FIELD):
                if vals["status"] != rec.status and vals["status"] != "superseded":
                    raise UserError(_(
                        "« %s » vient d'une assemblée des membres, qui l'a adoptée : son "
                        "statut ne change plus au registre, sauf pour la marquer remplacée.",
                        rec.name))
        keys = BRIDGED_FIELDS & vals.keys()
        if self.env.su or not keys:
            return
        for rec in self.filtered(LINK_FIELD):
            for name in keys:
                field = self._fields[name]
                current, new = rec[name], vals[name]
                if field.type == "many2one":
                    changed = current.id != (new or False)
                elif field.type == "date":
                    changed = current != fields.Date.to_date(new)
                elif field.type in ("char", "html", "text", "selection"):
                    changed = (current or "") != (new or "")
                else:
                    changed = current != new
                if changed:
                    raise UserError(_(
                        "« %s » vient d'une assemblée des membres : son titre, son texte, "
                        "ses totaux et sa séance sont ceux que l'assemblée a adoptés, et ne "
                        "se réécrivent pas au registre.", rec.name))

    def unlink(self):
        """🔴 Une résolution inscrite par le pont ne se supprime pas au registre.

        Supprimée, elle libérerait sa proposition, qui se réinscrirait : le
        livre des minutes perdrait sa référence, ou en gagnerait une seconde.
        Une résolution remplacée passe à l'état « remplacée », comme les autres.
        """
        if not self.env.su and self.filtered(LINK_FIELD):
            raise UserError(_(
                "« %s » vient d'une assemblée des membres : elle ne se supprime pas au "
                "registre. Marquez-la plutôt remplacée.", self.filtered(LINK_FIELD)[0].name))
        return super().unlink()

    def write(self, vals):
        self._check_bridged_kept(vals)
        if LINK_FIELD in vals and not self.env.su and any(
                rec[LINK_FIELD].id != (vals[LINK_FIELD] or False) for rec in self):
            raise UserError(_(
                "Le lien vers une proposition d'assemblée des membres se pose par "
                "l'inscription au registre depuis la proposition adoptée ; il ne se "
                "change pas à la main."))
        return super().write(vals)

    def _bf_open_action(self):
        """La résolution seule en fiche, ou plusieurs en liste."""
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "bf_corporate_governance.corporate_resolution_action")
        if len(self) == 1:
            action.update({"res_id": self.id, "view_mode": "form", "views": [(False, "form")]})
        else:
            action["domain"] = [("id", "in", self.ids)]
        return action
