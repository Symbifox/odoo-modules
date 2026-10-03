from odoo import api, models


class IrActionsActWindow(models.Model):
    _inherit = "ir.actions.act_window"

    @api.model
    def _bf_selecteur_du_bureau(self):
        """Le sélecteur d'action d'un panneau du bureau, appelé par un interne
        qui n'administre pas.

        `ir.actions.act_window` est fermé aux internes : le sélecteur et sa
        « Recherche avancée » levaient une AccessError, et seule
        l'administration composait un bureau.
        """
        return (self.env.context.get("bf_bureau_pane_picker") and not self.env.su
                and self.env.user._is_internal()
                and not self.env.user.has_group("base.group_system"))

    @api.model
    def _bf_actions_ouvrables(self, domain, limit=None):
        """Cherchées en superutilisateur, mais seulement celles qu'une personne
        pourrait de toute façon ouvrir : sans groupe ou d'un de ses groupes,
        sur un modèle qu'elle a le droit de lire. Rien de neuf :
        `/web/action/load` sert déjà n'importe quelle action, en
        superutilisateur, à tout interne."""
        user = self.env.user
        candidats = self.sudo().search(list(domain or []), limit=limit and limit * 5)
        return candidats.filtered(
            lambda a: (not a.groups_id or a.groups_id & user.groups_id)
            and a.res_model in self.env
            and self.env[a.res_model].has_access("read"))

    @api.model
    def name_search(self, name="", args=None, operator="ilike", limit=100):
        if not self._bf_selecteur_du_bureau():
            return super().name_search(name, args, operator, limit)
        domaine = list(args or []) + ([("name", operator, name)] if name else [])
        visibles = self._bf_actions_ouvrables(domaine, limit=limit or 100)
        return [(a.id, a.display_name) for a in visibles[:limit or None]]

    @api.model
    def web_search_read(self, domain, specification, offset=0, limit=None, order=None,
                        count_limit=None):
        if not self._bf_selecteur_du_bureau():
            return super().web_search_read(domain, specification, offset=offset, limit=limit,
                                           order=order, count_limit=count_limit)
        visibles = self._bf_actions_ouvrables(domain)
        if order:
            visibles = visibles.sorted(lambda a: a.display_name or "")
        tranche = visibles[offset:offset + limit if limit else None]
        return {"length": len(visibles), "records": tranche.web_read(specification)}

    @api.model
    def get_views(self, views, options=None):
        """La liste de « Recherche avancée » charge ses vues avant ses lignes,
        et Odoo n'y passe pas le contexte du champ : pas de drapeau ici. Les
        vues d'un modèle ne sont pas ses données ; tout interne les lit."""
        if (not self.env.su and self.env.user._is_internal()
                and not self.env.user.has_group("base.group_system")):
            return super(IrActionsActWindow, self.sudo()).get_views(views, options)
        return super().get_views(views, options)
