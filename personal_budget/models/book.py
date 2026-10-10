"""Budgets (carnets) : l'unité de propriété et de partage du module.

Une instance de ménage porte plusieurs personnes dans une même base. Chaque
donnée du budget appartient à un **budget** (`personal.budget.book`), et c'est
le budget qui décide qui la voit :

* la personne propriétaire (`user_id`) ;
* les personnes avec qui elle l'a partagé EXPRÈS (`member_ids`).

Personne d'autre, administratrices comprises : les règles d'enregistrement
sont globales (voir `security/budget_record_rules.xml`). Le superutilisateur
(`sudo()`, tâches planifiées) n'y est pas soumis, et un administrateur système
peut lever les règles elles-mêmes : l'isolation vaut entre les personnes, pas
contre l'administration de la base.

Odoo lit certains noms en superutilisateur (message d'erreur d'accès en mode
debug, many2one d'une fiche à soi, message d'une contrainte). Le nom affiché
d'un budget et de ses fiches se calcule donc selon la personne qui lit : un
libellé neutre pour qui ne voit pas le budget.

Chaque personne reçoit, à sa première visite, un budget personnel non partagé
(« Mon budget »). Un budget de couple est un deuxième budget, créé par l'une
des deux personnes et partagé avec l'autre.
"""
from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.models import NewId

# Le même refus pour un budget d'autrui et pour un budget qui n'existe pas :
# rien ne distingue les deux (le tableau de bord dit la même chose).
REFUS_BUDGET = "This budget does not exist or is not shared with you."


def valeur_id(value):
    """L'id réel visé par une valeur many2one, sous toutes ses formes (entier,
    chaîne, dict, liste, enregistrement neuf), ou False."""
    if isinstance(value, models.BaseModel):
        value = value._origin.id if value else False
    if isinstance(value, dict):
        value = value.get('id')
    if isinstance(value, (list, tuple)):
        value = value[0] if value else False
    if isinstance(value, NewId):
        value = value.origin
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int):
        return False
    return value


class PersonalBudgetBook(models.Model):
    _name = 'personal.budget.book'
    _description = 'Budget'
    _order = 'is_default desc, name, id'

    name = fields.Char(string="Name", required=True)
    user_id = fields.Many2one(
        'res.users', string="Owner", required=True, index=True,
        default=lambda self: self.env.user, ondelete='restrict',
    )
    member_ids = fields.Many2many(
        'res.users', 'personal_budget_book_member_rel', 'book_id', 'user_id',
        string="Shared with",
        help="People of this database who see and edit this budget. "
             "Nobody else has access.",
    )
    is_shared = fields.Boolean(
        string="Shared", compute='_compute_is_shared', store=True,
    )
    is_default = fields.Boolean(
        string="Default budget",
        help="Budget used by default for the owner's new entries.",
    )
    active = fields.Boolean(string="Active", default=True)
    notes = fields.Text(string="Notes")
    is_owner = fields.Boolean(
        string="I am the owner", compute='_compute_is_owner',
    )

    @api.depends('name', 'user_id', 'member_ids')
    @api.depends_context('uid')
    def _compute_display_name(self):
        """« Private budget » pour qui ne voit pas ce budget. ``sudo()`` garde
        ``env.uid`` : le calcul sait qui lit, même quand Odoo lit le nom en sudo."""
        uid = self.env.uid
        for book, lu in zip(self, self.sudo()):
            book.display_name = lu.name if lu._is_reader(uid) else _("Private budget")

    def _is_reader(self, uid):
        """La personne ``uid`` voit-elle ce budget : propriétaire, ou partagé avec elle ?"""
        self.ensure_one()
        return uid == SUPERUSER_ID or self.user_id.id == uid or uid in self.member_ids.ids

    @api.depends('member_ids')
    def _compute_is_shared(self):
        for book in self:
            book.is_shared = bool(book.member_ids)

    @api.depends_context('uid')
    @api.depends('user_id')
    def _compute_is_owner(self):
        for book in self:
            book.is_owner = book.user_id == self.env.user

    @api.constrains('user_id', 'member_ids')
    def _check_members(self):
        for book in self:
            if book.user_id in book.member_ids:
                raise ValidationError(_(
                    "The owner of a budget does not need to be listed among "
                    "the people it is shared with."))
            if any(u.share for u in book.member_ids):
                raise ValidationError(_(
                    "A budget can only be shared with people who have an internal account in this database."))

    @api.constrains('is_default', 'user_id')
    def _check_single_default(self):
        for book in self.filtered('is_default'):
            others = self.sudo().search_count([
                ('user_id', '=', book.user_id.id),
                ('is_default', '=', True),
                ('id', '!=', book.id),
            ])
            if others:
                raise ValidationError(_(
                    "A person has only one default budget."))

    @api.model_create_multi
    def create(self, vals_list):
        books = super().create(vals_list)
        books._check_members()
        return books

    def write(self, vals):
        res = super().write(vals)
        # Le contrôle d'accès d'Odoo passe AVANT l'écriture : il ne voit pas une
        # propriété qu'on céderait à quelqu'un d'autre. On le rejoue après.
        if not self.env.su and 'user_id' in vals:
            self.check_access('write')
        return res

    def unlink(self):
        for book in self:
            if book._has_content():
                raise UserError(_(
                    "The budget \"%s\" contains data: archive it instead of "
                    "deleting it.", book.name))
        return super().unlink()

    def _has_content(self):
        self.ensure_one()
        for model in self._content_models():
            if self.env[model].sudo().with_context(active_test=False).search_count(
                    [('book_id', '=', self.id)], limit=1):
                return True
        return False

    @api.model
    def _content_models(self):
        return [
            'personal.budget.category', 'personal.budget.contributor',
            'personal.budget.transaction', 'personal.budget.plan',
            'personal.budget.recurring', 'personal.budget.loan',
            'personal.budget.cheque', 'personal.budget.invoice',
            'personal.budget.share.line',
        ]

    @api.model
    def _default_book(self):
        """Budget par défaut de la personne connectée, créé à la première visite.

        Retourne un recordset vide en superutilisateur (cron, `sudo()`) : ces
        chemins-là doivent nommer leur budget, jamais en inventer un.
        """
        if self.env.su:
            return self.browse()
        user = self.env.user
        # Jamais de repli sur « le premier budget venu » : ce pourrait être un
        # budget partagé, et une saisie qu'on croyait privée y atterrirait.
        book = self.search([('user_id', '=', user.id), ('is_default', '=', True)], limit=1)
        if not book:
            book = self.create({
                'name': self._default_book_name(),
                'user_id': user.id,
                'is_default': True,
            })
        return book

    @api.model
    def _default_book_name(self):
        """Nom du budget personnel, dans la langue de la personne (donnée, pas libellé)."""
        return _("My budget")

    def action_open_transactions(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id(
            'personal_budget.action_budget_transaction')
        action['domain'] = [('book_id', '=', self.id)]
        action['context'] = {'search_default_this_year': 1}
        return action


class PersonalBudgetBookMixin(models.AbstractModel):
    """Rattache une donnée à un budget et garde ce rattachement honnête.

    Les modèles qui portent leur budget en propre héritent de ce mixin tel quel.
    Ceux qui le tiennent d'un parent (catégorie, prêt) redéfinissent `book_id`
    en champ lié stocké : la donnée suit son parent, elle ne peut pas le
    contredire.
    """
    _name = 'personal.budget.book.mixin'
    _description = 'Budget ownership'

    book_id = fields.Many2one(
        'personal.budget.book', string="Budget", required=True, index=True,
        ondelete='restrict',
        default=lambda self: self.env['personal.budget.book']._default_book(),
    )

    # Champs dont l'écriture peut déplacer l'enregistrement vers un autre budget.
    _book_moving_fields = ('book_id',)
    # Autres many2one vers une fiche de budget : la cible doit être lisible aussi.
    _book_target_fields = ()

    @api.depends(lambda self: ((self._rec_name,) if self._rec_name else ()) + ('book_id',))
    @api.depends_context('uid')
    def _compute_display_name(self):
        """Un libellé neutre pour qui ne voit pas le budget de la fiche (voir le
        nom du budget, plus haut)."""
        super()._compute_display_name()
        uid = self.env.uid
        if uid == SUPERUSER_ID:
            return
        for rec, lu in zip(self, self.sudo()):
            if lu.book_id and not lu.book_id._is_reader(uid):
                rec.display_name = _("Private budget entry")

    def _check_book_targets(self, values):
        """Refuse, AVANT toute écriture, une cible que la personne ne lit pas.

        Sans ce contrôle, l'INSERT ou l'UPDATE passe avant les règles : une
        contrainte d'unicité (« existe déjà ») ou le message d'une contrainte dit
        alors ce que contient le budget d'autrui. Une cible absente reçoit le même
        refus qu'une cible d'autrui.
        """
        if self.env.su or not values:
            return
        for fname in self._book_moving_fields + self._book_target_fields:
            if fname not in values:
                continue
            target_id = valeur_id(values[fname])
            if not target_id:
                continue
            target = self.env[self._fields[fname].comodel_name].browse(target_id)
            if not target.sudo().exists() or not target.has_access('read'):
                raise AccessError(_(REFUS_BUDGET))

    @api.model
    def default_get(self, fields_list):
        # Les défauts du contexte (`default_*`) et ceux de `ir.default` passent
        # par ici, à la création comme au premier `onchange`.
        res = super().default_get(fields_list)
        self._check_book_targets(res)
        return res

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._check_book_targets(vals)
        return super().create(vals_list)

    def onchange(self, values, field_names, fields_spec):
        # Les champs liés d'une fiche neuve se calculent en superutilisateur :
        # une cible d'autrui y rendrait son budget et son type.
        self._check_book_targets(values)
        return super().onchange(values, field_names, fields_spec)

    def write(self, vals):
        self._check_book_targets(vals)
        res = super().write(vals)
        # `write()` vérifie les règles AVANT d'écrire : il laisserait déplacer une
        # donnée vers le budget privé de quelqu'un d'autre. On revérifie après,
        # sur les valeurs écrites.
        if not self.env.su and any(f in vals for f in self._book_moving_fields):
            try:
                self.check_access('write')
            except AccessError:
                raise AccessError(_(
                    "You cannot move a record to a budget that is not shared "
                    "with you.")) from None
        return res

    def _check_same_book(self, field_name):
        """Le champ relationnel `field_name` doit viser le même budget. Le message
        ne nomme pas la cible : elle peut être d'un budget que la personne ne voit pas."""
        for rec in self.sudo():
            target = rec[field_name]
            if target and target.book_id != rec.book_id:
                raise ValidationError(_(
                    "The %(field)s belongs to a different budget than this entry.",
                    field=rec._fields[field_name]._description_string(rec.env)))
