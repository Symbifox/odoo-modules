"""Exiger l'entente depuis l'assistant d'envoi.

L'assistant est le seul chemin de création d'une audience ouverte, donc c'est
là que la case doit vivre. Elle est proposée dès qu'une entente est disponible
— sur le transfert à venir ou sur sa marque — et vaut pour les deux modes de
transmission : la tâche demandait la NDA « aussi pour les autres types d'envois
en option ».
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SecureSendWizard(models.TransientModel):
    _inherit = "secure.transfer.send.wizard"

    nda_required = fields.Boolean(
        string="Exiger la signature d'une entente de confidentialité",
        help="Chaque destinataire signera l'entente à son nom, après avoir "
             "confirmé son identité par code et avant de voir le contenu.",
    )
    nda_available = fields.Boolean(compute="_compute_nda_available")

    @api.depends("brand_id")
    def _compute_nda_available(self):
        for rec in self:
            rec.nda_available = bool(rec.brand_id.nda_document)

    @api.onchange("brand_id")
    def _onchange_brand_nda(self):
        """Reprendre la politique de la marque, et retomber à « non » quand la
        marque choisie n'a pas d'entente — sinon l'envoi refuserait à la fin
        pour une case que l'utilisateur ne voit même plus."""
        for rec in self:
            if not rec.brand_id.nda_document:
                rec.nda_required = False
            elif rec.brand_id.nda_required:
                rec.nda_required = True

    def action_send(self):
        """⚠ Une entente exige une identité courriel signable.

        Refuser ICI, avec un message, plutôt que de laisser l'envoi partir et
        les visiteurs mobiles se heurter à une entente qu'ils ne peuvent pas
        signer. (`secure.transfer._audience_limits` retire déjà le canal, mais
        l'expéditeur mérite de savoir qu'il vient de perdre une option.)"""
        for rec in self:
            if rec.nda_required and not rec.brand_id.nda_document \
                    and not rec.nda_available:
                raise UserError(_(
                    "« %s » n'a aucune entente de confidentialité téléversée : "
                    "il n'y a rien à faire signer.", rec.brand_id.display_name))
            if rec.nda_required and rec.audience_allow_sms:
                raise UserError(_(
                    "Une entente de confidentialité ne peut pas être signée par "
                    "un visiteur identifié seulement par son mobile (une "
                    "signature exige une adresse courriel). Décochez « Offrir "
                    "le code par SMS », ou n'exigez pas d'entente."))
        return super().action_send()

    def _transfer_vals(self, vals):
        vals = super()._transfer_vals(vals)
        vals["nda_required"] = bool(self.nda_required)
        return vals
