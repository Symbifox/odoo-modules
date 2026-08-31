"""Déménagement de la gouvernance corporative vers ``bf_corporate_governance``.

Cinq modèles, 37 résolutions, deux registres et un calendrier de conformité
sortent du module. Rien n'est recréé : les tables restent en place et leurs
identifiants externes CHANGENT DE MODULE. Une base qui portait déjà des
résolutions les retrouve à l'identique, numéro de séquence compris.

Pourquoi en PRE et pas en post
------------------------------
À la fin d'un chargement, Odoo supprime les enregistrements dont l'``ir.model.data``
appartient à un module mis à jour mais que ses fichiers de données ne nomment
plus (``ir.model.data._process_end``). La 18.0.12.0.0 ne nomme plus rien de
corporatif : sans cette passe, les cinq ``ir.model`` seraient effacés, et avec
eux les 173 ``ir.model.fields`` qui en dépendent. La réattribution doit donc
avoir eu lieu AVANT que le module ne se charge.

Ce que la passe couvre
----------------------
Les enregistrements déclarés dans les fichiers de données (liste explicite) et
ceux qu'Odoo engendre tout seul en reflétant le code (motifs) : modèles, champs,
valeurs de sélection, héritages, contraintes et relations many2many.

Trois cas, un seul dénouement par base
--------------------------------------
1. ``bf_corporate_governance`` est là — on réattribue. C'est le cas de Blue Fox.
2. Il n'est pas là et aucune donnée corporative n'existe — on retire proprement
   les tables vides plutôt que de laisser Odoo effacer les ``ir.model`` sans
   toucher aux tables, ce qui laisserait des tables orphelines. C'est le cas
   attendu chez les locataires qui n'ont jamais tenu de livre de minutes.
3. Il n'est pas là et des données existent — on refuse. Perdre 37 résolutions
   parce qu'un module manque sur le disque n'est pas un dénouement.
"""

import logging

_logger = logging.getLogger(__name__)

NOUVEAU = 'bf_corporate_governance'
ANCIEN = 'project_knowledge_matrix'

# Enregistrements nommés dans les fichiers de données. Cette liste est le miroir
# de ce que bf_corporate_governance déclare : son test
# `test_extraction.py::test_the_migration_moved_everything_the_module_declares`
# échoue si les deux divergent.
XMLIDS = [
    # security/corporate_security.xml
    'module_category_corporate',
    'group_corporate_manager',
    # security/ir.model.access.csv
    'access_corporate_resolution_user',
    'access_corporate_resolution_manager',
    'access_corporate_resolution_signatory_user',
    'access_corporate_resolution_signatory_manager',
    'access_corporate_director_user',
    'access_corporate_director_manager',
    'access_corporate_officer_user',
    'access_corporate_officer_manager',
    'access_corporate_compliance_user',
    'access_corporate_compliance_manager',
    # data/corporate_compliance_data.xml
    'compliance_annual_declaration',
    'compliance_agm',
    'compliance_director_election',
    'compliance_auditor',
    'compliance_financial_approval',
    # data/corporate_cron.xml — le cron ET l'action serveur qu'Odoo lui crée
    'cron_corporate_compliance_check',
    'cron_corporate_compliance_check_ir_actions_server',
    # report/corporate_resolution_templates.xml
    'paperformat_resolution',
    'action_report_corporate_resolution',
    'report_corporate_resolution',
    # views/corporate_resolution_views.xml
    'seq_corporate_resolution',
    'corporate_resolution_view_form',
    'corporate_resolution_view_list',
    'corporate_resolution_view_search',
    'corporate_resolution_action',
    # views/corporate_director_views.xml
    'corporate_director_view_form',
    'corporate_director_view_list',
    'corporate_director_view_search',
    'corporate_director_action',
    # views/corporate_officer_views.xml
    'corporate_officer_view_form',
    'corporate_officer_view_list',
    'corporate_officer_view_search',
    'corporate_officer_action',
    # views/corporate_compliance_views.xml
    'corporate_compliance_view_form',
    'corporate_compliance_view_list',
    'corporate_compliance_view_calendar',
    'corporate_compliance_view_search',
    'corporate_compliance_action',
    'corporate_minute_book_action',
    # views/corporate_menus.xml
    'menu_corporate_parent',
    'menu_corporate_resolutions',
    'menu_corporate_directors',
    'menu_corporate_officers',
    'menu_corporate_compliance',
    'menu_corporate_minute_book',
    # views/report_drilldown_actions.xml
    'report_action_corp_directors',
    'report_action_corp_officers',
    'report_action_corp_resolutions_adopted',
    'report_action_corp_compliance_overdue',
    'report_action_corp_compliance_due_soon',
]

# Ce qu'Odoo engendre en reflétant le code, et qui ne se liste pas à la main :
# 5 modèles, 173 champs, 53 valeurs de sélection, 6 héritages sur la production
# Blue Fox. Le champ `minute_book_section` de project.document suit la seule
# chose qui le lit — l'action « Livre des minutes ».
MOTIFS = [
    'model_corporate_%',
    'field_corporate_%',
    'selection__corporate_%',
    'model_inherit__corporate_%',
    'field_project_document__minute_book_section',
    'selection__project_document__minute_book_section__%',
]

# Tables du sous-système, de la feuille vers la racine : l'ordre du DROP dans le
# cas 2, et l'ordre du comptage dans les trois cas.
# Le motif des modèles du sous-système, employé partout où l'on interroge
# ir_model plutôt que les tables elles-mêmes.
MOTIF_MODELES = 'corporate.%'

TABLES = [
    'corporate_resolution_signatory',
    'corporate_compliance_event',
    'corporate_officer',
    'corporate_director',
    'corporate_resolution_attachment_rel',
    'corporate_resolution_document_rel',
    'corporate_resolution',
]

# Ce qui doit avoir quitté project_knowledge_matrix quand la passe a fini.
# Volontairement plus large que ce qui est déplacé : un contrôle trop large qui
# passe prouve plus qu'un contrôle taillé sur la liste qu'il vérifie.
RESTES = """
    SELECT name FROM ir_model_data
     WHERE module = %s
       AND (name ILIKE '%%corporate%%'
            OR name ILIKE '%%corp\\_%%'
            OR name ILIKE '%%minute\\_book%%'
            OR name LIKE 'compliance\\_%%'
            OR name = 'paperformat_resolution')
"""


# Les cinq échéances livrées par data/corporate_compliance_data.xml. Elles
# existent sur TOUT locataire qui a installé le module, qu'il tienne un livre de
# minutes ou non : les compter comme des données ferait refuser la passe chez
# quelqu'un qui n'a jamais ouvert la fonction.
#
# Une graine devient une donnée dès qu'une PERSONNE s'en est servie. Pour une
# échéance, ça se voit à deux colonnes : la date de complétion et la référence
# de dépôt. Sur Blue Fox, la déclaration annuelle 2026 porte les deux, avec un
# vrai numéro de confirmation du Registraire.
#
# `reminder_sent` en est écarté à dessein, et c'est le contrôle qui l'a montré :
# sur une autre base les cinq échéances le portent, alors qu'AUCUNE activité n'a
# jamais été créée — le groupe gestionnaire n'y a aucun usager. La tâche
# quotidienne pose le drapeau de toute façon. C'est une trace de machine, pas
# d'usage, et la retenir aurait bloqué la mise à niveau d'un locataire qui n'a
# jamais ouvert la fonction.
SEMENCES = """
    SELECT count(*) FROM corporate_compliance_event e
      JOIN ir_model_data d
        ON d.model = 'corporate.compliance.event' AND d.res_id = e.id
     WHERE d.module = %s
       AND e.completed_date IS NULL
       AND (e.filing_reference IS NULL OR e.filing_reference = '')
"""


def _compter_donnees(cr):
    """Enregistrements corporatifs présents, par table, GRAINES DÉDUITES.

    Tables absentes ignorées. Une table dont tout le contenu vient des fichiers
    de données du module et n'a jamais servi compte pour zéro : ce qu'on protège
    ici, c'est le travail de quelqu'un, pas les valeurs par défaut livrées avec
    le module.
    """
    comptes = {}
    for table in TABLES:
        cr.execute("SELECT to_regclass(%s)", ('public.%s' % table,))
        if not cr.fetchone()[0]:
            continue
        cr.execute('SELECT count(*) FROM "%s"' % table)
        total = cr.fetchone()[0]
        if table == 'corporate_compliance_event' and total:
            cr.execute(SEMENCES, (ANCIEN,))
            total -= cr.fetchone()[0]
        comptes[table] = total
    cr.execute("""
        SELECT count(*) FROM information_schema.columns
         WHERE table_name = 'project_document'
           AND column_name = 'minute_book_section'
    """)
    if cr.fetchone()[0]:
        cr.execute("""
            SELECT count(*) FROM project_document
             WHERE minute_book_section IS NOT NULL
        """)
        comptes['project_document.minute_book_section'] = cr.fetchone()[0]
    return comptes


def _reattribuer(cr, module_id):
    """Faire changer de module les identifiants externes, pas les données."""
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

    # Contraintes et relations many2many : leur `module` est une clé étrangère
    # vers ir_module_module, pas un texte. Les laisser derrière ne casse rien
    # tant que project_knowledge_matrix reste installé, mais sa désinstallation
    # emporterait alors les tables d'un module qui, lui, reste.
    cr.execute(
        """UPDATE ir_model_constraint SET module = %s
            WHERE module = (SELECT id FROM ir_module_module WHERE name = %s)
              AND name LIKE 'corporate\\_%%'""",
        (module_id, ANCIEN),
    )
    contraintes = cr.rowcount
    cr.execute(
        """UPDATE ir_model_relation SET module = %s
            WHERE module = (SELECT id FROM ir_module_module WHERE name = %s)
              AND name LIKE 'corporate\\_%%'""",
        (module_id, ANCIEN),
    )
    relations = cr.rowcount

    _logger.info(
        "Gouvernance corporative -> %s : %s identifiants déclarés, %s reflétés, "
        "%s contraintes, %s relations.",
        NOUVEAU, declares, reflets, contraintes, relations,
    )

    cr.execute(RESTES, (ANCIEN,))
    restes = [nom for (nom,) in cr.fetchall()]
    if restes:
        raise Exception(
            "Réattribution incomplète : %s identifiants corporatifs sont restés "
            "sous %s. Les voici : %s. Compléter XMLIDS ou MOTIFS dans "
            "migrations/18.0.12.0.0/pre-migrate.py avant de reprendre."
            % (len(restes), ANCIEN, ', '.join(sorted(restes)[:20]))
        )


def _autres_modules_etendent(cr):
    """Modules, autres que le socle, qui possèdent un identifiant sur un modèle
    corporatif — signe qu'ils l'étendent et qu'un retrait les casserait."""
    cr.execute("""
        SELECT DISTINCT d.module FROM ir_model_data d
          JOIN ir_model m ON m.id = d.res_id
         WHERE d.model = 'ir.model' AND m.model LIKE 'corporate.%%'
           AND d.module <> %s
        UNION
        SELECT DISTINCT d.module FROM ir_model_data d
          JOIN ir_model_fields f ON f.id = d.res_id
         WHERE d.model = 'ir.model.fields' AND f.model LIKE 'corporate.%%'
           AND d.module NOT IN (%s, 'mail', 'base', 'calendar', 'bus', 'web')
    """, (ANCIEN, ANCIEN))
    return sorted(module for (module,) in cr.fetchall())


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
    """Retrait propre quand le sous-système n'a jamais servi.

    Odoo, lui, effacerait les ``ir.model`` sans toucher aux tables : le modèle
    n'est plus au registre au moment où il les supprime, donc son ``_drop_table``
    ne trouve rien à supprimer. Sept tables resteraient derrière, invisibles.
    """
    cr.execute(RESTES, (ANCIEN,))
    noms = [nom for (nom,) in cr.fetchall()]
    if noms:
        cr.execute(
            "DELETE FROM ir_model_data WHERE module = %s AND name = ANY(%s)",
            (ANCIEN, noms),
        )
    # Les identifiants des autres modules (mail, calendar) sur ces modèles
    # pendraient dans le vide : ils partent avec ce qu'ils désignent.
    cr.execute("""
        DELETE FROM ir_model_data
         WHERE model = 'ir.model.fields.selection'
           AND res_id IN (SELECT s.id FROM ir_model_fields_selection s
                            JOIN ir_model_fields f ON f.id = s.field_id
                           WHERE f.model LIKE 'corporate.%')
    """)
    cr.execute("""
        DELETE FROM ir_model_data
         WHERE model = 'ir.model.fields'
           AND res_id IN (SELECT id FROM ir_model_fields WHERE model LIKE 'corporate.%')
    """)
    cr.execute("""
        DELETE FROM ir_model_data
         WHERE model = 'ir.model.inherit' AND name LIKE 'model_inherit__corporate\\_%'
    """)
    cr.execute("""
        DELETE FROM ir_model_data
         WHERE model = 'ir.model'
           AND res_id IN (SELECT id FROM ir_model WHERE model LIKE 'corporate.%')
    """)
    cr.execute("""
        DELETE FROM ir_model_constraint
         WHERE module = (SELECT id FROM ir_module_module WHERE name = %s)
           AND name LIKE 'corporate\\_%%'
    """, (ANCIEN,))
    cr.execute("""
        DELETE FROM ir_model_relation
         WHERE module = (SELECT id FROM ir_module_module WHERE name = %s)
           AND name LIKE 'corporate\\_%%'
    """, (ANCIEN,))
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
    cr.execute("DELETE FROM ir_model WHERE model LIKE 'corporate.%'")
    cr.execute("ALTER TABLE project_document DROP COLUMN IF EXISTS minute_book_section")
    for table in TABLES:
        cr.execute('DROP TABLE IF EXISTS "%s" CASCADE' % table)
    _logger.info(
        "Gouvernance corporative retirée : %s absent du disque et aucune donnée "
        "hors graines (%s). Sept tables et leurs métadonnées sont parties, "
        "y compris les échéances livrées par le module.",
        NOUVEAU, comptes,
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
                "project_knowledge_matrix 18.0.12.0.0 sort la gouvernance "
                "corporative du module, et cette base en porte : %s. Le module "
                "%s doit être présent dans l'addons_path et installé dans la "
                "même passe, sinon ces enregistrements seraient perdus. "
                "Commande : -u project_knowledge_matrix -i %s"
                % (comptes, NOUVEAU, NOUVEAU)
            )
        etendeurs = _autres_modules_etendent(cr)
        if etendeurs:
            raise Exception(
                "project_knowledge_matrix 18.0.12.0.0 sort la gouvernance "
                "corporative du module. Les tables sont vides, mais %s "
                "étend(ent) encore ces modèles : les retirer les casserait. "
                "Déployer %s, ou désinstaller ces modules d'abord."
                % (', '.join(etendeurs), NOUVEAU)
            )
        _retirer(cr, comptes)
        return

    module_id, etat = ligne
    if etat == 'uninstalled':
        # Marquer plutôt que d'échouer : la mise à niveau peut venir du bouton
        # « Mettre à jour » de la liste des applications, qui ne connaît pas le
        # module neuf. Odoo relit les états après cette passe et l'installe.
        cr.execute(
            "UPDATE ir_module_module SET state = 'to install' WHERE id = %s",
            (module_id,),
        )
        _logger.info("%s passe de « uninstalled » à « to install ».", NOUVEAU)
    elif etat not in ('to install', 'to upgrade', 'installed'):
        raise Exception(
            "%s est à l'état « %s » : la gouvernance corporative n'aurait où "
            "aller. Installer le module dans la même passe." % (NOUVEAU, etat)
        )

    _reattribuer(cr, module_id)
