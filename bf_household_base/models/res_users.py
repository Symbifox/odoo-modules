"""Les deux gardes du foyer, quel que soit le chemin qui écrit un compte.

1. Une personne du foyer n'est jamais administratrice. Un administrateur système passe
   en superutilisateur par /web/become, au-dessus de toutes les règles privées ; Gestion
   des droits (erp_manager) peut se donner les groupes qu'il veut. Les groupes « voit tout »
   de modules publics qu'une instance de foyer installe (tous les courriels, tous les SMS)
   sont refusés de même ; un autre module en ajoute par ``_household_forbidden_groups``.
2. Le palier Perso : au plus 10 comptes actifs par foyer. Le compte de Blue Fox
   (administrateur, hors du groupe) ne compte pas.

Les deux gardes lisent les groupes sur l'ENREGISTREMENT, jamais par ``has_group`` : son cache
(``_get_group_ids``) garde l'état d'avant l'écriture jusqu'à la validation, si bien qu'une
personne qui détenait un groupe interdit ne pouvait plus en être retirée. L'écran des
groupes écrit la relation par ``res.groups`` sans passer par ``res.users.write`` : voir
``res_groups.py``, qui rejoue les deux gardes.
"""
import logging

from odoo import _, api, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

HOUSEHOLD_GROUP = "bf_household_base.group_household_user"
FORBIDDEN_FOR_HOUSEHOLD = (
    "base.group_system", "base.group_erp_manager",
    # Voient toute la classe privée de leur module par une règle de GROUPE : les courriels et
    # les SMS de toute la maisonnée. Absents de l'instance, ils sont simplement ignorés.
    "bf_email_management.group_email_admin", "bf_sms_archive.group_sms_manager",
)
MAX_USERS_PARAM = "bf_household_base.max_users"
MAX_USERS_DEFAULT = 10
SEAT_FIELDS = ("active", "groups_id", "share")


def _touche_le_decompte(vals):
    return any(k in SEAT_FIELDS or k.startswith(("in_group_", "sel_groups_")) for k in vals)


class ResUsers(models.Model):
    _inherit = "res.users"

    # ------------------------------------------------------------------ jamais administrateur
    @api.model
    def _household_forbidden_groups(self):
        """Les identifiants des groupes qu'une personne du foyer ne peut jamais avoir.
        Un module du foyer étend la liste (super() puis ses propres groupes)."""
        return list(FORBIDDEN_FOR_HOUSEHOLD)

    def _household_held_groups(self):
        self.ensure_one()
        groupes = self.sudo().groups_id
        return groupes | groupes.trans_implied_ids

    @api.constrains("groups_id")
    def _check_household_user_is_not_admin(self):
        foyer = self.env.ref(HOUSEHOLD_GROUP, raise_if_not_found=False)
        if not foyer:
            return
        interdits = self.env["res.groups"]
        for xmlid in self._household_forbidden_groups():
            interdits |= self.env.ref(xmlid, raise_if_not_found=False) or self.env["res.groups"]
        for user in self:
            detenus = user._household_held_groups()
            if foyer in detenus and detenus & interdits:
                raise ValidationError(_(
                    "%(user)s is a household user and cannot hold an administration group, "
                    "nor one that reads every message of the household.",
                    user=user.name))

    def _household_check_all(self):
        """Les gardes du foyer sur ces comptes, rejouées quand l'écran des groupes écrit la
        relation sans passer par write. Un module du foyer y ajoute les siennes."""
        self._check_household_user_is_not_admin()

    # ------------------------------------------------------------------ au plus N comptes
    @api.model
    def _household_max_users(self):
        brut = self.env["ir.config_parameter"].sudo().get_param(MAX_USERS_PARAM)
        if not brut:
            return MAX_USERS_DEFAULT
        try:
            return max(1, int(float(brut)))
        except (TypeError, ValueError, OverflowError):
            _logger.warning("%s = %r is not a number: the household keeps %s accounts.",
                            MAX_USERS_PARAM, brut, MAX_USERS_DEFAULT)
            return MAX_USERS_DEFAULT

    @api.model
    def _household_counting_ids(self):
        """Les comptes qui comptent : internes, actifs, du groupe du foyer."""
        foyer = self.env.ref(HOUSEHOLD_GROUP, raise_if_not_found=False)
        if not foyer:
            return set()
        return set(self.sudo().with_context(active_test=True).search(
            [("groups_id", "in", foyer.id), ("share", "=", False)]).ids)

    @api.model
    def _household_seats_taken(self):
        return len(self._household_counting_ids())

    @api.model
    def _household_check_seats(self, before):
        """Refuse seulement l'écriture qui fait ENTRER un compte dans le décompte au-delà du
        plafond. Un foyer déjà au-delà (plafond abaissé) reste gérable : on y retire un groupe,
        on y nomme un responsable, on y archive un compte."""
        foyer = self.env.ref(HOUSEHOLD_GROUP, raise_if_not_found=False)
        if not foyer:
            return
        maintenant = self._household_counting_ids()
        if not (maintenant - before):
            return
        # Deux invitations simultanées ne voient pas le compte l'une de l'autre (lecture
        # répétable) : écrire la rangée du groupe fait échouer la seconde, qu'Odoo rejoue et
        # qui compte alors juste.
        self.env.cr.execute("UPDATE res_groups SET write_date = write_date WHERE id = %s", [foyer.id])
        plafond = self._household_max_users()
        if len(maintenant) > plafond:
            raise ValidationError(_(
                "This household already has %(max)s accounts, the most its plan allows. "
                "Remove an account first, or ask Blue Fox.", max=plafond))

    @api.model_create_multi
    def create(self, vals_list):
        avant = self._household_counting_ids()
        users = super().create(vals_list)
        self._household_check_seats(avant)
        return users

    def write(self, vals):
        avant = self._household_counting_ids() if _touche_le_decompte(vals) else None
        res = super().write(vals)
        if avant is not None:
            self._household_check_seats(avant)
        return res
