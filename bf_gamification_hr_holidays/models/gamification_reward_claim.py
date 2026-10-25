import logging

from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools.misc import clean_context

_logger = logging.getLogger(__name__)


class GamificationRewardClaim(models.Model):
    _inherit = 'bf.gamification.reward.claim'

    allocation_id = fields.Many2one(
        'hr.leave.allocation', string="Allocation de congé", readonly=True, copy=False,
        groups='hr_holidays.group_hr_holidays_user,bf_gamification.group_gamification_manager')

    # Posée par le pont seulement : un
    # gestionnaire qui y mettait une autre allocation empêchait la création de
    # la vraie à l'approbation, ou faisait refuser une allocation étrangère.
    def _bf_frozen_changes(self, vals):
        changed = super()._bf_frozen_changes(vals)
        if 'allocation_id' in vals and any(
                claim.sudo().allocation_id.id != (vals['allocation_id'] or False) for claim in self):
            changed.add('allocation_id')
        return changed

    def _bf_leave_type(self):
        value = self.env['ir.config_parameter'].sudo().get_param('bf_gamification.leave_type_id')
        try:
            return self.env['hr.leave.type'].sudo().browse(int(value or 0)).exists()
        except ValueError:
            return self.env['hr.leave.type']

    def action_approve(self):
        to_allocate = self.filtered(lambda c: c.is_constance_leave and not c.sudo().allocation_id)
        # Vérifier AVANT d'approuver : une approbation sans allocation possible
        # laisserait croire que le jour est accordé dans Congés.
        if to_allocate:
            if not self._bf_leave_type():
                raise UserError(_("Choisissez d'abord le type de congé des congés de constance "
                                  "dans les réglages de Fox Quest."))
            for claim in to_allocate:
                if not claim.user_id.sudo().employee_id:
                    raise UserError(_("%s n'a pas de fiche employé : l'allocation de congé ne "
                                      "peut pas être créée.") % claim.user_id.name)
        res = super().action_approve()
        for claim in to_allocate:
            claim._bf_create_allocation()
        return res

    def action_refuse(self):
        allocations = self.sudo().mapped('allocation_id').filtered(
            lambda a: a.state in ('confirm', 'validate1', 'validate'))
        res = super().action_refuse()
        for allocation in allocations:
            try:
                with self.env.cr.savepoint():
                    allocation.with_user(self.env.user).action_refuse()
            except (AccessError, UserError):
                allocation.message_post(body=_(
                    "La demande de congé de constance a été refusée dans Fox Quest : cette "
                    "allocation est à refuser par un responsable des congés."))
        return res

    def _bf_create_allocation(self):
        self.ensure_one()
        # Sans le contexte de l'appelant : par RPC, le cœur retire
        # déjà les `default_*` au passage en sudo ; un appelant DÉJÀ en sudo (code
        # d'un autre module, action serveur) les garde, et `default_state=validate`
        # validait alors l'allocation d'emblée, sans les droits de Congés.
        self = self.with_context(clean_context(self.env.context))
        employee = self.user_id.sudo().employee_id
        allocation = self.env['hr.leave.allocation'].sudo().create({
            'name': _("Congé de constance (Fox Quest)"),
            'holiday_status_id': self._bf_leave_type().id,
            'employee_id': employee.id,
            'number_of_days': self.leave_days,
            'allocation_type': 'regular',
        })
        self.sudo().allocation_id = allocation
        # La validation suit les droits de Congés : on ne les contourne pas.
        validated = allocation.state == 'validate'
        if not validated:
            try:
                with self.env.cr.savepoint():
                    allocation.with_user(self.env.user)._action_approve()
                validated = allocation.state in ('validate', 'validate1')
            except (AccessError, UserError) as error:
                _logger.info("Fox Quest : allocation %s laissée à approuver (%s)", allocation.id, error)
        self.sudo().message_post(
            body=_("Allocation de congé créée dans Congés (%s jour(s)) : %s.") % (
                ('%g' % self.leave_days).replace('.', ','),
                _("approuvée") if validated else _("à approuver par un responsable des congés")),
            message_type='notification', subtype_xmlid='mail.mt_note')
        return allocation
