from odoo import models


class BfSignRequest(models.Model):
    _inherit = "bf.sign.request"

    def action_send(self):
        # Figé AVANT les contrôles de l'envoi : `_copy_problems` mesure la
        # rangée de cases qui recopie le nom sur `_auto_value`, donc sur le nom
        # de l'acte. Mesurée sur le nom d'usage, une rangée trop courte pour le
        # nom légal passait, puis l'acte était coupé au scellement. `_ensure_signer_partners` ne relie que les
        # signataires qui n'ont pas de fiche : l'appeler ici puis dans
        # l'envoi ne fait rien deux fois.
        for request in self.filtered(lambda r: r.state == "draft"):
            request._ensure_signer_partners()
        # Le second rattachement que fait l'envoi ne refige pas : le
        # signataire relié par un courriel partagé y paraîtrait déjà relié, et
        # recevrait le nom légal de l'autre personne (vu à l'essai). Cette clé
        # n'évite aucun gel : celui d'ici a toujours lieu, avant.
        return super(BfSignRequest, self.with_context(bf_act_frozen=True)).action_send()

    def _ensure_signer_partners(self):
        # Après le rattachement, pas avant : c'est ici qu'un signataire saisi à
        # la main (ou créé sans fiche, comme l'entente du dépôt sécurisé)
        # rejoint sa fiche. Passé le brouillon,
        # l'identité ne s'écrit plus, et un renvoi ne la refige pas.
        sans_fiche = self.signer_ids.filtered(lambda s: not s.partner_id)
        res = super()._ensure_signer_partners()
        if self.state == "draft" and not self.env.context.get("bf_act_frozen"):
            # Un signataire relié par son seul courriel : la recherche prend
            # la première fiche venue. Un courriel partagé par deux personnes
            # mettait le nom légal de l'AUTRE sur l'acte. Sans
            # une seule fiche de personne à ce courriel, l'acte prend le nom
            # du signataire.
            Partner = self.env["res.partner"].sudo().with_context(active_test=False)
            ambigus = sans_fiche.filtered(lambda s: Partner.search_count([
                ("email", "=ilike", s.email), ("is_company", "=", False)]) != 1)
            self.signer_ids._bf_freeze_act_name(ambiguous=ambigus)
        return res
