{
    "name": "Recrutement : courriels au candidat",
    "summary": "Reprendre les quatre courriels que le candidat reçoit : un sujet "
               "qui les distingue, une décision énoncée en clair, et la marque "
               "de l'entreprise",
    "version": "18.0.1.2.0",
    "category": "Human Resources/Recruitment",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    # Pont : il s'installe seul quand le cahier d'entrevues et la marque sont
    # là tous les deux, et le cahier fonctionne parfaitement sans lui.
    "auto_install": True,
    "description": """
Recrutement : courriels au candidat
===================================

Ce que le recrutement d'Odoo envoie aux candidats est correct sur le plan
technique et faible sur le plan du contenu. Mesuré en recevant vraiment les
messages, le 2026-08-31 :

* **Les quatre gabarits portent le MÊME sujet**, « Your Job Application:
  <poste> ». Dans la boîte du candidat, l'accusé de réception, l'invitation en
  entrevue et le refus se ressemblent. On ne sait pas ce qu'on ouvre.
* **Le message de RETRAIT ne dit pas de quoi il parle.** Envoyé quand c'est le
  candidat qui se désiste, son corps entier tient en « Nous tenons à vous
  remercier de votre intérêt et de votre temps. Nous vous souhaitons le
  meilleur dans vos projets futurs. » Il n'accuse même pas le désistement.
  ⚠️ Le gabarit de REFUS, lui, est correct sur ce point : il dit « our hiring
  team reviewed your application and did not select it for further
  consideration ». Ce qu'on lui reproche est ailleurs, voir plus bas.
* **L'accusé de réception commence par « Félicitations ! »** pour un simple
  dépôt de dossier, et enchaîne sur une description de poste vide, un numéro de
  téléphone vide et un « Quelle est la prochaine étape ? » qui répond à côté.
* **L'adresse courriel interne du recruteur est publiée au candidat** dans le
  corps des messages.
* **Aucun expéditeur n'est posé** : Odoo retombe sur `odoobot@example.com`, que
  la plupart des relais rejettent (550, sender address rejected).

Ce que ce pont change
---------------------

Il remplace les quatre gabarits du coeur. Rien d'autre : aucun modèle, aucun
champ, aucune vue.

1. **Un sujet par intention.** Réception, invitation, refus et retrait ne se
   ressemblent plus dans une boîte de réception.
2. **Une intention par message.** Le retrait accuse le désistement au lieu de
   remercier dans le vide. Le refus garde ce que le coeur faisait bien, la
   décision énoncée tôt, et abandonne ce qu'il fait mal : la promesse de
   « garder votre CV pour de futures occasions », qui contredit un calendrier
   de conservation et que peu d'entreprises tiennent.
3. **Le message s'adapte au parcours réel.** Une personne qui a passé une
   entrevue n'est pas remerciée comme une personne qui n'en a pas passé. Le
   pont lit `held_interview_count`, que le cahier d'entrevues tient déjà.
4. **Les droits de la personne sont nommés**, pas sous-entendus : le droit de
   consulter ce qui a été consigné sur elle, appréciations d'entrevue
   comprises, et le fait que la décision a été prise par une personne et non
   par un calcul.
5. **La marque de l'entreprise**, par `bluefox_branding.bf_mail_layout` :
   en-tête au logo, filet d'accent, pied avec les coordonnées de la société.
   Tout est lu sur `res.company`, donc chaque locataire reçoit la sienne.
6. **Un expéditeur qui passe** : l'adresse de la société, pas `odoobot`.

⚠️ **L'adresse du recruteur ne sort plus.** Son nom, oui, parce qu'écrire à
quelqu'un de nommé vaut mieux que d'écrire à une équipe anonyme. Les
coordonnées de retour sont celles de la société, et la réponse revient sur la
candidature par l'alias du poste.

⚠️ **Le motif de refus consigné n'est PAS envoyé d'office.** Le cahier
d'entrevues exige un motif écrit après une entrevue tenue, et ce motif est
rédigé pour être lu par la personne évaluée. L'envoyer sans qu'elle l'ait
demandé serait un autre geste : le courriel lui dit qu'elle peut l'obtenir, et
attend sa demande. Un texte écrit pour un dossier interne ne devient pas
automatiquement un texte qu'on adresse à quelqu'un.

⚠️ **Le gabarit de refus sert aussi aux motifs « Doublon » et « Pourriel ».**
Son texte reste donc volontairement neutre sur la cause. Un locataire qui veut
un ton différent pour ces cas crée un motif de refus avec son propre gabarit.
""",
    "depends": [
        "bf_recruitment",
        "bluefox_branding",
    ],
    "data": [
        "data/mail_template_overrides.xml",
    ],
    # 🔴 Sans ce crochet, le module ne change RIEN sur un locataire installé en
    # français : la valeur `fr_CA` traduite d'origine survit à la réécriture de
    # la source et c'est elle qui est rendue. Voir hooks.py.
    "post_init_hook": "post_init_hook",
}
