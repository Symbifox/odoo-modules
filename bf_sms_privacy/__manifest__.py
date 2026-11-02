{
    "name": "SMS et appels — pont Vie privée (Loi 25)",
    "summary": "Rétention et destruction des messages SMS/MMS et des journaux "
               "d'appels, adossées au calendrier de conservation, avec "
               "inscription au registre",
    "version": "18.0.1.1.1",
    "category": "Productivity",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    # ⚠️ PAS d'auto_install, sur le patron de bf_email_privacy. Les ponts qui
    # se contentent de consigner (bf_cx_privacy, bf_securetransfer_privacy,
    # bf_meeting_privacy) peuvent s'installer seuls ; celui-ci DÉTRUIT.
    # `bf_sms_archive` sert plusieurs bases, et une durée de conservation est
    # une décision d'entreprise, prise par le responsable de la vie privée de
    # CETTE entreprise-là, inscrite à SON registre. Installation à la main,
    # jamais automatique.
    "depends": [
        "bf_sms_archive",
        "privacy_consent",
    ],
    "description": """
SMS et appels — pont Vie privée (Loi 25)
========================================

`bf_sms_archive` accumule sans borne. Sur une base en service depuis deux ans :
des dizaines de milliers de messages (`sms.archive.message`), des milliers de
journaux d'appels (`call.archive.call`), des milliers de parties MMS dont les
pièces jointes pèsent des centaines de Mo.

Ce sont des communications personnelles : la quasi-totalité vient de l'archive
du téléphone Android, journaux d'appels compris. Le reste passe par la ligne
d'affaires VOIP.ms.

Aucune rétention ne s'y appliquait. Les trois crons du module portent sur la
surveillance d'un dossier Nextcloud, l'import depuis `/mnt/sms-inbox` et le
filet des SMS entrants VOIP.ms ; la seule occurrence de « purge » du module vit
dans `models/push_transport.py` et vise les endpoints push morts (403/404/410),
pas le contenu.

Ce que ce pont fait
-------------------

1. **Il pose trois règles de conservation** au calendrier — TEL-001 pour les
   messages, TEL-002 pour les journaux d'appels, TEL-003 pour l'appel versé à
   un compte rendu de rencontre — avec leur base légale et leur durée.
2. **Il déclare la finalité** de l'archive au registre des traitements.
3. **Il propose, il ne détruit pas de lui-même.** Un cron quotidien balaie les
   lignes échues et les dépose dans une `privacy.destruction.campaign` laissée
   en révision. C'est le responsable de la vie privée qui approuve puis
   exécute, et c'est la campagne qui écrit au registre.
4. **Il fait dire vrai à la campagne** : la destruction est réelle, les pièces
   jointes MMS partent avec le message, et la ligne lève plutôt que de laisser
   certifier ce qui n'a pas eu lieu.
5. **Il empêche la résurrection.** Le hachage d'une ligne détruite est conservé
   en pierre tombale ; un import ultérieur qui le recroise passe outre.
6. **Il rend les trois modèles classifiables**, via le point d'extension
   `_privacy_classifiable_models()`.

Les durées, et pourquoi
-----------------------

**TEL-001 et TEL-002 — trois ans, alignés sur COR-001.** C'est de la
correspondance ; elle relève du même régime que le reste de la correspondance
de l'entreprise, et que le courriel non versé à un dossier (COR-BFMAIL-1). Un
régime unique couvre l'archive du téléphone et la ligne d'affaires : les
messages VOIP.ms sont trop récents (aucun n'a deux mois) et trop peu rattachés
(une poignée de fils sur des centaines portent un partenaire) pour qu'un second
régime, ancré sur le dossier client, rapporte plus qu'il ne coûte à défendre.

**TEL-003 — six ans, alignés sur RENC-001.** Dès que `bf_meeting_call_archive`
est installé, des comptes rendus pointent vers un appel archivé. Le compte rendu
se conserve cinq ans plus une année semi-active ; laisser l'appel mourir à trois
ans viderait en silence le renvoi de comptes rendus encore sous conservation. L'appel versé à une rencontre n'est plus une communication
personnelle isolée : c'est une pièce du dossier, et il suit le dossier.

⚠️ **Les médias MMS suivent le message, ils n'ont pas de durée propre.** Le
patron RENC-002 — la matière première meurt avant le document — ne s'applique
pas ici : une photo reçue par MMS n'est pas la matière première d'un document
qui la résumerait, c'est le message lui-même.

🔴 Ce que la destruction NE couvre pas
--------------------------------------

* **Le téléphone.** L'archive est une COPIE de ce qui vit sur l'appareil
  Android. Supprimer la ligne dans Odoo n'efface ni le SMS sur le téléphone,
  ni le journal d'appels de l'appareil.
* **Les fichiers de sauvegarde XML sur Nextcloud.** L'import déplace chaque
  fichier vers `done/` au lieu de le détruire : le matériau d'origine reste sur
  le Nextcloud, et c'est précisément ce qui rend la résurrection possible. D'où
  la pierre tombale — voir `models/privacy_tombstone.py`.
* **VOIP.ms.** Les messages de la ligne d'affaires transitent par le
  fournisseur, qui tient son propre journal, hors de portée d'Odoo.
* **Les sauvegardes.** Elles gardent l'état antérieur aussi longtemps que leur
  propre rotation le conserve. La politique vit chez l'exploitant ; sa
  profondeur RÉELLE se vérifie au dépôt plutôt que de se déduire de la
  politique.

La méthode déclarée est donc `delete`, jamais `secure_wipe` : déclarer un
effacement sécurisé sur des blocs qu'on ne contrôle pas serait un surclaim.
""",
    "data": [
        "security/ir.model.access.csv",
        "data/privacy_purpose_data.xml",
        "data/privacy_retention_calendar_data.xml",
        "data/bf_sms_privacy_cron.xml",
    ],
}
