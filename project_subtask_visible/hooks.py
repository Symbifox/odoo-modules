def post_init_hook(env):
    """Rend visibles les fiches que l'œil avait cachées avant la pose.

    Des sous-tâches pour la plupart — mais pas seulement : sur une base réelle,
    87 des 958 fiches cachées n'avaient aucun parent.

    Le drapeau du cœur (`project.task.display_in_project`) est stocké : les
    calculs du module ne touchent que les fiches qu'Odoo recalcule. Les lignes
    déjà à faux resteraient cachées — y compris les archivées, qui remontent
    dès qu'on coche « Archivé ». Une seule écriture SQL les retourne, et elle
    se rejoue sans dommage (l'installation est aussi le chemin de réparation).

    Un journal pris avant la pose garde la liste des fiches retournées : c'est
    lui, et non ce crochet, qui permet de tout recacher à l'identique.
    """
    # Vider AVANT l'écriture brute : `invalidate_all()` vide par défaut
    # (`flush=True`), donc appelé après, il écrirait par-dessus le SQL ce qui
    # traînait en attente.
    env.flush_all()
    env.cr.execute("""
        UPDATE project_task
           SET display_in_project = TRUE
         WHERE display_in_project IS DISTINCT FROM TRUE
    """)
    retournees = env.cr.rowcount
    env.invalidate_all(flush=False)
    env["ir.logging"].sudo().create({
        "name": "project_subtask_visible",
        "type": "server",
        "level": "INFO",
        "dbname": env.cr.dbname,
        "message": f"display_in_project forcé à vrai sur {retournees} tâche(s) "
                   "qui étaient cachées des vues de leur projet (des sous-tâches "
                   "pour la plupart, mais pas seulement : le drapeau se pose aussi "
                   "sur des tâches de premier niveau).",
        "path": "project_subtask_visible/hooks.py",
        "func": "post_init_hook",
        "line": "1",
    })
