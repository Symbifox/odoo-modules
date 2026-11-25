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
    # 18.0.4.6.0 : le partage NOMME ses destinataires. `bf.note.share`,
    #   une ligne par destinataire (personne, groupe de securite, canal Discuss),
    #   et le droit d'ecrire par ligne. `is_shared` reste et garde son sens.
    #   🔴 Les regles d'origine ne sont PAS touchees : leur `ir_model_data`
    #   porte `noupdate = true`, donc les redefinir sous le meme identifiant ne
    #   change RIEN, meme depuis un fichier sans `noupdate` (mesuré à l'essai :
    #   l'ancien domaine restait en place et dix essais de portee rougissaient).
    #   Le partage ajoute donc DEUX regles de plus, qui se combinent en OU avec
    #   les anciennes, et qui ne portent que `perm_read` ou que `perm_write` :
    #   creer et supprimer restent chez l'auteur, sans rien scinder.
    #   Un destinataire en ecriture change le titre et le contenu, rien d'autre
    #   (les etiquettes ont un proprietaire ; la couleur et l'epingle rangent le
    #   tableau de l'auteur).
    #   Contrat mobile : deux cles de plus (`can_write`, `shared_with`), numero
    #   d'API inchange, et `shared=False` rend toujours les seules notes de
    #   l'appelant.
    #   Les departements RH demanderaient `hr` : ils partent dans un pont.
    #   Premier tour de relecture, quatre bloquants, quatre decisions
    #   tranchees :
    #   🔴 un destinataire en ECRITURE joint un fichier et ne retire que les
    #   SIENS (`create_uid`). Les pieces de l'auteur restent a l'auteur, et
    #   celles des MESSAGES du fil ne se retirent par personne d'autre que lui,
    #   meme par qui a poste le message : elles vivent avec le message, et rien
    #   dans `ir.attachment` ne les separe de celles de la note.
    #   🔴 `base.group_system` est exempte de la garde des champs, sur le patron
    #   de `bf.note.tag.write` : un administrateur archive, epingle, colore et
    #   date la note d'un collegue comme avant le lot (il pouvait deja la
    #   supprimer et la renommer). L'auteur, lui, ne change pour personne hors
    #   superutilisateur.
    #   Le filtre « Partagees avec moi » et l'action « Epinglees » cherchent le
    #   partage NOMME autant que l'ancien : ils rendaient zero sur un partage
    #   nomme.
    #   L'ecriture de `bf.note.link` s'ouvre au partage en ecriture (regle de
    #   plus, `perm_create` + `perm_write`), sinon « En faire une tache » rendait
    #   un 403 : l'assistant pose un lien retour. ⚠️ `perm_unlink` reste a
    #   l'auteur, donc un destinataire ne re-route une note partagee qu'en mode
    #   « ajouter » ; « remplacer » retire des liens et refuse.
    #   Le refus d'un destinataire en lecture seule porte une phrase lisible sur
    #   le chemin de l'API, au lieu du mur generique d'Odoo.
    #   Le fil n'est pas arbitre, et c'est une decision : un destinataire lit
    #   TOUT le fil, notes internes et pieces de ces messages comprises ;
    #   personne n'est abonne d'office ; aucune notification ne part. Dit a
    #   l'onglet Partage, pour qu'on le sache avant de cocher.
    #   Un destinataire ne retire pas son propre acces : supprimer une ligne de
    #   partage reste a l'auteur de la note, comme la creer.
    #   🔴 « En faire une tache » depuis la note d'un AUTRE n'emporte plus ses
    #   fichiers vers la tache (la conversion les copiait sans regarder qui joue le
    #   geste) : la tache garde le lien retour, le titre et le contenu, les
    #   fichiers restent sur la note. Le droit de lire ne change pas, c'est le
    #   deplacement qui se ferme. ⚠️ La lecture est « l'auteur, ou `env.su` », et
    #   non `peut_gerer` : un compte `base.group_system` qui fait une tache
    #   depuis la note d'un collegue n'emporte plus les fichiers non plus.
    #   La liste et le formulaire passent en lecture seule, champ par champ
    #   (etiquettes, couleur, echeance, epingle, « Hors Gen », « Partagee »), sur
    #   la note d'autrui, comme la carte kanban. ⚠️ La liste porte `multi_edit` :
    #   le `readonly` est evalue par l'ecran, et c'est le serveur qui tient le
    #   lot (une ecriture sur un lot MIXTE leve et n'ecrit rien, pas meme la note
    #   de l'usager ; mesure).
    #   La carte kanban ne propose plus les quatre gestes qui ne pouvaient QUE
    #   lever sur la note d'autrui (archiver, nuancier, « Hors Gen », epingle) :
    #   ils suivent `peut_gerer`, comme Edit et Delete suivent deja
    #   `widget.editable` et `widget.deletable`.
    #   Second tour de relecture, quatre bloquants de plus, quatre
    #   decisions tranchees :
    #   🔴 la garde des pieces se pose a `ir.attachment` (suppression, et
    #   ecriture qui deplace ou renomme), et non au seul chemin du telephone :
    #   le bouton X du chatter, `unlink()` et `write()` passaient tous les
    #   trois, `ir.attachment.check()` ne jugeant que la fiche PARENTE. Elargir
    #   l'ecriture d'un modele ouvre ses pieces jointes.
    #   🔴 un compte non humain ne recoit pas de note partagee : une case sur sa
    #   fiche (`res.users.bf_note_no_shared_notes`), une CONTRAINTE qui refuse
    #   la ligne nommee (un `domain=` de champ n'est jamais applique par l'ORM
    #   au `create`) et l'exclusion de la PORTEE, qui ferme aussi les sortes
    #   « groupe » et « canal » — un partage a `base.group_user` donnait la note
    #   aux comptes de service, en ecriture. Le parametre
    #   `bf_bloc_notes.comptes_exclus` reste honore, et la migration 4.6.0 en
    #   fait des cases (elle ne devine RIEN d'elle-meme : aucun discriminant de
    #   la base ne separe les comptes de service des humains sans faux positif).
    #   🔴 un destinataire n'abonne personne au fil et n'y pose aucune activite,
    #   lui-meme compris, et il peut toujours POSTER (`mail.activity` juge aussi
    #   la fiche parente, et `message_subscribe` n'exige que la lecture pour
    #   s'abonner soi-meme). La phrase de l'onglet Partage dit maintenant ce que
    #   le code tient, mot par mot, et un essai mesure chaque mot.
    #   🔴 le nombre de destinataires ne franchit plus la regle de lecture : les
    #   calculs de portee lisaient `note.sudo().share_ids`, un One2many nu dont
    #   le creneau de cache n'est pas cle par l'usager, et `share_count` rendait
    #   4 la ou la regle n'en laissait voir que 2. Un non-auteur compte ZERO
    #   destinataire, et la regle vit a un seul endroit (le calcul du champ, plus
    #   la charge mobile).
    # 18.0.4.6.1 : le partage nomme se TROUVE. Premier usage reel :
    #   le geste de partager une note avec une seule personne ne se trouvait pas,
    #   que la seule chose marquee « Partagee » a l'ecran etait l'ancien
    #   interrupteur (`is_shared`, TOUS les internes), et que le vrai geste
    #   vivait dans un onglet sous les champs. Un bouton « Partager… » dans la
    #   barre ouvre une boite qui cree la ligne de partage, l'interrupteur
    #   s'appelle « Partagee avec tous les internes ». Aucune regle, aucun
    #   champ stocke, aucune migration.
    # 18.0.4.6.2 : l'ecran du bureau. Une note recue se
    #   reconnait (bandeau au formulaire, « Lecture seule » ou « Peut modifier »
    #   sur la carte), l'onglet Partage montre une colonne par destinataire et
    #   s'edite par la boite, « Partager… » est aussi au menu de la carte, le
    #   re-routage « remplacer » refuse en clair sur la note d'autrui, le fil dit
    #   « Note creee », et trois aides francaises vouvoient. Migration `end` : le
    #   francais de l'aide de `is_shared`, s'il porte encore le texte de 4.6.1.
    # 18.0.4.6.3 : la page Notes voit les notes recues. Elles
    #   vivent dans leur section « Partagees avec moi », disent qui les a
    #   partagees et si on peut les modifier, et n'offrent que les gestes que le
    #   serveur accepte. La page purge son cache par `/notes/api/ids` (une note
    #   departagee ou supprimee y restait pour toujours). Chaque note porte
    #   `can_manage` et `author_name` ; la preference `hide_shared`
    #   (`bf_note_hide_shared`) est commune a la page et au telephone ; la sonde
    #   annonce `named_shares`. Colonne neuve sur `res_users`, aucune migration.
    # 18.0.4.6.4 : au bureau, les rappels rapides (formulaire et
    #   carte) ne s'offrent au destinataire que sur une note liee qu'il peut
    #   ecrire, et « Personnaliser… » qu'a qui peut ecrire la note : ailleurs le
    #   serveur les refusait (`rappel_possible`, calcule, sans colonne).
    #   Migration `end` : quatre termes francais (trois de vues, une aide
    #   d'action) restes au tutoiement en base malgre les catalogues 4.6.2 et
    #   4.6.3, remplaces s'ils portent encore l'ancien texte. Deux traductions
    #   de code tutoyaient aussi (« Choisis… », « Saisis… »).
    "version": "18.0.4.6.4",
    "category": "Productivity",
    "summary": "Notes rapides riches, multi-liens, conversion en activité, raccourcis et systray",
    'author': 'Les services de consultation Blue Fox, Inc.',
    "website": "https://symbifox.com",
    'license': 'LGPL-3',
    "depends": ["web", "mail", "project", "crm", "contacts", "bf_onboarding_base", "bf_chatter_target", "bf_color"],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_note_security.xml",
        "security/bf_note_share_security.xml",
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
