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

    alerte_active = fields.Boolean(
        "Alerter quand c'est urgent", tracking=True,
        help="Le tri par IA dit aussi si un élément ne peut pas attendre le "
             "résumé. Si oui, un courriel part tout de suite aux personnes nommées.")
    alerte_consigne = fields.Text(
        "Ce qui mérite une alerte",
        help="En mots simples. Exemple : « Une nouvelle qui change le monde : "
             "guerre déclarée, catastrophe majeure, effondrement des marchés. »")
    alerte_user_ids = fields.Many2many(
        "res.users", "bf_flux_liste_alerte_user_rel", "liste_id", "user_id",
        string="Qui alerter", domain=[("share", "=", False)])
    alerte_plafond_jour = fields.Integer(
        "Alertes par jour au plus", default=0,
        help="0 : sans plafond propre à la liste ; le plafond de sûreté du "
             "locataire (20 alertes par 24 h, paramètre "
             "bf_flux_ia.alertes_par_jour_max) vaut toujours. Le jour est celui "
             "du premier destinataire, dans son fuseau. Une alerte retenue par "
             "un plafond reste visible sur l'élément, avec sa raison.")
    alerte_fraicheur_heures = fields.Integer(
        "N'alerter que sur du neuf (h)", default=48,
        help="Une recherche ramène aussi des articles anciens : au-delà de ce "
             "délai depuis la publication, l'élément va au résumé, jamais en alerte.")

    _sql_constraints = [
        ("ia_seuil_borne", "CHECK(ia_seuil >= 0 AND ia_seuil <= 100)",
         "Le seuil va de 0 à 100."),
        ("alerte_plafond_positif", "CHECK(alerte_plafond_jour >= 0)",
         "Le plafond d'alertes ne peut pas être négatif."),
    ]
