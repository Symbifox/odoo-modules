"""Fusion de billets, complétée pour l'assistance Blue Fox.

L'assistant OCA déplace les messages, les activités, les étiquettes et les
pièces jointes, abonne les suiveurs au billet conservé et archive les autres.
Il manquait :
- les feuilles de temps, qui restaient sur le billet archivé (et hors de la
  banque d'heures du billet conservé) ;
- des avis INTERNES : l'OCA poste un message public, en anglais, qui partait
  par courriel aux clients des deux billets ;
- une description fusionnée en HTML propre (l'OCA colle du texte dans un
  champ HTML) ;
- la trace de fusion des deux côtés ;
- l'arrêt de ce qui vivait encore sur le billet fondu : sondage prévu,
  attente client (relances).
"""
from markupsafe import Markup, escape

from odoo import _, models
from odoo.exceptions import UserError


class HelpdeskTicketMerge(models.TransientModel):
    _name = "helpdesk.ticket.merge"
    _inherit = ["helpdesk.ticket.merge", "bf.helpdesk.onchange.guard"]

    def merge_tickets(self):
        # Le billet de destination compte : l'OCA ne borne pas son choix, et
        # fusionner dans le billet d'un autre client l'y abonnerait.
        merged = self.ticket_ids
        if not self.create_new_ticket:
            merged |= self.dst_ticket_id
        if len(merged.company_id) > 1:
            raise UserError(_("On ne fusionne pas des billets de sociétés différentes."))
        # Deux clients différents : la fusion abonnerait l'un au billet de
        # l'autre et lui en montrerait les messages, au portail puis par
        # l'historique cité des courriels.
        # Même cause, demandeurs différents : c'est un incident, pas une fusion.
        clients = set()
        for ticket in merged:
            commercial = ticket.partner_id.commercial_partner_id
            key = ("p", commercial.id) if commercial else (
                "e", (ticket.partner_email or "").strip().lower())
            if key != ("e", ""):
                clients.add(key)
        if len(clients) > 1:
            raise UserError(_(
                "Ces billets viennent de clients différents : les fusionner montrerait "
                "à l'un les messages de l'autre. Rattachez-les plutôt à un incident."))
        existing_dst = False if self.create_new_ticket else self.dst_ticket_id
        sources = self.ticket_ids - existing_dst if existing_dst else self.ticket_ids
        # Relevé avant : l'OCA archive les sources, et un billet archivé ne se
        # relit plus sans active_test=False.
        sources = sources.with_context(active_test=False)
        timesheets = self.env["account.analytic.line"].sudo().search(
            [("ticket_id", "in", sources.ids)])
        # L'OCA déplace les messages en écrivant leur res_id, ce qu'Odoo 18
        # réserve aux administrateurs : un agent échouait en AccessError. Le droit de l'agent se vérifie ici, sur chaque billet, puis
        # l'assistant tourne en sudo() ; l'agent reste l'auteur.
        tickets = self.ticket_ids | self.dst_ticket_id
        tickets.check_access("write")
        res = super(HelpdeskTicketMerge, self.sudo().with_context(bf_hd_merge=True)).merge_tickets()
        dst = self.dst_ticket_id
        sources = sources - dst
        if timesheets:
            timesheets.write({"ticket_id": dst.id})
        sources.sudo().write({
            "merged_into_id": dst.id,
            "waiting_state": False,
        })
        if "helpdesk.ticket.csat" in self.env:
            self.env["helpdesk.ticket.csat"].sudo().search([
                ("ticket_id", "in", sources.ids), ("state", "=", "scheduled"),
            ])._cancel(_("billet fusionné"))
        dup_id = self.env.context.get("bf_hd_duplicate_id")
        if dup_id:
            # Le contexte vient de l'appelant : on ne clôt que la paire qui
            # porte sur les billets traités ici, jamais un doublon quelconque.
            dup = self.env["helpdesk.ticket.duplicate"].sudo().browse(dup_id).exists()
            if dup and (dup.ticket_id | dup.candidate_id) <= (
                    self.with_context(active_test=False).ticket_ids | dst):
                dup.state = "merged"
        dst.sudo().message_post(
            body=Markup("<p>%s</p>") % _(
                "Fusion : %(n)s billet(s) versé(s) ici avec leurs messages, "
                "pièces jointes, abonnés et %(h)s feuille(s) de temps.",
                n=len(sources), h=len(timesheets)),
            message_type="notification",
            subtype_xmlid="mail.mt_note",
        )
        return res

    def _merge_description(self, tickets):
        blocks = []
        for ticket in tickets:
            blocks.append(
                Markup("<p><strong>%s</strong></p>%s") % (
                    _("Description du billet %s :", ticket.number or ticket.name),
                    ticket.description or Markup("<p>%s</p>") % _("(aucune)"),
                )
            )
        return Markup("").join(blocks)

    def _add_message(self, way, ticket_numbers, ticket):
        """Avis de fusion en NOTE INTERNE, de type notification.

        Une note, parce que le client n'a pas à recevoir « merged to » par
        courriel. Le type notification, parce que l'OCA déplace ensuite tous
        les messages des billets fondus sauf ceux-là : c'est ce qui laisse la
        trace sur le billet d'origine.
        """
        text = (_("Ce billet a été fusionné dans %s.") if way == self.env._("to")
                else _("Billets fusionnés ici : %s."))
        # L'OCA joint des liens déjà échappés (Markup) avec ", ".join : le
        # résultat redevient une chaîne ordinaire, qu'il faut remarquer comme
        # du HTML sûr, sinon les liens sortent échappés dans la note.
        ticket.message_post(
            body=Markup("<p>%s</p>") % (escape(text) % Markup(ticket_numbers)),
            message_type="notification",
            subtype_xmlid="mail.mt_note",
        )
