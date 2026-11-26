"""18.0.1.10.0 : le groupe « Subscriptions / User » devient « User ».

Il entre dans la catégorie Abonnements, où l'écran des droits l'affiche à côté de
« Gestionnaire » : le préfixe n'y a plus de sens. Un `-u` n'écrase jamais une
traduction existante : le français
resterait « Abonnements / Utilisateur ». Étape `end`, parce que le catalogue se
charge après le `post`; on ne remplace que la valeur d'origine, pour laisser
intacte la retouche d'un opérateur.
"""
from odoo import SUPERUSER_ID, api

ANCIEN = "Abonnements / Utilisateur"
NEUF = "Utilisateur"


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    group = env.ref("bf_subscription.group_subscription_user", raise_if_not_found=False)
    if not group:
        return
    traductions = {t["lang"]: t["value"] for t in group.get_field_translations("name")[0]}
    if traductions.get("fr_CA") == ANCIEN:
        group.update_field_translations("name", {"fr_CA": NEUF})
