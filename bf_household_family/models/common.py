"""Ce que les modèles de la famille partagent : les groupes, les mois, l'âge.

Une personne du foyer est un compte INTERNE et ACTIF du groupe « Household user »
(bf_household_base). Blue Fox (administratrice, hors du groupe) gère
l'instance ; le responsable du foyer gère des comptes, jamais des droits.
"""
from datetime import date

from odoo import SUPERUSER_ID
from odoo.exceptions import AccessError
from odoo.tools.misc import clean_context

HOUSEHOLD_GROUP = "bf_household_base.group_household_user"
MANAGER_GROUP = "bf_household_family.group_household_manager"

MONTHS = [
    ("1", "January"), ("2", "February"), ("3", "March"), ("4", "April"),
    ("5", "May"), ("6", "June"), ("7", "July"), ("8", "August"),
    ("9", "September"), ("10", "October"), ("11", "November"), ("12", "December"),
]

#: Un ado a son compte de 14 à 17 ans ; à 18 ans, il devient adulte.
TEEN_MIN_AGE = 14
ADULT_AGE = 18


def household_group(env):
    return env.ref(HOUSEHOLD_GROUP)


def is_household_member(user):
    """Un compte interne, actif, du groupe du foyer, qui n'est pas le superutilisateur."""
    user = user.sudo()
    return bool(
        user and user.active and not user.share and user.id != SUPERUSER_ID
        and household_group(user.env) in user.groups_id
    )


def is_manager(env):
    """Le responsable du foyer, ou Blue Fox (administratrice de l'instance)."""
    user = env.user
    return user.has_group(MANAGER_GROUP) or user.has_group("base.group_system")


def check_manager(env):
    if env.su:
        return
    if not is_manager(env):
        raise AccessError(env._("Only a household manager can do this."))


def neutral_env(env):
    """Un environnement superutilisateur SANS les ``default_*`` du contexte.

    Une création en sudo prend les valeurs par défaut que l'appelant glisse dans
    le contexte (``default_password``, ``default_groups_id``...) : une garde qui ne
    lit que ``vals`` est contournée par là."""
    return env(context=clean_context(env.context), su=True)


def age_on(birth_year, birth_month, birth_day, today):
    """L'âge révolu à ``today``. Sans le jour, on compte le premier du mois : un
    ado n'a pas 14 ans avant le mois de ses 14 ans."""
    if not birth_year or not birth_month:
        return None
    born = date(int(birth_year), int(birth_month), int(birth_day or 1))
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))
