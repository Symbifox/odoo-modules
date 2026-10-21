"""Faire lire aux traductions du courriel la même liste d'éléments d'action
que le PDF et le portail.

Le PDF et le courriel listaient `task_ids` tel quel : les tâches annulées et la
tâche qui porte le compte rendu y passaient pour des actions à faire, alors que
le portail les écartait déjà. Les quatre surfaces lisent désormais
`_action_tasks_for_report()`.

Le PDF (`ir.ui.view.arch_db`) se traduit terme par terme : la mise à jour du
module y porte le nouvel appel dans chaque langue. Le courriel
(`mail.template.body_html`) se traduit EN BLOC : chaque clé autre que `en_US`
est un gabarit complet que la mise à jour ne touche pas (constaté sur une base d'essai :
après le `-u`, `en_US` lisait la nouvelle méthode, `fr_CA` encore
`object.task_ids`). On y remplace donc les deux seuls endroits qui lisent
`object.task_ids`.

Si une traduction ne porte pas exactement ces deux endroits (gabarit retouché à
la main), on la laisse telle quelle en le journalisant : mieux vaut une liste
qui montre encore les annulées qu'un gabarit cassé qui n'envoie plus rien.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

OLD_IF = '<t t-if="object.task_ids">'
NEW_IF = ('<t t-set="action_tasks" t-value="object._action_tasks_for_report()"/>\n'
          '<t t-if="action_tasks">')
OLD_FOREACH = 't-foreach="object.task_ids"'
NEW_FOREACH = 't-foreach="action_tasks"'


def rewrite_body(body):
    """Le corps réécrit, ou None s'il faut le laisser tel quel."""
    if "_action_tasks_for_report" in body:
        return None
    if body.count(OLD_IF) != 1 or body.count(OLD_FOREACH) != 1:
        return None
    return body.replace(OLD_IF, NEW_IF).replace(OLD_FOREACH, NEW_FOREACH)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    # Le chargement des données a écrit la nouvelle source par l'ORM, pas
    # encore en base : sans ce flush, les lectures SQL voient l'ancienne.
    env.flush_all()
    template = env.ref("bf_meeting.meeting_report_mail_template", raise_if_not_found=False)
    if not template:
        return
    cr.execute(
        "SELECT jsonb_object_keys(body_html) FROM mail_template WHERE id = %s",
        [template.id])
    langs = [lang for (lang,) in cr.fetchall() if lang != "en_US"]
    for lang in langs:
        cr.execute("SELECT body_html ->> %s FROM mail_template WHERE id = %s",
                   [lang, template.id])
        body = cr.fetchone()[0] or ""
        if "_action_tasks_for_report" in body:
            continue
        new_body = rewrite_body(body)
        if new_body is None:
            _logger.warning(
                "bf_meeting 18.0.3.69.0 : traduction %s du courriel laissée telle quelle "
                "(les deux lectures de object.task_ids n'y sont pas exactement une fois)",
                lang)
            continue
        cr.execute(
            "UPDATE mail_template SET body_html = jsonb_set(body_html, %s, to_jsonb(%s::text)) "
            "WHERE id = %s",
            [[lang], new_body, template.id])
        _logger.info("bf_meeting 18.0.3.69.0 : éléments d'action filtrés dans le courriel en %s",
                     lang)
    template.invalidate_recordset(["body_html"])
