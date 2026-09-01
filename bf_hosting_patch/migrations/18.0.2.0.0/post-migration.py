# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""1.0.0 -> 2.0.0 : l'état de mise à jour descend du poste vers ses systèmes.

Le parc est presque tout en double amorçage. Une fiche de parc décrit
désormais la MACHINE, et chaque système installé a sa ligne. Cette migration
transporte l'état relevé en 1.0.0 vers un premier système par poste suivi.

⚠️ Le jeton est transporté tel quel (c'est une empreinte SHA-256) : sans ça,
les agents déjà enrôlés seraient refusés au prochain relevé et les machines
deviendraient muettes sans que personne comprenne pourquoi.

⚠️ Les colonnes de 1.0.0 sont supprimées par Odoo lui-même juste après ce
script, parce que les champs ne sont plus déclarés par le module. Ce script est
donc la SEULE occasion de les lire : il n'est pas rejouable.

⚠️ L'insertion se fait en SQL brut, donc AUCUN calcul stocké ne tourne. Sans le
recalcul explicite de la fin, les systèmes migrés naîtraient avec un
`patch_state` vide et seraient invisibles dans toutes les vues filtrées, sans
la moindre erreur pour le signaler.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

# (colonne sur hosting_endpoint, colonne sur bf_patch_system)
MOVED = [
    ("machine_id", "machine_id"),
    ("agent_token_hash", "agent_token_hash"),
    ("agent_last_report", "agent_last_report"),
    ("agent_version", "agent_version"),
    ("os_release", "os_release"),
    ("kernel_running", "kernel_running"),
    ("kernel_installed", "kernel_installed"),
    ("boot_time", "boot_time"),
    ("reboot_required", "reboot_required"),
    ("reboot_pending_since", "reboot_pending_since"),
    ("reboot_packages", "reboot_packages"),
    ("package_manager", "package_manager"),
    ("pending_known", "pending_known"),
    ("pending_count", "pending_count"),
    ("pending_security_count", "pending_security_count"),
    ("pending_delta", "pending_delta"),
    ("auto_update_mode", "auto_update_mode"),
    ("auto_update_detail", "auto_update_detail"),
    ("disk_root_pct", "disk_root_pct"),
    ("disk_boot_pct", "disk_boot_pct"),
    ("os_support_end", "os_support_end"),
    ("os_support_state", "os_support_state"),
]


def _existing(cr, table, columns):
    cr.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = %s", (table,),
    )
    present = {row[0] for row in cr.fetchall()}
    return [c for c in columns if c in present]


def migrate(cr, version):
    if not version:
        return

    src = _existing(cr, "hosting_endpoint", [old for old, _new in MOVED])
    if "machine_id" not in src:
        _logger.info("bf_hosting_patch : rien à migrer, colonnes 1.0.0 absentes")
        return
    dst = [new for old, new in MOVED if old in src]

    # Un système par poste QUI EN AVAIT UN en 1.0.0 : soit un machine-id, soit
    # au moins un relevé. ⚠️ Ne PAS se limiter aux postes encore suivis : un
    # agent révoqué garde son historique, et le laisser dehors orpheline ses
    # relevés. `patch_managed` est recopié, jamais forcé.
    cr.execute(
        f"""
        INSERT INTO bf_patch_system
            (endpoint_id, name, hostname, os_family, patch_managed, active,
             {', '.join(dst)}, create_uid, write_uid, create_date, write_date)
        SELECT e.id,
               COALESCE(NULLIF(e.hostname, ''), e.name),
               e.hostname,
               'linux',
               COALESCE(e.patch_managed, FALSE),
               TRUE,
               {', '.join('e.' + c for c in src)},
               1, 1, now(), now()
          FROM hosting_endpoint e
         WHERE (e.machine_id IS NOT NULL
                OR EXISTS (SELECT 1 FROM bf_patch_report r
                            WHERE r.endpoint_id = e.id))
           AND NOT EXISTS (SELECT 1 FROM bf_patch_system s
                            WHERE s.endpoint_id = e.id)
        RETURNING id
        """
    )
    created = len(cr.fetchall())

    # Les relevés suivent leur système. Sans ça, `system_id` resterait NULL et
    # chaque relevé deviendrait illisible.
    cr.execute(
        """
        UPDATE bf_patch_report r
           SET system_id = s.id
          FROM bf_patch_system s
         WHERE s.endpoint_id = r.endpoint_id
           AND r.system_id IS NULL
        """
    )
    moved = cr.rowcount

    cr.execute("SELECT count(*) FROM bf_patch_report WHERE system_id IS NULL")
    orphans = cr.fetchone()[0]
    if orphans:
        # Ne devrait plus arriver : tout poste portant un relevé reçoit un
        # système. Si ça arrive quand même, on le dit fort plutôt que de
        # supprimer de l'historique en silence.
        _logger.error(
            "bf_hosting_patch : %d relevé(s) sans système après migration. "
            "Ils sont CONSERVÉS mais invisibles : à rattacher à la main.",
            orphans,
        )

    # 🔴 Le SQL brut ne déclenche aucun calcul stocké. Il faut rejouer
    # `patch_state` et `disk_tight` à la main, sinon les systèmes migrés
    # restent NULL et disparaissent des filtres en silence.
    env = api.Environment(cr, SUPERUSER_ID, {})
    systems = env["bf.patch.system"].search([("patch_state", "=", False)])
    if systems:
        systems.modified(["patch_managed", "agent_last_report", "pending_known",
                          "pending_security_count", "reboot_required",
                          "pending_count", "disk_root_pct", "disk_boot_pct"])
        systems.flush_recordset(["patch_state", "disk_tight"])
    still_null = env["bf.patch.system"].search_count([("patch_state", "=", False)])

    _logger.info(
        "bf_hosting_patch 2.0.0 : %d système(s) créé(s), %d relevé(s) "
        "rattaché(s), %d orphelin(s), %d état(s) recalculé(s).",
        created, moved, orphans, len(systems),
    )
    if still_null:
        _logger.error(
            "bf_hosting_patch : %d système(s) restent sans état après "
            "recalcul : ils seront invisibles dans les vues filtrées.",
            still_null,
        )
