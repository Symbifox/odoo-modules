"""Le rattachement, vu depuis le client et depuis le projet.

Le module porte `partner_id` et `project_id` sur chaque token depuis la
18.0.2.0.0, et l'application les regroupe. Il manquait le chemin inverse :
personne ne pouvait poser dans Odoo la question qui justifie ce module, à savoir
**qu'est-ce qu'on détient pour ce client, et qu'est-ce qu'on lui rend à la fin
du mandat**. Un champ qu'on ne peut pas interroger depuis l'endroit où la
question se pose n'est pas encore une fonction.

⚠️ Le compte se calcule **sous l'identité de qui regarde**, jamais en `sudo` :
la règle d'enregistrement veut qu'un coffre n'appartienne qu'à une personne, et
un compteur qui la contournerait dirait à un gestionnaire combien de tokens
quelqu'un d'autre détient pour ce client. C'est déjà un renseignement.

Rien ici ne révèle de graine : les métadonnées de rattachement sont en clair
depuis le premier jour, par choix, parce que c'est ce qui permet de chercher et
de rendre sans ouvrir le coffre.
"""

from odoo import api, fields, models, _


class ResPartner(models.Model):
    _inherit = 'res.partner'

    bf_otp_token_count = fields.Integer(
        string='Tokens OTP', compute='_compute_bf_otp_token_count',
    )

    def _compute_bf_otp_token_count(self):
        comptes = self.env['bf.otp.token']._compter_par('partner_id', self.ids)
        for partner in self:
            partner.bf_otp_token_count = comptes.get(partner.id, 0)

    def action_bf_otp_tokens(self):
        self.ensure_one()
        return self.env['bf.otp.token']._action_rattachement(
            'partner_id', self.id, self.display_name)


class ProjectProject(models.Model):
    _inherit = 'project.project'

    bf_otp_token_count = fields.Integer(
        string='Tokens OTP', compute='_compute_bf_otp_token_count',
    )

    def _compute_bf_otp_token_count(self):
        comptes = self.env['bf.otp.token']._compter_par('project_id', self.ids)
        for project in self:
            project.bf_otp_token_count = comptes.get(project.id, 0)

    def action_bf_otp_tokens(self):
        self.ensure_one()
        return self.env['bf.otp.token']._action_rattachement(
            'project_id', self.id, self.display_name)


class BfOtpTokenRattachement(models.Model):
    _inherit = 'bf.otp.token'

    @api.model
    def _compter_par(self, champ, ids):
        """Combien de MES tokens sont rattachés à chacun de ces enregistrements.

        ⚠️ Une seule requête groupée pour toute la liste : le compteur s'affiche
        sur une fiche client ouverte en vue liste, donc il se calcule autant de
        fois qu'il y a de lignes à l'écran.

        ⚠️ Renvoie zéro partout si la personne n'a pas le groupe : le champ ne
        devrait alors pas figurer dans sa vue, mais un calcul qui lève sur une
        fiche client casserait bien plus que ce compteur.
        """
        if not ids or not self.env.user.has_group('bf_otp.group_otp_user'):
            return {}
        lignes = self._read_group(
            [(champ, 'in', list(ids)), ('user_id', '=', self.env.uid)],
            [champ], ['__count'],
        )
        return {cible.id: nombre for cible, nombre in lignes}

    @api.model
    def _action_rattachement(self, champ, cible_id, titre):
        """Ouvre l'inventaire filtré sur ce client ou ce projet."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('Tokens OTP : %s', titre),
            'res_model': 'bf.otp.token',
            'view_mode': 'list,form',
            'domain': [(champ, '=', cible_id)],
            'context': {'default_%s' % champ: cible_id, 'search_default_%s' % champ: cible_id},
        }
