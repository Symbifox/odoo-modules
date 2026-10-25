from odoo import _, api, fields, models
from odoo.exceptions import AccessError
from odoo.tools.misc import clean_context


class GamificationXpTransaction(models.Model):
    _name = 'bf.gamification.xp.transaction'
    _description = 'Transaction XP'
    _order = 'date desc, id desc'

    user_id = fields.Many2one('res.users', string="Utilisateur", required=True,
                              ondelete='cascade', index=True)
    xp_amount = fields.Integer(string="Montant XP", required=True)
    source = fields.Selection([
        ('timesheet', 'Feuille de temps'),
        ('task', 'Tâche'),
        ('document', 'Document'),
        ('badge', 'Badge'),
        ('streak', 'Streak'),
        ('manual', 'Manuel'),
        ('hosting', 'Hébergement'),
        ('reward', 'Récompense'),
        ('message', 'Message'),
        ('helpdesk', 'Ticket'),
        ('activity', 'Activité'),
        ('meeting', 'Compte rendu'),
        ('agenda', 'Ordre du jour'),
        ('decision', 'Décision'),
        ('knowledge_item', 'Élément de matrice'),
        ('resolution', 'Résolution corporative'),
        ('email_triage', 'Triage courriel'),
        ('on_time', 'À temps'),
        ('caught_up', 'À jour'),
    ], string="Source", required=True, index=True)
    description = fields.Char(string="Description")
    reference = fields.Reference(
        selection=[
            ('account.analytic.line', 'Feuille de temps'),
            ('project.task', 'Tâche'),
            ('project.document', 'Document'),
            ('knowledge.item', 'Item connaissance'),
            ('bf.gamification.badge', 'Badge'),
            ('bf.gamification.reward.claim', 'Réclamation'),
            ('mail.message', 'Message'),
            ('helpdesk.ticket', 'Ticket'),
            ('meeting.record', 'Compte rendu'),
            ('meeting.agenda', 'Ordre du jour'),
            ('meeting.decision', 'Décision'),
            ('project.knowledge.item', 'Élément de matrice'),
            ('corporate.resolution', 'Résolution corporative'),
            ('bf.email', 'Courriel BF'),
            ('bf.gamification.caught.up', 'Journée à jour'),
        ],
        string="Référence",
    )
    date = fields.Datetime(string="Date", default=fields.Datetime.now, index=True)
    # Un jour de constance exige une transaction née d'un geste de la
    # personne elle-même, dans une requête. Les crons d'ingestion et de miroir
    # IMAP tournent souvent sous le compte d'une personne : sans cette marque,
    # ils comptaient des jours à sa place. Posée par `_bf_create_entry`, jamais par défaut : ce
    # qui ne prouve pas le geste n'en est pas un. L'historique d'avant
    # 18.0.3.0.0 est réputé né d'un geste (migration).
    from_request = fields.Boolean(
        string="Née d'un geste", readonly=True, default=False,
        help="Créée pendant une requête de la personne elle-même (un clic, un appel), "
             "et non par un cron, un import ou un collègue.")

    # Ce qu'un gestionnaire Fox Quest, qui a tous les droits sur le journal, ne
    # pose pas à la main : il antidatait des
    # transactions au nom d'un collègue (des jours de constance, jusqu'au palier)
    # ou effaçait les siennes. Seul le code de Fox Quest, en sudo, y touche.
    _BF_LOCKED = ('user_id', 'date', 'from_request', 'source', 'xp_amount', 'reference')

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            now = fields.Datetime.now()
            for vals in vals_list:
                vals.update(date=now, from_request=False)
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and set(self._BF_LOCKED) & set(vals):
            raise AccessError(_("Une transaction d'XP ne change ni de personne, ni de date, ni "
                                "de source, ni de montant, ni de référence."))
        return super().write(vals)

    def unlink(self):
        if not self.env.su:
            raise AccessError(_("Une transaction d'XP ne se supprime pas : le journal garde tout."))
        return super().unlink()

    @api.model
    def _bf_create_entry(self, user, amount, source, description, reference=None,
                         date=None, from_request=None):
        """La seule porte du code de Fox Quest vers le journal d'XP.

        La date et « née d'un geste » sont posées ici, jamais lues du contexte :
        un `default_date` passé par RPC à un geste (faire une activité, fermer
        une tâche) antidatait l'XP et fabriquait des jours de constance, jusqu'au
        palier. Le contexte perd aussi ses autres `default_*` (la référence,
        quand il n'y en a pas). `from_request` : par défaut, le geste de la
        personne elle-même dans une requête.
        """
        if from_request is None:
            from_request = self.env['bf.gamification.profile']._bf_is_own_gesture(user)
        vals = {
            'user_id': user.id,
            'xp_amount': amount,
            'source': source,
            'description': description,
            'date': date or fields.Datetime.now(),
            'from_request': bool(from_request),
        }
        if reference:
            vals['reference'] = (reference if isinstance(reference, str)
                                 else '%s,%s' % (reference._name, reference.id))
        return self.sudo().with_context(clean_context(self.env.context)).create(vals)
