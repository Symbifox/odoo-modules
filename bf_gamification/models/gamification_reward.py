from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools.misc import clean_context


class GamificationReward(models.Model):
    _name = 'bf.gamification.reward'
    _description = 'Récompense Fox Quest'
    _order = 'xp_cost asc'

    name = fields.Char(string="Nom", required=True)
    description = fields.Html(string="Description")
    image = fields.Image(string="Image", max_width=256, max_height=256)
    xp_cost = fields.Integer(string="Coût XP", required=True)
    available = fields.Boolean(string="Disponible", default=True)
    stock = fields.Integer(string="Stock", default=0,
                           help="0 = illimité")
    limit_per_user = fields.Integer(string="Limite par utilisateur", default=0,
                                     help="0 = illimité")
    category = fields.Selection([
        ('time_off', 'Congé'),
        ('perk', 'Avantage'),
        ('recognition', 'Reconnaissance'),
        ('custom', 'Personnalisé'),
    ], string="Catégorie", default='perk', required=True)
    active = fields.Boolean(string="Actif", default=True)
    claim_count = fields.Integer(string="Réclamations", compute='_compute_claim_count')
    # La récompense que le palier de constance ouvre tout seul. Elle
    # ne s'achète pas (indisponible au catalogue) et ne coûte aucun XP.
    is_constance_leave = fields.Boolean(
        string="Congé de constance", readonly=True,
        help="Ouverte par Fox Quest quand une personne qui participe atteint "
             "le palier de constance. Ne s'achète pas au catalogue.")

    # Un gestionnaire Fox Quest, qui écrit les récompenses, faisait d'une
    # récompense ordinaire un congé de constance (ou l'inverse) le temps de
    # s'approuver sa propre réclamation. Seules les données du module,
    # chargées en sudo, posent ce drapeau.
    def _bf_check_constance_flag(self, vals):
        if self.env.su or 'is_constance_leave' not in vals:
            return
        flag = bool(vals['is_constance_leave'])
        if (not self and flag) or any(reward.is_constance_leave != flag for reward in self):
            raise AccessError(_("Le congé de constance ne se pose pas sur une récompense "
                                "à la main."))

    # `bf_avatar_gamification` approuve en sudo toute réclamation dont la
    # récompense porte des pièces d'avatar. Un gestionnaire qui en posait sur le
    # congé de constance faisait approuver chaque demande ouverte par le palier,
    # sans personne pour la décider. Le verrou est ici, côté Fox Quest, par le
    # préfixe `avatar_` : il tient sans lire le module ni passer par le contexte.
    # (À la création, une récompense de constance ne se pose pas hors sudo : le
    # drapeau ci-dessus la refuse déjà.)
    def _bf_check_constance_avatar(self, vals):
        if self.env.su or not any(name.startswith('avatar_') for name in vals):
            return
        if any(reward.is_constance_leave for reward in self):
            raise AccessError(_("Le congé de constance ne vend pas de pièces d'avatar : "
                                "elles s'approuveraient sans personne pour décider."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self.browse()._bf_check_constance_flag(vals)
        if not self.env.su:
            # Le drapeau se lit dans `vals` : un `default_is_constance_leave` du
            # contexte de l'appelant le posait sans passer par la garde.
            return super(GamificationReward, self.with_context(
                clean_context(self.env.context))).create(vals_list)
        return super().create(vals_list)

    def write(self, vals):
        self._bf_check_constance_flag(vals)
        self._bf_check_constance_avatar(vals)
        return super().write(vals)

    def _compute_claim_count(self):
        Claim = self.env['bf.gamification.reward.claim']
        for reward in self:
            reward.claim_count = Claim.search_count([
                ('reward_id', '=', reward.id),
                ('state', '!=', 'refused'),
            ])


class GamificationRewardClaim(models.Model):
    _name = 'bf.gamification.reward.claim'
    _description = 'Réclamation de récompense'
    _order = 'date_claimed desc'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    user_id = fields.Many2one('res.users', string="Utilisateur", required=True,
                              ondelete='cascade', default=lambda self: self.env.user)
    reward_id = fields.Many2one('bf.gamification.reward', string="Récompense",
                                required=True, ondelete='restrict')
    state = fields.Selection([
        ('pending', 'En attente'),
        ('approved', 'Approuvée'),
        ('refused', 'Refusée'),
        ('consumed', 'Consommée'),
    ], string="État", default='pending', required=True, tracking=True)
    xp_spent = fields.Integer(string="XP dépensé")
    date_claimed = fields.Datetime(string="Date de réclamation",
                                    default=fields.Datetime.now)
    date_processed = fields.Datetime(string="Date de traitement")
    approved_by = fields.Many2one('res.users', string="Approuvé par")
    refusal_reason = fields.Text(string="Raison du refus")
    leave_days = fields.Float(
        string="Jours de congé", readonly=True,
        help="Jours accordés si la demande de congé de constance est approuvée.")
    is_constance_leave = fields.Boolean(related='reward_id.is_constance_leave')

    @api.depends('reward_id.name', 'user_id.name')
    def _compute_display_name(self):
        # Sans nom, l'objet du courriel de l'activité, sa colonne « Document » et
        # l'adresse de réponse montraient « bf.gamification.reward.claim,77 ».
        for claim in self:
            claim.display_name = ' – '.join(
                name for name in (claim.reward_id.name, claim.user_id.name) if name
            ) or _('Réclamation de récompense')

    # Ce qu'un usager ne décide pas lui-même en réclamant : l'état,
    # l'approbation et le prix payé. Un gestionnaire, lui, peut tout poser. Posés
    # explicitement et non plus seulement retirés des valeurs : ce que `vals` ne
    # dit pas, Odoo le remplit depuis `default_*` dans le contexte de l'appelant,
    # et `default_state=approved` donnait une réclamation déjà approuvée, XP
    # débité, sans gestionnaire.
    _CLAIM_PRIVILEGED = {'state': 'pending', 'approved_by': False, 'date_processed': False,
                         'xp_spent': 0, 'leave_days': 0}
    # Ce qui ne change plus après la création, sauf par le code de Fox Quest en
    # sudo : un gestionnaire changeait la récompense de SA réclamation
    # en congé de constance pour se l'approuver, ou passait la sienne au nom
    # d'un collègue le temps de l'approuver. `approved_by` : qui a
    # approuvé ne se réécrit pas, il se pose par « Approuver » seulement.
    _CLAIM_FROZEN = ('user_id', 'reward_id', 'leave_days', 'xp_spent', 'approved_by')
    # Ce qui décide d'une demande : la bénéficiaire n'y touche jamais.
    _CLAIM_DECISION = ('state', 'approved_by', 'date_processed')

    @api.model_create_multi
    def create(self, vals_list):
        manager = self.env.su or self.env.user.has_group(
            'bf_gamification.group_gamification_manager')
        # Un non-gestionnaire crée sous un contexte sans `default_*` : rien de ce
        # qu'il n'a pas le droit de poser ne vient du contexte.
        base = self if manager else self.with_context(clean_context(self.env.context))
        for vals in vals_list:
            if not manager:
                vals.update(self._CLAIM_PRIVILEGED)
                vals['user_id'] = self.env.user.id
            reward = self.env['bf.gamification.reward'].browse(vals.get('reward_id'))
            user = self.env['res.users'].browse(vals.get('user_id', self.env.user.id))

            # Le palier de constance ouvre sa demande lui-même : seul
            # le code en sudo y arrive, jamais un appel RPC (qui ne pose pas su).
            if reward.is_constance_leave:
                if not (self.env.su and self.env.context.get('bf_gamification_constance')):
                    raise UserError("Cette récompense s'obtient par la constance, pas au catalogue.")
                vals['xp_spent'] = 0
                vals['state'] = 'pending'
                continue

            # Check availability
            if not reward.available:
                raise UserError("Cette récompense n'est pas disponible.")

            # Check stock
            if reward.stock > 0:
                claimed = self.search_count([
                    ('reward_id', '=', reward.id),
                    ('state', 'in', ['pending', 'approved', 'consumed']),
                ])
                if claimed >= reward.stock:
                    raise UserError("Stock épuisé pour cette récompense.")

            # Check per-user limit
            if reward.limit_per_user > 0:
                user_claims = self.search_count([
                    ('reward_id', '=', reward.id),
                    ('user_id', '=', user.id),
                    ('state', 'in', ['pending', 'approved', 'consumed']),
                ])
                if user_claims >= reward.limit_per_user:
                    raise UserError("Limite de réclamations atteinte pour cette récompense.")

            # Check XP balance
            profile = self.env['bf.gamification.profile']._get_or_create_profile(user)
            profile._compute_xp()
            if profile.xp_balance < reward.xp_cost:
                raise UserError(
                    "XP insuffisant. Votre solde est de %d XP, il en faut %d." % (
                        profile.xp_balance, reward.xp_cost))

            vals['xp_spent'] = reward.xp_cost

        claims = super(GamificationRewardClaim, base).create(vals_list)

        # Deduct XP for each claim
        for claim in claims:
            if not claim.xp_spent:
                continue
            profile = self.env['bf.gamification.profile']._get_or_create_profile(claim.user_id)
            self.env['bf.gamification.xp.transaction']._bf_create_entry(
                claim.user_id, -claim.xp_spent, 'reward',
                'Récompense : %s' % claim.reward_id.name, reference=claim)
            profile._compute_xp()

        return claims

    def unlink(self):
        # Un congé de constance est une décision sur des jours de congé : sa
        # trace ne s'efface pas à la main, pas plus qu'à la remise à zéro
        #.
        if not self.env.su and any(claim.is_constance_leave for claim in self):
            raise UserError(_("Une demande de congé de constance ne se supprime pas."))
        return super().unlink()

    def write(self, vals):
        # Le formulaire et le RPC passent par ici aussi, pas seulement les boutons.
        if self.env.su:
            return super().write(vals)
        frozen = self._bf_frozen_changes(vals)
        if frozen:
            raise UserError(_("Une réclamation ne change plus de personne, de récompense ni "
                              "de quantité après sa création (%s).") % ', '.join(sorted(frozen)))
        guarded = bool(set(self._CLAIM_DECISION) & set(vals))
        if guarded:
            self._bf_check_not_impersonated()
        # Avant ET après : la garde lit la réclamation telle qu'elle sera décidée.
        if guarded:
            self._bf_check_not_own_leave()
        res = super().write(vals)
        if guarded:
            self._bf_check_not_own_leave()
        return res

    def _bf_frozen_changes(self, vals):
        """Les champs figés que `vals` changerait vraiment sur l'une des réclamations."""
        changed = set()
        for name in set(self._CLAIM_FROZEN) & set(vals):
            field = self._fields[name]
            for claim in self:
                if field.convert_to_write(claim[name], claim) != vals[name]:
                    changed.add(name)
        return changed

    def _bf_check_state(self, allowed, action):
        """Vérifie l'état COURANT, relu en base : une demande déjà refusée ne se
        rembourse pas deux fois, une demande refusée ne s'approuve plus."""
        self.invalidate_recordset(['state'])
        labels = dict(self._fields['state']._description_selection(self.env))
        for claim in self:
            if claim.state not in allowed:
                raise UserError(_("La réclamation est déjà « %(state)s » : elle ne peut plus "
                                  "être %(action)s.", state=labels.get(claim.state, claim.state),
                                  action=action))

    def _bf_check_not_own_leave(self):
        """Un congé de constance se décide par une autre personne que la
        bénéficiaire : c'est ce qui garde la décision humaine."""
        for claim in self:
            if claim.is_constance_leave and claim.user_id == self.env.user:
                raise UserError("Une demande de congé de constance se décide par une autre "
                                "personne que la bénéficiaire.")

    def _bf_check_not_impersonated(self):
        """Une demande de congé de constance ne se décide pas sous usurpation du
        support (`bf_impersonate`) : la décision serait prise sous l'identité de
        l'approbateur, par quelqu'un d'autre que lui."""
        if any(claim.is_constance_leave for claim in self):
            self.env['bf.gamification.profile']._bf_refuse_if_impersonated(_(
                "Vous voyez Symbifox sous le compte d'une autre personne (usurpation du "
                "support) : une demande de congé de constance ne se décide pas ainsi, la "
                "décision serait prise au nom de cette personne. Revenez à votre propre "
                "compte pour l'approuver ou la refuser."))

    def action_approve(self):
        self._bf_check_state(('pending',), _("approuvée"))
        self._bf_check_not_own_leave()
        self._bf_check_not_impersonated()
        # `approved_by` est figé hors du code de Fox Quest : on
        # écrit en sudo, après avoir vérifié que qui approuve a le droit d'écrire
        # la réclamation (une méthode publique se joint aussi par RPC).
        self.check_access('write')
        self.sudo().write({
            'state': 'approved',
            'date_processed': fields.Datetime.now(),
            'approved_by': self.env.user.id,
        })
        self.activity_unlink(['mail.mail_activity_data_todo'])

    def action_refuse(self):
        self._bf_check_state(('pending', 'approved'), _("refusée"))
        self._bf_check_not_own_leave()
        self._bf_check_not_impersonated()
        for claim in self:
            claim.state = 'refused'
            claim.date_processed = fields.Datetime.now()
            if not claim.xp_spent:
                continue
            # Refund XP
            self.env['bf.gamification.xp.transaction']._bf_create_entry(
                claim.user_id, claim.xp_spent, 'reward',
                'Remboursement : %s' % claim.reward_id.name, reference=claim)
            self.env['bf.gamification.profile']._get_or_create_profile(claim.user_id)._compute_xp()
        self.activity_unlink(['mail.mail_activity_data_todo'])

    def action_consume(self):
        self.filtered(lambda c: c.state == 'approved').write({'state': 'consumed'})

    def _bf_leave_approver(self):
        """Qui décide : le gestionnaire de la fiche employé s'il gère Fox Quest,
        sinon un gestionnaire Fox Quest, sinon un administrateur (une société
        peut n'avoir aucun gestionnaire Fox Quest) ; jamais la personne elle-même."""
        self.ensure_one()
        user = self.user_id.sudo()
        group = self.env.ref('bf_gamification.group_gamification_manager')
        admins = self.env.ref('base.group_system')
        Profile = self.env['bf.gamification.profile']

        def eligible(candidate, among=group):
            return (candidate and candidate != user and Profile._bf_is_player(candidate)
                    and among in candidate.groups_id)

        employee = user.employee_id if 'employee_id' in user._fields else False
        boss = employee.parent_id.user_id if employee and employee.parent_id else False
        if eligible(boss):
            return boss
        managers = group.sudo().users.filtered(eligible).sorted('id')[:1]
        if managers:
            return managers
        return admins.sudo().users.filtered(lambda u: eligible(u, admins)).sorted('id')[:1]

    def _bf_ask_approval(self):
        # Activité (et note) sans le contexte de l'appelant : un
        # appelant déjà en sudo garde ses `default_*` (le cœur ne les retire qu'au
        # passage en sudo), et `default_active=False` créait l'activité archivée,
        # invisible de l'approbateur. La note, `mail` la nettoie de lui-même.
        for claim in self.sudo().with_context(clean_context(self.env.context)):
            approver = claim._bf_leave_approver()
            days = ('%g' % claim.leave_days).replace('.', ',')
            body = ("Palier de constance atteint : demande de %s jour(s) de congé, "
                    "à approuver ou refuser par un gestionnaire." % days)
            if not approver:
                body += (" Aucun gestionnaire Fox Quest ni administrateur autre que la "
                         "personne n'a été trouvé : la demande attend qu'on en nomme un.")
            # La bénéficiaire et l'approbatrice suivent la demande ; pas l'acteur
            # du geste qui l'a ouverte.
            claim.message_subscribe(partner_ids=(claim.user_id.partner_id
                                                 | approver.partner_id).ids)
            claim.message_post(body=body, message_type='notification',
                               subtype_xmlid='mail.mt_note')
            if approver:
                claim.activity_schedule(
                    'mail.mail_activity_data_todo',
                    summary="Approuver ou refuser la demande",
                    user_id=approver.id)
