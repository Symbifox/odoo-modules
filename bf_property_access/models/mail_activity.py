from odoo import _, models


class MailActivity(models.Model):
    _inherit = "mail.activity"

    def _action_done(self, feedback=False, attachment_ids=None):
        """Marquer l'activité d'un ordre comme faite marque l'ordre fait.

        Le responsable du point (un concierge) n'a souvent que la lecture :
        l'activité lui est assignée, et c'est son geste qui fait foi. La
        vérification d'accès est celle de l'activité elle-même ; l'ordre est
        ensuite écrit en sudo, au nom de la personne qui l'a fait.
        """
        # 🔴 L'usager assigné peut réécrire son activité (modèle, document,
        # type) : on ne croit donc ni `res_id` ni le type. Seule compte
        # l'activité que le module a lui-même remise à l'ordre, marquée faite
        # par la personne à qui elle est assignée, et cette personne doit
        # encore pouvoir faire l'ordre (responsable du point ou gestionnaire).
        Order = self.env["bf.property.access.order"].sudo()
        orders = Order.search([("hand_over_activity_id", "in", self.ids)]) if self.ids else Order
        user = self.env.user
        is_manager = user.has_group("bf_property_core.group_bf_property_manager")
        legit = orders.filtered(
            lambda o: is_manager or (o.hand_over_activity_id.user_id == user
                                     and o.point_id.user_id == user)
        )
        res = super()._action_done(feedback=feedback, attachment_ids=attachment_ids)
        # sudo garde l'usager (env.uid) : « Fait par » reste la bonne personne.
        legit.exists()._bf_mark_done()
        # Fermée par quelqu'un qui ne peut pas faire l'ordre (ancien
        # responsable du point…) : l'ordre ne reste pas sans rappel.
        (orders - legit).exists().filtered(lambda o: o.state == "waiting")._bf_hand_over(
            _("Activité fermée par %s, qui ne peut pas faire cet ordre : remise "
              "au responsable actuel du point.") % user.name
        )
        return res
