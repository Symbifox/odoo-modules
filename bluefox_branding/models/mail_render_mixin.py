from odoo import api, models

from . import brand_color_mixin as bc


class MailRenderMixin(models.AbstractModel):
    _inherit = 'mail.render.mixin'

    @api.model
    def _replace_button_colors(self, html_content, company=None):
        """Replace old Odoo button colors with ``company``'s brand colors."""
        return bc.replace_button_colors(self.env, html_content, company=company)

    @api.model
    def _bf_societes_des_dossiers(self, model, res_ids):
        """La société de chaque dossier rendu, à défaut celle de la session.

        Le rendu reçoit déjà son modèle et ses identifiants : la couleur se
        décide donc par dossier, sans rien chercher de plus que le champ
        Société. En sudo, pour la même raison que sur `mail.mail` : une
        couleur n'est pas un droit d'accès.

        ⚠️ La chaîne s'arrête ici à la session, sans passer par l'auteur :
        dans ce chemin l'auteur EST l'usager en place, et la société de la
        session porte déjà son choix de sélecteur. Lire sa société principale
        plutôt que la session reviendrait à défaire ce choix.

        `res_ids` est une LISTE d'identifiants : le seul appelant est
        `_render_template`, qui passe les clés de son propre résultat.
        """
        defaut = self.env.company
        # `bc.entier` et non `int()` : le même garde-fou que sur `mail.mail`,
        # pour la même raison — un identifiant illisible ne doit pas lever
        # dans un chemin qui ne décide qu'une couleur.
        ids = [ident for ident in map(bc.entier, res_ids or ()) if ident]
        # Même garde que sur `mail.mail`, pour la même raison : être dans le
        # registre ne veut pas dire avoir une table (`bc.modele_sans_table`).
        if not model or model not in self.env or not ids \
                or bc.modele_sans_table(self.env[model]):
            return {}
        dossiers = self.env[model].sudo().browse(ids).exists()
        if not dossiers:
            return {}
        return dossiers._mail_get_companies(default=defaut)

    @api.model
    def _render_template(self, template_src, model, res_ids, engine='inline_template',
                         add_context=None, options=None):
        """Override to replace button colors in rendered email templates."""
        result = super()._render_template(
            template_src, model, res_ids, engine=engine,
            add_context=add_context, options=options
        )

        # 🔴 Aucune branche « chaîne nue » : le cœur d'Odoo 18 rend TOUJOURS
        # un dict ici, et lève si `res_ids` n'est pas une liste. Ce lot en a
        # porté une, qui tenait sa seule heuristique (« une seule société
        # rendue, donc c'est celle-là ») — rien ne l'éprouvait, et elle
        # pouvait peindre une couleur fausse en silence. Retirée : une
        # surcharge moins dérivée que la nôtre qui rendrait autre chose verra
        # son corps simplement non balayé, un non-geste plutôt qu'une couleur
        # devinée.
        #
        # La société ne se cherche que pour ce qui va changer : la très grande
        # majorité des rendus (sujets, corps sans bouton) ne porte aucun mauve.
        if isinstance(result, dict):
            a_remplacer = [res_id for res_id, content in result.items()
                           if isinstance(content, str) and bc.porte_une_vieille_couleur(content)]
            if not a_remplacer:
                return result
            societes = self._bf_societes_des_dossiers(model, a_remplacer)
            for res_id in a_remplacer:
                result[res_id] = self._replace_button_colors(
                    result[res_id], company=societes.get(res_id) or self.env.company)

        return result
