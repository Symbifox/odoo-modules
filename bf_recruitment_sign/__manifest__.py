{
    "name": "Recrutement : l'offre part en signature",
    "summary": "La lettre d'offre devient signable, avec le papier brandé sous "
               "la signature et non le corps nu, et la candidature apprend la "
               "réponse du candidat",
    "version": "18.0.1.0.1",
    "category": "Human Resources/Recruitment",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "auto_install": True,
    "description": """
Recrutement : l'offre part en signature
=======================================

Ce que ça fait
--------------

`letter.document` reçoit `bf.sign.mixin` : une lettre s'envoie en signature,
et la demande se rattache à la lettre. ⚠️ **Toutes** les lettres deviennent
signables, pas seulement l'offre, et c'est ce qu'il faut pour signer une offre,
puisque l'offre EST une lettre, et les autres lettres candidate facing des lots
suivants en héritent d'office.

Quand la lettre signée porte une candidature (`applicant_id`), la réponse est
aussi consignée **sur la candidature** : c'est là que la personne qui recrute
travaille, et c'est là que le fil de discussion est lu.

🔴 Le papier brandé sous la signature, pas le corps nu
------------------------------------------------------

`letter.document` rend son PDF en **deux temps** : le rapport QWeb produit le
corps, puis `_get_pdf_binary()` le **superpose** au papier en-tête de la société
quand le mode est `pdf_overlay`. Un pont qui se contenterait de déclarer le
rapport ferait donc signer un document **sans l'en-tête**, pas celui que le
candidat a lu.

Le module rend donc le PDF complet par `_sign_document_file()`, et garde
`_sign_report_ref()` comme repli.

⚠️ **Et les deux lignées de `bf_sign` ne se valent pas.** `_sign_document_file()`
n'existe qu'à partir de 18.0.3.22.0 ; le catalogue publié est encore en
18.0.3.19.0 et ne l'a pas. Sur cette lignée-là, la surcharge est **silencieusement
ignorée** et c'est le rapport qui sert, ce qui est juste dans quatre modes
d'en-tête sur cinq. Pour le cinquième, `pdf_overlay`, le module **lève** au lieu
de faire signer un document qui n'est pas celui qu'on a montré. C'est la même
leçon que le pont vie privée a payée : un crochet absent en amont rend un pont
inerte sans le dire.

Ce qu'il refuse de faire
------------------------

* Une lettre en **brouillon** ne part pas en signature : on signe un texte
  arrêté, pas un texte qu'on est encore en train d'écrire.
* Il ne fait **pas** avancer la candidature tout seul. Une offre signée n'est
  pas une entrée en fonction, et `date_closed` est la date d'embauche : la poser
  d'office ferait compter la personne comme embauchée avant qu'elle ait
  commencé. La signature se consigne, la décision reste humaine.
""",
    "depends": [
        "bf_recruitment_letter",
        "bf_sign",
    ],
    "data": [
        "views/letter_document_views.xml",
    ],
}
