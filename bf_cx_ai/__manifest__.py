{
    "name": "Expérience client : analyse IA des verbatims",
    "summary": "Sentiment, thèmes et résumé des commentaires par le pont IA",
    "version": "18.0.1.2.3",
    "category": "Marketing/Customer Experience",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "application": False,
    "installable": True,
    "auto_install": False,
    "description": """
Analyse IA des verbatims de l'expérience client.

Ajoute au registre des feedbacks un sentiment (positif, neutre ou
négatif), un résumé d'une phrase et des thèmes, déduits du commentaire
par le point /cx/analyze de bf_ai_bridge : une passe, aucun outil,
réponse bornée. Le texte du commentaire est transmis au modèle d'IA par
le pont du serveur. Le vocabulaire de thèmes est partagé avec
l'assistance (helpdesk.theme) quand elle est installée. Un bouton
« Analyser (IA) » lance l'analyse à la demande ; un traitement quotidien
facultatif (paramètre bf_cx.ai_auto_analyze, désactivé par défaut)
analyse jusqu'à 20 commentaires récents. Un pont éteint ne bloque rien :
une note interne est posée au fil. Aucun envoi au client.
""",
    "depends": [
        "bf_ai_bridge",
        "bf_cx",
    ],
    "data": [
        "data/ir_cron_data.xml",
        "views/bf_cx_feedback_views.xml",
        "views/res_config_settings_views.xml",
    ],
}
