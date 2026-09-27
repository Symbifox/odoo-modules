{
    "name": "Immeubles : de la demande au travail",
    "summary": "Acheminer la demande d'un occupant vers l'équipe de son immeuble, et en tirer les travaux à exécuter",
    "version": "18.0.2.1.1",
    "category": "Services/Property",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    # ⚠️ BUSL-1.1 — voir la note du manifeste de bf_property_core. Le fichier
    # LICENSE fait foi, et sa Change Date se retamponne à la publication.
    "license": "Other proprietary",
    "images": ["static/description/request_work_en.png"],
    "application": False,
    "installable": True,
    # ⚠️ Pont, sur le patron de bf_property_cx : il s'installe SEUL quand les
    # deux côtés sont là. L'exploitation vaut sans le portail de l'occupant —
    # c'est le cas du gestionnaire qui n'ouvre rien à ses locataires — et le
    # portail vaut sans l'exploitation.
    "auto_install": True,
    "description": """
Immeubles : de la demande au travail
====================================

Un occupant ouvre un billet parce que la porte du garage grince. Quelqu'un doit
aller la réparer. Ce sont deux objets, et ce module est le pont entre les deux.

**L'acheminement.** La demande porte l'immeuble ; l'immeuble nomme son équipe ;
la demande revient donc à cette équipe sans que personne la distribue. Prise en
charge, elle est confiée au responsable de l'équipe, qui reste modifiable.
Avant, il n'y avait qu'un responsable à désigner à la main, billet par billet.

**Le pont.** Une demande peut donner zéro, un ou plusieurs travaux à exécuter :
zéro quand elle est refusée ou qu'un mot suffit, plusieurs quand la porte du
garage tient à la fois du moteur et du rail.

⚠️ **Ce que l'occupant voit et ce que l'équipe voit ne sont pas la même chose.**
L'occupant voit sa demande, son état et ce qui a été fait. Il ne voit ni
l'équipe, ni le technicien, ni les travaux, ni leurs durées. Ce n'est pas une
préférence d'affichage : le registre des travaux est fermé au portail par les
droits d'accès, et un test l'éprouve plutôt que de s'en remettre au gabarit.

⚠️ **La lecture de l'art. 1064 ne traverse pas le pont.** Qui porte la dépense
se lit sur la demande, à partir de la partie visée et de la nature des travaux.
Le travail à exécuter ne la recopie pas : deux endroits où lire la même règle,
c'est un endroit de trop, et c'est celui qui se désaccorde qu'on lira.

⚠️ **Une seule chose traverse, et elle est nommée : la nature des travaux.** Le
même fait sert des deux côtés sous deux règles différentes : répartir la dépense
sur la demande (art. 1064 C.c.Q.), décider de ce qui remonte au carnet
d'entretien sur le travail (r. 8.01, art. 2 al. 2, par. 3° contre art. 3 al. 2).
La redemander à qui ouvre le travail lui ferait donner deux fois la même
réponse, avec le droit de se contredire, et un dossier réglementaire porterait
alors deux qualifications du même travail. Le pont préremplit donc, et n'impose
rien : la personne au chantier a le dernier mot, et requalifier la demande après
coup ne reprend pas ce qu'elle a écrit.

⚠️ **« À déterminer » n'est pas une réponse.** C'est le défaut de la demande, et
le formulaire du portail ne pose pas la question : c'est le syndicat qui
qualifie. Le traiter comme une réponse ferait entrer au carnet une nature que
personne n'a donnée.

⚠️ **Les bons de travail fournisseurs restent hors périmètre.** Un travail à exécuter n'est ni un devis, ni une commande.
""",
    "depends": [
        "bf_property_operations",
        "bf_property_portal",
    ],
    "data": [
        "views/bf_property_operations_portal_views.xml",
    ],
}
