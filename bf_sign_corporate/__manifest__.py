{
    'name': "Blue Fox — Signature des résolutions corporatives",
    'version': '18.0.1.1.1',
    'category': 'Productivity/Sign',
    'summary': "Envoyer une résolution corporative pour signature électronique (bf_sign).",
    'description': """
Ajoute « Envoyer pour signature » sur les résolutions corporatives
(``corporate.resolution``) : la résolution est rendue en PDF brandé, une demande
de signature bf_sign liée est créée, et le document signé (+ certificat de
complétion) est reversé dans le fil de la résolution une fois signé par tous.

Signataires par défaut :
* résolution du conseil → les administrateurs actifs ;
* résolution des actionnaires → le proposeur (et le secondeur, s'il y a lieu).
Ils restent modifiables sur la demande en brouillon avant l'envoi.
""",
    'author': "Les services de consultation Blue Fox, Inc.",
    'website': "https://bluefoxconsultant.com",
    'license': 'Other proprietary',
    'depends': ['bf_sign', 'project_knowledge_matrix'],
    'data': [
        'views/corporate_resolution_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
