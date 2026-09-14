from odoo import fields, models


class SlideChannel(models.Model):
    _inherit = "slide.channel"

    #: 🔴 Ajouter une valeur à `slide_category` EXIGE ce champ sur DEUX modèles,
    #: et rien ne le dit. `slide.channel` porte les compteurs du cours ;
    #: `slide.slide` porte ceux de chaque SECTION (une diapositive dont
    #: `is_category` est vrai). Le second est le plus facile à oublier, parce
    #: qu'un compteur sur un modèle nommé « diapositive » ne saute pas aux yeux.
    #: `_compute_slides_statistics` du natif construit ses clés ainsi :
    #:
    #:     keys = ['nbr_%s' % c for c in
    #:             self.env['slide.slide']._fields['slide_category'].get_values(env)]
    #:
    #: puis fait `channel[cle] = ...`. Une catégorie ajoutée par `selection_add`
    #: sans son compteur lève donc `KeyError: 'nbr_scorm'` au premier flush qui
    #: touche un canal — pas à l'installation, pas aux vues : bien plus tard, sur
    #: une écriture sans rapport apparent avec le module.
    nbr_scorm = fields.Integer(
        string="Nombre de paquets SCORM", compute="_compute_slides_statistics",
        store=True)
