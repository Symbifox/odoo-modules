"""Déménagement du coffre d'identifiants vers ``bf_credentials``.

Même mécanique qu'à la 18.0.12.0.0 pour la gouvernance corporative, et même
raison : à la fin d'un chargement, Odoo supprime les enregistrements dont
l'``ir.model.data`` appartient à un module mis à jour mais que ses fichiers de
données ne nomment plus. La 13.0.0 ne nomme plus rien du coffre : sans cette
passe, les trois ``ir.model`` seraient effacés, et avec eux les 94
``ir.model.fields`` qui en dépendent.

Ce que ce déménagement-ci a de particulier
------------------------------------------
1. **Les valeurs sont chiffrées.** La clé vit dans ``ir.config_parameter`` sous
   ``project_credential.encryption_key`` et ne bouge pas : les jetons Fernet
   restent lisibles. C'est éprouvé, pas supposé —
   ``bf_credentials/tests/test_extraction.py`` relit chaque secret en base et
   refuse de passer si l'un d'eux redevient son propre jeton, ce que
   ``_decrypt_value`` rend SILENCIEUSEMENT quand la clé ne correspond pas.

2. **Le socle portait un champ typé vers le coffre.**
   ``project.project.credential_ids`` est un ``One2many`` vers
   ``project.credential`` : tant qu'il restait dans le socle, la dépendance
   était circulaire. Son identifiant externe déménage donc aussi. Un
   ``One2many`` n'a pas de colonne — rien à déplacer en base, seulement son
   appartenance.

3. **Aucun module dépendant à reprendre.** Quatre modules du locataire citent
   ``project.credential`` — ``bf_home``, ``hosting_management``,
   ``privacy_consent`` et ``bf_universal_search`` — mais tous par une garde de
   registre ou une simple chaîne, jamais par un champ typé ni un identifiant
   externe. Contrairement, il n'y a pas de ``depends`` à
   changer.
"""

import logging

_logger = logging.getLogger(__name__)

NOUVEAU = 'bf_credentials'
ANCIEN = 'project_knowledge_matrix'

# Enregistrements nommés dans les fichiers de données. Miroir de ce que
# bf_credentials déclare : son test
# `test_extraction.py::test_the_migration_moved_everything_the_module_declares`
# échoue si les deux divergent, dans un sens comme dans l'autre.
XMLIDS = [
    # security/credential_security.xml
    'module_category_credentials',
    'group_credential_user',
    'group_credential_manager',
    'rule_credential_user',
    'rule_credential_manager',
    'rule_credential_type_read',
    'rule_credential_type_manager',
    # security/ir.model.access.csv
    'access_credential_user',
    'access_credential_manager',
    'access_credential_type_user',
    'access_credential_type_manager',
    'access_credential_rotate_wizard_user',
    'access_credential_rotate_wizard_manager',
    # data/credential_type_data.xml
    'credential_type_ad',
    'credential_type_api',
    'credential_type_app',
    'credential_type_cloud',
    'credential_type_db',
    'credential_type_email',
    'credential_type_other',
    'credential_type_server',
    'credential_type_vpn',
    # data/credential_cron.xml — le cron ET l'action serveur qu'Odoo lui crée
    'ir_cron_check_expiring_credentials',
    'ir_cron_check_expiring_credentials_ir_actions_server',
    # views/credential_views.xml
    'credential_view_form',
    'credential_view_list',
    'credential_view_kanban',
    'credential_view_search',
    'credential_action',
    'credential_rotate_wizard_view_form',
    # views/credential_type_views.xml
    'credential_type_view_form',
    'credential_type_view_list',
    'credential_type_action',
    # views/report_drilldown_actions.xml — attention, « cred » et non
    # « credential » : un motif en %credential% les manque tous les trois.
    'report_action_cred_active',
    'report_action_cred_expiring',
    'report_action_cred_expired',
    # views/credential_menus.xml
    'menu_credentials',
    'menu_credential_types',
]

# Ce qu'Odoo engendre en reflétant le code : 3 modèles, 94 champs, 13 valeurs
# de sélection, 2 héritages sur la production Blue Fox. Les deux derniers
# motifs sont les champs que le coffre posait sur project.project.
MOTIFS = [
    'model_project_credential%',
    'field_project_credential%',
    'selection__project_credential%',
    'model_inherit__project_credential%',
    'constraint_project_credential%',
    'field_project_project__credential_ids',
    'field_project_project__credential_count',
]

# De la feuille vers la racine : l'ordre du DROP dans le cas 2.
# Le motif des modèles du sous-système, employé partout où l'on interroge
# ir_model plutôt que les tables elles-mêmes.
MOTIF_MODELES = 'project.credential%'

TABLES = [
    'project_credential_rotate_wizard',
    'project_credential',
    'project_credential_type',
]

# Ce qui doit avoir quitté project_knowledge_matrix quand la passe a fini.
# « cred » et non « credential » : sous ce module, les seuls noms en « cred »
# qui ne disent pas « credential » sont justement les trois actions de forage.
RESTES = """
    SELECT name FROM ir_model_data
     WHERE module = %s AND name ILIKE '%%cred%%'
"""


# Les neuf types livrés par data/credential_type_data.xml. Ils existent sur TOUT
# locataire qui a installé le module, qu'il range des secrets ou non : les
# compter comme des données ferait refuser la passe chez quelqu'un qui n'a
# jamais ouvert le coffre.
#
# Un type devient une donnée dès qu'un identifiant s'y rattache. C'est le seul
# usage qu'on puisse en faire.
SEMENCES = """
    SELECT count(*) FROM project_credential_type t
      JOIN ir_model_data d
        ON d.model = 'project.credential.type' AND d.res_id = t.id
     WHERE d.module = %s
       AND NOT EXISTS (SELECT 1 FROM project_credential c WHERE c.type_id = t.id)
"""


def _compter_donnees(cr):
    """Enregistrements du coffre, par table, GRAINES DÉDUITES.

    Ce qu'on protège ici, c'est le travail de quelqu'un — pas les valeurs par
    défaut livrées avec le module. Un locataire qui n'a jamais rangé de secret
    porte quand même les neuf types, et refuser sa mise à niveau pour ça
    reviendrait à le geler sur une version pour des données qui ne sont pas les
    siennes.
    """
    comptes = {}
    for table in TABLES:
        cr.execute("SELECT to_regclass(%s)", ('public.%s' % table,))
        if not cr.fetchone()[0]:
            continue
        cr.execute('SELECT count(*) FROM "%s"' % table)
        total = cr.fetchone()[0]
        if table == 'project_credential_type' and total:
            cr.execute(SEMENCES, (ANCIEN,))
            total -= cr.fetchone()[0]
        comptes[table] = total
    return comptes


def _autres_modules_etendent(cr):
    cr.execute("""
        SELECT DISTINCT d.module FROM ir_model_data d
          JOIN ir_model m ON m.id = d.res_id
         WHERE d.model = 'ir.model' AND m.model LIKE 'project.credential%%'
           AND d.module <> %s
        UNION
        SELECT DISTINCT d.module FROM ir_model_data d
          JOIN ir_model_fields f ON f.id = d.res_id
         WHERE d.model = 'ir.model.fields' AND f.model LIKE 'project.credential%%'
           AND d.module NOT IN (%s, 'mail', 'base', 'calendar', 'bus', 'web')
    """, (ANCIEN, ANCIEN))
    return sorted(module for (module,) in cr.fetchall())


def _reattribuer(cr, module_id):
    cr.execute(
        "UPDATE ir_model_data SET module = %s WHERE module = %s AND name = ANY(%s)",
        (NOUVEAU, ANCIEN, XMLIDS),
    )
    declares = cr.rowcount

    reflets = 0
    for motif in MOTIFS:
        cr.execute(
            "UPDATE ir_model_data SET module = %s WHERE module = %s AND name LIKE %s",
            (NOUVEAU, ANCIEN, motif),
        )
        reflets += cr.rowcount

    cr.execute(
        """UPDATE ir_model_constraint SET module = %s
            WHERE module = (SELECT id FROM ir_module_module WHERE name = %s)
              AND name LIKE 'project\\_credential%%'""",
        (module_id, ANCIEN),
    )
    contraintes = cr.rowcount

    _logger.info(
        "Coffre d'identifiants -> %s : %s identifiants déclarés, %s reflétés, "
        "%s contraintes.", NOUVEAU, declares, reflets, contraintes,
    )

    cr.execute(RESTES, (ANCIEN,))
    restes = [nom for (nom,) in cr.fetchall()]
    if restes:
        raise Exception(
            "Réattribution incomplète : %s identifiants du coffre sont restés "
            "sous %s. Les voici : %s. Compléter XMLIDS ou MOTIFS dans "
            "migrations/18.0.13.0.0/pre-migrate.py avant de reprendre."
            % (len(restes), ANCIEN, ', '.join(sorted(restes)[:20]))
        )


def _cascades_depuis_ir_model(cr, motif):
    """Ce que la suppression des ``ir.model`` emporterait par CASCADE.

    D'autres modules pointent un modèle par une clé étrangère vers ``ir_model``,
    et la plupart de ces clés sont en CASCADE : supprimer le modèle emporte la
    ligne SANS un mot. Sur certaines bases, c'est une config de recherche
    universelle qui nomme ``corporate.resolution``.

    La ligne, elle, doit bien partir — le modèle qu'elle désigne n'existe plus.
    Ce qui ne doit PAS rester, c'est son identifiant externe, qui pointerait
    alors dans le vide : le module qui l'a créé le retrouve, en conclut que sa
    config existe déjà, et ne la recrée jamais.

    Découvert par le schéma plutôt que par une liste de modules : la question
    « qui pointe vers ir_model » se pose à Postgres, elle ne se devine pas.
    """
    cr.execute("""
        SELECT tc.table_name, kcu.column_name
          FROM information_schema.table_constraints tc
          JOIN information_schema.key_column_usage kcu
            ON kcu.constraint_name = tc.constraint_name
          JOIN information_schema.constraint_column_usage ccu
            ON ccu.constraint_name = tc.constraint_name
          JOIN information_schema.referential_constraints rc
            ON rc.constraint_name = tc.constraint_name
         WHERE tc.constraint_type = 'FOREIGN KEY'
           AND ccu.table_name = 'ir_model'
           AND rc.delete_rule = 'CASCADE'
           AND tc.table_name NOT LIKE 'ir\\_%'
    """)
    emportes = []
    for table, colonne in cr.fetchall():
        cr.execute("SELECT to_regclass(%s)", ('public.%s' % table,))
        if not cr.fetchone()[0]:
            continue
        # Les tables de relation many2many n'ont pas de colonne `id` — et pas
        # d'identifiant externe non plus : rien à retirer, la CASCADE fait le
        # travail. Le banc l'a montré sur bf_claude_instruction_model_rel, où
        # le SELECT id échouait et annulait toute la mise à niveau.
        cr.execute("""
            SELECT 1 FROM information_schema.columns
             WHERE table_name = %s AND column_name = 'id'
        """, (table,))
        if not cr.fetchone():
            continue
        cr.execute(
            'SELECT id FROM "%s" WHERE "%s" IN '
            '(SELECT id FROM ir_model WHERE model LIKE %%s)' % (table, colonne),
            (motif,),
        )
        ids = [i for (i,) in cr.fetchall()]
        if ids:
            emportes.append((table, ids))
    return emportes


def _retirer_identifiants_des_cascades(cr, motif):
    """Retirer les identifiants externes de ce que la CASCADE va emporter."""
    total = 0
    for table, ids in _cascades_depuis_ir_model(cr, motif):
        modele = table.replace('_', '.')
        cr.execute(
            "DELETE FROM ir_model_data WHERE model = %s AND res_id = ANY(%s)",
            (modele, ids),
        )
        total += cr.rowcount
        _logger.info(
            "Emporté par CASCADE : %s ligne(s) de %s, %s identifiant(s) externe(s).",
            len(ids), table, cr.rowcount,
        )
    return total

def _retirer(cr, comptes):
    """Retrait propre quand le coffre n'a jamais servi.

    Odoo effacerait les ``ir.model`` sans toucher aux tables : le modèle n'est
    plus au registre au moment où il les supprime, donc son ``_drop_table`` ne
    trouve rien. Trois tables resteraient derrière, invisibles.

    La clé de chiffrement reste en paramètre système : elle ne coûte rien, et
    l'effacer rendrait irrécupérable une base restaurée d'une sauvegarde
    antérieure.
    """
    cr.execute(RESTES, (ANCIEN,))
    noms = [nom for (nom,) in cr.fetchall()]
    if noms:
        cr.execute(
            "DELETE FROM ir_model_data WHERE module = %s AND name = ANY(%s)",
            (ANCIEN, noms),
        )
    cr.execute("""
        DELETE FROM ir_model_data
         WHERE model = 'ir.model.fields.selection'
           AND res_id IN (SELECT s.id FROM ir_model_fields_selection s
                            JOIN ir_model_fields f ON f.id = s.field_id
                           WHERE f.model LIKE 'project.credential%')
    """)
    cr.execute("""
        DELETE FROM ir_model_data
         WHERE model = 'ir.model.fields'
           AND res_id IN (SELECT id FROM ir_model_fields
                           WHERE model LIKE 'project.credential%'
                              OR (model = 'project.project'
                                  AND name IN ('credential_ids', 'credential_count')))
    """)
    cr.execute("""
        DELETE FROM ir_model_data
         WHERE model = 'ir.model.inherit'
           AND name LIKE 'model_inherit__project\\_credential%'
    """)
    cr.execute("""
        DELETE FROM ir_model_data
         WHERE model = 'ir.model'
           AND res_id IN (SELECT id FROM ir_model WHERE model LIKE 'project.credential%')
    """)
    cr.execute("""
        DELETE FROM ir_model_constraint
         WHERE module = (SELECT id FROM ir_module_module WHERE name = %s)
           AND name LIKE 'project\\_credential%%'
    """, (ANCIEN,))
    cr.execute("""
        DELETE FROM ir_model_fields
         WHERE model = 'project.project'
           AND name IN ('credential_ids', 'credential_count')
    """)
    # ir_model_fields part en cascade depuis ir_model.
    # Les tâches planifiées du sous-système, AVANT les modèles.
    #
    # Supprimer un `ir.model` emporte par CASCADE l'action serveur qui le
    # nomme — mais `ir_cron.ir_actions_server_id` référence cette action en
    # RESTRICT, et bloque la suppression. La CASCADE ne traverse pas la chaîne :
    # elle s'arrête sur la première clé qui refuse. Retirer le cron d'abord
    # libère l'action, qui part ensuite avec son modèle.
    cr.execute("""
        DELETE FROM ir_cron WHERE ir_actions_server_id IN (
            SELECT id FROM ir_act_server WHERE model_id IN (
                SELECT id FROM ir_model WHERE model LIKE %s))
    """, (MOTIF_MODELES,))
    if cr.rowcount:
        _logger.info("Tâches planifiées retirées : %s.", cr.rowcount)

    _retirer_identifiants_des_cascades(cr, MOTIF_MODELES)
    cr.execute("DELETE FROM ir_model WHERE model LIKE 'project.credential%'")
    for table in TABLES:
        cr.execute('DROP TABLE IF EXISTS "%s" CASCADE' % table)
    _logger.info(
        "Coffre d'identifiants retiré : %s absent du disque et aucune donnée "
        "hors graines (%s). Trois tables et leurs métadonnées sont parties, y "
        "compris les neuf types livrés par le module ; la clé de chiffrement "
        "reste en paramètre système.", NOUVEAU, comptes,
    )


def migrate(cr, version):
    if not version:
        return

    cr.execute("SELECT id, state FROM ir_module_module WHERE name = %s", (NOUVEAU,))
    ligne = cr.fetchone()
    comptes = _compter_donnees(cr)

    if not ligne:
        if any(comptes.values()):
            raise Exception(
                "project_knowledge_matrix 18.0.13.0.0 sort le coffre "
                "d'identifiants du module, et cette base en porte : %s. Le "
                "module %s doit être présent dans l'addons_path et installé "
                "dans la même passe, sinon ces secrets seraient perdus. "
                "Commande : -u project_knowledge_matrix -i %s"
                % (comptes, NOUVEAU, NOUVEAU)
            )
        etendeurs = _autres_modules_etendent(cr)
        if etendeurs:
            raise Exception(
                "Les tables du coffre sont vides, mais %s étend(ent) encore "
                "ces modèles : les retirer les casserait. Déployer %s, ou "
                "désinstaller ces modules d'abord."
                % (', '.join(etendeurs), NOUVEAU)
            )
        _retirer(cr, comptes)
        return

    module_id, etat = ligne
    if etat == 'uninstalled':
        cr.execute(
            "UPDATE ir_module_module SET state = 'to install' WHERE id = %s",
            (module_id,),
        )
        _logger.info("%s passe de « uninstalled » à « to install ».", NOUVEAU)
    elif etat not in ('to install', 'to upgrade', 'installed'):
        raise Exception(
            "%s est à l'état « %s » : le coffre n'aurait où aller. Installer "
            "le module dans la même passe." % (NOUVEAU, etat)
        )

    _reattribuer(cr, module_id)
