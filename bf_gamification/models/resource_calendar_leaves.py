from odoo import api, models


class ResourceCalendarLeaves(models.Model):
    """Un férié ou un congé change : la constance se refait.

    `hr_holidays` pose ici un congé validé (sur la ressource de l'employé) et
    l'y retire quand il est refusé ou annulé. Validé après coup, il doit
    réparer la constance que le premier XP du retour avait cassée.
    """
    _inherit = 'resource.calendar.leaves'

    _BF_FQ_FIELDS = {'date_from', 'date_to', 'resource_id', 'calendar_id', 'company_id', 'time_type'}

    def _bf_fq_users(self):
        """Les usagers dont ces congés touchent les jours ouvrables : la
        personne d'un congé, tous les joueurs (de la société) pour un férié."""
        users = self.env['res.users'].sudo()
        holidays = self.sudo().browse()
        for leave in self.sudo():
            if leave.resource_id:
                users |= leave.resource_id.user_id
            else:
                holidays |= leave
        if holidays:
            Profile = self.env['bf.gamification.profile'].sudo()
            domain = Profile._bf_player_domain()
            if all(leave.company_id for leave in holidays):
                domain = domain + [('user_id.company_id', 'in', holidays.company_id.ids)]
            users |= Profile.search(domain).user_id
        return users

    @api.model_create_multi
    def create(self, vals_list):
        leaves = super().create(vals_list)
        self.env['bf.gamification.profile']._bf_constance_changed(leaves._bf_fq_users())
        return leaves

    def write(self, vals):
        if not self._BF_FQ_FIELDS & set(vals):
            return super().write(vals)
        before = self._bf_fq_users()
        res = super().write(vals)
        self.env['bf.gamification.profile']._bf_constance_changed(before | self._bf_fq_users())
        return res

    def unlink(self):
        users = self._bf_fq_users()
        res = super().unlink()
        self.env['bf.gamification.profile']._bf_constance_changed(users)
        return res
