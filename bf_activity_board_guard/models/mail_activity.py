# Copyright 2026 Les services de consultation Blue Fox, Inc.
# License LGPL-3
from odoo import api, models


class MailActivity(models.Model):
    _inherit = "mail.activity"

    @api.depends("res_id", "res_model")
    def _compute_related_model_instance(self):
        """N'écrire la référence que si le champ accepte ce modèle.

        `mail_activity_board` bâtit la sélection de `related_model_instance`
        sur `ir.model.is_mail_activity`, donc sur les modèles qui héritent de
        `mail.activity.mixin`, mais son calcul y écrit « modèle,id » sans
        vérifier que le modèle est dans cette sélection. `convert_to_cache`
        lève alors un `ValueError`.

        Un champ calculé se calcule pour TOUT le lot lu : une seule activité
        fautive fait donc tomber, pour tout le monde, les deux endroits qu'un
        lecteur atteint : la liste du tableau et son formulaire. (Le module OCA
        injecte aussi le champ dans la fenêtre d'activité d'Odoo,
        `mail.mail_activity_view_form_popup`, mais ce formulaire ne le lit que
        pour l'activité qu'il affiche, et une activité sur un modèle sans mixin
        n'a pas de chatter d'où l'ouvrir. Le kanban ne le lit pas du tout.)

        Une activité peut légitimement porter un tel modèle : le bouton
        « Planifier une activité » exige le mixin, mais un appel XML-RPC, non,
        et un modèle peut cesser de porter le mixin sans que ses activités
        disparaissent. La garde rend la ligne lisible au lieu de tout casser.

        `get_values()` est volontairement la source interrogée : c'est le même
        objet champ et le même appel que ceux que `convert_to_cache` valide
        (odoo/fields.py). Ni `lang`, ni le `sudo()` de la méthode OCA, ni un
        cache ne peuvent les faire diverger. L'appel est sorti de la boucle :
        une recherche `ir.model` par lot, pas une par ligne.
        """
        if not self:
            return
        permis = set(self._fields["related_model_instance"].get_values(self.env))
        for record in self:
            ref = False
            if record.res_model in permis and record.res_id:
                ref = f"{record.res_model},{record.res_id}"
            record.related_model_instance = ref
