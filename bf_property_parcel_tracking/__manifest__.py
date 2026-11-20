{
    "name": "Immeubles : suivi des colis chez le transporteur",
    "summary": "Le résident annonce le colis qu'il attend ; Symbifox suit le "
               "numéro chez UPS ou DHL et l'avise à la livraison",
    # 18.0.1.0.0 : premier jet. État « Annoncé », compte de
    #   transporteur par société, suivi UPS (jeton client_credentials, Track
    #   v1) et DHL (Shipment Tracking), Purolator déclaré et non branché,
    #   annonce au portail, plafond horaire, arrêt après trente jours.
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
Immeubles : suivi des colis chez le transporteur
================================================

Le résident annonce au portail le colis qu'il attend — son transporteur et son
numéro de suivi. Symbifox demande au transporteur où en est ce colis, et le
jour où le transporteur répond « livré », le colis passe en dépôt au journal et
l'avis part au résident.

**Ce que ces API permettent, et ce qu'elles ne permettent pas**
  UPS, DHL et Purolator répondent **sur un numéro de suivi**. Aucun ne dit
  « voici ce qui a été livré à cette adresse aujourd'hui ». Un colis ne peut
  donc pas se créer tout seul à partir de rien : il faut qu'une personne — le
  résident, qui a le numéro dans son courriel de confirmation — ou un casier
  relié (voir ``bf_property_parcel_expedibox``, qui donne le transporteur et
  le numéro lus par le livreur) ait fourni ce numéro.

**UPS**
  Branché, et lu à la source : UPS publie ses spécifications OpenAPI
  (``Tracking.yaml``, ``OAuthClientCredentials.yaml``). Jeton
  ``client_credentials``, suivi ``GET /track/v1/details/{numéro}``, deux
  environnements (essai et production). **« Livré » ne se conclut que sur une
  date de livraison de type ``DEL``**, le seul signal que leur spécification
  documente ; le vocabulaire de leurs codes d'état vit dans une annexe de
  portail qui refuse les clients non-navigateur, donc il est affiché et gardé,
  jamais interprété.

**DHL**
  Branché, et mesuré sur l'API elle-même : hôte, chemin, nom de l'en-tête de
  clé (``DHL-API-Key``) et forme des erreurs (RFC 7807) viennent de leur
  serveur, pas d'un billet de blogue. Leur vocabulaire d'état n'a pas pu être
  lu à la source : seul ``delivered``, exactement, conclut à une livraison.

**Purolator : déclaré, pas branché, et il le dit**
  Leur WSDL rend 401 et leur documentation vit derrière un compte. Aucune
  enveloppe SOAP n'a donc été écrite d'après des exemples tiers : le compte
  existe, ses champs existent, et l'interrogation refuse en expliquant ce qui
  manque (clé de développement au portail, puis certification, puis clé de
  production avec numéro de compte d'affaires et trois contacts). Un numéro
  Purolator se suit à la main, et la fiche du colis le dit.

**Ce qui protège le quota**
  Chaque compte porte un plafond d'interrogations par heure (soixante par
  défaut) ; au-delà, les interrogations sont **repoussées**, jamais
  abandonnées. Et chaque colis porte sa propre échéance : une demi-heure quand
  il est en livraison, quatre heures quand une étiquette vient d'être créée.
  Un colis annoncé il y a plus de trente jours, ou un numéro que le
  transporteur ne connaît pas après six essais, cesse d'être interrogé et le
  dit.

**Vie privée (Loi 25)**
  Un numéro de suivi et les étapes d'une livraison disent ce que quelqu'un
  reçoit. Le module garde l'état courant, son libellé et sa date — pas
  l'itinéraire, pas la signature, pas la photo de livraison : il ne les
  demande jamais (``returnSignature`` et ``returnPOD`` restent à « false »).
  Le numéro disparaît avec la fiche du colis, à la purge du journal.
""",
    "depends": [
        "bf_property_portal",
    ],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_property_parcel_tracking_security.xml",
        "data/ir_cron_data.xml",
        "views/bf_property_carrier_account_views.xml",
        "views/bf_property_parcel_views.xml",
        "views/portal_templates.xml",
    ],
    "external_dependencies": {"python": ["requests"]},
    # ⚠️ `bf_property_sms` n'est pas une dépendance (un immeuble sans texto n'a
    # pas à l'installer), mais s'il est là, il doit porter la garde qui attend
    # la livraison : le crochet refuse l'installation devant une version
    # antérieure plutôt que de laisser partir un avis faux.
    "post_init_hook": "post_init_hook",
    # ⚠️ L'état « Annoncé » disparaît avec ce module : les fiches qui le
    # portent s'effacent au retrait, sinon elles restent hors sélection sur un
    # champ requis, invisibles à la purge et illisibles à l'écran.
    "uninstall_hook": "uninstall_hook",
}
