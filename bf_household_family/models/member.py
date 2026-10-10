"""Les membres du foyer, vus par le foyer : une vue en lecture seule.

Pourquoi pas l'écran des utilisateurs d'Odoo : il montre les droits et il se
modifie. Un responsable du foyer n'a pas la gestion des droits (le socle du foyer la
lui interdit) ; il agit par les boutons de cette vue, chacun gardé ici, en
superutilisateur sans les ``default_*`` du contexte.

Ce qu'un responsable ne fait JAMAIS à un autre adulte :
définir son mot de passe, changer son courriel, toucher à sa double
authentification, le retirer. Chacun de ces gestes donnerait le compte, donc sa
classe privée, ou couperait la personne de ses propres fiches. Le lien de mot de
passe part toujours à l'adresse du membre : le cœur d'Odoo refuse qu'un membre
modifie le contact d'un autre usager interne (``res_partner.py``, garde de
``write``).
"""
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .common import MANAGER_GROUP, check_manager, is_household_member, is_manager, neutral_env


class HouseholdMember(models.Model):
    _name = "bf.household.member"
    _description = "Household member"
    _auto = False
    _order = "active desc, name, id"

    user_id = fields.Many2one("res.users", string="Account", readonly=True)
    name = fields.Char(string="Name", readonly=True)
    login = fields.Char(string="Email", readonly=True)
    active = fields.Boolean(string="Active", readonly=True)
    role = fields.Selection(
        [("adult", "Adult"), ("teen", "Teen (14 to 17)")], string="Role", readonly=True)
    is_manager = fields.Boolean(string="Household manager", readonly=True)
    last_login = fields.Datetime(string="Last sign-in", readonly=True)
    state = fields.Selection(
        [("invited", "Invited"), ("active", "Active"), ("leaving", "Leaving"), ("left", "Left")],
        string="State", readonly=True)

    is_me = fields.Boolean(compute="_compute_permissions")
    can_manage = fields.Boolean(compute="_compute_permissions")
    can_remove = fields.Boolean(compute="_compute_permissions")

    @property
    def _table_query(self):
        # Les groupes se retrouvent par leur identifiant XML, dans la requête :
        # elle ne dépend pas de l'ordre de chargement des données.
        return """
            SELECT u.id AS id,
                   u.id AS user_id,
                   p.name AS name,
                   u.login AS login,
                   u.active AS active,
                   COALESCE(u.bf_household_role, 'adult') AS role,
                   EXISTS (
                       SELECT 1 FROM res_groups_users_rel r
                         JOIN ir_model_data d ON d.res_id = r.gid AND d.model = 'res.groups'
                        WHERE r.uid = u.id AND d.module = 'bf_household_family'
                          AND d.name = 'group_household_manager'
                   ) AS is_manager,
                   (SELECT max(l.create_date) FROM res_users_log l WHERE l.create_uid = u.id) AS last_login,
                   CASE
                       WHEN NOT u.active THEN 'left'
                       WHEN EXISTS (SELECT 1 FROM bf_household_departure dp
                                     WHERE dp.user_id = u.id AND dp.state = 'scheduled') THEN 'leaving'
                       WHEN NOT EXISTS (SELECT 1 FROM res_users_log l WHERE l.create_uid = u.id) THEN 'invited'
                       ELSE 'active'
                   END AS state
              FROM res_users u
              JOIN res_partner p ON p.id = u.partner_id
             WHERE u.share IS NOT TRUE
               AND EXISTS (
                   SELECT 1 FROM res_groups_users_rel r
                     JOIN ir_model_data d ON d.res_id = r.gid AND d.model = 'res.groups'
                    WHERE r.uid = u.id AND d.module = 'bf_household_base'
                      AND d.name = 'group_household_user'
               )
        """

    @api.depends_context("uid")
    def _compute_permissions(self):
        gestion = is_manager(self.env)
        for rec in self:
            rec.is_me = rec.user_id.id == self.env.uid
            rec.can_manage = gestion and rec.active
            rec.can_remove = (
                gestion and rec.active and not rec.is_me
                and (rec.role == "teen" or rec.state == "invited")
            )

    # ------------------------------------------------------------------
    # Gardes
    # ------------------------------------------------------------------
    def _bf_targets(self, allow_self=True):
        """Les comptes visés, relus en base : actifs, du foyer, jamais un administrateur."""
        check_manager(self.env)
        users = self.mapped("user_id").sudo()
        for user in users:
            if not is_household_member(user) or user.has_group("base.group_system"):
                raise AccessError(_("%s is not an active member of this household.", user.name))
            if not allow_self and user.id == self.env.uid:
                raise UserError(_("You cannot do this to your own account."))
        return users

    @staticmethod
    def _bf_has_signed_in(user):
        return bool(user.env["res.users.log"].sudo().search_count([("create_uid", "=", user.id)], limit=1))

    # ------------------------------------------------------------------
    # Gestes du responsable
    # ------------------------------------------------------------------
    def action_open_invite(self):
        # Méthode ordinaire, pas @api.model : le bouton d'en-tête de la liste
        # passe la sélection (vide) en argument, qu'un @api.model refuse (vu au
        # navigateur, invisible aux essais Python qui l'appellent directement).
        check_manager(self.env)
        return {
            "type": "ir.actions.act_window",
            "name": _("Invite a member"),
            "res_model": "bf.household.member.invite",
            "view_mode": "form",
            "target": "new",
        }

    def action_resend_invitation(self):
        """Renvoie le lien qui choisit le mot de passe, à l'adresse du membre."""
        for user in self._bf_targets(allow_self=True):
            if not user.email:
                raise UserError(_("%s has no email address: ask Blue Fox.", user.name))
            # Jamais connectée : le gabarit d'INVITATION (« create_user », comme le bouton
            # « Renvoyer l'invitation » d'Odoo), et non « une réinitialisation a été
            # demandée » (vu à l'envoi réel du 2026-10-10).
            neutre = neutral_env(user.env)
            if self._bf_has_signed_in(user):
                user.with_env(neutre)._action_reset_password(signup_type="reset")
            else:
                user.with_env(neutre(context=dict(neutre.context, create_user=1)))._action_reset_password(
                    signup_type="signup")
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {"type": "success", "message": _("The link was sent to the member's own address.")},
        }

    def action_remove(self):
        """Retire un ado, ou un compte jamais utilisé. Un adulte qui s'est déjà
        connecté part de lui-même, ou par Blue Fox."""
        users = self._bf_targets(allow_self=False)
        depart_env = neutral_env(self.env)
        for user in users:
            ado = user.bf_household_role == "teen"
            if not ado and self._bf_has_signed_in(user):
                raise UserError(_(
                    "%s is an adult who has used their account: they leave the household by "
                    "themselves, or Blue Fox removes them on request.", user.name))
            depart_env["bf.household.departure"].create({
                "user_id": user.id, "kind": "removed", "requested_by_id": self.env.uid,
                "effective_on": fields.Date.context_today(self), "state": "done",
            })
            user._bf_household_archive()
        return {"type": "ir.actions.client", "tag": "soft_reload"}

    def action_grant_manager(self):
        manager = self.env.ref(MANAGER_GROUP)
        for user in self._bf_targets(allow_self=True):
            if user.bf_household_role == "teen":
                raise UserError(_("A household manager is an adult, not a teen."))
            user.with_env(neutral_env(user.env)).write({"groups_id": [(4, manager.id)]})
        return {"type": "ir.actions.client", "tag": "soft_reload"}

    def action_revoke_manager(self):
        manager = self.env.ref(MANAGER_GROUP)
        users = self._bf_targets(allow_self=True)
        restants = neutral_env(self.env)["res.users"].search_count([
            ("groups_id", "in", manager.id), ("share", "=", False), ("id", "not in", users.ids)])
        if self.env.uid in users.ids and not restants and not self.env.user.has_group("base.group_system"):
            raise UserError(_("You are the only household manager: name another one first."))
        for user in users:
            user.with_env(neutral_env(user.env)).write({"groups_id": [(3, manager.id)]})
        return {"type": "ir.actions.client", "tag": "soft_reload"}

    # ------------------------------------------------------------------
    # Gestes de chacun et de Blue Fox
    # ------------------------------------------------------------------
    def action_open_leave(self):
        self.ensure_one()
        if not self.is_me:
            raise AccessError(_("Only the person themselves can leave the household."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Leave the household"),
            "res_model": "bf.household.leave",
            "view_mode": "form",
            "target": "new",
        }

    def action_open_removal(self):
        self.ensure_one()
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Only Blue Fox schedules the removal of an adult."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Schedule a removal"),
            "res_model": "bf.household.member.removal",
            "view_mode": "form",
            "target": "new",
            "context": {"default_user_id": self.user_id.id},
        }
