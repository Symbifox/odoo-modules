# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Fait passer le rafraîchissement des indicateurs d'échéance à l'heure.

Le cron « Rafraîchir les indicateurs d'échéance » tournait une fois par jour, à
l'heure où Odoo l'avait ancré au moment de l'installation. Or ``days_until_due``
et ``is_overdue`` se comparent à ``fields.Date.today()``, qui bascule à minuit
dans le fuseau du serveur : entre deux passages, les valeurs sont fausses d'un
jour entier. Selon l'heure d'ancrage, un passage quotidien peut donc n'être juste
qu'une fraction de la journée.

Recaler l'heure ne serait pas portable : ``nextcall`` n'avance que par pas de
24 h UTC, il glisse au changement d'heure, et le bon créneau dépend du fuseau du
conteneur, dont le module ne sait rien. Le passage horaire borne l'écart à une
heure partout.

``data/hosting_cron.xml`` est ``noupdate="1"`` : il ne sert que les installations
neuves. C'est ce script qui reprend les bases déjà installées.
"""
from datetime import timedelta

from odoo import SUPERUSER_ID, api, fields


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    cron = env.ref(
        "hosting_management.ir_cron_hosting_maintenance_refresh_due",
        raise_if_not_found=False,
    )
    if cron:
        # nextcall est repoussé lui aussi : laissé tel quel, il pourrait encore
        # être à près de 24 h, et la nouvelle cadence ne prendrait effet
        # qu'après ce dernier passage tardif.
        cron.write({
            "interval_number": 1,
            "interval_type": "hours",
            "nextcall": fields.Datetime.now() + timedelta(hours=1),
        })

    # Sans ce rappel, les valeurs resteraient périmées jusqu'au passage suivant.
    env["hosting.maintenance.schedule"]._cron_refresh_due_indicators()
