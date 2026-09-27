# -*- coding: utf-8 -*-
{
    "name": "Flux RSS",
    "summary": "Des flux RSS relevés, dédoublonnés et triés par règles, "
               "diffusés par service ou par projet dans Discuss et par courriel",
    "version": "18.0.1.1.2",
    "category": "Productivity",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    # ⚠️ « Other proprietary » est la valeur que le schéma de manifeste d'Odoo
    # offre pour dire BUSL-1.1 : il n'a pas de valeur BUSL. C'est le fichier
    # LICENSE qui gouverne, pas cette ligne.
    "license": "Other proprietary",
    "application": True,
    "installable": True,
    "depends": ["mail", "hr", "project"],
    "external_dependencies": {"python": ["lxml", "requests"]},
    "data": [
        "security/bf_flux_groups.xml",
        "security/ir.model.access.csv",
        "security/bf_flux_rules.xml",
        "data/ir_cron.xml",
        "data/mail_template.xml",
        "wizard/poser_projet_views.xml",
        "views/flux_source_views.xml",
        "views/flux_element_views.xml",
        "views/flux_lecteur_views.xml",
        "views/flux_liste_views.xml",
        "views/flux_preference_views.xml",
        "views/flux_menus.xml",
    ],
    "description": """
Flux RSS
========

Un flux RSS ne garde que ses derniers éléments, et il n'existe pas d'archive
à rattraper : ce qui sort de la fenêtre entre deux passages est perdu. Le
module relève donc chaque source à sa propre cadence, et ce qu'il a vu reste
en base.

Ce qu'il fait
-------------

* **Une source se relève à sa cadence.** Une limitation de débit, un refus
  temporaire ou un délai d'attente sont des échecs passagers : on retente au
  passage suivant. Seuls un 404 et un contenu qui n'est pas un flux sont
  définitifs.
* **Un élément n'entre qu'une fois**, par son identifiant stable, pas par son
  titre ni son lien. Un même communiqué reçu par deux flux est un seul
  élément, rattaché aux deux sources.
* **La langue de travail prime.** Quand un élément paraît en deux langues sous
  le même identifiant, la version dans la langue préférée de la source
  remplace l'autre, même si elle arrive plus tard.
* **Le texte complet, si on le demande.** Le chapeau du flux ne suffit pas
  toujours : le module va chercher la page. Le champ « Langue du texte » dit
  la langue de ce qui a réellement été récupéré, pas de ce qui a été demandé.
* **Une liste trie par règles**, dans cet ordre : les exclusions écartent
  (un appel de résultats trimestriels, un avis aux actionnaires) ; un terme du
  secteur suffit à retenir ; un émetteur spécialisé retient seul ; un émetteur
  à deux lignes d'affaires n'est noté que si un terme du secteur est déjà là.
  Le motif est écrit sur chaque élément retenu.
* **On s'abonne par service, par projet ou nommément.** Les membres d'une
  liste sont calculés : les employés des départements visés, les abonnés
  internes des projets visés, et les personnes nommées. Qui entre reçoit, qui sort cesse de recevoir. Chacun peut se
  désabonner d'une liste sans quitter son service.
* **La diffusion se fait dans Discuss et par courriel.** Chaque liste a son
  canal, tenu au fil de ses membres. Le résumé courriel est quotidien,
  hebdomadaire ou coupé, au choix de chacun ; il porte la mise en page de la
  maison et s'arrête à 25 éléments par liste, avec un lien vers le reste.
* **Un élément se pose au fil d'un projet** d'un geste.

Ce qu'il ne fait pas
--------------------

* Il ne juge pas la pertinence au-delà des règles : le tri par IA vit dans un
  module de liaison, pour que celui-ci s'installe sans modèle de langage.
* Il ne publie pas de flux : le blogue d'Odoo le fait déjà.
""",
}
