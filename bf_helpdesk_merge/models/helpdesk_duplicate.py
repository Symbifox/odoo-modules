"""Doublons suggérés à la création d'un billet, jamais fusionnés tout seuls.

Candidats : billets ouverts des 7 derniers jours, du même contact (ou de la
même adresse) ou de la même organisation, dont le sujet et la description se
ressemblent. Même contact : on propose la fusion (même demande). Même
organisation, autre contact : on propose plutôt le rattachement à un incident
(même cause, demandeurs différents). L'agent tranche ; chaque suggestion
garde son issue (fusionnée, rattachée, écartée) pour mesurer leur justesse.
"""
from datetime import timedelta

from odoo import api, fields, models
from odoo.tools import html2plaintext

from odoo.addons.bf_helpdesk.models.helpdesk_article import search_terms

WINDOW_DAYS = 7
THRESHOLD = 0.35


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class HelpdeskTicketDuplicate(models.Model):
    _name = "helpdesk.ticket.duplicate"
    _inherit = ["bf.helpdesk.onchange.guard"]
    _description = "Doublon possible d'un billet"
    _order = "score desc, id"

    ticket_id = fields.Many2one("helpdesk.ticket", required=True, index=True,
                                ondelete="cascade")
    candidate_id = fields.Many2one("helpdesk.ticket", string="Billet semblable",
                                   required=True, ondelete="cascade")
    # Lus avec les droits de l'usager : en sudo (le défaut d'Odoo), un onchange
    # sur un candidat quelconque en rendait le sujet, le numéro et le client.
    candidate_number = fields.Char(related="candidate_id.number", related_sudo=False)
    candidate_name = fields.Char(related="candidate_id.name", related_sudo=False)
    candidate_partner_id = fields.Many2one(related="candidate_id.partner_id", related_sudo=False)
    candidate_create_date = fields.Datetime(related="candidate_id.create_date", related_sudo=False)
    score = fields.Integer(string="Ressemblance (%)")
    same_contact = fields.Boolean(string="Même contact")
    state = fields.Selection(
        [("suggested", "Suggéré"), ("merged", "Fusionné"),
         ("linked", "Rattaché à un incident"), ("dismissed", "Écarté")],
        default="suggested", required=True, index=True,
    )
    recommendation = fields.Char(compute="_compute_recommendation")

    _sql_constraints = [
        ("pair_uniq", "unique(ticket_id, candidate_id)", "Suggestion déjà faite."),
    ]

    @api.depends("same_contact")
    def _compute_recommendation(self):
        for dup in self:
            dup.recommendation = (
                "Même demande ? Fusionner." if dup.same_contact
                else "Même cause, autre demandeur ? Rattacher à un incident."
            )

    def action_merge(self):
        """Ouvrir l'assistant de fusion : le plus ancien billet est conservé."""
        self.ensure_one()
        tickets = self.ticket_id | self.candidate_id
        keep = tickets.sorted("id")[:1]
        return {
            "type": "ir.actions.act_window",
            "name": "Fusionner les billets",
            "res_model": "helpdesk.ticket.merge",
            "view_mode": "form",
            "target": "new",
            "context": {
                "active_model": "helpdesk.ticket",
                "active_ids": [keep.id] + (tickets - keep).ids,
                "bf_hd_duplicate_id": self.id,
            },
        }

    def action_link_incident(self):
        self.ensure_one()
        return self.ticket_id.with_context(
            bf_hd_duplicate_id=self.id,
        ).action_open_incident_link(self.ticket_id | self.candidate_id)

    def action_dismiss(self):
        self.write({"state": "dismissed"})


class HelpdeskTicket(models.Model):
    _inherit = "helpdesk.ticket"

    duplicate_ids = fields.One2many(
        "helpdesk.ticket.duplicate", "ticket_id", string="Doublons possibles")
    duplicate_open_ids = fields.One2many(
        "helpdesk.ticket.duplicate", compute="_compute_duplicate_open",
        string="Doublons à trancher")
    duplicate_open_count = fields.Integer(compute="_compute_duplicate_open")

    @api.depends("duplicate_ids.state")
    def _compute_duplicate_open(self):
        for ticket in self:
            open_ = ticket.duplicate_ids.filtered(lambda d: d.state == "suggested")
            ticket.duplicate_open_ids = open_
            ticket.duplicate_open_count = len(open_)

    def _bf_similarity_terms(self):
        self.ensure_one()
        return (set(search_terms(self.name or "")),
                set(search_terms(html2plaintext(self.description or ""))))

    def _bf_find_duplicates(self):
        """Poser les suggestions de doublons de ces billets. Rend les lignes créées."""
        Duplicate = self.env["helpdesk.ticket.duplicate"].sudo()
        created = Duplicate
        since = fields.Datetime.now() - timedelta(days=WINDOW_DAYS)
        for ticket in self.sudo():
            partner = ticket.partner_id
            org = partner.commercial_partner_id
            email = (ticket.partner_email or "").strip().lower()
            scope = []
            if partner:
                scope.append(("partner_id", "=", partner.id))
            if org and org != partner:
                scope.append(("partner_id", "child_of", org.id))
            if email:
                scope.append(("partner_email", "=ilike", email))
            if not scope:
                continue
            domain = ["|"] * (len(scope) - 1) + scope
            candidates = self.sudo().search(domain + [
                ("id", "!=", ticket.id),
                ("create_date", ">=", since),
                ("stage_id.closed", "=", False),
                ("company_id", "=", ticket.company_id.id),
            ], limit=50)
            known = set(ticket.duplicate_ids.candidate_id.ids)
            subject, body = ticket._bf_similarity_terms()
            for other in candidates:
                if other.id in known:
                    continue
                o_subject, o_body = other._bf_similarity_terms()
                score = 0.6 * _jaccard(subject, o_subject) + 0.4 * _jaccard(body, o_body)
                if score < THRESHOLD:
                    continue
                same_contact = bool(
                    (partner and other.partner_id == partner)
                    or (email and (other.partner_email or "").strip().lower() == email)
                )
                created |= Duplicate.create({
                    "ticket_id": ticket.id,
                    "candidate_id": other.id,
                    "score": round(score * 100),
                    "same_contact": same_contact,
                })
        return created

    @api.model_create_multi
    def create(self, vals_list):
        tickets = super().create(vals_list)
        if not self.env.context.get("import_file"):
            tickets._bf_find_duplicates()
        return tickets

    def action_find_duplicates(self):
        # Appelable par RPC : la recherche tourne en sudo, donc l'appelant doit
        # pouvoir modifier ces billets (un usager du portail, ou un agent hors
        # de l'équipe, ne déclenche rien).
        self.check_access("write")
        self._bf_find_duplicates()
        return True
