from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .guard import check_computed_not_written


class AssemblyBallot(models.Model):
    """Le registre des bulletins remis, au scrutin secret.

    🔴 Ce modèle ne porte AUCUN choix, et c'est sa raison d'être. Il dit qu'un
    votant a reçu un bulletin pour une proposition, et rien d'autre. Le
    dépouillement vit ailleurs (les totaux de `bf.membership.assembly.proposal`)
    et ne porte que des totaux saisis par les personnes qui dépouillent. Aucune
    ligne, aucun champ, aucun ordre d'enregistrement ne relie une personne à ce
    qu'elle a mis dans l'urne, parce que ce qu'elle y a mis n'entre jamais en
    base un bulletin à la fois.

    C'est la séparation du bulletin papier, reprise de `bf_property_governance` :
    le registre se recompte (un seul bulletin par votant et par proposition,
    procurations comprises), l'urne se recompte (on ne dépouille pas plus de
    bulletins qu'on en a remis), et les deux ne se recoupent pas.

    🔴 Aucun bulletin ne se remet ni ne se reprend une fois un résultat saisi :
    l'écart entre deux décomptes, l'un avant et l'autre après un bulletin de
    plus ou de moins, dirait le choix de la personne concernée.

    ⚠️ Ce que le module ne fait pas : le vote électronique individuel. Un bulletin
    déposé en ligne entre en base un à la fois ; il faudrait alors garantir que
    ni l'ordre des identifiants, ni l'heure de création, ni aucune colonne de
    journalisation ne permet de le rattacher à la personne qui l'a déposé.
    Voir le README.
    """

    _name = "bf.membership.assembly.ballot"
    _description = "Bulletin remis (registre du scrutin secret)"
    _order = "proposal_id, voter_id"

    proposal_id = fields.Many2one(
        "bf.membership.assembly.proposal", string="Proposition", required=True,
        ondelete="cascade", index=True, readonly=True,
    )
    assembly_id = fields.Many2one(related="proposal_id.assembly_id", store=True, index=True)
    company_id = fields.Many2one(related="proposal_id.company_id", store=True, index=True)
    voter_id = fields.Many2one(
        "bf.membership.assembly.voter", string="Votant", required=True,
        ondelete="cascade", index=True, readonly=True,
    )
    received_by_id = fields.Many2one(
        "res.partner", string="Remis à", readonly=True,
        help="La personne qui a reçu le bulletin : celle qui vote pour le "
             "membre, ou son mandataire s'il est représenté.",
    )
    via_proxy = fields.Boolean(string="Par procuration", readonly=True)

    _sql_constraints = [
        ("proposal_voter_uniq", "unique(proposal_id, voter_id)",
         "Ce votant a déjà reçu son bulletin pour cette proposition."),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        """Contrôle chaque remise, et note à qui le bulletin est allé.

        Le contrôle de l'unicité se fait ici en clair avant la contrainte SQL,
        qui reste le filet : deux clics simultanés ne passent pas non plus.
        """
        Proposal = self.env["bf.membership.assembly.proposal"]
        Voter = self.env["bf.membership.assembly.voter"]
        seen = set()
        for vals in vals_list:
            check_computed_not_written(self, vals)
            proposal = Proposal.browse(vals.get("proposal_id"))
            voter = Voter.browse(vals.get("voter_id"))
            if not proposal or not voter:
                raise UserError(_("Un bulletin remis nomme sa proposition et son votant."))
            if proposal.vote_mode != "secret":
                raise UserError(_("« %s » se vote à main levée : il n'y a pas de bulletin.", proposal.name))
            if proposal.assembly_id.state != "open":
                raise UserError(_("Les bulletins se remettent pendant l'assemblée, une fois celle-ci ouverte."))
            if proposal.tally_started or proposal._has_tally():
                raise UserError(_(
                    "Le dépouillement de « %s » est commencé : plus aucun bulletin ne "
                    "se remet. Un bulletin de plus après un décompte révélerait, par "
                    "l'écart entre les deux décomptes, le choix de la personne qui "
                    "le recevrait.", proposal.name))
            if voter.assembly_id != proposal.assembly_id:
                raise UserError(_("Ce votant ne figure pas à la liste de cette assemblée."))
            if not voter.has_voice:
                raise UserError(_(
                    "%s n'a pas de voix à exercer (ni présence notée, ni mandataire "
                    "présent) : pas de bulletin.", voter.display_name))
            key = (proposal.id, voter.id)
            if key in seen or proposal.ballot_ids.filtered(lambda b, v=voter: b.voter_id == v):
                raise UserError(_(
                    "%(voter)s a déjà reçu son bulletin pour « %(proposal)s » : un "
                    "seul bulletin par votant, procuration comprise.",
                    voter=voter.display_name, proposal=proposal.name))
            seen.add(key)
            proxy = voter.attendance == "proxy"
            vals["via_proxy"] = proxy
            vals["received_by_id"] = (voter.proxy_holder_id if proxy else voter).representative_id.id
        return super().create(vals_list)

    def write(self, vals):
        raise UserError(_(
            "Un bulletin remis ne se modifie pas. S'il a été remis par erreur, "
            "reprenez-le avant le dépouillement."))

    def unlink(self):
        """Reprendre un bulletin remis par erreur, avant le dépouillement seulement.

        Après, le registre et l'urne ne se répondraient plus : on aurait
        dépouillé un bulletin que le registre ne connaît pas.
        """
        for proposal in self.proposal_id:
            if proposal.assembly_id.state != "open":
                raise UserError(_("Un bulletin se reprend pendant l'assemblée seulement."))
            if proposal.tally_started or proposal._has_tally():
                raise UserError(_(
                    "Le dépouillement de « %s » est commencé : le registre des "
                    "bulletins ne change plus.", proposal.name))
        return super().unlink()
