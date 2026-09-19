{
    "name": "Numériser depuis le téléphone",
    "version": "18.0.1.1.0",
    "category": "Productivity",
    "summary": "La page /scan gagne deux gestes : photographier une facture, "
               "et déposer un document au bloc-notes ou au chatter d'une fiche",
    "description": """
Numériser depuis le téléphone
=============================

``bf_contact_enrichment`` sert déjà une page autonome et installable à ``/scan``,
faite pour un geste de dix secondes à bout de bras : on photographie une carte
d'affaires, on corrige, on enregistre. Ce module garde cette page et lui ajoute
les deux autres papiers qui traînent dans une poche.

**Une facture.** La photo crée un brouillon de facture fournisseur et s'y
attache dans le fil. La lecture n'est pas faite ici : c'est ``bf_invoice_ocr``
qui lit, quand il sait lire la pièce. Tant qu'il ne sait pas, la facture reste
en attente de lecture plutôt que marquée en erreur — elle sera reprise par le
passage suivant, sans que personne n'ait à y revenir.

**Un document.** La photo va au bloc-notes (``bf.note``), avec un rappel qui
devient une activité, ou directement au fil d'une fiche choisie sur le
téléphone. Le bloc-notes est un tampon : on y dépose debout, on range assis.

Ce que ce module ne fait pas
----------------------------

* **Lire.** Aucune invite, aucun appel au modèle : la lecture appartient aux
  modules qui la portent déjà.
* **Deviner la fiche.** Le sélecteur passe par ``bf.chatter.target``, qui
  n'offre que des fiches lisibles par la personne connectée.
* **Élargir un droit.** Chaque tuile est gardée par le droit du geste qu'elle
  fait : les contacts pour la carte, la facturation pour la facture, le compte
  interne pour le document.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": [
        "bf_contact_enrichment",
        "bf_bloc_notes",
        "account",
    ],
    "data": [
        "views/scan_templates.xml",
        "data/scan_menu.xml",
    ],
    "installable": True,
    "application": True,
}
