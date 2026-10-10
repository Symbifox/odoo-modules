"""18.0.2.1.0 : les libellés source passent en ANGLAIS, le français vient de fr_CA.po.

Une base montée garde, dans la fente `en_US` de ses champs traduisibles, la
source FRANÇAISE d'avant. Deux choses ne se rattrapent pas seules au `-u` :

1. **Les enregistrements `noupdate`** (groupe, catégorie de module, tâche
   planifiée) ne sont jamais rechargés : on réécrit leur source anglaise.
2. **Les catalogues** : on recharge `personal_budget` pour chaque langue
   active autre que `en_US`, AVEC écrasement, pour que la fente `fr_CA` ne
   reste pas figée sur une ancienne source ou vide (repli sur l'anglais).

L'ordre compte : source anglaise d'abord, catalogue ensuite.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

NOUPDATE_SOURCES = {
    'personal_budget.module_category_budget': {'name': 'Budget', 'description': 'Personal Budget'},
    'personal_budget.group_budget_user': {
        'name': 'Budget user',
        'comment': "Full access to the personal budget (each person only sees their own "
                   "budgets and the ones shared with them).",
    },
    'personal_budget.ir_cron_post_due_recurring': {'name': 'Budget: record due recurring expenses'},
}


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {'lang': 'en_US'})
    for xmlid, vals in NOUPDATE_SOURCES.items():
        record = env.ref(xmlid, raise_if_not_found=False)
        if record:
            record.with_context(lang='en_US').write(vals)
    langs = [code for code, _name in env['res.lang'].get_installed() if code != 'en_US']
    if langs:
        env['ir.module.module']._load_module_terms(['personal_budget'], langs, overwrite=True)
    _logger.info("personal_budget : source anglaise posée, catalogues rechargés pour %s", langs)
