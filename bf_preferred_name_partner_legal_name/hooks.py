"""À l'installation du pont : les noms légaux déjà tapés sur des personnes.

Avant le pont, rien n'empêchait d'écrire le nom légal d'une personne dans le
« Legal name » de `partner_legal_name`. Le hook le déplace vers `bf_legal_name` (protégé,
non suivi), vide ce champ sans suivi, et efface les traces de suivi
de ce champ sur ces fiches : elles portent le nom légal, lisible par tous les
internes, au fil.

Sans effet sur une base où aucune personne n'a de « Legal name ». N'écrase jamais un `bf_legal_name` déjà posé : si les deux
diffèrent, la fiche est laissée telle quelle et signalée au journal, par son
seul numéro (jamais la valeur), pour être tranchée à la main.
"""

import logging

from odoo.tools import is_html_empty

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    Partner = env["res.partner"].sudo().with_context(active_test=False, mail_notrack=True)
    persons = Partner.search([("legal_name", "!=", False), ("is_company", "=", False)])
    persons -= env["res.company"].sudo().with_context(active_test=False).search([]).partner_id
    if not persons:
        return

    cleared = Partner
    conflicts = Partner
    for person in persons:
        if not person.bf_legal_name:
            person.write({"bf_legal_name": person.legal_name, "legal_name": False})
        elif person.bf_legal_name == person.legal_name:
            person.write({"legal_name": False})
        else:
            conflicts |= person
            continue
        cleared |= person

    if cleared:
        field = env["ir.model.fields"]._get("res.partner", "legal_name")
        traces = env["mail.tracking.value"].sudo().search([
            ("field_id", "=", field.id),
            ("mail_message_id.model", "=", "res.partner"),
            ("mail_message_id.res_id", "in", cleared.ids),
        ])
        messages = traces.mail_message_id
        traces.unlink()
        messages.filtered(
            lambda m: not m.tracking_value_ids and not m.attachment_ids
            and is_html_empty(m.body)
        ).unlink()
        _logger.info("Legal name of %d individual(s) moved to the protected field.", len(cleared))
    if conflicts:
        _logger.warning(
            "Partners %s carry two different legal names (company field and protected "
            "field): left as is, to be settled by hand.", conflicts.ids)
