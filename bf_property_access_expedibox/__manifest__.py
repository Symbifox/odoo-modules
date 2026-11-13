{
    "name": "Immeubles : boîte à colis Expedibox",
    "summary": "Le résident est inscrit au casier Expedibox à son arrivée et en "
               "est retiré à son départ confirmé, sans ressaisie",
    # 18.0.1.0.0 : premier jet. Connecteur « Expedibox » :
    #   inscription et retrait d'un destinataire, rapprochement par
    #   custom_identifier, garde avant toute suppression, section d'EFVP.
    # 18.0.1.0.1 : numéros d'essai dans la plage réservée à la fiction
    #   (555-01xx), et la date du relevé retirée des commentaires.
    "version": "18.0.1.0.1",
    "category": "Services/Property",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    # ⚠️ BUSL-1.1 — le fichier LICENSE fait foi (voir bf_property_access).
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "auto_install": False,
    "description": """
Immeubles : boîte à colis Expedibox
===================================

Un point d'accès de type « Boîte à colis » relié à un connecteur Expedibox
n'envoie plus ses ordres au concierge : Symbifox inscrit lui-même le résident
au casier à son arrivée, et l'en retire à son départ confirmé.

**Ce qui part au casier**
  Prénom, nom, logement, un moyen de l'aviser d'un colis (son courriel, ou son
  numéro de mobile s'il n'a pas de courriel), et un identifiant de
  rapprochement propre à ce logiciel. Rien d'autre : aucun numéro fixe, aucune
  date de bail, aucun code.

**Le module ne touche que ses propres inscriptions**
  Chaque destinataire inscrit porte un identifiant qui nomme la base et le
  droit. Avant de retirer quelqu'un du casier, le module relit le destinataire
  et vérifie cet identifiant : une référence saisie à la main qui désignerait
  le destinataire d'un autre logiciel est refusée, et le retrait revient au
  responsable du point.

**Seuls les résidents**
  Un accès temporaire (visiteur, entrepreneur, personnel) n'est jamais
  transmis au casier : le casier n'a pas de plage de validité, et l'avis de
  vie privée ne couvre que les résidents. L'ordre revient au responsable, qui
  le fait à la main si l'organisation le veut.

**Le passage de nuit relit le casier**
  Un nom ou un logement corrigé au dossier est porté au casier (art. 28 : la
  correction suit le renseignement communiqué). Un destinataire inscrit par
  cette base qu'aucun droit ne réclame plus est signalé au responsable du
  point, jamais supprimé d'office.

**Rien ne se perd si Expedibox ne répond pas**
  Un refus d'Expedibox (jeton absent, résident sans courriel ni mobile,
  données refusées) revient tout de suite en activité au responsable du point,
  avec la raison. Une panne ou un délai laisse l'ordre à renvoyer : le passage
  de nuit le rejoue.

⚠️ L'avis de vie privée qui nomme ce que le casier reçoit vit dans
``bf_property_access_privacy`` 18.0.1.0.3 : sur une base qui porte une version
plus ancienne, le logiciel transmettrait un moyen de joindre le résident avec
un avis et un registre qui ne le nomment pas.

Le casier n'attend rien d'Odoo pour s'ouvrir : il valide seul ses
destinataires. Les avis de dépôt et de retrait d'un colis sont un autre
chantier (webhook ``reservation_event``).
""",
    # bf_property_core est déjà une dépendance de bf_property_access ; il est
    # nommé parce que ce module se sert de son groupe Gestionnaire, au code
    # comme dans les vues.
    "depends": ["bf_property_access", "bf_property_core"],
    "data": [
        "views/bf_property_access_connector_views.xml",
        "views/bf_property_access_point_views.xml",
    ],
    "external_dependencies": {"python": ["requests"]},
}
