"""Pièces que le syndicat rend consultables, et le régime de chacune.

⚠️ **Le registre ne se publie pas par distraction.** L'art. 1070 C.c.Q. énumère
ce que le syndicat tient au registre : procès-verbaux de l'assemblée ET du
conseil, résolutions écrites, règlement de l'immeuble, états financiers,
déclaration de copropriété, contrats, plan cadastral, plans et devis,
certificats de localisation, carnet d'entretien, étude du fonds de prévoyance,
description des parties privatives.

L'art. 1070.1 dit comment il se consulte, et c'est là que tout se joue :

  « La consultation se fait en présence d'un administrateur ou d'une personne
  désignée par le conseil d'administration, à des heures raisonnables et selon
  les modalités prévues au règlement de l'immeuble. Tout copropriétaire a le
  droit, moyennant des frais raisonnables, d'obtenir copie du contenu du
  registre et de ces documents. »

Trois conditions, donc : la présence, l'heure raisonnable, le règlement de
l'immeuble. Et la copie, elle, se paie. Déposer une pièce du registre sur un
portail accessible en tout temps donne **davantage** que ce que l'article
impose. Ce n'est pas illégal, c'est une décision du syndicat : le module la lui
fait prendre expressément (`register_ack`) et la consigne, plutôt que de la
prendre à sa place.

⚠️ **Le locataire figure au registre sans y avoir droit.** L'art. 1070 al. 1
veut son nom et son adresse ; l'art. 1070.1 réserve la consultation au
copropriétaire. Un document d'auditoire « copropriétaires » ne doit donc jamais
atteindre un occupant, et cela s'applique par une règle d'accès, pas par un
`invisible` dans une vue.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError

# Art. 1070 C.c.Q. : ce que le registre contient. Une pièce de cette liste
# tombe sous le régime de consultation de l'art. 1070.1.
REGISTER_CATEGORIES = [
    ("minutes_assembly", "Procès-verbal d'assemblée"),
    ("minutes_board", "Procès-verbal du conseil d'administration"),
    ("written_resolution", "Résolution écrite"),
    ("by_laws", "Règlement de l'immeuble"),
    ("financial_statements", "États financiers"),
    ("declaration", "Déclaration de copropriété"),
    ("contract", "Contrat auquel le syndicat est partie"),
    ("cadastral_plan", "Plan cadastral"),
    ("plans_specs", "Plans et devis"),
    ("location_certificate", "Certificat de localisation"),
    ("maintenance_log", "Carnet d'entretien"),
    ("contingency_study", "Étude du fonds de prévoyance"),
    ("private_description", "Description des parties privatives"),
]
# Hors registre : le syndicat en dispose librement.
FREE_CATEGORIES = [
    ("notice", "Avis aux copropriétaires ou aux occupants"),
    ("guide", "Guide ou consigne pratique"),
    ("form", "Formulaire"),
    ("other", "Autre document"),
]
DOCUMENT_CATEGORIES = REGISTER_CATEGORIES + FREE_CATEGORIES
REGISTER_KEYS = tuple(key for key, _label in REGISTER_CATEGORIES)

AUDIENCES = [
    ("owners", "Copropriétaires seulement"),
    ("occupants", "Occupants seulement"),
    ("all", "Copropriétaires et occupants"),
]


class BfPropertyDocument(models.Model):
    _name = "bf.property.document"
    _description = "Document consultable du syndicat"
    _inherit = ["mail.thread"]
    _order = "date desc, id desc"

    name = fields.Char(string="Titre", required=True, tracking=True)
    active = fields.Boolean(default=True)
    syndicat_id = fields.Many2one(
        "bf.property.syndicat",
        string="Syndicat",
        required=True,
        ondelete="cascade",
        index=True,
        tracking=True,
    )
    company_id = fields.Many2one(
        related="syndicat_id.company_id", store=True, string="Société"
    )
    building_id = fields.Many2one(
        "bf.property.building",
        string="Immeuble",
        domain="[('syndicat_id', '=', syndicat_id)]",
        help="Laisser vide quand la pièce vise toute la copropriété.",
    )
    category = fields.Selection(
        DOCUMENT_CATEGORIES,
        string="Nature",
        required=True,
        default="notice",
        tracking=True,
        help="La nature décide du régime : les treize premières valeurs sont "
             "des pièces du registre de l'art. 1070 C.c.Q., dont la "
             "consultation est encadrée par l'art. 1070.1.",
    )
    date = fields.Date(
        string="Date de la pièce",
        default=fields.Date.context_today,
        required=True,
    )
    attachment_id = fields.Many2one(
        "ir.attachment",
        string="Fichier",
        required=True,
        ondelete="restrict",
        help="La pièce elle-même. Elle n'est servie qu'aux personnes que "
             "l'auditoire vise, et jamais par un lien anonyme.",
    )
    audience = fields.Selection(
        AUDIENCES,
        string="Auditoire",
        required=True,
        default="owners",
        tracking=True,
        help="Art. 1070.1 C.c.Q. réserve la consultation du registre au "
             "copropriétaire. Un locataire figure au registre (art. 1070 "
             "al. 1) sans pour autant y avoir accès.",
    )
    published = fields.Boolean(
        string="Publié au portail",
        default=False,
        tracking=True,
        copy=False,
    )
    is_register_item = fields.Boolean(
        string="Pièce du registre",
        compute="_compute_is_register_item",
        store=True,
        help="Art. 1070 C.c.Q.",
    )
    register_ack = fields.Boolean(
        string="Publication du registre assumée",
        copy=False,
        tracking=True,
        help="Art. 1070.1 C.c.Q. : la consultation du registre se fait en "
             "présence d'un administrateur, à des heures raisonnables et selon "
             "le règlement de l'immeuble, et la copie s'obtient moyennant des "
             "frais raisonnables. Publier la pièce au portail donne davantage. "
             "Cochez pour l'assumer.",
    )
    note = fields.Text(string="Note interne")

    @api.depends("category")
    def _compute_is_register_item(self):
        for document in self:
            document.is_register_item = document.category in REGISTER_KEYS

    @api.onchange("category")
    def _onchange_category(self):
        """Changer de nature retire l'assentiment donné pour une autre.

        Sans cela, une pièce cochée « assumée » comme avis libre garderait la
        case en devenant un procès-verbal, et la garde de publication ne
        jouerait pas.
        """
        if self.category not in REGISTER_KEYS:
            self.register_ack = False

    def action_publish(self):
        """Publier au portail, et refuser de le faire par distraction."""
        for document in self:
            if document.is_register_item and not document.register_ack:
                raise UserError(
                    _(
                        "« %(name)s » est une pièce du registre (%(category)s). "
                        "L'art. 1070.1 C.c.Q. veut que le registre se consulte "
                        "en présence d'un administrateur, à des heures "
                        "raisonnables et selon le règlement de l'immeuble, et "
                        "que la copie s'obtienne moyennant des frais "
                        "raisonnables. La publier au portail donne davantage : "
                        "c'est possible, mais cela s'assume. Cochez "
                        "« Publication du registre assumée »."
                    )
                    % {
                        "name": document.name,
                        "category": dict(DOCUMENT_CATEGORIES)[document.category],
                    }
                )
            document.published = True
            document.message_post(
                body=_(
                    "Publiée au portail, auditoire : %(audience)s.%(register)s"
                )
                % {
                    "audience": dict(AUDIENCES)[document.audience],
                    "register": _(
                        " Pièce du registre de l'art. 1070 C.c.Q. : la "
                        "publication va au-delà de l'art. 1070.1, et le "
                        "syndicat l'assume."
                    )
                    if document.is_register_item
                    else "",
                },
            )
        return True

    def action_unpublish(self):
        for document in self:
            document.published = False
            document.message_post(body=_("Retirée du portail."))
        return True

    def write(self, vals):
        """Changer de nature en cours de route ne doit pas laisser un trou.

        Une pièce publiée comme avis libre qui devient un procès-verbal
        resterait publiée sans que personne n'ait rien assumé. On la dépublie
        et on le dit, plutôt que de laisser filer.
        """
        result = super().write(vals)
        if "category" in vals:
            for document in self:
                if (
                    document.published
                    and document.is_register_item
                    and not document.register_ack
                ):
                    document.published = False
                    document.message_post(
                        body=_(
                            "Retirée du portail : la pièce est devenue une "
                            "pièce du registre (art. 1070 C.c.Q.) et sa "
                            "publication n'a pas été assumée."
                        )
                    )
        return result
