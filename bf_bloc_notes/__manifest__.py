{
    "name": "BF Bloc-notes",
    # 18.0.2.10.0: les libellés sont écrits en anglais dans la source, et
    #   fr_CA.po porte le français. Odoo ne traduit jamais vers en_US,
    #   la langue source : un usager réglé en anglais lisait le module
    #   en français.
    # 18.0.3.0.0: le bloc-notes au téléphone. Page installable
    #   /notes (saisie d'abord, file hors ligne, cible du menu Partager),
    #   API à jeton pour Symbifox Mobile, gestes rapides sur la carte kanban.
    # 18.0.3.0.1: le compteur « Notes » d'une fiche partagée se compte avec
    #   les droits de la personne ; le nom d'une fiche liée se calcule sous
    #   les droits de l'auteur de la note, et l'API mobile le résout sous
    #   ceux de l'appelant. Migration : les noms déjà stockés sont recalculés.
    # 18.0.3.0.2: l'auteur d'une note ne change plus hors superutilisateur ;
    #   la migration recalcule aussi le nom stocké sur la note.
    # 18.0.3.0.3: un lien ne change plus de note hors superutilisateur ; le
    #   compteur « Notes » est calculé par personne.
    # 18.0.3.1.0: les fichiers partagés vers une note depuis Symbifox Mobile
    #   (photo, PDF) s'y joignent, se lisent au téléphone et suivent la note
    #   quand elle devient une tâche.
    # 18.0.4.0.0: couleurs libres par bf_color. Les notes lisent leur index
    #   dans la palette pastel du mobile (aucune donnée réécrite) ; contrat
    #   mobile : `color_hex`, `text_color`, `tags`, route `/prefs` (mise en
    #   page, nuanciers) et `features` au ping ; numéro d'API inchangé.
    # 18.0.4.1.0: création rapide : la case « Lier à » remplace le bandeau,
    #   on peut refuser le lien (clic ou Alt+L). La fiche proposée est celle
    #   qui est affichée, et non plus celle de l'ouverture du formulaire ; le
    #   libellé nomme « Type · Nom ».
    # 18.0.4.2.0 : le nom d'une fiche liée
    #   (`bf.note.link.res_name`, `bf.note.res_name`) n'est plus stocké. Il se
    #   calcule sous les droits de qui lit, comme à l'API mobile, et la
    #   recherche par ce nom ne porte que sur les fiches que l'on lit. Une
    #   activité posée sur la fiche liée porte un renvoi à la note, plus son
    #   corps. Étiquettes : propriétaire (`user_id`) ; celles du module et
    #   toutes celles d'avant restent communes ; une personne ne crée, renomme
    #   ou supprime que les siennes, et n'en change pas le propriétaire.
    #   Les colonnes `res_name` restent en base,
    #   inutilisées, pour qu'un retour à la 4.1.0 sans -u ne casse rien.
    # 18.0.4.3.0 : « Hors Gen » (`gen_excluded`). Les passes de Gen sur les
    #   notes la laissent de côté (toute routine d'assistant qui lit les
    #   notes). Œil barré sur la carte, au kanban comme à /notes ; champ, gestes
    #   `gen_exclude`/`gen_include` et capacité `gen_excluded` au contrat mobile. Une
    #   règle des passes, pas un verrou.
    # 18.0.4.4.0 : le bloc-notes sur le bureau de Blue Fox OS. Les jetons
    #   d'appareil de `bf_desktop_pairing` sont reconnus (sans en dépendre) ;
    #   la liste accepte `shared` et ajoute alors les notes que des collègues
    #   ont partagées, en lecture seule ; deux routes neuves tiennent la
    #   synchronisation d'un widget : `notes/stamp` (empreinte légère, pour
    #   n'appeler le delta que s'il a bougé) et `notes/ids` (le ménage de ce
    #   qu'un delta ne peut pas annoncer : une note sortie de la portée).
    #   Contrat mobile inchangé, numéro d'API inchangé, trois capacités de plus.
    # 18.0.4.5.0 : une note accepte un sous-ensemble HTML — gras, italique,
    #   souligné, barré, puces — par deux clés à la charge mobile, servies SUR
    #   DEMANDE (en-tête `X-Symbifox-Rich`) : `html`, toujours assaini, et
    #   `rich_editable`, qui dit si le client peut le renvoyer sans rien
    #   perdre. `_mobile_update` et `_mobile_create` acceptent `html` à la
    #   place de `text` (jamais les deux) et annoncent `flattened` quand le
    #   HTML reçu portait plus que le sous-ensemble ; capacité `rich_text` au
    #   ping.
    #   L'assainisseur lit les effets dans les BALISES et dans le `style`,
    #   parce qu'un éditeur Qt n'écrit le gras qu'en `font-weight:700`, et sait
    #   qu'un style ANNULE celui du parent (`font-weight:normal`).
    #   `text` et `editable` restent le contrat de l'application posée : le
    #   téléphone n'écrit que du texte, et une note mise en forme y reste
    #   affichée. Les deux clés neuves ne sortent que si on les demande.
    #   ⚠️ Ce qui change pour TOUS les clients, demande ou pas : `text`. Une
    #   note du sous-ensemble ne passe plus par `html2plaintext` (qui écrivait
    #   `*gras*` et collait les puces) mais se rend en prose, puces comprises.
    #   Le titre en dérive, donc il change avec lui.
    #   La LECTURE d'un corps ne lève jamais (plafond séparé, `MAX_HTML_LECTURE`,
    #   au-delà duquel on sert un corps vide et « non modifiable »).
    #   🔴 `_mobile_browse` applique enfin `_mobile_scope` : `check_access`
    #   seul laissait un compte `base.group_system` lire ET écrire n'importe
    #   quelle note par son identifiant.
    #   🔴 Le juge « modifiable » croise QUATRE instruments, dont un témoin
    #   SANS analyseur, qui lit les noms de balise dans la chaîne brute :
    #   libxml2 TRONQUE l'imbrication, et un élément caché sous 400 `<div>`
    #   n'est alors dans AUCUN arbre. Ce nom se lit jusqu'au bord — un
    #   `<div-onclick>` n'est pas un `div`.
    "version": "18.0.4.5.0",
    "category": "Productivity",
    "summary": "Notes rapides riches, multi-liens, conversion en activité, raccourcis et systray",
    'author': 'Les services de consultation Blue Fox, Inc.',
    "website": "https://symbifox.com",
    'license': 'LGPL-3',
    "depends": ["web", "mail", "project", "crm", "contacts", "bf_onboarding_base", "bf_chatter_target", "bf_color"],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_note_security.xml",
        "data/bf_note_tag_data.xml",
        "data/bf_onboarding.xml",
        "views/bf_note_views.xml",
        "views/bf_note_tag_views.xml",
        "views/bf_note_activity_wizard_views.xml",
        "views/bf_note_task_wizard_views.xml",
        "views/bf_note_reroute_views.xml",
        "views/res_users_views.xml",
        "views/bf_note_menus.xml",
        "views/res_partner_views.xml",
        "views/project_task_views.xml",
        "views/crm_lead_views.xml",
        "views/project_project_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_bloc_notes/static/src/scss/bf_note.scss",
            "bf_bloc_notes/static/src/js/bf_note_service.js",
            "bf_bloc_notes/static/src/js/bf_note_systray.js",
            "bf_bloc_notes/static/src/js/bf_note_quick_create.js",
            "bf_bloc_notes/static/src/js/bf_note_hotkeys.js",
            "bf_bloc_notes/static/src/xml/bf_note_templates.xml",
            "bf_bloc_notes/static/src/js/bf_note_kanban.js",
            "bf_bloc_notes/static/src/xml/bf_note_kanban.xml",
        ],
    },
    "installable": True,
    "application": True,
}
