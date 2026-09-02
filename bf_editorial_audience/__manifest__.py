# -*- coding: utf-8 -*-
{
    "name": "Atelier éditorial — audience",
    "version": "18.0.1.2.0",
    "category": "Marketing",
    "summary": "L'audience d'un article une fois retirés les robots qu'Odoo"
               " ne reconnaît pas, relevée chaque jour avant que la purge des"
               " visiteurs n'emporte la trace",
    "description": """
Atelier éditorial — audience
============================

Odoo mesure la lecture d'un article de deux façons qui ne disent pas la même
chose, et il ne le dit nulle part.

* Le compteur natif du billet (``visits``) s'incrémente à chaque rendu de la
  page, sans le moindre contrôle d'agent. Un robot n'a pas de session, donc
  chaque passage de robot le fait monter. C'est le vrai brut.
* ``website.track`` est déjà filtré : ``_register_website_track`` refuse de
  tracer dès que l'agent contient une des treize sous-chaînes d'Odoo (bot,
  crawl, slurp, spider, curl, wget, facebookexternalhit, whatsapp,
  trendsmapresolver, pinterest, instagram, google-pagerenderer, preview).

Sur un blogue de taille moyenne, le compteur natif annonce couramment huit
fois le nombre de traces d'articles. L'écart, c'est ce qu'Odoo a écarté.

Le filtre d'Odoo est large mais grossier : il attrape tout ce qui se NOMME
robot, et laisse passer meta-externalagent, Barkrowler, DataForSeo,
python-requests, Go-http-client, okhttp, Scrapy, node-fetch et les navigateurs
sans tête. Ce module capture l'agent utilisateur, en tire un verdict et une
famille, et relève chaque jour, par article et par langue, quatre compteurs qui
s'additionnent : les vues retenues par Odoo, et leur partage en robots passés
au travers, lecteurs déclarés navigateurs, et agents non relevés.

Pourquoi un relevé quotidien
----------------------------
Odoo purge les visiteurs inactifs tous les jours, et leurs traces partent
avec eux. Une somme calculée à la demande sur `website.track` mesure donc ce
qui reste, pas ce qui s'est passé. Le relevé fige chaque journée pendant
qu'elle est encore là.

Ce que le module ne prétend pas
-------------------------------
* La série filtrée ne commence qu'au jour où la capture commence. Les
  visiteurs d'avant n'ont pas d'agent relevé : leurs vues comptent dans
  « agent inconnu », jamais dans « humains ». C'est visible, et c'est voulu.
* Un agent utilisateur se falsifie. Un robot qui se déclare navigateur passe
  pour un lecteur. La série filtrée retire les robots DÉCLARÉS, elle ne
  prétend pas compter des personnes.
* Le palmarès historique du compteur natif est gardé tel quel, annoté comme
  brut. Il n'est pas rétroactivement corrigé : on ne sait pas ce qu'il
  contenait.

⚠️ Vie privée
-------------
L'agent utilisateur est un identifiant d'appareil. Le module le conserve en
clair pour pouvoir reclasser l'historique quand un robot nouveau apparaît. Une
durée de conservation se règle par le paramètre
`bf_editorial_audience.ua_retention_days` : passé ce délai, un ménage
quotidien efface la chaîne et garde le verdict. À zéro, rien n'est effacé, et
c'est alors une décision à porter au registre des politiques.
    """,
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_editorial", "website"],
    "data": [
        "security/ir.model.access.csv",
        "data/bf_editorial_audience_data.xml",
        "views/bf_editorial_audience_views.xml",
        "views/bf_editorial_entry_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "auto_install": False,
    "application": False,
}
