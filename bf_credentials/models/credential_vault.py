"""Le porteur d'un deuxième facteur : là où le facteur vit, jamais son contenu.

Un identifiant du coffre porte un mot de passe. Son deuxième facteur, lui, vit
ailleurs : dans un gestionnaire de mots de passe, dans une application
d'authentification, sur le téléphone d'une personne, sur un jeton matériel.

Ce modèle nomme ces endroits. Il ne contient aucun secret et n'en contiendra
jamais : le registre sait où chercher et qui peut produire un code, le coffre
garde. C'est la règle qui a fait écarter l'idée de ranger des graines TOTP dans
Odoo.
"""

from odoo import api, fields, models


class ProjectCredentialVault(models.Model):
    """Un endroit où peut vivre un deuxième facteur."""

    _name = 'project.credential.vault'
    _description = "Holder of a second factor"
    _order = 'sequence, name'

    name = fields.Char(string="Name", required=True, translate=True)
    sequence = fields.Integer(string="Sequence", default=10)
    active = fields.Boolean(string="Active", default=True)

    kind = fields.Selection([
        ('password_manager', "Password manager"),
        ('authenticator', "Authenticator app"),
        ('device', "A person's device"),
        ('hardware', "Hardware token"),
        ('offline', "Offline medium"),
        ('other', "Other"),
    ], string="Kind", required=True, default='password_manager')

    shared = fields.Boolean(
        string="Shared access",
        default=True,
        help="Unchecked, the factor can only be produced by the person "
             "who holds the device or token. This is what makes a "
             "departure blocking rather than inconvenient.",
    )

    base_url = fields.Char(
        string="Address",
        help="Home page of the holder, if it has one.",
    )
    item_url_pattern = fields.Char(
        string="Item link template",
        help="Address template leading straight to the item at its "
             "holder. The {ref} marker is replaced by the credential's "
             "reference. Example: "
             "https://vault.example.com/#/vault?itemId={ref}\n⚠️ The link "
             "leads to the holder; it never brings a secret in here.",
    )

    notes = fields.Text(string='Notes')

    credential_ids = fields.One2many(
        'project.credential', 'mfa_vault_id', string="Credentials",
    )
    credential_count = fields.Integer(
        string="Credential count", compute='_compute_credential_count',
    )

    _sql_constraints = [
        ('name_uniq', 'unique(name)', "A holder with this name already exists."),
    ]

    @api.depends('credential_ids')
    def _compute_credential_count(self):
        # `read_group` plutôt qu'un len() par enregistrement : la liste des
        # porteurs affiche la colonne, et un One2many parcouru ligne par ligne
        # ferait une requête par porteur.
        comptes = dict(self.env['project.credential']._read_group(
            [('mfa_vault_id', 'in', self.ids)], ['mfa_vault_id'], ['__count'],
        ))
        for vault in self:
            vault.credential_count = comptes.get(vault, 0)

    def action_open_credentials(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.name,
            'res_model': 'project.credential',
            'view_mode': 'list,kanban,form',
            'domain': [('mfa_vault_id', '=', self.id)],
            'context': {'default_mfa_vault_id': self.id},
        }
