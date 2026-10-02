from odoo import http
from odoo.http import content_disposition, request

from odoo.addons.bf_membership.models.membership import LIVE_STATES
from odoo.addons.portal.controllers.portal import CustomerPortal

#: Les messages affichés après un geste, par clé : le texte n'est jamais repris
#: de l'adresse, qui se fabrique.
PORTAL_MESSAGES = {
    "consent": "Vos choix sont enregistrés.",
    "renewed": "Votre adhésion est renouvelée.",
    "renewal_office": "Votre renouvellement est préparé. Communiquez avec l'organisme pour le "
                      "paiement de la cotisation.",
}


class MembershipPortal(CustomerPortal):
    """Le portail du membre.

    🔴 Le membre se reconnaît par le contact de l'usager connecté
    (`request.env.user.partner_id`), jamais par une adresse courriel ni par
    un identifiant dans l'adresse de la page : aucune route d'ici ne prend
    d'identifiant d'adhésion ou de contact. Les adhésions se lisent avec les
    droits de l'usager (règle du portail) ; le superutilisateur ne sert
    qu'à lire ce qu'elles pointent (catégorie, facture) et à écrire les
    gestes permis (consentements, renouvellement).
    """

    def _membership_model(self):
        Membership = request.env["bf.membership"]
        # Un employé sans le groupe Membres peut être membre lui-même : il lit
        # ses adhésions par le domaine explicite des appelants, en superutilisateur.
        return Membership if Membership.has_access("read") else Membership.sudo()

    def _membership_partners(self):
        partner = request.env.user.partner_id
        return partner, partner._membership_represented()

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if "membership_count" in counters:
            partner, orgs = self._membership_partners()
            values["membership_count"] = self._membership_model().search_count(
                [("partner_id", "in", (partner | orgs).ids)])
        return values

    @staticmethod
    def _member_invoice(membership, owner):
        """La facture de l'adhésion, si l'adhésion est celle de `owner` (la
        personne connectée, ou l'organisation qu'elle représente) et la
        facture à son nom.

        🔴 Comparé à `owner`, que l'appelant tire de l'usager connecté, jamais
        au seul partenaire de l'adhésion : une adhésion d'une autre personne
        (un renouvellement rattaché par erreur) mènerait sinon à sa facture et
        à son lien à jeton. Et une demande publique rattachée au contact d'un
        membre garde, payée, la facture émise au nom tapé au formulaire : son
        lien et ce nom n'ont rien à faire au portail du membre.
        """
        sudo = membership.sudo()
        invoice = sudo.invoice_id
        if (invoice and sudo.partner_id == owner
                and invoice.commercial_partner_id == owner.commercial_partner_id):
            return invoice
        return invoice.browse()

    def _membership_rows(self, memberships, with_payment, owner):
        rows = []
        for membership in memberships:
            sudo = membership.sudo()
            invoice = self._member_invoice(membership, owner)
            pay_url = False
            if with_payment and invoice and sudo.invoice_drives and sudo.payment_state == "to_pay":
                pay_url = invoice.get_portal_url()
            rows.append({
                "membership": membership,
                "type_name": sudo.type_id.name,
                "invoice_name": invoice.name if invoice and invoice.state == "posted" else False,
                "invoice_url": invoice.get_portal_url() if with_payment and invoice and invoice.state == "posted" else False,
                "pay_url": pay_url,
            })
        return rows

    @http.route("/my/membership", type="http", auth="user", website=True)
    def portal_my_membership(self, message=None, **kw):
        partner, orgs = self._membership_partners()
        Membership = self._membership_model()
        own = Membership.search([("partner_id", "=", partner.id)])
        organizations = []
        for org in orgs:
            organizations.append({
                "partner": org.sudo(),
                "rows": self._membership_rows(Membership.search([("partner_id", "=", org.id)]), False, org),
            })
        source, renewal = partner._membership_renewal_offer(request.env.company)
        renewal_pay_url = False
        renewal_invoice = self._member_invoice(renewal, partner) if renewal else False
        if renewal_invoice and renewal.invoice_drives and renewal.payment_state == "to_pay":
            renewal_pay_url = renewal_invoice.get_portal_url()
        values = self._prepare_portal_layout_values()
        values.update({
            "page_name": "membership",
            "member": partner.sudo(),
            "rows": self._membership_rows(own, True, partner),
            "organizations": organizations,
            "renewal_source": source,
            "renewal": renewal,
            "renewal_pay_url": renewal_pay_url,
            "can_renew": bool(source) and not renewal_pay_url and not (
                renewal and renewal.payment_state in ("paid", "exempt")),
            # Le statut de CETTE société, comme la route de la carte : sinon le
            # bouton paraît, et la carte répond 404.
            "card_available": partner.sudo()._member_status_in(request.env.company) == "member",
            "message": PORTAL_MESSAGES.get(message),
        })
        return request.render("bf_membership_portal.portal_my_membership", values)

    @http.route("/my/membership/consent", type="http", auth="user", methods=["POST"], website=True)
    def portal_membership_consent(self, **post):
        """Le membre change lui-même ses consentements.

        La preuve (Loi 25 : qui, quand, par où) est consignée par le socle sur
        l'adhésion la plus récente, avec la mention « au portail », que seuls
        les agents lisent, jamais au fil du contact : une seule ligne par
        changement.
        """
        partner = request.env.user.partner_id
        if not request.env["bf.membership"].sudo().search_count([("partner_id", "=", partner.id)]):
            raise request.not_found()
        values = {
            "directory_consent": bool(post.get("directory_consent")),
            "notice_email_consent": bool(post.get("notice_email_consent")),
        }
        partner_sudo = partner.sudo()
        if any(partner_sudo[k] != v for k, v in values.items()):
            # En superutilisateur, pour le compte de l'usager connecté : le socle
            # consigne le changement sur l'adhésion, « au portail ».
            partner_sudo.write(values)
        return request.redirect("/my/membership?message=consent")

    @http.route("/my/membership/renew", type="http", auth="user", methods=["POST"], website=True)
    def portal_membership_renew(self, **post):
        """Renouveler : préparer le renouvellement s'il manque, sa facture s'il
        en faut une, puis mener à la page de paiement de la facture.

        🔴 Deux clics (ou deux onglets) ne font qu'un renouvellement et qu'une
        facture. Le second clic retrouve ce que le premier a créé ; deux clics
        SIMULTANÉS, eux, se verraient chacun sans renouvellement. Toucher la
        ligne de l'adhésion renouvelée les met en file : le second attend le
        premier, échoue à la sérialisation, et Odoo le rejoue ; rejoué, il
        retrouve le renouvellement.
        """
        partner = request.env.user.partner_id
        source, renewal = partner._membership_renewal_offer(request.env.company)
        if not source:
            return request.redirect("/my/membership")
        request.env.cr.execute(
            "UPDATE bf_membership SET write_date = (now() AT TIME ZONE 'UTC') WHERE id = %s", [source.id])
        source.invalidate_recordset(["renewal_ids"])
        live = source.renewal_ids.filtered(lambda r: r.state in LIVE_STATES)
        renewal = live.filtered(lambda r: r.partner_id == partner)[:1]
        if live and not renewal:
            # 🔴 Le renouvellement vivant n'est pas celui de la personne
            # connectée : ni facture ni redirection à un autre nom.
            raise request.not_found()
        if not renewal:
            source.with_context(mail_create_nosubscribe=True).action_renew()
            renewal = source.renewal_ids.filtered(lambda r: r.state in LIVE_STATES and r.partner_id == partner)[:1]
        if renewal.partner_id != partner:
            raise request.not_found()
        if renewal.payment_state in ("paid", "exempt"):
            return request.redirect("/my/membership?message=renewed")
        if not renewal.invoice_drives and not renewal._automatic_invoice_allowed():
            # La garde du bouton « Facturer » vaut ici aussi : une personne
            # rattachée à une entreprise, ou une cotisation qui diffère de celle
            # de la catégorie, ne reçoit pas de facture automatique.
            return request.redirect("/my/membership?message=renewal_office")
        invoice = self._member_invoice(renewal, partner) if renewal.invoice_drives else renewal._create_invoice()
        if not invoice:
            raise request.not_found()
        return request.redirect(invoice.get_portal_url())

    @http.route("/my/membership/card", type="http", auth="user", website=True)
    def portal_membership_card(self, **kw):
        """La carte de membre du membre connecté, s'il est en règle dans la
        société de ce site.

        🔴 Lue dans CETTE société (`_member_status_in`), jamais le statut toutes
        sociétés confondues : une personne en règle dans une autre société
        n'obtient pas la carte d'ici.
        """
        partner = request.env.user.partner_id.sudo()
        company = request.env.company
        if partner._member_status_in(company) != "member":
            raise request.not_found()
        content, _type = request.env["ir.actions.report"].sudo()._render_qweb_pdf(
            "bf_membership_portal.report_membership_card", partner.ids, data={"card_company_id": company.id})
        return request.make_response(content, headers=[
            ("Content-Type", "application/pdf"),
            ("Content-Length", str(len(content))),
            ("Content-Disposition", content_disposition("carte-de-membre.pdf")),
        ])
