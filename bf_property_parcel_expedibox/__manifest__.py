{
    "name": "Immeubles : avis de colis du casier Expedibox",
    "summary": "Un colis déposé dans le casier se consigne et avise son "
               "destinataire tout seul ; son retrait se constate de même",
    # 18.0.1.0.0 : premier jet. Route d'écoute par connecteur, journal
    #   des avis, relecture de la réservation chez Expedibox avant tout effet,
    #   dépôt consigné, retrait constaté, dépôt échu remis au responsable.
    "version": "18.0.1.0.0",
    "category": "Services/Property",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    # ⚠️ BUSL-1.1 — le fichier LICENSE fait foi (voir bf_property_access).
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "auto_install": False,
    "description": """
Immeubles : avis de colis du casier Expedibox
=============================================

``bf_property_access_expedibox`` inscrit le résident au casier et l'en retire.
Ce module-ci écoute ce que le casier a à dire : un colis vient d'être déposé,
un colis vient d'être retiré.

**Ce qui se passe quand un livreur dépose un colis**
  Expedibox envoie un avis à l'adresse d'écoute du connecteur. Symbifox relit
  la réservation chez Expedibox, reconnaît le résident à l'identifiant que ce
  logiciel lui a donné, et consigne le colis au journal, à l'heure du dépôt,
  avec le casier et la porte où il est rangé. Le concierge n'a rien saisi.

**Un seul avis au résident**
  Le casier écrit lui-même au destinataire (son réglage par défaut). Symbifox
  ne double donc pas l'avis : il consigne, et le fil dit d'où l'avis est parti.
  Une organisation dont le casier est muet décoche le réglage, et le texto de
  ``bf_property_sms`` repart comme pour un colis remis au comptoir.

**Un avis d'Expedibox n'est pas signé, et il est traité comme tel**
  Leur spécification ne prévoit ni signature ni secret partagé. L'adresse
  d'écoute porte donc un secret, l'en-tête d'organisation est vérifié, et
  surtout : le corps reçu ne décide de rien. Ce qui décide, c'est la relecture
  de la réservation avec le jeton d'application de l'organisation. Un avis que
  la relecture ne confirme pas ne consigne aucun colis.

**Le module ne conclut jamais à la place de quelqu'un**
  Un dépôt qui ne correspond à aucun résident inscrit par ce logiciel devient
  une activité pour le responsable du point — et cette activité ne nomme pas
  la personne du casier : ce logiciel n'a pas de raison de garder le nom de
  quelqu'un qu'il ne gère pas. Un dépôt qu'Expedibox déclare échu ne devient
  pas « retourné à l'expéditeur » : le concierge constate, le logiciel
  demande.

**Ce qui n'entre jamais dans la base**
  Les codes de dépôt, de retrait et d'ouverture de porte, les trois images QR
  de la réservation, les photos de dépôt et de retrait, le courriel et le
  mobile que le casier détient. Le journal des avis garde une référence de
  réservation, un type d'événement, deux heures et un identifiant de casier ;
  il se purge après 90 jours, ou plus tôt si l'organisation a choisi une
  conservation plus courte pour ses journaux.

**Rien ne se perd si le traitement échoue**
  L'avis est écrit avant d'être compris : la route répond à Expedibox dès que
  la ligne est en base, et un passage planifié (cinq minutes, et un passage
  immédiat demandé par la route) relit, rapproche et agit. Une panne
  d'Expedibox se rejoue ; après cinq essais, l'avis revient à une personne avec
  la raison.
""",
    "depends": [
        "bf_property_portal",
        "bf_property_access_expedibox",
    ],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_property_parcel_expedibox_security.xml",
        "data/ir_cron_data.xml",
        "views/bf_property_access_connector_views.xml",
        "views/bf_property_parcel_views.xml",
        "views/bf_property_parcel_expedibox_event_views.xml",
    ],
    "external_dependencies": {"python": ["requests"]},
    # ⚠️ `bf_property_sms` n'est pas une dépendance, mais s'il est là, il doit
    # porter la garde qui honore `bf_parcel_notice_elsewhere` : sinon deux avis
    # partent pour le même colis. Le crochet refuse plutôt que de le laisser
    # arriver en silence.
    "post_init_hook": "post_init_hook",
}
