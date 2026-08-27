{
    'name': 'Symbifox — Transfert sécurisé : entente de confidentialité',
    'version': '18.0.1.1.0',
    'category': 'Website',
    'summary': "Pont bf_securetransfer × bf_sign : exiger la signature d'une "
               "entente de confidentialité avant l'accès au contenu d'un transfert",
    'description': """
Blue Fox — Transfert sécurisé : entente de confidentialité
==========================================================

Module **pont**. Il ajoute une barrière entre le code à usage unique et le
contenu d'un transfert : la signature d'une entente de confidentialité (NDA).

Parcours du visiteur
--------------------
1. Il ouvre le lien et déclare son identité (courriel — ou mobile, en audience
   ouverte sans NDA).
2. Il confirme par le code à usage unique : son identité est prouvée.
3. **Si le transfert exige une entente**, il est conduit vers une page qui la
   lui présente et l'envoie signer. La demande de signature est créée pour
   *lui seul*, sur son identité confirmée.
4. La signature scellée, il revient au transfert et accède au contenu.

Pourquoi un module séparé
-------------------------
``bf_securetransfer`` est sous LGPL-3 et ne connaît pas ``bf_sign``. Un simple
champ ``Many2one`` vers ``bf.sign.request`` posé dans le socle en aurait fait
une **dépendance dure** — Odoo résout les comodèles au chargement du registre,
bien avant qu'une garde de calcul ait voix au chapitre, et le défaut ne se
serait vu que sur une installation neuve. Le socle n'expose donc qu'un seul
point d'extension, ``secure.transfer._extra_access_gate()`` ; tout ce qui
touche à la signature vit ici.

Ce que le pont garantit
-----------------------
* **L'état de l'entente est LU, jamais reçu.** La barrière relit l'état de la
  demande de signature à chaque requête. Aucun rappel, aucun drapeau de
  session : un rappel manqué rouvrirait la porte en silence.
* **Le lien direct d'un fichier est barré comme la page.** C'est ce lien-là qui
  circule ; ne protéger que la page laisserait la barrière ornementale.
* **Une entente par personne.** Chaque visiteur signe la sienne, sur son
  identité confirmée — pas une signature collective dont on ne saurait dire qui
  l'a apposée.
* **Pas de second code.** L'identité a déjà été prouvée par le code du
  transfert, envoyé à cette même adresse : redemander un code de signature
  serait de la friction sans gain de preuve.
* **Le mobile ne signe pas.** Un signataire sans adresse courriel n'existe pas
  dans ``bf_sign``, et fabriquer une adresse dans une pièce juridique n'est pas
  une option : dès qu'une entente est exigée, l'identification par mobile est
  retirée de la page.
    """,
    'author': 'Les services de consultation Blue Fox, Inc.',
    'website': 'https://symbifox.com',
    # bf_sign est propriétaire (BUSL-1.1) : un pont qui en dépend ne peut pas
    # être plus permissif que ce qu'il relie.
    'license': 'Other proprietary',
    'depends': [
        'bf_securetransfer',
        'bf_sign',
    ],
    'data': [
        'data/secure_transfer_template_data.xml',
        'views/secure_transfer_sign_views.xml',
        'views/portal_templates.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
