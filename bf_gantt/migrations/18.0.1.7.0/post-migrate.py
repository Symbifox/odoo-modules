# -*- coding: utf-8 -*-
"""18.0.1.7.0 : le lien publié change de jeton.

Avant, il portait l'`access_token` du projet, qui ouvre aussi /my/projects et
chacune de ses tâches. Cette page des tâches donne à son tour le jeton de la
tâche et un jeton par pièce jointe. Pour chaque projet dont l'échéancier a été
publié un jour (`bf_gantt_published` posé, vrai ou faux : dépublier gardait le
jeton, et le lien distribué restait bon) :

* l'`access_token` du projet est effacé ;
* celui de ses tâches aussi, comme Odoo le fait quand il retire l'accès portail ;
* celui des pièces jointes de ces tâches aussi.

Odoo refait un jeton au besoin. Un projet encore publié reçoit en plus son jeton
d'échéancier (`bf_gantt_token`). Les adresses déjà données cessent de
fonctionner : il faut redonner la nouvelle (« Show the full address »). Un projet
créé par le code et jamais publié garde le drapeau à NULL et n'est pas touché ;
créé au formulaire, il porte FALSE et perd ses jetons sans rien ouvrir (Odoo en
refait au besoin).
"""
import logging
import secrets

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("SELECT id, bf_gantt_published FROM project_project "
               "WHERE bf_gantt_published IS NOT NULL")
    lignes = cr.fetchall()
    if not lignes:
        _logger.info("bf_gantt 18.0.1.7.0 : aucun échéancier publié, rien à changer")
        return
    ids = [r[0] for r in lignes]
    cr.execute("UPDATE project_project SET access_token = NULL WHERE id = ANY(%s)", (ids,))
    cr.execute("UPDATE project_task SET access_token = NULL "
               "WHERE project_id = ANY(%s) AND access_token IS NOT NULL", (ids,))
    taches = cr.rowcount
    cr.execute("""
        UPDATE ir_attachment SET access_token = NULL
         WHERE access_token IS NOT NULL
           AND ((res_model = 'project.task'
                 AND res_id IN (SELECT id FROM project_task WHERE project_id = ANY(%s)))
             OR (res_model = 'project.project' AND res_id = ANY(%s)))
    """, (ids, ids))
    pieces = cr.rowcount
    publies = [pid for pid, publie in lignes if publie]
    for pid in publies:
        cr.execute("UPDATE project_project SET bf_gantt_token = %s WHERE id = %s",
                   (secrets.token_urlsafe(32), pid))
    _logger.info(
        "bf_gantt 18.0.1.7.0 : %s projet(s) déjà publié(s) un jour, jetons effacés "
        "(projet, %s tâche(s), %s pièce(s) jointe(s)) ; %s échéancier(s) publié(s) "
        "passé(s) au jeton propre", len(ids), taches, pieces, len(publies))
