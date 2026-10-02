from odoo import _, models
from odoo.exceptions import UserError


class ResPartner(models.Model):
    """Un contact porté par une assemblée ne se supprime ni ne se fusionne sans
    le rôle Membres.

    🔴 Le socle retient déjà les contacts qui portent une adhésion ou une
    délégation. Une assemblée en porte d'autres : la personne qui votait pour
    une organisation (sa délégation a pu prendre fin depuis), celle qui a reçu
    un bulletin pour un membre représenté, la présidence, le secrétariat et les
    scrutatrices et scrutateurs d'une assemblée tenue. Supprimer l'un d'eux
    viderait la présidence d'une assemblée close ou emporterait les
    scrutateurs en cascade, et le refus natif d'Odoo nommerait le modèle qui
    le retient. Sans le rôle, le socle archive ces contacts en silence et
    refuse leur fusion sans dire pourquoi.
    """

    _inherit = "res.partner"

    def _assembly_records(self):
        """Ce qui, dans les assemblées, retient ces contacts (en superutilisateur)."""
        ids = self.ids
        Voter = self.env["bf.membership.assembly.voter"].sudo()
        voters = Voter.search(["|", ("member_id", "in", ids), ("representative_id", "in", ids)])
        ballots = self.env["bf.membership.assembly.ballot"].sudo().search([("received_by_id", "in", ids)])
        officers = self.env["bf.membership.assembly"].sudo().search([
            ("state", "!=", "draft"), "|", ("chair_id", "in", ids), ("secretary_id", "in", ids)])
        proposals = self.env["bf.membership.assembly.proposal"].sudo().search([
            ("assembly_id.state", "!=", "draft"), ("scrutineer_ids", "in", ids)])
        return voters, ballots, officers, proposals

    def _membership_held(self):
        held = super()._membership_held()
        if not self:
            return held
        voters, ballots, officers, proposals = self._assembly_records()
        people = (voters.member_id | voters.representative_id | ballots.received_by_id
                  | officers.chair_id | officers.secretary_id | proposals.scrutineer_ids)
        return held | self.sudo().filtered(lambda p: p in people)

    def _note_membership_archive(self, user):
        super()._note_membership_archive(user)
        for partner in self.sudo():
            voters, ballots, officers, proposals = partner._assembly_records()
            assemblies = voters.assembly_id | ballots.assembly_id | officers | proposals.assembly_id
            for assembly in assemblies:
                assembly._message_log(body=_(
                    "%(partner)s : contact archivé au lieu d'être supprimé, par %(who)s.",
                    partner=partner.display_name, who=user.name))

    def unlink(self):
        """Avec le rôle Membres, un refus explicite.

        🔴 La présidence, le secrétariat et le bulletin reçu sont des liens qui
        se vident à la suppression du contact, et les scrutateurs partent en
        cascade : une assemblée close perdrait sans trace qui l'a présidée ou
        dépouillée. Sans le rôle, le socle archive ces contacts en silence (voir
        `_membership_held`) ; avec le rôle, la personne lit pourquoi, et archive.
        """
        if not self.env.su and self.env.user.has_group("bf_membership.group_membership_user"):
            voters, ballots, officers, proposals = self._assembly_records()
            held = self & (officers.chair_id | officers.secretary_id | proposals.scrutineer_ids
                           | ballots.received_by_id)
            if held:
                raise UserError(_(
                    "%s préside, tient le secrétariat, dépouille ou a reçu un bulletin dans "
                    "une assemblée qui n'est plus en brouillon : la supprimer viderait ce "
                    "lien. Archivez plutôt ce contact.", ", ".join(held.mapped("display_name"))))
        return super().unlink()
