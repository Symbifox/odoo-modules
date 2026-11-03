import logging

from .models.project_project import CHIFFRES, LARGEUR

_logger = logging.getLogger(__name__)


def post_init_numeroter_existants(env):
    """Donne un numéro aux projets qui existaient avant la pose du module.

    Sans ça, la liste des projets s'ouvre avec une colonne vide le premier jour
    et seuls les projets créés ensuite portent un numéro.

    Numérotés dans l'ordre de leur création (leur `id`), chacun au plus petit
    numéro encore libre : les projets déjà numérotés à la main avant la pose
    gardent leur numéro, et les trous qu'ils laissent se comblent.

    Écrit en SQL, pas par l'ORM, pour trois raisons mesurées : le suivi du champ
    déposerait un message au chatter de chaque projet existant, les surcharges
    d'écriture des autres modules du projet se déclencheraient pour un champ qui
    ne les concerne pas, et aucun champ calculé ne dépend de celui-ci — il n'y a
    donc rien à recalculer.
    """
    projets = (
        env["project.project"]
        .sudo()
        .with_context(active_test=False)
        .search_read([], ["bf_project_number"], order="id")
    )
    pris = set()
    for ligne in projets:
        texte = (ligne["bf_project_number"] or "").strip()
        if CHIFFRES.match(texte):
            pris.add(int(texte))

    poses = []
    candidat = 1
    for ligne in projets:
        if (ligne["bf_project_number"] or "").strip():
            continue
        while candidat in pris:
            candidat += 1
        pris.add(candidat)
        poses.append((f"{candidat:0{LARGEUR}d}", ligne["id"]))

    if not poses:
        _logger.info("bf_project_number: no project to number")
        return

    env.cr.execute(
        """UPDATE project_project AS p SET bf_project_number = v.numero
           FROM (VALUES %s) AS v(numero, id) WHERE p.id = v.id"""
        % ", ".join(["(%s, %s)"] * len(poses)),
        [x for couple in poses for x in couple],
    )
    env.invalidate_all()
    _logger.info(
        "bf_project_number: %s project(s) numbered, from %s to %s",
        len(poses),
        poses[0][0],
        poses[-1][0],
    )
