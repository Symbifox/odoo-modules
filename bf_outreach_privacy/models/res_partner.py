"""Le refus noté sur le contact s'inscrit aussi à la liste d'opposition.

Le contact survit à la destruction des cibles, et son drapeau
`outreach_opt_out` avec lui. Mais un contact se fusionne, s'archive, se
supprime ; et une nouvelle liste peut porter la même adresse sur une autre
fiche. L'empreinte, elle, ne dépend d'aucune fiche.

⚠️ `res.partner.write` est appelé partout dans Odoo : tout ce qui suit ne coûte
rien tant que ni le drapeau ni une coordonnée d'un contact qui a refusé ne
change.
"""
from odoo import SUPERUSER_ID, api, models

from .outreach_optout import check_can_lift

_CONTACT_FIELDS = ("email", "phone", "mobile")


class ResPartner(models.Model):
    _inherit = "res.partner"

    @api.model_create_multi
    def create(self, vals_list):
        partners = super().create(vals_list)
        for partner in partners.filtered("outreach_opt_out"):
            partner._bf_engrave_outreach_optout()
        return partners

    def write(self, vals):
        lifted = self.browse()
        if "outreach_opt_out" in vals and not vals["outreach_opt_out"]:
            lifted = self.filtered("outreach_opt_out")
            if lifted:
                check_can_lift(self.env)
        res = super().write(vals)
        if vals.get("outreach_opt_out"):
            for partner in self:
                partner._bf_engrave_outreach_optout()
        elif any(f in vals for f in _CONTACT_FIELDS):
            for partner in self.filtered("outreach_opt_out"):
                partner._bf_engrave_outreach_optout()
        if lifted:
            Optout = self.env["bf.outreach.optout"]
            for partner in lifted:
                Optout._bf_lift(partner)
        return res

    def _bf_engrave_outreach_optout(self):
        """Inscrit le contact, et les coordonnées propres de ses cibles.

        Une cible peut porter un courriel ou un cellulaire que le contact n'a
        pas : sans les inscrire, DEM-001 détruirait la cible sans empreinte.
        """
        self.ensure_one()
        Optout = self.env["bf.outreach.optout"]
        # ⚠️ bf_outreach propage le refus d'une cible au contact en `sudo()` :
        # `env.user` reste alors l'usager qui a noté le refus. Seul OdooBot
        # (cron, reprise) ne doit rien signer.
        user = self.env.user if self.env.uid != SUPERUSER_ID else False
        Target = self.env["bf.outreach.target"].sudo().with_context(active_test=False)
        # Les cibles d'abord. Une cible qui a refusé elle-même est propriétaire
        # de SES coordonnées (la lever lève ce qu'elle a apporté) ; les autres
        # cibles du contact lui laissent la propriété : lever le contact doit
        # tout lever.
        for target in Target.search([("partner_id", "=", self.id)]):
            owner = target if target.do_not_contact else self
            Optout._bf_engrave(
                Optout._bf_target_keys(target), owner._name == "bf.outreach.target"
                and "target" or "partner", owner,
                refused_on=target.do_not_contact_date or self.outreach_opt_out_date,
                user=target.do_not_contact_user_id or user,
                reason=target.do_not_contact_reason or self.outreach_opt_out_reason,
                company=target.company_id,
            )
        Optout._bf_engrave(
            Optout._bf_partner_keys(self), "partner", self,
            refused_on=self.outreach_opt_out_date,
            user=user,
            reason=self.outreach_opt_out_reason,
            company=self.company_id,
        )
