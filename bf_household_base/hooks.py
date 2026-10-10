"""Reprendre le groupe du foyer là où il vivait avant ce module.

Jusqu'à la 18.0.1.2.0 du profil du foyer, la catégorie et le groupe portaient l'identifiant
``bf_household_profile.*``. Une base qui les a déjà les garde : on renomme leurs
identifiants AVANT le chargement des données de ce module, sinon Odoo créerait un second
groupe, vide, à côté de celui qui porte les comptes et les règles. Le plafond de comptes
suit le même chemin.
"""

ANCIEN = "bf_household_profile"
NOUVEAU = "bf_household_base"
IDENTIFIANTS = ("module_category_household", "group_household_user")


def pre_init_hook(env):
    cr = env.cr
    # Les deux identifiants à la fois : la montée du profil supprimerait l'ancien groupe avec
    # ses membres. Ça ne devrait jamais arriver ; si ça arrive, on s'arrête plutôt que deviner.
    cr.execute(
        """SELECT a.name FROM ir_model_data a JOIN ir_model_data n ON n.name = a.name
            WHERE a.module = %s AND n.module = %s AND a.name IN %s""",
        [ANCIEN, NOUVEAU, IDENTIFIANTS])
    doubles = [r[0] for r in cr.fetchall()]
    if doubles:
        raise RuntimeError(
            "bf_household_base: %s exist under both %s and %s; merge them by hand before installing."
            % (", ".join(doubles), ANCIEN, NOUVEAU))
    cr.execute(
        """UPDATE ir_model_data SET module = %s
            WHERE module = %s AND name IN %s
              AND NOT EXISTS (SELECT 1 FROM ir_model_data n WHERE n.module = %s AND n.name = ir_model_data.name)
        RETURNING name, res_id""",
        [NOUVEAU, ANCIEN, IDENTIFIANTS, NOUVEAU])
    repris = dict(cr.fetchall())
    # Le commentaire du groupe repris portait le texte du profil dans chaque langue. Les données
    # du socle réécrivent l'anglais seul et un .po ne remplace pas une traduction déjà là : on ne
    # garde que l'anglais, que le catalogue du socle complète.
    if repris.get("group_household_user"):
        cr.execute(
            """UPDATE res_groups SET comment = jsonb_build_object('en_US', comment->'en_US')
                WHERE id = %s AND comment IS NOT NULL""",
            [repris["group_household_user"]])
    cr.execute(
        """UPDATE ir_config_parameter SET key = %s
            WHERE key = %s AND NOT EXISTS (SELECT 1 FROM ir_config_parameter WHERE key = %s)""",
        [f"{NOUVEAU}.max_users", f"{ANCIEN}.max_users", f"{NOUVEAU}.max_users"])
