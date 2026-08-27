"""Convertit la condition unique de chaque règle en ligne `bf.email.rule.condition`.

Jusqu'en 18.0.9.10.0 une règle portait sa condition dans deux colonnes,
`condition_type` et `condition_value`. Le moteur en accepte maintenant
plusieurs, avec des exceptions, alors la condition déménage dans son propre
modèle. Cette migration fait le déménagement.

Trois précautions.

**On saute les règles qui ont déjà une condition.** Les fichiers de données du
module se chargent AVANT ce script : `data/bf_email_rule_default.xml` porte
désormais des enregistrements de condition avec des xmlid neufs, donc Odoo les
crée contre les quatre règles livrées. Les convertir une seconde fois depuis
les vieilles colonnes les doublerait — et deux conditions en mode « ET » sur
une règle qui n'en attendait qu'une, c'est une règle qui ne se déclenche plus.

**On écrit en SQL, pas par l'ORM.** Une valeur déjà en base peut ne pas passer
les nouvelles contraintes — une expression régulière invalide, par exemple.
Refuser la migration pour ça bloquerait la mise à niveau alors que l'ancien
moteur, lui, avalait l'erreur au moment de l'évaluation (et le nouveau aussi :
`bf.email.rule.condition._match` attrape et journalise). On préserve donc le
comportement exact plutôt que la propreté formelle.

**On ne supprime les vieilles colonnes qu'à la fin**, et le script est
ré-exécutable : si les colonnes ont déjà disparu, il n'y a rien à convertir.
"""

import logging

_logger = logging.getLogger(__name__)

# ancien condition_type -> (field_name, operator, la valeur va dans header_name ?)
CONVERSION = {
    "domain": ("odoo_domain", "expr", False),
    "regex_subject": ("subject", "regex", False),
    "regex_from": ("email_from", "regex", False),
    "regex_body": ("body", "regex", False),
    "header_present": ("header", "is_set", True),
    "partner_tag": ("partner_field", "expr", False),
}

FIELD_KIND = {
    "odoo_domain": "expr",
    "partner_field": "expr",
    "header": "header",
    "subject": "text",
    "email_from": "text",
    "body": "text",
}

# xmlid du module -> clé de recette du catalogue
RECIPE_BY_XMLID = {
    "rule_noreply_notification": "noreply",
    "rule_list_unsubscribe_marketing": "list_unsubscribe",
    "rule_client_partner": "client_partner",
    "rule_vendor_partner": "vendor_partner",
    "rule_internal_bluefox": "internal_sender",
}


def _column_exists(cr, table, column):
    cr.execute(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_name = %s AND column_name = %s
        """,
        (table, column),
    )
    return bool(cr.fetchone())


def migrate(cr, version):
    if not _column_exists(cr, "bf_email_rule", "condition_type"):
        _logger.info(
            "bf.email.rule : colonnes héritées absentes, rien à convertir.")
    else:
        _convert_conditions(cr)

    _backfill_scope(cr)
    _backfill_recipe_keys(cr)

    if _column_exists(cr, "bf_email_rule", "condition_type"):
        cr.execute(
            "ALTER TABLE bf_email_rule "
            "DROP COLUMN IF EXISTS condition_type, "
            "DROP COLUMN IF EXISTS condition_value"
        )
        _logger.info("bf.email.rule : colonnes héritées supprimées.")


def _convert_conditions(cr):
    cr.execute(
        """
        SELECT r.id, r.condition_type, r.condition_value
          FROM bf_email_rule r
         WHERE NOT EXISTS (
                   SELECT 1 FROM bf_email_rule_condition c
                    WHERE c.rule_id = r.id
               )
         ORDER BY r.id
        """
    )
    rows = cr.fetchall()
    if not rows:
        _logger.info(
            "bf.email.rule : aucune règle sans condition, rien à convertir.")
        return

    converted, skipped = 0, []
    for rule_id, condition_type, condition_value in rows:
        mapping = CONVERSION.get(condition_type)
        if not mapping:
            skipped.append((rule_id, condition_type))
            continue
        field_name, operator, value_is_header = mapping
        header_name = (condition_value or "").strip() if value_is_header else None
        value = None if value_is_header else condition_value
        cr.execute(
            """
            INSERT INTO bf_email_rule_condition
                (rule_id, sequence, kind, field_name, field_kind,
                 operator, value, header_name,
                 create_uid, create_date, write_uid, write_date)
            VALUES (%s, 10, 'condition', %s, %s, %s, %s, %s,
                    1, NOW() AT TIME ZONE 'UTC', 1, NOW() AT TIME ZONE 'UTC')
            """,
            (rule_id, field_name, FIELD_KIND[field_name],
             operator, value, header_name),
        )
        converted += 1

    _logger.info(
        "bf.email.rule : %d condition(s) converties en lignes.", converted)
    if skipped:
        # Une règle sans condition ne se déclenche jamais : il faut que ça se
        # voie dans les journaux, pas qu'on le découvre au courriel manqué.
        _logger.warning(
            "bf.email.rule : %d règle(s) non converties (type de condition "
            "inconnu) — elles ne se déclencheront plus tant qu'une condition "
            "n'y est pas posée à la main : %s",
            len(skipped),
            ", ".join("#%s (%s)" % (rid, ctype) for rid, ctype in skipped),
        )


def _backfill_scope(cr):
    """Toutes les règles existantes sont personnelles : user_id était requis."""
    cr.execute(
        "UPDATE bf_email_rule SET scope = 'user' "
        "WHERE scope IS NULL AND user_id IS NOT NULL"
    )
    personal = cr.rowcount
    cr.execute(
        "UPDATE bf_email_rule SET scope = 'company' "
        "WHERE scope IS NULL AND user_id IS NULL"
    )
    company = cr.rowcount
    # La société est reprise du propriétaire, pas laissée au défaut du champ
    # (qui vaut la société de qui exécute la migration). Elle ne filtre rien
    # pour une règle personnelle — cf. bf.email.rule._match — mais une valeur
    # fausse serait quand même trompeuse à la lecture.
    cr.execute(
        """
        UPDATE bf_email_rule r
           SET company_id = u.company_id
          FROM res_users u
         WHERE r.user_id = u.id
           AND (r.company_id IS NULL OR r.company_id <> u.company_id)
        """
    )
    _logger.info(
        "bf.email.rule : portée renseignée (%d personnelles, %d "
        "d'organisation) et société reprise du propriétaire.",
        personal, company,
    )


def _backfill_recipe_keys(cr):
    """Marque les règles livrées avec leur clé de recette.

    Sans ça l'assistant « règles courantes » reproposerait à la personne qui a
    installé le module les quatre règles qu'elle a déjà — les
    enregistrements XML sont en `noupdate`, donc le nouveau champ n'y arrive
    pas tout seul.
    """
    for xmlid, recipe_key in RECIPE_BY_XMLID.items():
        cr.execute(
            """
            UPDATE bf_email_rule r
               SET recipe_key = %s
              FROM ir_model_data d
             WHERE d.module = 'bf_email_management'
               AND d.name = %s
               AND d.model = 'bf.email.rule'
               AND d.res_id = r.id
               AND r.recipe_key IS NULL
            """,
            (recipe_key, xmlid),
        )

    # Les règles semées par _seed_defaults_for_user n'ont pas de xmlid : on les
    # reconnaît à leur nom d'origine, qui n'a jamais changé.
    for name_like, recipe_key in (
        ("Expéditeurs noreply%", "noreply"),
        ("List-Unsubscribe présent%", "list_unsubscribe"),
        ("Partenaire client_rank%", "client_partner"),
        ("Partenaire supplier_rank%", "vendor_partner"),
    ):
        cr.execute(
            "UPDATE bf_email_rule SET recipe_key = %s "
            "WHERE recipe_key IS NULL AND name LIKE %s",
            (recipe_key, name_like),
        )
    _logger.info("bf.email.rule : clés de recette renseignées.")
