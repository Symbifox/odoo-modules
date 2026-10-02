from markupsafe import Markup

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import format_date


class AssemblyProposal(models.Model):
    """Une proposition adoptée devient une résolution du registre corporatif.

    Elle s'inscrit comme « Résolution des membres » (type ajouté à
    `bf_corporate_governance` 18.0.1.1.0 pour les OBNL), signée par la
    présidence et le secrétariat d'assemblée quand l'assemblée les nomme. La
    séance est une AGA si l'assemblée est annuelle, une séance extraordinaire
    sinon.
    """

    _inherit = "bf.membership.assembly.proposal"

    corporate_resolution_ids = fields.One2many(
        "corporate.resolution", "bf_assembly_proposal_id", string="Résolutions au registre corporatif",
    )
    corporate_resolution_id = fields.Many2one(
        "corporate.resolution", string="Résolution au registre corporatif",
        compute="_compute_corporate_resolution", compute_sudo=True,
        help="Lue depuis le registre : la personne qui tient l'assemblée la "
             "voit même sans droit sur le registre corporatif.",
    )

    @api.depends("corporate_resolution_ids")
    def _compute_corporate_resolution(self):
        for rec in self:
            rec.corporate_resolution_id = rec.corporate_resolution_ids[:1]

    def _adopted(self):
        """Adoptée selon ses totaux, réévalués ici.

        🔴 Le pont ne lit pas le résultat stocké : c'est lui qui écrit au
        registre corporatif, et il doit y inscrire ce que les totaux disent,
        pas une valeur qui aurait pu être écrite à côté d'eux.
        """
        self.ensure_one()
        return self._evaluate()[0] == "adopted"

    def _check_ready_for_register(self):
        self.ensure_one()
        if self.assembly_id.state != "closed":
            raise UserError(_(
                "« %s » s'inscrit au registre une fois l'assemblée close : avant, "
                "son résultat peut encore bouger.", self.name))
        if not self._adopted():
            raise UserError(_(
                "« %s » n'a pas été adoptée : seule une proposition adoptée "
                "devient une résolution.", self.name))
        if not self.assembly_id._quorum_reached():
            raise UserError(_(
                "L'assemblée « %s » n'a pas atteint le quorum : ses décisions ne "
                "s'inscrivent pas au registre corporatif.", self.assembly_id.name))

    def _corporate_resolution_vals(self):
        self.ensure_one()
        assembly = self.assembly_id
        signatories = [
            Command.create({"partner_id": partner.id, "capacity": capacity, "sequence": sequence})
            for sequence, (partner, capacity) in enumerate(
                ((assembly.chair_id, "assembly_chair"), (assembly.secretary_id, "assembly_secretary")), start=1)
            if partner
        ]
        return {
            "name": self.name,
            # Le type « Résolution des membres » vient de bf_corporate_governance
            # 18.0.1.1.0 : l'assemblée des membres n'est pas une assemblée
            # d'actionnaires, et le PDF de la résolution le dit maintenant.
            "resolution_type": "members",
            "signatory_ids": signatories,
            "meeting_type": "agm" if assembly.kind == "annual" else "special",
            "meeting_date": assembly._meeting_day(),
            "resolved_text": self.text,
            "vote_for": self.votes_for,
            "vote_against": self.votes_against,
            "vote_abstain": self.votes_abstain,
            "unanimously_adopted": bool(self.votes_for) and not (
                self.votes_against or self.votes_abstain or self.votes_spoiled),
            "company_id": assembly.company_id.id,
            "status": "proposed",
            "notes": self.with_context(lang=self._organization_lang())._corporate_resolution_note(),
        }

    def _organization_lang(self):
        """La langue de l'organisme : celle de la société, sinon celle de la personne."""
        self.ensure_one()
        return self.assembly_id.company_id.partner_id.lang or self.env.lang

    def _corporate_resolution_note(self):
        """La note de la résolution, dans la langue du contexte.

        🔴 Appelée dans la langue de l'organisme, pas dans celle de la personne
        qui inscrit : la note se lit au registre corporatif de l'organisme, et
        une date au format de l'interface de la personne (« 10/16/2026 »)
        jurerait au milieu d'une phrase en français. Le jour s'écrit en toutes
        lettres, au format de cette langue.
        """
        self.ensure_one()
        assembly = self.assembly_id
        modes = dict(self._fields["vote_mode"]._description_selection(self.env))
        majorities = dict(self._fields["majority"]._description_selection(self.env))
        return _(
            "Adoptée à l'assemblée des membres « %(assembly)s » du %(day)s : vote "
            "%(mode)s, %(majority)s, %(for)s pour, %(against)s contre, "
            "%(abstain)s abstention(s).",
            assembly=assembly.name,
            day=format_date(self.env, assembly._meeting_day(), lang_code=self.env.lang,
                            date_format="d MMMM y"),
            mode=modes.get(self.vote_mode, "").lower(),
            majority=majorities.get(self.majority, "").lower(),
            **{"for": self.votes_for, "against": self.votes_against,
               "abstain": self.votes_abstain})

    def action_create_corporate_resolution(self):
        """Inscrit chaque proposition adoptée au registre, une seule fois.

        Un second clic ne crée rien : il rouvre la résolution déjà inscrite.
        La résolution naît proposée, puis passe par `action_adopt()` du
        registre, pour que la date d'entrée en vigueur et ce que le registre
        fera un jour à l'adoption s'appliquent comme à toute autre résolution.

        Les droits du registre ne sont pas contournés : qui n'a pas le droit
        d'y créer une résolution se le fait dire en clair, avant toute
        écriture.
        """
        Resolution = self.env["corporate.resolution"]
        if not Resolution.has_access("create"):
            raise UserError(_(
                "Seule une personne gestionnaire de la gouvernance corporative "
                "inscrit une résolution au registre."))
        resolutions = Resolution
        for rec in self:
            existing = Resolution.search([("bf_assembly_proposal_id", "=", rec.id)], limit=1)
            if existing:
                resolutions |= existing
                continue
            rec._check_ready_for_register()
            # Créée dans le rôle de la personne, avec les droits du registre ;
            # le lien vers la proposition, lui, ne s'écrit qu'en superutilisateur.
            resolution = Resolution.create(rec._corporate_resolution_vals())
            # Adoptée par l'action du registre, puis liée : une fois liée, son
            # statut ne change plus hors superutilisateur, sauf pour la remplacer.
            resolution.action_adopt()
            resolution.sudo().write({"bf_assembly_proposal_id": rec.id})
            rec.assembly_id._message_log(body=Markup("%s") % _(
                "« %(proposal)s » inscrite au registre corporatif : %(ref)s.",
                proposal=rec.name, ref=resolution.sequence))
            resolutions |= resolution
        return resolutions._bf_open_action()
