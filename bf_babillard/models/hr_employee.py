# -*- coding: utf-8 -*-
from odoo import api, models


class HrEmployee(models.Model):
    """Le ménage que la base ne fera pas à notre place.

    🔴 `bf.babillard.post.personne_id` pointe `hr.employee.public`, qui est une
    VUE SQL. Odoo ne crée aucune clé étrangère vers une vue, donc un
    `ondelete="set null"` déclaré sur le champ ne s'exécute JAMAIS : il a l'air
    d'une protection et n'en est pas une.

    Sans ce crochet, un employé supprimé (pas archivé : supprimé) laisse un
    identifiant mort dans la publication. Le champ lié `personne_nom` lève alors
    MissingError à la lecture, et comme le fil lit ce champ sur chaque carte,
    c'est le babillard ENTIER qui tombe, pour toute l'audience, pas seulement la
    carte fautive.
    """

    _inherit = "hr.employee"

    @api.model_create_multi
    def create(self, vals_list):
        employes = super().create(vals_list)
        employes._babillard_bienvenue(len(vals_list))
        return employes

    def _babillard_bienvenue(self, taille_du_lot):
        """Souhaiter la bienvenue, une arrivée à la fois.

        C'est ce que le personnel de terrain dit aider le plus, et c'est de la
        « participation automatique » au sens de NN/g : le fil se remplit de ce
        que la maison fait déjà, sans que personne ait à y penser.

        🔴 La garde est le NOMBRE d'employés créés d'un coup. Une arrivée se
        crée seule ; un import, une base de démonstration ou une migration en
        crée des dizaines, et souhaiter deux cents bienvenues le même matin
        aurait enterré le fil au lieu de l'animer.
        """
        if taille_du_lot != 1:
            return
        contexte = self.env.context
        if contexte.get("install_mode") or contexte.get("import_file"):
            return
        Post = self.env["bf.babillard.post"]
        langue = Post._langue_de_la_maison()
        for employe in self:
            fiche = employe.sudo()
            if not fiche.name or not fiche.active:
                continue
            departement = fiche.department_id.with_context(lang=langue).name
            # 🔴 Le titre s'écrit dans la langue de la MAISON. Un appel sans
            # langue au contexte retombe sur `en_US`, et la carte arrive en
            # anglais sur un babillard français.
            maison = Post.with_context(lang=langue)
            titre = (maison.env._("Bienvenue à %(nom)s, qui rejoint %(equipe)s",
                                  nom=fiche.name, equipe=departement)
                     if departement
                     else maison.env._("Bienvenue à %(nom)s", nom=fiche.name))
            Post._depuis_source("hr.employee", employe.id, {
                "name": titre,
                "type_publication": "celebration",
                "audience": "tous",
                "personne_id": employe.id,
                "company_id": fiche.company_id.id or self.env.company.id,
            })

    def unlink(self):
        if self.ids:
            self.env["bf.babillard.post"].sudo().search(
                [("personne_id", "in", self.ids)]).write({"personne_id": False})
        return super().unlink()
