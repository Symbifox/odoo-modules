import time

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request

from .. import impersonation as imp

MIN_REASON_LENGTH = 10


class BfImpersonateWizard(models.TransientModel):
    _name = "bf.impersonate.wizard"
    _description = "See Symbifox as someone else"

    target_user_id = fields.Many2one(
        "res.users", string="Person", required=True,
        domain="[('share', '=', False), ('id', '!=', uid), "
               "('bf_impersonate_protected', '=', False)]")
    reason = fields.Text(
        required=True,
        help="Why you need to see Symbifox as this person. The person can read it.")
    mode = fields.Selection(
        [("read", "Read only"), ("write", "Read and write")],
        required=True, default="read")
    # Affichage seulement : l'onchange d'Odoo 18 ne calcule pas un champ sans
    # dépendance de champ sur un nouvel enregistrement. Le contrôle, lui, relit
    # le groupe côté serveur (un champ d'assistant vient du client).
    can_write = fields.Boolean(default=lambda self: self._bf_can_write())
    duration = fields.Integer(
        string="Duration (minutes)", required=True,
        default=lambda self: self._bf_param_int("duration_default", 30))
    notice = fields.Char(compute="_compute_notice")

    @api.model
    def _bf_param_int(self, key, default):
        value = self.env["ir.config_parameter"].sudo().get_param(f"bf_impersonate.{key}")
        try:
            return int(value) if value else default
        except ValueError:
            return default

    @api.model
    def _bf_can_write(self):
        return self.env.user.has_group("bf_impersonate.group_impersonate_write")

    @api.depends("target_user_id")
    def _compute_notice(self):
        policy = self.env["ir.config_parameter"].sudo().get_param(
            "bf_impersonate.notify", "start")
        for wizard in self:
            name = wizard.target_user_id.name or _("The person")
            if policy == "never":
                wizard.notice = _("Nobody is notified; the session is recorded in the journal.")
            elif policy == "start_end":
                wizard.notice = _(
                    "%s will be notified now, then receive a summary at the end.", name)
            else:
                wizard.notice = _("%s will be notified now.", name)

    # ------------------------------------------------------------------

    def _bf_check(self):
        """Tout ce qui interdit d'ouvrir l'incarnation, avant d'écrire quoi que ce soit."""
        self.ensure_one()
        origin = self.env.user
        target = self.target_user_id.sudo()
        if not request:
            raise UserError(_("An impersonation can only be opened from the web client."))
        if imp.current():
            raise UserError(_("Go back to your own account before seeing Symbifox as someone else."))
        if origin.share or not origin.has_group("bf_impersonate.group_impersonate_read"):
            raise AccessError(_("You are not allowed to see Symbifox as someone else."))
        if not target.exists() or not target.active or target.share:
            raise ValidationError(_("Only an active internal user can be seen this way."))
        if target.id in (origin.id, SUPERUSER_ID):
            raise ValidationError(_("Choose someone other than yourself."))
        if target.bf_impersonate_protected:
            raise ValidationError(_("%s is protected from impersonation.", target.name))
        is_admin_target = target.has_group("base.group_system")
        allow_admin = self.env["ir.config_parameter"].sudo().get_param(
            "bf_impersonate.allow_admin_targets")
        if is_admin_target and not allow_admin:
            raise ValidationError(_(
                "%s is an administrator. Seeing Symbifox as an administrator is "
                "turned off in the settings.", target.name))
        if not origin.has_group("base.group_system"):
            # Un incarnateur qui n'est pas administrateur ne voit jamais plus
            # que ce qu'il voit déjà : la personne visée ne doit avoir aucun
            # groupe qu'il n'a pas.
            missing = target.groups_id - origin.sudo().groups_id
            if missing:
                raise ValidationError(_(
                    "%(name)s has access rights you do not have (%(groups)s). "
                    "Only an administrator can see Symbifox as them.",
                    name=target.name,
                    groups=", ".join(missing[:5].mapped("full_name"))))
        if self.mode == imp.MODE_WRITE and not self._bf_can_write():
            raise AccessError(_("You are allowed to see Symbifox as someone else in read-only mode only."))
        maximum = self._bf_param_int("duration_max", 120)
        if not 1 <= self.duration <= maximum:
            raise ValidationError(_("The duration must be between 1 and %s minutes.", maximum))
        if len((self.reason or "").strip()) < MIN_REASON_LENGTH:
            raise ValidationError(_(
                "Give a real reason (at least %s characters): the person can read it.",
                MIN_REASON_LENGTH))

    def action_start(self):
        self._bf_check()
        origin = self.env.user
        target = self.target_user_id.sudo()
        journal = self.env["bf.impersonate.session"]._bf_open(
            origin, target, self.reason.strip(), self.mode, self.duration,
            ip_address=request.httprequest.remote_addr)
        imp.switch_to(target, {
            "from_uid": origin.id,
            "journal_id": journal.id,
            "mode": self.mode,
            "expires": time.time() + self.duration * 60,
        })
        # L'accueil de la personne, pas la page courante : elle n'a peut-être
        # pas accès à la fiche d'usager d'où l'on vient. L'action cliente
        # prévient les autres onglets avant de naviguer (user_menu.js).
        return {
            "type": "ir.actions.client",
            "tag": "bf_impersonate_switched",
            "params": {"uid": target.id},
        }
