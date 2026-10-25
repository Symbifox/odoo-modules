from odoo import api, models


class BfPartnerAbsence(models.Model):
    """Une absence déclarée change : la constance Fox Quest se refait.

    Malade lundi et mardi, déclaré mercredi : le premier XP du mercredi avait
    cassé la constance et la marche vers le congé. L'absence déclarée après coup
    les répare ; corrigée, archivée ou retirée, elle les refait.

    Un héritage ordinaire, et non plus une enveloppe posée par Fox Quest à
    l'exécution : base_automation retire toute
    enveloppe de create, write et unlink dans le processus où l'on modifie une
    règle automatisée, et la constance cessait d'y suivre les absences.
    """
    _inherit = 'bf.partner.absence'

    # Les champs d'une absence qui changent les jours ouvrables de quelqu'un.
    # `active` : archiver est la seule façon, pour un interne, de retirer une
    # absence (l'archivage ne refaisait rien).
    _BF_FQ_FIELDS = {'partner_id', 'date_from', 'date_to', 'applies_to_children', 'active'}

    def _bf_fq_users(self):
        """Les usagers que ces absences touchent : la personne elle-même, et
        tout le personnel pour une fermeture de son employeur
        (`applies_to_children`), comme `fq_days.non_working_days`."""
        Users = self.env['res.users'].sudo()
        users = Users.browse()
        for absence in self.sudo():
            partner = absence.partner_id
            if not partner:
                continue
            users |= Users.search([('partner_id', '=', partner.id)])
            if absence.applies_to_children:
                users |= Users.search(['|', ('partner_id.parent_id', '=', partner.id),
                                       ('company_id.partner_id', '=', partner.id)])
        return users

    @api.model_create_multi
    def create(self, vals_list):
        absences = super().create(vals_list)
        self.env['bf.gamification.profile']._bf_constance_changed(absences._bf_fq_users())
        return absences

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
