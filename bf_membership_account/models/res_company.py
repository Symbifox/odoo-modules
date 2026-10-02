from odoo import fields, models


class ResCompany(models.Model):
    """Ce que le reçu fiscal de cotisation lit sur l'organisme.

    Les noms portent le préfixe `membership_` exprès : le module des reçus de
    dons (`bf_receipt_ca`, sous une autre licence) pose ses propres champs sur
    la société, et les deux modules doivent pouvoir vivre dans la même base
    sans se marcher dessus.
    """

    _inherit = "res.company"

    membership_charity_number = fields.Char(
        string="Numéro d'enregistrement (organisme de bienfaisance)",
        help="Le numéro attribué par l'ARC, de la forme 123456789 RR 0001. Imprimé "
             "sur chaque reçu fiscal de cotisation.",
    )
    membership_receipt_signer = fields.Char(
        string="Personne autorisée à signer les reçus",
        help="Une personne responsable désignée par l'organisme (art. 3501 du Règlement).",
    )
    membership_receipt_signer_title = fields.Char(string="Titre de la personne autorisée")
    # 🔴 Réservée au responsable des membres et à l'administration, qui la
    # pose dans les réglages : sans `groups`, la route `/web/image` la sert à
    # quiconque peut lire la société, inconnu compris, et une signature
    # téléchargée suffit à contrefaire un reçu officiel. Le reçu la recopie en
    # superutilisateur au moment de le préparer.
    membership_receipt_signature = fields.Image(
        string="Signature", max_width=600, max_height=200,
        groups="base.group_system,bf_membership.group_membership_manager",
        help="Image de la signature. Sans image, le reçu laisse une ligne à signer à la main.",
    )
    membership_receipt_place = fields.Char(
        string="Lieu de délivrance des reçus",
        help="La localité où les reçus sont délivrés. À défaut, la ville de la société.",
    )
