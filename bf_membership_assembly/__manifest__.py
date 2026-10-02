{
    "name": "Membres : AGA et votes",
    "summary": "Assemblées des membres : convocation dans les délais, votants à "
               "une date de référence, présences, quorum, procurations, votes "
               "à main levée ou au scrutin secret, procès-verbal",
    "version": "18.0.1.0.3",
    "category": "Association",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "description": """
Membres : AGA et votes
======================

Une assemblée de membres se conteste sur des détails : un avis parti trop
tard, une personne qui a voté sans être en règle, une procuration de trop, un
quorum compté de mémoire. Ce greffon tient ces détails-là, dans l'ordre où ils
arrivent.

Ce qu'il ajoute
---------------

* **L'assemblée** (`bf.membership.assembly`) : annuelle ou extraordinaire,
  date, lieu ou lien, délais d'avis minimal et maximal (10 et 45 jours d'office,
  C.c.Q. art. 346), date de l'avis, **date de référence** des votants, quorum
  en majorité, en nombre ou en pourcentage, procurations (refusées d'office),
  procès-verbal et documents joints.
* **La liste des votants** (`bf.membership.assembly.voter`), bâtie à la date
  de référence : les membres en règle ce jour-là dont la catégorie vote. Une
  organisation membre tient UNE ligne, et c'est son délégué votant en fonction
  qui vote pour elle. La liste gèle à l'ouverture.
* **La convocation** : le délai se contrôle avant l'envoi, et le compositeur de
  courriel s'ouvre pré-rempli vers les membres qui ont consenti aux avis par
  courriel. Rien ne part tant que la personne n'a pas cliqué sur Envoyer. Les
  autres sont listés pour l'avis par la poste.
* **Les présences et les procurations**, avec plafond par mandataire et sans
  procuration en chaîne. Le quorum se calcule.
* **Les propositions** (`bf.membership.assembly.proposal`) : proposeur,
  appuyeur, majorité simple, deux tiers ou pourcentage, vote à main levée ou au
  scrutin secret, résultat calculé.
* **Le scrutin secret** : un registre des bulletins remis
  (`bf.membership.assembly.ballot`) séparé du dépouillement, qui ne porte que
  des totaux.
* **Le procès-verbal en PDF**.

Ce qu'il ne fait pas
--------------------

* **Pas de vote électronique individuel.** Un vote en ligne secret exige de
  garantir que personne ne peut recouper un bulletin et son votant, même par
  l'ordre des enregistrements ou leur heure de création. Le module ne le
  promet pas : le scrutin secret se tient sur papier, et Odoo en garde le
  registre et les totaux.
* **Il n'envoie rien tout seul.** La convocation ouvre le compositeur ; c'est
  une personne qui envoie.
* **Il ne tient pas le registre corporatif.** Le pont vers les résolutions de
  `bf_corporate_governance` est un module à part, installé d'office quand les
  deux sont là.
""",
    "depends": [
        "bf_membership",
        "mail",
    ],
    "data": [
        "security/assembly_security.xml",
        "security/ir.model.access.csv",
        "data/mail_template.xml",
        "report/assembly_minutes_report.xml",
        "views/voter_views.xml",
        "views/proposal_views.xml",
        "views/assembly_views.xml",
        "views/menuitems.xml",
    ],
}
