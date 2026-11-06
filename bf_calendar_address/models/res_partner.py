# -*- coding: utf-8 -*-
"""The address of a contact, on the one line a phone can act on."""

from odoo import api, models

# Address parts that make a place findable. A contact that carries none of
# them has no address worth putting in a meeting: a city alone sends nobody
# anywhere, and it would hide that the place is still to be filled in.
CHAMPS_UTILES = ("street", "street2")

# An invoicing address is a real address and belongs in the picker, but it is
# not where a visit happens: it is the accounts department, or a PO box. It is
# offered, never chosen on our own. A delivery address is: that is the loading
# dock, and someone is there.
TYPES_PAS_DOFFICE = ("invoice",)


class ResPartner(models.Model):
    _inherit = "res.partner"

    # ----------------------------------------------------------- the address

    def _bf_address_lignes(self):
        """The address book's own lines for this contact, cleaned up.

        `_display_address` already knows each country's order and spelling, so
        we keep it and only squeeze the whitespace. An empty list when there
        is no street: a city on its own sends nobody anywhere.
        """
        self.ensure_one()
        if not any(self[champ] for champ in CHAMPS_UTILES):
            return []
        brut = self._display_address(without_company=True) or ""
        return [" ".join(ligne.split()) for ligne in brut.splitlines()
                if ligne.strip()]

    def bf_address_one_line(self):
        """This contact's address as a single line, or an empty string.

        Why one line: the invitation is read on a phone, between two sites,
        and the place is tapped to open the GPS.
        Phones linkify an address they can parse on one line; a block of four
        lines in a calendar entry is text to retype.

        The country is dropped when it is the company's own: « Canada » at the
        end of every place, for a consultant driving around Estrie, is noise.
        It stays for anywhere else, where it is the part that matters.
        """
        self.ensure_one()
        lignes = self._bf_address_lignes()
        if not lignes:
            return ""
        maison = self.env.company.country_id
        if maison and self.country_id == maison and len(lignes) > 1:
            nom = (self.country_id.name or "").casefold()
            if lignes[-1].casefold() == nom:
                lignes = lignes[:-1]
        return ", ".join(lignes)

    def bf_address_correspond(self, texte):
        """Is this text one of the ways we write this contact's address?

        ⚠️ Not a plain equality: the country's name is translated, and whether
        it is written at all depends on which company the reader belongs to.
        A meeting filled in by someone working in French reads
        « …, États-Unis »; a colleague opens it in English and the very same
        address renders « …, United States ». If the comparison were
        exact, the place would stop being recognised as ours the moment
        somebody else opened the record — and would then never be cleaned up.
        So the last segment, which is where the country sits, is optional on
        both sides.
        """
        self.ensure_one()
        texte = " ".join((texte or "").split())
        if not texte:
            return False
        lignes = self._bf_address_lignes()
        if not lignes:
            return False
        formes = {", ".join(lignes)}
        if len(lignes) > 1:
            formes.add(", ".join(lignes[:-1]))
        if texte in formes:
            return True
        # ⚠️ Le segment de trop n'est toléré que si c'est bien le nom du pays.
        # Tolérer n'importe quoi nous ferait adopter le texte de quelqu'un :
        # « notre ligne, porte 2 (quai) » serait à nous, donc effaçable.
        tete, _, queue = texte.rpartition(", ")
        return (bool(tete) and tete in formes
                and queue.strip().casefold() in self._bf_address_noms_de_pays())

    def _bf_address_noms_de_pays(self):
        """This contact's country, named in every language on this database."""
        self.ensure_one()
        pays = self.country_id
        if not pays:
            return set()
        codes = {code for code, _nom in self.env["res.lang"].sudo().get_installed()}
        codes |= {"en_US", self.env.lang or "en_US"}
        noms = {(pays.sudo().with_context(lang=code).name or "").casefold()
                for code in codes}
        return {nom for nom in noms if nom}

    # ------------------------------------------------------------ who is who

    @api.model
    def _bf_address_partenaires_internes(self):
        """The contacts of our own companies, read once per call."""
        # Une société fermée reste la nôtre : son ancien bureau n'est pas
        # devenu une destination à suggérer.
        return self.env["res.company"].sudo().with_context(
            active_test=False).search([]).partner_id

    def _bf_address_est_interne(self, internes=None):
        """True for us, not for a customer.

        Our own companies, anyone filed under one of them, and anyone who
        signs in as an internal user are not who the place of the meeting is
        about: suggesting our own office for a visit to a customer would be
        worse than suggesting nothing. The company filed above matters because
        Odoo copies a company's address onto the contacts under it — a
        subcontractor filed under our own company carries our street.
        A portal user is a customer and stays a candidate.
        """
        self.ensure_one()
        if internes is None:
            internes = self._bf_address_partenaires_internes()
        nous = self.sudo()
        if nous in internes or nous.commercial_partner_id in internes:
            return True
        return any(not usager.share for usager in nous.user_ids)

    def _bf_address_candidats(self, internes=None, archives=False):
        """Addresses on file for these contacts, best first.

        The company first, then the guest themselves, then the other addresses
        filed under that company — a company with three plants has them all
        here. The company comes first because that is whose address a visit is
        at, and because Odoo copies a company's address onto the contacts
        filed under it: both carry the same text, and naming the company says
        where it came from.

        Two contacts that carry the very same address appear once. Nothing is
        read from outside the address book: no address is guessed from an
        email, none is fetched from a website.

        ⚠️ Read as the user, not in `sudo`: what this returns is offered in a
        picker and written into a place people will read. A record-rule the
        reader is not allowed to see must not come back out through here.

        `archives=True` keeps the archived contacts in. The picker must not
        offer them, but a meeting whose customer was archived last month —
        membership ended, duplicate merged — has not lost its guest, and the
        place of a visit still in the calendar must not vanish because of a
        tidy-up in the address book.
        """
        if internes is None:
            internes = self._bf_address_partenaires_internes()
        candidats = self.env["res.partner"]
        for partenaire in self:
            if partenaire._bf_address_est_interne(internes):
                continue
            racine = partenaire.commercial_partner_id
            voisines = racine.child_ids if racine else self.env["res.partner"]
            candidats |= racine + partenaire + voisines
        retenus = self.env["res.partner"]
        adresses = {}
        for candidat in candidats:
            if (not candidat.active and not archives) or candidat._bf_address_est_interne(internes):
                continue
            ligne = candidat.bf_address_one_line()
            if not ligne:
                continue
            jumeau = adresses.get(ligne)
            if jumeau is not None:
                # Même adresse, deux fiches : on garde celle qu'on pourrait
                # aussi proposer d'office. Sinon le quai de réception se fait
                # évincer par les comptes payables, et plus rien n'est offert.
                if jumeau.type in TYPES_PAS_DOFFICE and candidat.type not in TYPES_PAS_DOFFICE:
                    retenus -= jumeau
                    retenus |= candidat
                    adresses[ligne] = candidat
                continue
            adresses[ligne] = candidat
            retenus |= candidat
        return retenus

    def _bf_address_choix_doffice(self):
        """The one address we are willing to fill in without being asked.

        ⚠️ « First » is the address book's own order (`complete_name`), not
        the order the guests were typed in: Odoo reads a many2many through the
        comodel's `_order`. With two customers on the same invitation, the one
        that comes first alphabetically wins — which is why what we fill in is
        only ever a suggestion, and why we never do it twice.
        """
        for candidat in self:
            if candidat.type in TYPES_PAS_DOFFICE:
                continue
            ligne = candidat.bf_address_one_line()
            if ligne:
                return candidat, ligne
        return self.env["res.partner"], ""
