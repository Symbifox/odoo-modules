from . import models

# Pas de post_init_hook. Celui qui vivait ici (_set_home_action) ecrivait
# action_id sur TOUS les usagers internes a chaque installation, ecrasant un
# reglage personnel que personne ne lui avait demande de toucher. La porte
# d'entree est bf_home, qui pose un ir.default sur res.users.action_id : un
# defaut, defait depuis Parametres, jamais impose. Voir la tache #24862.
