{
    "name": "Immeubles : accès des résidents",
    "summary": "Qui a droit à quelle porte : les accès d'un résident s'ajoutent à son "
               "arrivée et se retirent à son départ confirmé, par ordres aux "
               "appareils ou au concierge",
    # 18.0.1.0.0 : premier jet (lot 1). Points d'accès, résidents,
    #   droits, file d'ordres, connecteur manuel (activité au responsable),
    #   départ jamais déduit d'une date : confirmé par une personne.
    # 18.0.1.0.1 : défauts vus à la pose sur la démo : pastille « À faire » (au
    #   lieu de « À faire à la main », tronquée) ; signaux de départ rédigés dans
    #   la langue de la société de l'immeuble (dates au format ISO depuis le cron).
    # 18.0.1.0.2 : migration 0.0.0 qui recharge les catalogues à chaque version (la
    #   traduction anglaise de la pastille restait « To do by hand » après la 18.0.1.0.1).
    # 18.0.1.0.3 : défauts d'écran relevés en écrivant le guide : nom d'un ordre
    #   (fil d'Ariane), « Synchroniser » sur une ligne, colonne « Départ », titres des
    #   vues alignés sur les menus, compte des droits en vigueur sur le point, compte
    #   des ordres sur le droit, « Dernier message » libellé, pas de « Nouveau » sur
    #   les départs à confirmer ; traductions anglaises corrigées et complétées.
    # 18.0.1.1.0 : accès temporaires (lot 4) : visiteur, entrepreneur,
    #   personnel, entrée au logement (art. 1930-1933 : préavis de 24 h, heures,
    #   urgence motivée) ; un droit porte un résident OU un accès temporaire ;
    #   retrait à la fin de la plage par un cron du quart d'heure.
    "version": "18.0.1.1.2",
    "category": "Services/Property",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    # ⚠️ BUSL-1.1 — « Other proprietary » est la seule valeur du vocabulaire
    # Odoo qui puisse la porter. Le fichier LICENSE fait foi. La Change Date
    # se retamponne à la publication (date de publication + 4 ans).
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "auto_install": False,
    "description": """
Immeubles : accès des résidents
===============================

Symbifox tient la liste de qui a droit à quelle porte, et la tient à jour à
mesure que les gens arrivent et partent. Il ne remplace pas le portier, la
boîte à colis ni la serrure : il leur dit qui ajouter et qui retirer.

**Points d'accès**
  Entrée commune, garage, boîte à colis, ascenseur, local commun, porte de
  logement. Chacun a un responsable et, plus tard, un connecteur vers
  l'appareil. Sans connecteur, chaque ordre devient une activité au
  responsable : « Ajouter Untel au portier de l'entrée nord ».

**Résidents**
  Tirés du dossier : copropriétaire qui occupe sa fraction, occupant d'une
  fraction louée et, avec le module locatif, signataires du bail. On peut
  déclarer à la main un occupant qui n'a rien signé (conjoint, enfant
  majeur) : il garde ses accès quand le signataire part (art. 1938 C.c.Q.).

**Le départ ne se devine pas**
  Le bail se reconduit de plein droit (art. 1941) et seul le tribunal
  expulse (art. 1889) : aucune date ne retire un accès. Les signaux du
  dossier (propriété cédée, loyer dû jusqu'au, résiliation, déguerpissement)
  mettent le résident « départ à confirmer », avec une activité. Les accès
  ne sont retirés qu'une fois la date des clés remises saisie.

**Porte du logement**
  Aucun mécanisme restreignant l'accès au logement ne se pose ni ne se
  change sans le consentement du locateur et du locataire (art. 1934) : un
  droit sur une porte de logement attend les deux consentements consignés
  avant tout ordre.

Aucun NIP ni secret n'est conservé : seule une référence (numéro de carte,
identifiant d'annuaire) l'est. Aucune biométrie.
""",
    "depends": ["bf_property_core", "mail"],
    "data": [
        "security/bf_property_access_security.xml",
        "security/ir.model.access.csv",
        "data/ir_cron_data.xml",
        "wizard/bf_property_access_departure_views.xml",
        "views/bf_property_access_point_views.xml",
        "views/bf_property_access_resident_views.xml",
        "views/bf_property_access_grant_views.xml",
        "views/bf_property_access_order_views.xml",
        "views/bf_property_access_pass_views.xml",
        "views/bf_property_access_connector_views.xml",
        "views/bf_property_building_views.xml",
        "views/bf_property_access_menus.xml",
    ],
}
