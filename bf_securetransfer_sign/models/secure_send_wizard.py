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
        string="Require signing an NDA",
        help="Each recipient will sign the NDA in their own name, after "
             "confirming their identity with a code and before seeing the "
             "content.",
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
                    "\"%s\" has no NDA uploaded: there is nothing to sign.", rec.brand_id.display_name))
            if rec.nda_required and rec.audience_allow_sms:
                raise UserError(_(
                    "An NDA cannot be signed by a visitor identified only "
                    "by their mobile (a signature requires an email "
                    "address). Uncheck \"Offer the code by SMS\", or do "
                    "not require an NDA."))
        return super().action_send()


    @api.onchange("template_id")
    def _onchange_template_id(self):
        """⚠ Le décorateur est REPOSÉ sur la surcharge.

        `@api.onchange` marque la fonction, pas le nom : une surcharge non
        décorée remplacerait la méthode enregistrée par une méthode que rien
        n'appelle, et le préréglage cesserait de s'appliquer — sans erreur.

        Le socle a déjà écrit `nda_required` (il boucle sur ce que
        `_apply_vals` lui rend). Il reste à refuser ce que la marque ne peut
        pas honorer, et à le dire : une entente exigée sur une marque sans
        document ferait échouer l'envoi tout à la fin, sur une case que
        l'expéditeur ne regarde plus."""
        res = super()._onchange_template_id()
        extra = None
        for rec in self:
            if not rec.template_id or not rec.nda_required:
                continue
            if not rec.brand_id.nda_document:
                rec.nda_required = False
                extra = _(
                    "\"%(tmpl)s\" requires an NDA, but the \"%(brand)s\" "
                    "brand has none uploaded. The requirement is "
                    "removed.\n\nUpload the NDA on the brand (Settings › "
                    "Brands), or choose a brand that has one.",
                    tmpl=rec.template_id.display_name,
                    brand=rec.brand_id.display_name or _("(none)"))
            elif rec.audience_allow_sms:
                # Même refus que `action_send`, un écran plus tôt : une
                # signature exige une adresse courriel.
                rec.audience_allow_sms = False
                extra = _(
                    "\"%(tmpl)s\" requires an NDA: identification by "
                    "mobile is removed, since a signer must have an email "
                    "address.",
                    tmpl=rec.template_id.display_name)
        if not extra:
            return res
        existing = (res or {}).get("warning") or {}
        message = "%s\n\n%s" % (existing["message"], extra) \
            if existing.get("message") else extra
        return {"warning": {
            "title": existing.get("title") or _("Preset partly applied"),
            "message": message,
        }}

    def _transfer_vals(self, vals):
        vals = super()._transfer_vals(vals)
        vals["nda_required"] = bool(self.nda_required)
        return vals
