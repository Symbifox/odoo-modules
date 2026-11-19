{
    'name': 'Symbifox Appareils',
    'version': '18.0.1.3.0',
    'category': 'Productivity',
    'summary': "Les appareils appariés d'une personne, et le geste qui les révoque tous",
    'description': """
Symbifox Appareils / Mes appareils
==================================

Trois modules maison apparient des téléphones — la messagerie SMS, le courriel
et le coffre de tokens — et chacun le fait bien, chacun de son côté. Personne
ne pouvait dire **quels appareils** peuvent atteindre ses affaires, ni tout
couper d'un seul geste : chaque module a son ``/logout``, et il ne désactive
que son propre enregistrement.

Ce module n'ajoute aucun modèle et n'apparie rien. Il regarde les registres
existants et en fait une page, ``/my/appareils`` :

* un appareil par ligne, avec le module qu'il ouvre et sa dernière activité ;
* un bouton pour retirer l'accès d'un appareil ;
* un bouton pour tout retirer d'un coup.

⚠️ **Retirer l'accès n'efface pas le téléphone.** L'appareil garde son token,
il ne lui ouvre simplement plus rien. C'est exactement ce que fait le
``/logout`` de chaque module, et le contraire d'une fausse promesse.

18.0.1.3.0 : quatrième source, les widgets du bureau de Blue Fox OS
(``bf.desktop.device``), et la capacité « Bloc-notes » — que les jetons de
Messages et de Courriel ouvrent aussi, ce que la page ne disait pas.
""",
    'author': 'Les services de consultation Blue Fox, Inc.',
    'website': 'https://symbifox.com',
    'license': 'Other proprietary',
    'depends': ['portal'],
    'data': [
        'views/portal_templates.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
