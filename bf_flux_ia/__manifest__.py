# -*- coding: utf-8 -*-
{
    "name": "Flux RSS : tri par IA",
    "summary": "Un modèle de langage note la pertinence de ce que les règles "
               "d'une liste ont retenu, et écarte ce qui ne sert pas, en disant pourquoi",
    "version": "18.0.1.1.2",
    "category": "Productivity",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    # ⚠️ « Other proprietary » est la valeur que le schéma de manifeste d'Odoo
    # offre pour dire BUSL-1.1 : il n'a pas de valeur BUSL. C'est le fichier
    # LICENSE qui gouverne, pas cette ligne.
    "license": "Other proprietary",
    "installable": True,
    # Le modèle se joint par bf_llm (clé d'API) ou par bf_ai_bridge (le pont) :
    # l'un ou l'autre, jamais exigé, pour s'installer chez l'un comme chez l'autre.
    "depends": ["bf_flux"],
    "data": [
        "data/ir_cron.xml",
        "views/flux_liste_views.xml",
    ],
    "description": """
Flux RSS : tri par IA
=====================

Les règles d'une liste sont gratuites, déterministes et disent pourquoi elles
retiennent. Elles ne savent pas qu'une convocation d'appel de résultats
n'apprend rien, même quand elle vient d'un émetteur du bon secteur. Ce module
ajoute un second tri, après les règles et seulement sur ce qu'elles ont
retenu : le coût suit le volume retenu, pas celui du flux.

* **Une consigne par liste**, en mots : ce qui compte pour ses lecteurs.
* **Une note de 0 à 100 et une raison d'une ligne** pour chaque élément.
  Sous le seuil de la liste, l'élément est écarté : il reste visible au
  filtre « Écartés au jugement », avec sa raison, pour régler la consigne.
* **Le contenu d'un flux est une donnée, jamais une consigne.** Le modèle ne
  rend que des notes ; seuls les identifiants du lot soumis sont acceptés.
* **Une panne ne bloque rien.** Un élément que le modèle n'a pas pu juger est
  repris au passage suivant. Passé le délai de la liste, il est diffusé sur la
  foi des règles, et sa raison le dit.
* **L'alerte quand ça presse.** Le modèle dit aussi si un élément ne peut pas
  attendre le résumé. Un courriel part alors tout de suite, derrière quatre
  gardes tenues en code : publication récente, un seul courriel par événement
  (même raconté par cinq sources), plafond par jour, destinataires nommés.
* **Les homonymes écartés.** Pour une liste de sujets surveillés, le modèle lit
  la description de chaque sujet et note 0 ce qui porte le nom sans en parler.
""",
}
