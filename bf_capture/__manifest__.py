{
    "name": "Captation audio",
    "version": "18.0.1.2.1",
    "category": "Productivity",
    "summary": "Enregistrer une rencontre ou un mémo depuis le téléphone, et le déposer là où la suite est déjà automatique",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "auto_install": False,
    # `calendar` pour nommer d'après l'événement, la passerelle Nextcloud pour
    # déposer, le bloc-notes pour le mémo. Le service de transcription, lui, est
    # reconnu au moment de la requête et NON déclaré : un mémo se dépose même là
    # où aucun n'est configuré, il arrive alors sans texte.
    "depends": ["calendar", "bf_document_nextcloud_sync", "bf_bloc_notes"],
    "description": """
Captation audio
===============

Le téléphone sait déjà ce que le nom du fichier essaie de dire.

Le processeur de rencontres n'a qu'une porte d'entrée : un fichier déposé dans
un dossier Nextcloud. Tout ce qu'il saura de la rencontre, il le lira dans le
**nom** de ce fichier. Un nom d'entreprise dans le nom vaut 0,60 au routage,
chaque participant apparié 0,40, le verbatim au plus 0,30, et le seuil est 0,50 :
un enregistrement mal nommé ne peut donc pas être rattrapé par son contenu.

Ce module met le nom entre les mains du serveur. Le téléphone envoie le son et
dit de quelle rencontre il s'agit ; c'est Odoo qui compose l'horodatage et le
titre à partir de l'événement au calendrier, puis qui pose le fichier dans le
dossier surveillé.

**Deux portes, et elles ne mènent pas au même endroit**
  Une rencontre part vers le processeur, qui en fera un compte rendu, des tâches
  et un rapport. Un mémo de quarante secondes n'a ni participants, ni décisions,
  ni destinataire : il devient une note, avec son texte quand la dictée est
  configurée, et son audio en pièce jointe. En faire un compte rendu fabriquerait
  un objet qui affirme plus qu'il ne sait.

**L'horodatage est en heure de Montréal, et ce n'est pas un détail**
  Le processeur relit l'heure du nom comme une heure de Montréal pour dater le
  compte rendu, et la date du nom comme une journée UTC pour retrouver
  l'événement au calendrier. Un appareil qui horodate en heure locale de
  Nouvelle-Zélande écrit donc une date en avance d'un jour. Le serveur compose
  l'horodatage depuis le début de l'événement, jamais depuis l'horloge du
  téléphone.

**Deux fichiers ne portent jamais le même nom**
  Le surveillant déduplique par nom, en mémoire : un second fichier au même nom
  serait ignoré jusqu'au redémarrage de son conteneur. Le module regarde le
  dossier surveillé et son sous-dossier des traités avant de poser, et numérote
  si le nom est pris.

⚠️ **Ce que le module ne fait pas**
  Il ne transcrit pas les rencontres : c'est le travail du processeur, qui a le
  GPU, la diarisation et le temps. Il n'envoie rien à personne. Il ne décide pas
  ce qui mérite d'être enregistré.
""",
    "data": [
        "views/res_config_settings_views.xml",
    ],
}
