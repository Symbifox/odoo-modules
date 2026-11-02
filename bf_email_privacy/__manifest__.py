{
    "name": "Gestion des courriels — pont Vie privée (Loi 25)",
    "summary": "Rétention et destruction du contenu de bf.email, adossées au "
               "calendrier de conservation, avec inscription au registre",
    "version": "18.0.3.4.0",
    "category": "Productivity",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    # ⚠️ PAS d'auto_install, contrairement aux ponts bf_cx_privacy /
    # bf_sign_privacy / bf_securetransfer_privacy. Ceux-là consignent ou
    # verrouillent ; celui-ci DÉTRUIT. Une base qui porte des dizaines de
    # milliers de lignes et des centaines de Mo de RFC 2822 appartient à son
    # entreprise : c'est son registre de destruction et sa décision, pas la
    # nôtre. Installation à la main, jamais automatique.
    "depends": [
        "bf_email_management",
        "privacy_consent",
    ],
    "description": """
Gestion des courriels — pont Vie privée (Loi 25)
================================================

`bf.email` est l'inbox unifiée : tout le courriel client, avec ses pièces
jointes, dans un seul modèle. C'était le plus gros gisement de renseignements
personnels de l'instance et **aucune rétention ne s'y appliquait**. Les quinze
crons du module portent sur l'ingestion, la synchronisation, le rattrapage et
la recopie ; le seul « purger » de la liste vise le journal des réacheminements
(`bf.email.auto.log`), soit un journal technique, pas le contenu.

Ce que ce pont ajoute
---------------------

1. **Deux règles de conservation** au calendrier de l'article 3.2 LPRPSP, parce
   qu'il y a deux régimes et non un seul.
2. **Un cron qui PROPOSE** la destruction de ce qu'il sait dater, sans jamais
   détruire : il alimente une `privacy.destruction.campaign` laissée en
   révision. Le responsable de la vie privée approuve, puis exécute.
3. **L'inscription au registre**, écrite par la campagne, complétée de
   l'énoncé de portée.
4. **Un écran de réglages** où un administrateur ajuste les deux durées et voit
   ce que la prochaine passe ferait, avant qu'elle le fasse.

Les deux régimes
----------------

**Le courriel qui dort dans la boîte** (`res_model` vide). Ici la ligne est la
seule copie côté Odoo : le corps est stocké, et `raw_rfc822` porte le message
RFC 2822 entier. Conservation de **3 ans à compter de la réception**, alignée
sur la règle « Correspondance » (COR-001) déjà semée par `privacy_consent` —
un seul régime de correspondance à défendre en audit plutôt que deux.

**Le courriel versé à un dossier** (`res_model` rempli). Ici la ligne est un
pointeur : le corps est une projection du `mail.message`, qui vit dans le
chatter du dossier client. Le détruire sur son âge propre casserait le pointeur
sans rien supprimer. Sa fin n'est pas accomplie tant que le dossier vit, donc
la conservation court **5 ans après la clôture du projet** qui le gouverne,
alignée sur la règle « Dossiers de projets » (PRJ-001). Huit modèles savent
désigner leur projet : `project.project`, `project.task`, et les six qui
portent un `project_id` en propre (`meeting.record`, `sale.order`,
`helpdesk.ticket`, `project.document`, `meeting.agenda`,
`project.knowledge.item`).

⚠️ Ce qu'il refuse de faire
---------------------------

**Il ne détruit rien qu'il n'ait su dater.** Un courriel classé sur un modèle
sans projet — une piste CRM, une facture — n'a pas d'ancrage de clôture, donc
il est CONSERVÉ et compté à part. Deviner une date pour pouvoir détruire, c'est
détruire ce qu'on n'a pas su lire.

🔴 **Il ne confond pas ranger et clore.** Mesuré sur une base réelle : aucun
signal déclaratif ne dit la clôture. « Archivé » compte des dossiers encore
actifs — archiver sert à désencombrer la liste. « Étape repliée » en compte
davantage : un projet d'accueil peut être en « Terminé » avec quarante tâches
ouvertes et un mois de messages. Et « Date de fin posée » en compte
une bonne part : c'est une fin PRÉVUE, pas une clôture. Un dossier n'est donc
tenu pour clos que s'il est rangé **et** silencieux depuis
`bf_email_privacy.closure_quiet_days` (180 j) — plus aucun message ni feuille
de temps. La date de clôture est celle du dernier signe de vie, et le silence
est réévalué à chaque balayage : un dossier qui repart repousse sa propre date.

⚠️ **Il ne date jamais par `write_date`.** Sur une base réelle, la quasi-
totalité des projets portaient le même `write_date`, trace d'une passe
automatisée. Un premier essai de datation par « dernière modification »
proposait donc cette date-là pour la plupart des dossiers. Une modification
n'est pas une activité.

**Il refuse de tourner sous la fenêtre de réconciliation IMAP.**
`_cron_imap_reconcile` rebalaie INBOX et Sent sur `bf_email.reconcile_days`
jours (30 par défaut) et re-crée toute ligne dont le Message-ID a disparu. Une
rétention plus courte que cette fenêtre produirait une boucle
détruire/ré-ingérer qui écrirait une entrée de registre chaque nuit sans que
rien ne disparaisse. Le cron vérifie l'écart et s'arrête plutôt que de le
faire.

🔴 **Il refuse la « suppression » de `privacy_consent`, qui archive.**
`privacy.destruction.campaign.line._execute_destruction()` traite la méthode
« delete » par `write({"active": False})` dès que le modèle a un champ `active`.
`bf.email` en a un. Sans surcharge, une campagne approuvée masquerait la ligne
en laissant le corps, les adresses et la copie RFC 2822, parfois des centaines
de Mo, puis
inscrirait « Suppression » au registre **immuable**. Ce module surcharge
`_execute_destruction` pour `bf.email` : suppression réelle, **vérifiée** avant
de rendre la main (une exception est le seul moyen d'empêcher la certification,
`action_execute` créant l'entrée dès que la méthode rend sans lever). Le défaut
vit dans `privacy_consent` et vaut pour tout modèle archivable ; il est signalé
à part plutôt que corrigé ici, redéfinir « supprimer » pour des modèles non
examinés dépassant le mandat.

**Il ne touche pas à `raw_rfc822` séparément.** Les lignes promues au chatter
par `_should_sync` gardent leur copie brute alors que le contenu vit désormais
dans le `mail.message`. Ce n'est pas un doublon jetable :
`mail_message.py` s'en sert comme source de fidélité pour l'export `.eml` du
chatter — la reconstruction depuis `mail.message` perd des en-têtes et la
structure MIME. Ces copies suivent le régime du courriel classé.

⚠️ Portée réelle de la destruction
----------------------------------

`unlink()` supprime la ligne et, par la cascade d'`ir.attachment` d'Odoo, sa
copie RFC 2822. Le blob quitte le filestore au passage suivant du ramasse-
miettes des pièces jointes.

Ce que le registre dit explicitement :

* **Les sauvegardes ne sont pas atteintes.** Elles gardent l'état antérieur
  aussi longtemps que leur propre rotation le conserve. Le registre renvoie à
  la politique de sauvegarde de l'exploitant, et dit que la profondeur RÉELLE
  se vérifie au dépôt — parce qu'elle ne se déduit pas de la politique.
  🔴 Le piège est mesuré, pas théorique : une politique annoncée de
  14 quotidiennes / 8 hebdomadaires / 12 mensuelles peut cohabiter avec des
  centaines d'instantanés vieux de plusieurs mois. Il suffit que chaque
  sauvegarde écrive dans un répertoire temporaire unique : `restic forget`
  groupe par `host,paths`, chaque instantané forme alors son propre groupe et
  garde son unique exemplaire, et l'élagage ne retire rien.
  ⚠️ Deux formulations antérieures de ce texte étaient fausses, dans les deux
  sens — « rétention illimitée de type legal hold » (mauvais motif), puis
  « disparu de toutes les copies environ 12 mois après » (mauvaise promesse :
  une politique de rotation n'a pas cet effet). Une entrée de registre est
  immuable ; ni l'une ni l'autre n'a été inscrite.
* **La copie sur le serveur IMAP**, le cas échéant. Le module écrit vers IMAP
  (archivage, déplacement) mais ne supprime pas la boîte du client.

La méthode déclarée est donc `delete`, jamais `secure_wipe` : déclarer un
effacement sécurisé sur des blocs qu'on ne contrôle pas serait un surclaim.
""",
    "data": [
        "data/privacy_retention_data.xml",
        "data/ir_config_parameter.xml",
        "data/bf_email_retention_cron.xml",
        "views/res_config_settings_views.xml",
    ],
}
