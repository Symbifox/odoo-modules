# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Repose les liaisons optionnelles que la mise à jour retire.

Les deux actions « Réordonner ce chatter par date » des rencontres vivaient
dans `data/actions.xml` avec un xmlid de ce module. En les sortant des données
(pour supprimer la dépendance dure vers `bf_meeting`, cf. `hooks.py`), elles
deviennent des orphelins que le nettoyage de fin de mise à jour supprime.

⚠️ ORDRE D'EXÉCUTION. Cette migration tourne AVANT ce nettoyage. Se contenter
d'appeler `_ensure_optional_bindings` ne suffit donc pas : la garde
d'idempotence voit encore les anciens enregistrements, ne crée rien, et le
nettoyage emporte tout ensuite. Constaté sur une vraie mise à jour, où les
liaisons sont passées de 8 à 6 en silence.

On retire donc explicitement les anciens enregistrements et leur `ir.model.data`
AVANT de reposer les nouveaux, qui eux n'ont pas de xmlid et survivent au
nettoyage.
"""

from odoo.addons.bf_chatter_chronological.hooks import _ensure_optional_bindings

ANCIENS = (
    "bf_chatter_chronological.action_chatter_chrono_meeting_record",
    "bf_chatter_chronological.action_chatter_chrono_meeting_agenda",
)


def migrate(cr, version):
    if not version:
        return
    from odoo import SUPERUSER_ID, api
    env = api.Environment(cr, SUPERUSER_ID, {})
    for xmlid in ANCIENS:
        rec = env.ref(xmlid, raise_if_not_found=False)
        if not rec:
            continue
        module, nom = xmlid.split(".", 1)
        env["ir.model.data"].search([
            ("module", "=", module), ("name", "=", nom),
        ]).unlink()
        rec.unlink()
    _ensure_optional_bindings(env)
