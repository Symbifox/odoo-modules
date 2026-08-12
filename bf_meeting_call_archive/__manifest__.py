{
    'name': 'Rencontres ↔ Archive d\'appels',
    'version': '18.0.1.0.0',
    'category': 'Services/Meetings',
    'summary': 'Lien optionnel entre un compte rendu et un appel archivé',
    'description': """
Pont entre les modules ``bf_meeting`` et ``bf_sms_archive``.

Quand une rencontre de type téléphonique a fait l'objet d'un compte rendu
structuré, ce module permet de pointer vers l'appel archivé correspondant
(durée, recording_url, partner). Le journal brut des appels n'est pas
modifié — la promotion est purement déclarative.

* Champ ``call_archive_id`` sur ``meeting.record`` (visible si mode = Téléphonique).
* Inverse ``meeting_record_ids`` sur ``call.archive.call`` + bandeau dans le form.
* Pré-remplissage automatique de ``duration_minutes`` et ``partner_id`` au
  choix de l'appel.

S'auto-installe quand ``bf_meeting`` et ``bf_sms_archive`` sont tous deux
installés.
    """,
    'author': 'Les services de consultation Les services de consultation Blue Fox, Inc.',
    'website': 'https://symbifox.com',
    'license': 'LGPL-3',
    'depends': ['bf_meeting', 'bf_sms_archive'],
    'data': [
        'views/meeting_record_views.xml',
        'views/call_archive_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': True,
}
