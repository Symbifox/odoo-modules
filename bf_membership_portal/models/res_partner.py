from dateutil.relativedelta import relativedelta

from odoo import _, fields, models
from odoo.exceptions import UserError

from odoo.addons.bf_membership.models.membership import LIVE_STATES


class ResPartner(models.Model):
    _inherit = "res.partner"

    def unlink(self):
        # 🔴 Notre message, qui nomme le formulaire et la facture, ne parle qu'au
        # rôle Membres. Sans le rôle, le socle archive en silence les contacts
        # que `_membership_held` lui désigne, contacts du formulaire compris.
        if self.env.su or self.env.user.has_group("bf_membership.group_membership_user"):
            self._check_form_partner_invoices()
        return super().unlink()

    def _membership_held(self):
        """Le socle y met les contacts qui portent une adhésion ou une
        délégation. En plus : les contacts créés par le formulaire public, même
        une fois leur demande rattachée à un contact existant. Sans le rôle
        Membres, ils ne se suppriment ni ne se fusionnent, et le refus du socle,
        neutre, passe avant nos messages."""
        held = super()._membership_held()
        forms = self.env["bf.membership"].sudo().with_context(active_test=False).search(
            [("public_partner_id", "in", self.ids)]).public_partner_id
        return held | (self.sudo() & forms)

    def _note_membership_archive(self, user):
        """Le socle note l'archivage sur les adhésions du contact. En plus : sur
        la demande dont il est le contact du formulaire, quand elle a été
        rattachée à un autre contact."""
        super()._note_membership_archive(user)
        partners = self.sudo()
        requests = self.env["bf.membership"].sudo().with_context(active_test=False).search(
            [("public_partner_id", "in", partners.ids)]) - partners.membership_ids
        for request in requests:
            request._message_log(body=_(
                "Contact du formulaire archivé au lieu d'être supprimé, par %s.", user.name))

    def _form_partners_with_invoices(self):
        """Les contacts créés par le formulaire public, parmi ceux-ci, qui
        portent une facture, même annulée."""
        forms = self.env["bf.membership"].sudo().search([("public_partner_id", "in", self.ids)]).public_partner_id
        if not forms:
            return self.browse()
        moves = self.env["account.move"].sudo().search([
            "|", ("partner_id", "in", forms.ids), ("commercial_partner_id", "in", forms.ids)])
        return self.browse(((moves.partner_id | moves.commercial_partner_id) & forms).ids)

    def _check_form_partner_invoices(self):
        """Un contact créé par le formulaire public qui porte une facture, même
        annulée, ne se supprime pas : la comptabilité garde la facture et son
        contact (la garde d'Odoo ne voit que les brouillons et les factures
        validées ; une facture annulée finirait en erreur de la base). Il
        s'archive.

        🔴 Contrôlé AVANT le `unlink` du socle, qui supprime d'abord les
        demandes jamais payées du contact, et avec elles ce qui le désigne
        comme contact du formulaire.
        """
        invoiced = self._form_partners_with_invoices()
        if invoiced:
            raise UserError(_(
                "Le contact « %s », créé par le formulaire public d'adhésion, porte une facture "
                "(même annulée) : la comptabilité la garde avec son contact. Archivez-le plutôt "
                "que de le supprimer.", invoiced[:1].display_name))

    def _membership_represented(self):
        """Les organisations membres que cette personne représente aujourd'hui
        (délégué ou substitut en fonction).

        🔴 Le lien se fait par le contact de l'USAGER connecté, jamais par une
        adresse courriel : la personne change son courriel elle-même au portail,
        et deux contacts peuvent porter la même adresse.
        """
        self.ensure_one()
        today = fields.Date.context_today(self)
        delegations = self.sudo().delegation_ids.filtered(lambda d: d._in_office(today))
        return delegations.organization_id

    def _membership_renewal_offer(self, company):
        """(adhésion à renouveler, renouvellement déjà préparé) pour le portail,
        ou deux ensembles vides.

        Offert quand la dernière adhésion en règle approche de son échéance
        (le délai de préparation de la catégorie), ou qu'elle est échue depuis
        moins que le délai de grâce. Un ancien membre, au-delà de la grâce, ne
        « renouvelle » pas une période passée : il adhère de nouveau.
        """
        self.ensure_one()
        Membership = self.env["bf.membership"].sudo()
        empty = (Membership, Membership)
        today = fields.Date.context_today(self)
        last = Membership.search([
            ("partner_id", "=", self.id),
            ("company_id", "=", company.id),
            ("state", "in", ("active", "expired")),
            ("date_end", "!=", False),
        ], order="date_end desc, id desc", limit=1)
        if not last or not last.type_id.active:
            return empty
        # Le renouvellement de CETTE personne. Un renouvellement vivant d'une
        # autre personne rattaché à son adhésion (par erreur) : rien à offrir,
        # ni le bouton ni la facture ; l'équipe démêle.
        live = last.renewal_ids.filtered(lambda r: r.state in LIVE_STATES)
        renewal = live.filtered(lambda r: r.partner_id == self)[:1]
        if live and not renewal:
            return empty
        if renewal:
            return last, renewal
        # Les retraits de CETTE société seulement : un retrait dans une autre
        # société ne bloque pas le renouvellement d'ici.
        withdrawn = self.sudo().membership_ids.filtered(
            lambda m: m.company_id == company and m.state == "withdrawn" and m.withdrawal_date
            and m.withdrawal_date >= last.date_start)
        if withdrawn:
            return empty
        if last.date_end - relativedelta(days=last.type_id.renewal_days_before) > today:
            return empty
        if today > last.date_end + relativedelta(days=last.type_id.grace_days):
            return empty
        return last, renewal
