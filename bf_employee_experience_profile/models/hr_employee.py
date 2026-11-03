from odoo import fields, models


class HrEmployee(models.Model):
    """La fiche employé porte les déclarations, et rien de plus.

    🔴 Aucun champ calculé du genre « porte un profil » ni « type dominant »
    n'est posé ici, et c'est la leçon du satellite des allergies : un drapeau
    calculé sur une liste que la règle d'accès vide répond « non » à qui n'a pas
    le droit de lire, au lieu de refuser. Un indicateur faux est pire qu'un
    indicateur absent.

    ⚠️ Et rien n'est STOCKE : un champ stocké sur la fiche serait filtrable et
    permettrait de regrouper le personnel par type de personnalité.
    """

    _inherit = "hr.employee"

    # Pas de `groups=` sur le CHAMP, parce qu'il sert ailleurs que sur la fiche :
    # c'est la règle d'accès de `bf.ex.profile` qui décide des lignes lisibles.
    #
    # 🔴 Mesuré, et contraire à ce qu'on croit en écrivant la vue : un
    # usager sans `hr.group_hr_user` ne peut PAS ouvrir le formulaire
    # `hr.employee` du tout. Odoo 18 lève un RedirectWarning dans
    # `hr.employee.get_views` et le renvoie vers l'annuaire public. La fiche
    # employé n'est donc jamais la surface du gestionnaire : la sienne est
    # l'action « Les profils de mon équipe ».
    ex_profile_ids = fields.One2many(
        "bf.ex.profile", "employee_id", string="Profile types",
    )
