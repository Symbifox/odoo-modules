"""Le titre de la page de liens suit le nom d'usage de la fiche.

Une page de personne naît avec le nom de la fiche (`_sync_page_values`, ou à
la main) et le gardait : la passe périodique de `bf_linkpage` ne rafraîchit que
les liens du gabarit. Renommer une personne laissait donc l'ancien nom en haut
de sa page publique, dans le titre de l'onglet et dans l'aperçu de partage.

Ici, à chaque écriture du nom de la fiche (fiche, geste « Changer le nom
d'usage », portail, préférences, API) et à la fusion de contacts : le titre des
pages de la personne suit, sans trace au fil de la page. Seulement quand il
était le nom de la fiche : un titre écrit à la main n'est jamais écrasé.

⚠️ Un employé renommé dans les RH ne renomme pas sa fiche : Odoo 18 ne recopie
pas le nom de l'employé vers son contact de travail. Le geste « Changer le nom
d'usage » renomme les deux.
"""

from odoo import models


class ResPartner(models.Model):
    _inherit = "res.partner"

    def write(self, vals):
        if "name" not in vals:
            return super().write(vals)
        # Relevé AVANT l'écriture : quelles pages portent encore, pour titre,
        # le nom que la fiche quitte.
        following = self.env["bf.linkpage"]._bf_owner_pages(self).filtered(
            lambda page: page.name == page.partner_id.name)
        res = super().write(vals)
        following._bf_follow_partner_name()
        return res

    def unlink(self):
        # Les pages d'une fiche supprimée partaient en cascade SQL, sans passer
        # par `bf.linkpage.unlink` : leur adresse devenait libre. On les supprime d'abord par l'ORM,
        # pages d'organisation et archivées comprises : c'est l'effet de la
        # cascade, et `bf.linkpage.unlink` réserve leur adresse. (Réserver puis
        # laisser la cascade faisait vérifier deux fois la même ligne de la
        # réserve par PostgreSQL, contre une page déjà supprimée.)
        self.env["bf.linkpage"].sudo().with_context(active_test=False).search(
            [("partner_id", "in", self.ids)]).unlink()
        return super().unlink()

    def _bf_erase_name_history(self):
        count = super()._bf_erase_name_history()
        pages = self.env["bf.linkpage"]._bf_owner_pages(self)
        return count + pages._bf_erase_name_history()
