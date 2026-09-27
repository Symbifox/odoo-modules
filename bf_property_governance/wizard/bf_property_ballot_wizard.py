"""Les trois gestes d'un scrutin secret : remettre, déposer, vérifier.

Ils passent par des modèles transitoires parce qu'aucun des trois ne doit
laisser de trace qui refasse le lien entre une personne et son vote.

🔴 **« Transitoire » ne veut pas dire « pas en base ».** Un enregistrement
transitoire est une ligne d'une table PostgreSQL ordinaire, que le nettoyage
d'Odoo passe prendre plus tard. Sans les mesures qui suivent, la liste de distribution
y restait une heure avec le nom, la fraction, les voix et le code en clair, et
l'assistant de dépôt gardait le code saisi À CÔTÉ du choix déposé. Il suffisait
de lire la table, recalculer l'empreinte et sortir le vote d'une personne nommée. Une
sauvegarde prise pendant l'assemblée gardait le tout pour toujours.

Trois mesures, et il faut les trois :

1. **La liste s'efface quand l'officier a fini de distribuer.** Le bouton n'est
   plus « Fermer » : il dit ce qu'il fait, et il supprime la ligne. La durée de
   vie du modèle est ramenée à trente minutes pour le cas où la fenêtre est
   abandonnée, et l'ouverture d'un scrutin balaie ce qu'une session précédente
   aurait laissé.
2. **Le code déposé s'efface dans la transaction du dépôt.** Ce qui rendait le
   dépôt dangereux n'était pas le code seul, c'était le code et le choix sur la
   même ligne.
3. **Le code vérifié s'efface après la vérification**, pour la même raison : à
   côté de `outcome`, il refait le lien.

Ce qui reste vrai malgré tout : entre le moment où l'officier voit la liste et
celui où il clique, le lien existe en base. Il est inévitable — remettre le bon
récépissé au bon copropriétaire suppose de savoir lequel est le sien — mais il
se compte maintenant en minutes de fenêtre ouverte, pas en heures.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.addons.bf_property_core.tools import format_decimal


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
             "⚠️ Tant que cette fenêtre est ouverte, cette liste EST en base : "
             "c'est le seul endroit où le lien entre une personne et son "
             "bulletin existe. Elle est supprimée dès que vous confirmez avoir "
             "distribué les récépissés. Ensuite, un code perdu ne se retrouve "
             "plus et son bulletin reste dans l'urne sans pouvoir y être "
             "déposé : c'est le prix du secret.",
    )

    # ⚠️ Le ramassage d'Odoo ne passe qu'à la création d'un autre enregistrement
    # transitoire du même modèle, donc on ne compte pas dessus : c'est le filet,
    # pas la mesure. Trente minutes plutôt qu'une heure pour la fenêtre qu'on
    # abandonne sans cliquer.
    _transient_max_hours = 0.5

    def action_distributed(self):
        """Efface la liste. C'est le seul geste qui referme le lien.

        🔴 Supprime plutôt que de laisser le ramassage s'en charger : entre
        l'assemblée et le prochain passage du nettoyeur, il y a des sauvegardes
        nocturnes.
        """
        self.unlink()
        return {"type": "ir.actions.act_window_close"}

    @api.model
    def _purge_for(self, resolution):
        """Balaie ce qu'une fenêtre abandonnée aurait laissé sur ce scrutin."""
        leftovers = self.search([("resolution_id", "=", resolution.id)])
        count = len(leftovers)
        leftovers.unlink()
        return count


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
        # 🔴 Dans LA MÊME transaction que le dépôt. Ce qui rendait cette ligne
        # dangereuse n'était pas le code seul : c'était le code et le choix
        # ensemble, ce qui est exactement le lien que l'urne existe pour ne pas
        # garder. Le commentaire d'origine disait « ne reste pas à l'écran », ce
        # qui était vrai de l'écran et faux de la table.
        self.write({"receipt_code": False, "choice": False})
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
            _("Bulletin de %(votes)s voix, déposé : %(choice)s.")
            % {"votes": format_decimal(self.env, ballot.votes), "choice": labels.get(ballot.choice, "")}
            if ballot.choice
            else _(
                "Bulletin de %(votes)s voix, jamais déposé dans l'urne."
            )
            % {"votes": format_decimal(self.env, ballot.votes)}
        )
        # 🔴 Même raison qu'au dépôt : à côté de `outcome`, le code refait le
        # lien. Le résultat reste affiché, le code s'en va.
        self.receipt_code = False
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }
