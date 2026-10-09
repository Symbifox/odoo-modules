{
    "name": "Impersonate a User",
    # 18.0.1.0.0 : première version. Lecture seule par
    #   défaut (écritures explicites refusées, le reste joué à blanc), motif obligatoire,
    #   durée bornée, avis à la personne réglable, journal que personne ne
    #   réécrit. L'OCA impersonate_login a été lu et écarté : voir README.
    # 18.0.1.0.1 : plus aucun _inherit de base ni de mail.thread. Ils font
    #   réinitialiser tous les modèles qui en héritent à chaque installation
    #   ou montée ; la pose sur un locataire est morte sur une clé étrangère de
    #   mail_tracking_email que des données orphelines empêchent de recréer.
    #   Les gardes d'envoi passent sur mail.message et mail.mail, le relevé
    #   des écritures sur un enrobage de BaseModel (orm_journal.py).
    # 18.0.1.0.2 : relecture adverse indépendante. En écriture : droits et
    #   configuration jamais modifiés (res.groups, ir.rule, ir.model.*,
    #   ir.config_parameter, ir.actions.*, ir.cron, res.company...) ; adresse et
    #   téléphones de la personne protégés ; auteur d'un message toujours
    #   l'incarnateur ; écritures sudo() et actions serveur consignées. En
    #   lecture seule : un commit explicite est refusé pendant le jeu à blanc.
    #   Le relevé d'écriture échoue ouvert.
    # 18.0.1.0.3 : données intimes cachées dans les deux modes, administrateurs
    #   compris (décision du 2026-10-08) : modèles
    #   à _gen_scope ou health., conversations Gen gen_private, et ce qui s'y
    #   rattache (messages, activités, pièces jointes, abonnés), coffre des
    #   identifiants ; l'erreur d'accès n'en nomme aucune fiche.
    #   Deuxième tour de la porte : « Envoyer plus tard », infolettres et
    #   téléphone refusés ; avis différés envoyés sur-le-champ, sous les gardes.
    #   Troisième tour : envoi programmé ou courriel en file non modifiables,
    #   SMS refusés (même à blanc), méthodes « send » refusées, un action_* reçu
    #   par call_kw compte comme un bouton, appels Discuss refusés.
    #   Quatrième tour : coordonnées de la personne protégées aussi par
    #   res.users (téléphones, champs liés de l'employé, contact lié) ; en
    #   lecture seule, deux appels automatiques nommés (marquer lu, marquer
    #   utilisé) répondent « fait » sans s'exécuter, action_download_* navigue.
    #   Sixième tour : la garde de l'adresse et des droits passe à l'ORM (tout
    #   chemin, sudo() compris, dans les deux modes) ; créer un usager est refusé.
    #   Septième tour : fusion de contacts refusée (elle relie par SQL),
    #   suppression d'un usager ou du contact de la personne refusée, les
    #   coordonnées de res.users jugées à la valeur.
    #   Porte adverse de publication. Refusés dans les deux modes :
    #   l'appairage d'un téléphone ou d'un compte (routes /auth/start,
    #   /auth/consent, /oauth/, modèles d'appareils), le formulaire Paramètres.
    #   Sur un modèle sensible, seules les lectures et les méthodes get_… passent,
    #   même en lecture seule. L'adresse IP du journal est réservée à
    #   l'administration. Libellé « Début » pour une installation neuve.
    "version": "18.0.1.0.3",
    "category": "Administration",
    "summary": "See Symbifox as one of your users, read-only by default, with a reason, "
               "a time limit, a notice to the person and a journal no access right can rewrite",
    "description": """
Impersonate a User
==================

Support staff open a time-boxed session as another internal user to see
exactly what that person sees: menus, access rights, preferences, data.

* Read-only by default: explicit writes (save, buttons, posting, uploads)
  are refused at the RPC entry point; every other call runs in a
  rolled-back savepoint.
* Write mode is a separate right; every request that writes, every button
  and every server action is journaled with what really changed.
* A reason is mandatory, the session ends by itself, and the person can be
  notified at the start, and again with a summary at the end (setting).
* The person's private data (health, mood journal, credentials vault, and
  private Gen conversations from bf_claude_chat 18.0.1.36.0 on) stays
  hidden in both modes, administrators included.
* Outgoing email (immediate, scheduled or deferred), text messages,
  newsletters, Discuss calls, the phone, password, API-key and two-factor changes, device and account
  pairing, rights and settings, and the Gen assistant are refused in both
  modes.
* No access right lets anyone write to the journal, administrators included.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "depends": ["base_setup", "mail", "bf_onboarding_base"],
    "data": [
        "security/bf_impersonate_security.xml",
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "views/bf_impersonate_session_views.xml",
        "wizard/bf_impersonate_wizard_views.xml",
        "views/res_users_views.xml",
        "views/res_config_settings_views.xml",
        "views/menu.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_impersonate/static/src/scss/impersonate_banner.scss",
            "bf_impersonate/static/src/xml/impersonate_banner.xml",
            "bf_impersonate/static/src/js/impersonate_banner.js",
            "bf_impersonate/static/src/js/user_menu.js",
        ],
    },
    "installable": True,
    "application": False,
}
