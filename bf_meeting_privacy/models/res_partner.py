from odoo import models


class ResPartner(models.Model):
    _inherit = "res.partner"

    def _bf_privacy_is_internal(self):
        """Vrai lorsque ce contact est l'organisation elle-même ou l'un des siens.

        Le consentement à l'enregistrement d'une rencontre s'adresse aux
        personnes de l'extérieur. Pour l'équipe, le fondement n'est pas le
        consentement mais le lien d'emploi et l'exécution du mandat : demander
        un consentement à un employé pour une rencontre de travail laisserait
        croire qu'il peut le refuser sans conséquence sur son travail, ce qui
        n'est pas vrai et vicierait le consentement lui-même.

        ⚠️ « Interne » se lit par l'utilisateur, pas par le domaine courriel.
        Un client peut porter une adresse du même domaine qu'un fournisseur, et
        un employé peut consulter avec son adresse personnelle. Un utilisateur
        Odoo non-portail (`share = False`) est le seul marqueur qui suit
        vraiment le lien d'emploi.
        """
        self.ensure_one()
        if self == self.env.company.partner_id:
            return True
        return any(not user.share for user in self.user_ids)
