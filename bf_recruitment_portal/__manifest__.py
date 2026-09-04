{
    "name": "Recrutement : portail du candidat",
    "summary": "Laisser la personne qui a postulé suivre son dossier et obtenir "
               "elle-même le cahier de ses entrevues, une fois la décision prise",
    "version": "18.0.2.0.0",
    "category": "Human Resources/Recruitment",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "auto_install": False,
    "description": """
Recrutement : portail du candidat
=================================

Avant ce module, la personne qui a postulé ne voit **rien**. Ni `hr.applicant`
ni `hr.candidate` n'est portalisé, aucun contrôleur ne les expose, et aucun
compte n'est créé. Le seul canal est le courriel, et le droit d'accès s'exerce
en écrivant à quelqu'un qui prépare la réponse à la main.

Deux portes, et c'est voulu
---------------------------

1. **Le jeton dans le courriel.** Un lien signé, sans mot de passe. La personne
   clique et voit son dossier. C'est le patron d'Odoo pour les devis et les
   factures, et c'est la porte qui demande le moins à quelqu'un qui vient de
   se faire refuser.
2. **Le compte.** Depuis la même page, elle peut se créer un compte ou
   réinitialiser son mot de passe, et retrouver ensuite toutes ses candidatures
   sous « Mes candidatures ». L'inscription passe par une **invitation**
   (`signup_prepare` sur son partenaire), donc elle fonctionne même quand
   l'instance refuse les inscriptions libres, ce qui est le réglage par défaut.

🔴 Ce que la personne voit, et quand
------------------------------------

**Avant la décision : l'état, et rien d'autre.** Le poste, la date de dépôt, le
nombre d'entrevues tenues, et le fait que le dossier est à l'étude. Montrer des
appréciations en cours de processus empoisonnerait le processus et exposerait
l'opinion de tiers avant que quiconque ait tranché.

**Après la décision : le cahier.** La décision elle-même, le motif écrit, et le
cahier de ses entrevues en PDF. Ce cahier est celui de `bf_recruitment`,
`report_interview_book_candidate` : **sans le nom des évaluateurs**, panel
réduit à son effectif, nom du décideur retiré. Ce sont des renseignements qui
portent sur des tiers.

⚠️ **Ce module change le régime de vie privée, et c'est assumé.** Le droit
d'accès cesse d'être une demande traitée à la main pour devenir automatique et
permanent : toute appréciation écrite est de fait remise à son sujet dès la
décision rendue. Le champ de commentaire du cahier le dit déjà à qui évalue,
« écrivez-le en sachant que la personne évaluée a le droit de le lire ». Ce
module transforme cette phrase en fait.

La sécurité, par liste blanche
------------------------------

⚠️ **Les gabarits ne reçoivent JAMAIS l'enregistrement**, seulement des
dictionnaires construits ici. C'est ce qui rend `bf.interview.rating`, les noms
d'évaluateurs et les notes internes inatteignables depuis une page, même si un
gabarit est modifié plus tard par distraction. Même patron que
`bf_meeting_portal`.

Aucun droit ORM n'est accordé au groupe portail. Les recherches se font en
`sudo` avec un domaine borné au partenaire, et l'accès unitaire passe par
`_document_check_access`, qui accepte soit le jeton, soit un utilisateur qui a
le droit de lire.
""",
    "depends": [
        "bf_recruitment",
        "portal",
        "auth_signup",
    ],
    "data": [
        "views/portal_templates.xml",
        "views/res_config_settings_views.xml",
    ],
}
