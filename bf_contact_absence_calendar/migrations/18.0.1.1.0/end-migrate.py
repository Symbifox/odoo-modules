"""18.0.1.1.0 : les libellés passent d'une source française à une source anglaise.

🔴 Une mise à jour d'Odoo n'écrase JAMAIS une traduction déjà en base. Sur une
base installée avant, le catalogue fr_CA était une identité : il a posé des
valeurs que le nouveau catalogue ne remplace pas de lui-même (les libellés hérités
d'Odoo, « Created by » et les autres, restaient anglais pour un usager français).
On recharge donc le catalogue de ce module en écrasant.

Le travail planifié est une donnée `noupdate` : la mise à jour ne récrit pas son
nom, qui resterait français en en_US. On le bascule SEULEMENT s'il porte encore
le nom livré.

Un nom retouché en français, lui, survit au rechargement : pour une donnée
`noupdate` traduite d'un seul tenant, Odoo n'écrase jamais la traduction
existante, même en forçant. Vérifié au banc sur une copie retouchée.

⚠️ En `end` et non en `post` : Odoo charge les traductions du module juste
après le `post`, sans écraser, et c'est ce passage qui doit venir en dernier.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

MODULE = "bf_contact_absence_calendar"
CRON = "bf_contact_absence_calendar.cron_bf_absence_calendar"
NOM_LIVRE_FR = "Absences des contacts : lire le calendrier"
NOM_EN = "Contact absences: read the calendar"


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    cron = env.ref(CRON, raise_if_not_found=False)
    if cron:
        if cron.with_context(lang="en_US").name == NOM_LIVRE_FR:
            cron.with_context(lang="en_US").write({"name": NOM_EN})
        else:
            _logger.info("%s : travail planifié renommé à la main, laissé tel quel", MODULE)
    langues = [code for code, _nom in env["res.lang"].get_installed() if code != "en_US"]
    if langues:
        env["ir.module.module"]._load_module_terms([MODULE], langues, overwrite=True)

