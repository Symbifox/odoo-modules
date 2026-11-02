{
    "name": "Atelier éditorial — audience : pont vie privée (Loi 25)",
    "summary": "Déclarer au registre des traitements la chaîne d'agent "
               "utilisateur relevée sur les visiteurs du site, en régler la "
               "durée par une politique de conservation, et inscrire chaque "
               "effacement au registre de destruction",
    "version": "18.0.1.0.0",
    "category": "Services/Privacy",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    # ⚠️ PAS d'auto_install. Ce pont fixe la durée pendant laquelle un
    # identifiant d'appareil est conservé : c'est une décision de
    # l'entreprise qui exploite le site, inscrite à SON registre. Installation
    # à la main, jamais automatique.
    "auto_install": False,
    "depends": [
        "bf_editorial_audience",
        "privacy_consent",
    ],
    "description": """
Atelier éditorial — audience : pont vie privée (Loi 25)
=======================================================

`bf_editorial_audience` relève, sur chaque visiteur du site public, la chaîne
d'agent utilisateur déclarée par le navigateur ou le robot. Elle sert à une
seule chose : distinguer un lecteur d'un robot dans les mesures d'audience du
blogue, et pouvoir reclasser les visiteurs déjà captés quand une signature de
robot s'ajoute. Une chaîne d'agent est un identifiant d'appareil.

Sur un site qui reçoit du trafic : des dizaines de milliers de visiteurs, dont
la plupart portent une chaîne d'agent, la plus ancienne datant du jour où la
mesure a été branchée.

Ce que ce pont fait
-------------------

1. **Il déclare la finalité** au registre des traitements, avec sa politique
   de conservation (60 jours après la dernière visite).
2. **Il pose la règle AUD-001** au calendrier de conservation.
3. **Il devient la source de la durée.** Tant qu'il est installé et que la
   politique est active, le ménage quotidien du module lit la durée dans la
   politique, plus dans le paramètre `bf_editorial_audience.ua_retention_days`.
   La durée APPLIQUÉE ne vient que de la politique. Le résumé public de la
   finalité écrit deux nombres en clair (le public a droit à un chiffre),
   chacun à reprendre si sa source change, ce que rappellent les notes de la
   politique : l'effacement de la chaîne vient de la politique, la
   suppression de la trace de la visite vient de `website.visitor.live.days`
   (purge native d'Odoo). La règle AUD-001 renvoie à la politique sans recopier le nombre.
4. **Il inscrit chaque effacement au registre de destruction**, par lot et
   par société (celle du site du visiteur) : une entrée par passe du ménage
   (portée partielle : la chaîne part, le verdict, la famille et le visiteur
   restent), et une entrée par lot de la purge native d'Odoo quand elle
   emporte des visiteurs qui portaient encore leur chaîne.
5. **Une inscription en échec ne traite pas les deux chemins pareil.** Le
   ménage est bloquant : l'effacement et son inscription vont ensemble, la
   chaîne revient et rien n'est effacé ce jour-là. Si l'échec dure, le ménage
   s'arrête et Odoo finit par désactiver son cron (l'administrateur en est
   averti) : les anonymes partent quand même avec la purge native, mais les
   chaînes des visiteurs rattachés sont alors gardées au-delà de la durée.
   La purge native, elle, n'est pas bloquante : la
   suppression se fait, l'échec est journalisé. Une purge arrêtée chaque nuit
   finirait par faire désactiver son cron par Odoo, et les visiteurs
   s'accumuleraient sans borne.

Pourquoi 60 jours
-----------------

Odoo supprime déjà le visiteur anonyme 60 jours après sa dernière visite
(`website.visitor.live.days`, 60 par défaut), et la chaîne part avec lui ;
website_crm et website_event, quand ils sont installés, en retirent les
visiteurs liés à un prospect ou à une inscription à un événement. Le
reclassement d'une journée passée est donc déjà borné à 60 jours pour presque
tous. La durée retenue est fixée à cette borne, avec deux effets :

* la chaîne des visiteurs qu'Odoo ne purge jamais (rattachés à un contact,
  ou liés à un prospect ou à une inscription) est
  enfin bornée elle aussi ; le visiteur lui-même reste ;
* la durée déclarée ne dépend plus d'un réglage d'Odoo que quelqu'un pourrait
  allonger sans savoir qu'il allonge aussi une conservation déclarée.

Ce que l'effacement NE couvre PAS
---------------------------------

* **Les sauvegardes** de la base gardent la chaîne aussi longtemps que leur
  propre rotation. La méthode déclarée est donc `delete`, jamais
  `secure_wipe`.
* **Les journaux du serveur web** (mandataire inverse, Odoo) consignent eux
  aussi l'agent utilisateur de chaque requête, sous leur propre rotation,
  hors d'atteinte de ce module.
* **Le visiteur lui-même.** Le ménage n'efface que la chaîne : le visiteur
  garde son témoin de navigateur (`visitor_uuid`), son pays, sa langue, son
  fuseau horaire et ses pages vues jusqu'à la purge native d'Odoo, ou sans
  borne tant qu'il est rattaché à un contact, à un prospect ou à une
  inscription. Seuls le verdict et la famille, tirés de la chaîne, sont sans
  valeur d'identification.
""",
    "data": [
        "data/privacy_purpose_data.xml",
        "data/privacy_retention_calendar_data.xml",
    ],
}
