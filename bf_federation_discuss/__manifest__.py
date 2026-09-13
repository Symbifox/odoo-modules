{
    "name": "Fédération : canal de discussion",
    "version": "18.0.1.0.0",
    "category": "Productivity/Discuss",
    "summary": "Un canal Discuss par objet fédéré, adossé au chatter qui fait foi",
    "description": """
Fédération : canal de discussion
================================

Chaque objet fédéré peut avoir son canal Discuss, ouvert des deux côtés, où les
deux équipes se parlent dans la forme du clavardage plutôt que dans celle du
suivi.

**Le canal est la fenêtre, le chatter est le registre.** Un message écrit dans le
canal est posté au chatter de l'objet, d'où la fédération le porte déjà chez le
pair ; un message qui arrive au chatter reparaît dans le canal. Il n'y a donc
aucun nouveau genre sur le réseau, aucune conversation en double, et l'objet
garde une seule histoire, celle que le chatter raconte.

**Le canal s'ouvre à la demande, jamais tout seul.** Un canal que personne n'a
ouvert est exactement la porte vide qu'on cherche à éviter.

⚠️ **Ce n'est pas du clavardage en direct.** La boîte de sortie est relue par un
cron : comptez deux à quatre minutes par aller-retour. Pour une conversation qui
doit être instantanée, le téléphone et Nextcloud Talk restent meilleurs.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_federation", "mail"],
    "data": ["views/federation_link_views.xml"],
    "installable": True,
    "application": False,
    "auto_install": False,
}
