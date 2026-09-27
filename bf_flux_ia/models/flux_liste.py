# -*- coding: utf-8 -*-
from odoo import fields, models


class FluxListe(models.Model):
    _inherit = "bf.flux.liste"

    ia_actif = fields.Boolean(
        "Trier par IA", tracking=True,
        help="Après les règles, un modèle de langage note chaque élément retenu.")
    ia_consigne = fields.Text(
        "Ce qui compte pour cette liste",
        help="En mots simples : qui lit, ce qui leur sert, ce qui ne leur sert "
             "pas. Exemple : « Appels d'offres publics et subventions "
             "pour les PME. Pas les résultats financiers ni les avis aux actionnaires. »")
    ia_seuil = fields.Integer(
        "Seuil de pertinence", default=50,
        help="De 0 à 100. Sous ce seuil, l'élément est écarté, avec sa raison.")
    ia_delai_heures = fields.Integer(
        "Diffuser sans jugement après (h)", default=6,
        help="Si le modèle reste indisponible, l'élément est diffusé sur la foi "
             "des règles passé ce délai. Une panne ne bloque jamais la veille.")

    _sql_constraints = [
        ("ia_seuil_borne", "CHECK(ia_seuil >= 0 AND ia_seuil <= 100)",
         "Le seuil va de 0 à 100."),
    ]
