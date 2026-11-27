{
    "name": "Step-by-Step — Questionnaire on the client page",
    "summary": "A questionnaire button on the client's progression page, and "
               "the answer tied to the mandate itself.",
    # 18.0.1.0.0: le questionnaire s'ouvre depuis la page de progression. Un
    #   module PONT, et non le cœur : `survey` n'est pas posé chez tous les
    #   locataires qui servent la page, et le cœur n'a pas à l'exiger.
    #   Le bouton passe par une route du module, sous la MÊME garde que la page
    #   (projet publié, jeton juste), et mène à la réponse du client du mandat :
    #   le questionnaire rempli se retrouve dans son dossier, pas dans un tas
    #   anonyme. Une réponse FINIE ne se rouvre pas ; la page dit « reçu ».
    # 18.0.1.0.1: un mandat sans questionnaire ne pose plus de bloc VIDE sur la
    #   page du client. Le gabarit testait l'étape, pas s'il y avait quelque
    #   chose à y mettre ; les pages en service portaient un `<div>` mort.
    # 18.0.1.0.2: un essai de plus, sans changement de comportement — la route
    #   se ferme quand le mandat demande un code d'accès (cœur 18.0.2.7.0). La
    #   promesse « tout chemin subit la garde » se mesure sur un pont, et non
    #   seulement sur le dépôt du cœur.
    # 18.0.1.1.0: le questionnaire rempli depuis la page avise le responsable
    #   du mandat, par les canaux réglés au cœur (bf_stepbystep_clients 2.8.0).
    # 18.0.1.1.1: l'étape et la réponse du questionnaire sont réservées au
    #   groupe qui publie le portail (`readonly` ne protège pas d'une écriture
    #   directe, là où une base élargit le droit d'écrire les projets).
    # 18.0.1.2.0: le bouton reprend la réponse que l'entreprise a DÉJÀ reçue par
    #   un lien de courriel au lieu d'en créer une seconde. Rien n'est repris
    #   quand la page nomme un payeur qui a l'air d'un donneur d'ordre. Une
    #   réponse déjà finie ramène le client à sa page, qui dit « reçu ».
    "version": "18.0.1.2.0",
    "category": "Project",
    "website": "https://symbifox.com",
    "author": "Les services de consultation Blue Fox, Inc.",
    "license": "Other proprietary",
    "installable": True,
    # Un pont, pas une application : rien à ouvrir dans le menu principal.
    "application": False,
    # ⚠️ Exige bf_stepbystep_clients >= 18.0.2.8.0 (ses deux ancres xpath, et
    # `_progression_aviser` depuis 1.1.0) ;
    # `depends` ne sait pas porter de plancher de version, le README le dit.
    "depends": [
        "bf_stepbystep_clients",
        "survey",
    ],
    "data": [
        "views/project_project_views.xml",
        "views/progression_portal_templates.xml",
    ],
}
