"""Le porteur d'un deuxième facteur : là où le facteur vit, jamais son contenu.

Un identifiant du coffre porte un mot de passe. Son deuxième facteur, lui, vit
ailleurs : dans un gestionnaire de mots de passe, dans une application
d'authentification, sur le téléphone d'une personne, sur un jeton matériel.

Ce modèle nomme ces endroits. Il ne contient aucun secret et n'en contiendra
jamais : le registre sait où chercher et qui peut produire un code, le coffre
garde. C'est la règle qui a fait écarter l'idée de ranger des graines TOTP dans
Odoo (napkin BF #25077).
"""

from odoo import api, fields, models


class ProjectCredentialVault(models.Model):
    """Un endroit où peut vivre un deuxième facteur."""

    _name = 'project.credential.vault'
    _description = "Porteur d'un deuxième facteur"
    _order = 'sequence, name'

    name = fields.Char(string='Nom', required=True, translate=True)
    sequence = fields.Integer(string='Séquence', default=10)
    active = fields.Boolean(string='Actif', default=True)

    kind = fields.Selection([
        ('password_manager', 'Gestionnaire de mots de passe'),
        ('authenticator', "Application d'authentification"),
        ('device', "Appareil d'une personne"),
        ('hardware', 'Jeton matériel'),
        ('offline', 'Support hors ligne'),
        ('other', 'Autre'),
    ], string='Nature', required=True, default='password_manager')

    shared = fields.Boolean(
        string='Accessible à plusieurs',
        default=True,
        help="Décoché, le facteur ne peut être produit que par la personne qui "
             "détient l'appareil ou le jeton. C'est ce qui rend un départ "
             "bloquant plutôt que gênant.",
    )

    base_url = fields.Char(
        string='Adresse',
        help="Adresse d'accueil du porteur, s'il en a une.",
    )
    item_url_pattern = fields.Char(
        string="Gabarit du lien vers l'élément",
        help="Gabarit d'adresse menant directement à l'élément chez son porteur. "
             "Le marqueur {ref} est remplacé par la référence de l'identifiant. "
             "Exemple : https://coffre.exemple.com/#/vault?itemId={ref}\n"
             "⚠️ Le lien mène au porteur ; il ne fait jamais entrer un secret ici.",
    )

    notes = fields.Text(string='Notes')

    credential_ids = fields.One2many(
        'project.credential', 'mfa_vault_id', string='Identifiants',
    )
    credential_count = fields.Integer(
        string="Nombre d'identifiants", compute='_compute_credential_count',
    )

    _sql_constraints = [
        ('name_uniq', 'unique(name)', 'Un porteur de ce nom existe déjà.'),
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
