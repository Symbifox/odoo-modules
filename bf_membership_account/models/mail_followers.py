from odoo import api, models


class MailFollowers(models.Model):
    _inherit = "mail.followers"

    @api.model_create_multi
    def create(self, vals_list):
        """🔴 Une facture de cotisation n'a pour abonnés que le membre lui-même et
        des usagers internes.

        La règle du portail d'Odoo ouvre une facture à toute personne dont le
        partenaire commercial est un parent d'un ABONNÉ de la facture. Écrire à
        la personne de facturation depuis le fil de la facture (l'abonnement
        automatique l'inscrit), l'ajouter aux abonnés, ou une personne
        déléguée abonnée, puis la rattacher à une autre entreprise : le portail
        de celle-ci lirait la facture et son PDF. Tous les chemins d'abonnement
        (`message_subscribe`, abonnement automatique, invitation) finissent ici :
        les autres inscriptions sont écartées, en silence, pour ces factures
        et leurs avoirs.
        """
        move_ids = {v["res_id"] for v in vals_list
                    if v.get("res_model") == "account.move" and v.get("res_id") and v.get("partner_id")}
        if move_ids:
            members = self.env["account.move"].sudo().browse(list(move_ids))._bf_membership_fee_members()
            if members:
                partners = self.env["res.partner"].sudo().browse(
                    [v["partner_id"] for v in vals_list if v.get("res_id") in members])
                internal = partners.filtered(lambda p: p.user_ids.filtered(lambda u: not u.share))
                vals_list = [
                    v for v in vals_list
                    if not (v.get("res_model") == "account.move" and v.get("res_id") in members)
                    or v.get("partner_id") == members[v["res_id"]] or v.get("partner_id") in internal.ids
                ]
        return super().create(vals_list)
