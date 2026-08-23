"""Les trois gestes d'un scrutin secret : remettre, déposer, vérifier.

Ils passent par des modèles transitoires parce qu'aucun des trois ne doit
laisser de trace qui refasse le lien entre une personne et son vote.

La liste de distribution est le seul endroit où le lien existe, et il est
inévitable : remettre le bon récépissé au bon copropriétaire suppose de savoir
lequel est le sien. Il vit le temps d'une fenêtre, dans une table transitoire
que le nettoyage d'Odoo vide, et n'est jamais versé au dossier de la
résolution.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class BfPropertyBallotIssue(models.TransientModel):
    _name = "bf.property.ballot.issue"
    _description = "Récépissés d'un scrutin secret"

    resolution_id = fields.Many2one(
        "bf.property.resolution", string="Résolution", required=True, readonly=True
    )
    distribution = fields.Text(
        string="À remettre aux votants",
        readonly=True,
        help="Chaque ligne porte un récépissé et le votant à qui il revient. "
             "Cette liste ne s'affiche qu'une fois : les codes ne sont "
             "conservés nulle part, seule leur empreinte est en base. Une fois "
             "cette fenêtre fermée, un code perdu ne se retrouve plus, et le "
             "bulletin correspondant reste dans l'urne sans jamais pouvoir "
             "être déposé.",
    )


class BfPropertyBallotDeposit(models.TransientModel):
    _name = "bf.property.ballot.deposit"
    _description = "Dépôt d'un bulletin secret"

    resolution_id = fields.Many2one(
        "bf.property.resolution", string="Résolution", required=True
    )
    # Ni le code ni le choix ne sont obligatoires au modèle : l'assistant se
    # rouvre vide après chaque dépôt, pour le votant suivant. La vue les exige,
    # et `action_deposit` les redemande — un enregistrement vide ne dépose rien.
    receipt_code = fields.Char(string="Récépissé")
    choice = fields.Selection(
        [
            ("for", "Pour"),
            ("against", "Contre"),
            ("abstain", "Abstention"),
        ],
        string="Vote",
    )
    feedback = fields.Char(string="Dernier dépôt", readonly=True)

    def action_deposit(self):
        self.ensure_one()
        if not self.receipt_code or not self.choice:
            raise UserError(_("Saisissez le récépissé et le vote."))
        if self.resolution_id.assembly_id.state == "closed":
            raise UserError(_("L'assemblée est clôturée."))
        if self.resolution_id.ballot_mode != "secret":
            raise UserError(
                _("Cette résolution se vote à main levée : il n'y a pas d'urne.")
            )
        self.env["bf.property.secret.ballot"].deposit(
            self.resolution_id, self.receipt_code, self.choice
        )
        # Le scrutin avance votant par votant : on rend une fenêtre vide plutôt
        # que de fermer, sinon il faut rouvrir l'assistant à chaque personne.
        # Le code qui vient d'être déposé ne reste pas à l'écran.
        follow_up = self.create(
            {
                "resolution_id": self.resolution_id.id,
                "feedback": _(
                    "Bulletin déposé. %(cast)d sur %(issued)d bulletins remis."
                )
                % {
                    "cast": self.resolution_id.ballot_cast_count,
                    "issued": self.resolution_id.ballot_issued_count,
                },
            }
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Déposer un bulletin"),
            "res_model": self._name,
            "res_id": follow_up.id,
            "view_mode": "form",
            "target": "new",
        }


class BfPropertyBallotReceipt(models.TransientModel):
    _name = "bf.property.ballot.receipt"
    _description = "Vérification d'un récépissé"

    resolution_id = fields.Many2one(
        "bf.property.resolution", string="Résolution", required=True
    )
    receipt_code = fields.Char(string="Récépissé")
    outcome = fields.Char(string="Ce que porte le bulletin", readonly=True)

    def action_check(self):
        """Rend au votant ce que son bulletin porte, à lui et à personne d'autre.

        C'est la moitié individuelle de « vérifiés subséquemment » au sens de
        l'art. 1089.1 : le recomptage prouve que l'urne est entière, ceci prouve
        à chacun que sa voix y est telle qu'il l'a déposée.
        """
        self.ensure_one()
        if not self.receipt_code:
            raise UserError(_("Saisissez le récépissé à vérifier."))
        ballot = self.env["bf.property.secret.ballot"]._find_by_receipt(
            self.resolution_id, self.receipt_code
        )
        if not ballot:
            raise UserError(
                _(
                    "Aucun bulletin ne porte ce récépissé pour cette "
                    "résolution."
                )
            )
        labels = dict(
            ballot._fields["choice"]._description_selection(self.env)
        )
        self.outcome = (
            _("Bulletin de %(votes).4f voix, déposé : %(choice)s.")
            % {"votes": ballot.votes, "choice": labels.get(ballot.choice, "")}
            if ballot.choice
            else _(
                "Bulletin de %(votes).4f voix, jamais déposé dans l'urne."
            )
            % {"votes": ballot.votes}
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }
