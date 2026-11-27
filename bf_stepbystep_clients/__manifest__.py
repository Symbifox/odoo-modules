{
    "name": "Step-by-Step — Suivi d'accompagnement client",
    "summary": "Tableau de bord interne et portail libre-service de progression "
               "linéaire pour tout mandat d'accompagnement client.",
    # 18.0.2.3.0: les libellés sont écrits en anglais dans la source, et
    #   fr_CA.po porte le français. Odoo ne traduit jamais vers en_US,
    #   la langue source : un usager réglé en anglais lisait le module
    #   en français.
    # 18.0.2.4.0: le portail client que la fiche promettait. Une page
    #   publique à jeton propre au portail (pas l'access_token du projet, qui
    #   ouvre aussi les tâches par /my/projects), publiée depuis la fiche projet.
    #   Le client y dépose les pièces attendues à l'étape (bf.progression.document) ;
    #   chacune devient une pièce jointe de la tâche de l'étape (bf.progression.deposit).
    # 18.0.2.4.1: la page du portail déclarée website=True ; sur un locataire qui a
    #   le module website (la démo), elle rendait une 500 (KeyError 'website').
    # 18.0.2.4.2: « Étape 2 sur 4: » n'avait pas d'espace avant le deux-points en
    #   français ; séparateur neutre (point médian) entre le rang et le nom de l'étape.
    # 18.0.2.4.3: un mandat sans heures allouées n'a plus de budget inventé de 40 h
    #   (« Sans budget », hors des alertes) ; le tableau est réservé aux usagers des projets.
    # 18.0.2.4.5: l'activité récente du détail ne montre que les feuilles de temps que
    #   l'usager a le droit de lire (règles des feuilles de temps) ; tâches, étapes et
    #   chatter du détail, seulement les tâches qu'il peut lire (règles de société).
    # 18.0.2.5.0: la page du client ne porte plus le nom INTERNE du projet, ni en
    #   titre d'onglet ni en tête (il disait au client le raccourci de chantier
    #   interne, ou le palier de l'offre qu'on lui a vendu) : un titre neutre, et le mandat se reconnaît à son client et
    #   à ses étapes. Le client que la page nomme devient une valeur du rendu
    #   (`portal_client`), qu'un module pont remplace par le bénéficiaire du
    #   mandat quand le payeur et le lecteur sont deux entreprises.
    # 18.0.2.6.0: la page porte deux textes que le mandat lui donne. Le nom
    #   PUBLIC du programme (« Accélération Export ») identifie
    #   l'accompagnement au client qui en suivra plusieurs ; il se pose au-dessus
    #   du titre ou dedans, au choix, parce que le surtitre est discret et que le
    #   client peut ne pas le voir. Et une note de confidentialité propre au
    #   mandat, pour les dossiers où elle est contractuelle. Les deux sont du
    #   texte, rendus échappés : la page reste sans HTML du dossier.
    # 18.0.2.7.0: la double authentification du lien tokenisé. Une coche par
    #   mandat, un code à six chiffres par courriel au premier accès depuis un
    #   appareil, et l'appareil reconnu pour la durée du mandat (90 jours). Le
    #   code n'est gardé qu'en empreinte, le jeton de l'appareil aussi, et le
    #   courriel qui porte le code est `auto_delete` : une lecture de la base ne
    #   rend ni l'un ni l'autre utilisable. 🔴 La garde vit dans
    #   `_progression_projet`, donc TOUT chemin la subit — le dépôt de pièces
    #   comme les routes des modules ponts —, et seule la page la désactive pour
    #   pouvoir demander le code au lieu de refuser. 🔴 Le code part à la partie
    #   que la page NOMME, jamais au payeur à sa place. 🔴 Par la file déclenchée
    #   tout de suite, jamais en synchrone : deux fils HTTP suffisent à coucher
    #   un locataire pendant une lenteur SMTP.
    # 18.0.2.7.1: le courriel du code passe par un `mail.template` qui pointe
    #   `bf_onboarding_base.bf_mail_layout` — la mise en page des autres
    #   courriels de l'instance. Un corps nu arrivait sans logo ni pied. Le
    #   message est détaché du chatter après sa mise en file : sans ça, le code
    #   en clair aurait figuré au dossier du projet jusqu'à l'envoi.
    # 18.0.2.8.0: le responsable du mandat est avisé quand le client agit sur sa
    #   page : une pièce déposée, le questionnaire rempli (pont), une suite
    #   d'erreurs de code d'accès (3 par défaut). Activité, notification Odoo,
    #   courriel : trois canaux cumulables, réglés par société dans un menu
    #   Configuration > Paramètres du module (activité et courriel par défaut).
    #   🔴 Le geste du client ne fait que METTRE EN FILE (bf.progression.notice) ;
    #   une tâche planifiée réveillée sur-le-champ avise dans sa propre
    #   transaction : rien de l'avis ne peut faire échouer ni rejouer le dépôt.
    #   🔴 Courriel et notification ne joignent jamais la pièce et ne citent
    #   rien de ce que le client a écrit ; le client n'est ni abonné ni
    #   destinataire ; l'IP du visiteur n'est recopiée dans aucun avis.
    # 18.0.2.8.1: le tableau ne montre que les mandats clients, les projets dont
    #   une étape porte un numéro de progression et paraît au portail (repris de
    #   la 18.0.2.4.6, posée sur une autre branche).
    # 18.0.2.8.2: relancer un avis en échec exige le droit d'écrire les avis
    #   (administrateurs) ; la méthode écrivait en sudo pour qui pouvait la lire.
    #   Les actions du portail (copier l'adresse, régénérer le jeton, oublier les
    #   appareils) exigent en plus le droit d'écrire CE mandat : sur une base
    #   multi-société, le groupe seul laissait agir sur ceux d'une autre société.
    # 18.0.2.9.0: un élément de la page peut être un LIEN : libellé,
    #   adresse https, consigne ; rendu en bouton à une étape faite ou en cours,
    #   à part des pièces. Rien ne s'y dépose. Les pièces existantes restent
    #   des pièces.
    "version": "18.0.2.9.0",
    "category": "Project",
    "website": "https://symbifox.com",
    "author": "Les services de consultation Blue Fox, Inc.",
    "license": "Other proprietary",
    "application": True,
    "installable": True,
    "post_init_hook": "post_init_hook",
    "depends": [
        "base",
        "project",
        "hr_timesheet",
        "portal",
        # Le code d'accès passe par la file de courriel et par l'xmlid de son
        # cron : `mail` arrive par transitivité, mais le lot s'en sert nommément.
        "mail",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/mail_template_otp.xml",
        "data/progression_avis_data.xml",
        "security/progression_security.xml",
        "views/project_task_type_views.xml",
        "views/dashboard_views.xml",
        "views/res_config_settings_views.xml",
        "views/progression_notice_views.xml",
        "views/dashboard_menu.xml",
        "views/project_project_views.xml",
        "views/progression_portal_templates.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_stepbystep_clients/static/src/js/bf_stepbystep_dashboard.js",
            "bf_stepbystep_clients/static/src/xml/bf_stepbystep_dashboard.xml",
            "bf_stepbystep_clients/static/src/scss/dashboard.scss",
        ],
        "web.assets_frontend": [
            "bf_stepbystep_clients/static/src/scss/progression_portal.scss",
        ],
    },
}
