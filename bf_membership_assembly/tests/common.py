from datetime import datetime, time

from dateutil.relativedelta import relativedelta

from odoo import Command

from odoo.addons.bf_membership.tests.common import MembershipCase


class AssemblyCase(MembershipCase):
    """Décor : une assemblée dans 30 jours, date de référence hier.

    Votants attendus à la date de référence (sept lignes) :

    * cinq personnes en règle : Alice (courriel et consentement), Bruno
      (courriel sans consentement), Francine (consentement sans courriel),
      Gilles (courriel et consentement), Henri (ni l'un ni l'autre) ;
    * le Organisme Les Essais, organisation membre, qui vote par Carole ;
    * le Organisme Sans Délégué, organisation membre sans délégué votant.

    Exclus : Ines (honoraire, sans droit de vote), Jules (entré aujourd'hui,
    après la date de référence), Karine (échue cinq jours avant la date de
    référence : en grâce, exclue d'office), Louis (adhésion jamais payée, échue
    depuis la date de référence).

    Les dates sont relatives au jour de l'essai, et les fins de période sont
    posées à la main : la catégorie des personnes suit un exercice d'avril à
    mars, qui ferait finir une adhésion avant la date de référence selon le
    mois où l'essai tourne.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Partner = cls.env["res.partner"]
        cls.meeting_day = cls.today + relativedelta(days=30)
        cls.record_day = cls.today - relativedelta(days=1)
        cls.start = cls.record_day - relativedelta(days=60)
        cls.end = cls.meeting_day + relativedelta(days=300)

        cls.alice.notice_email_consent = True
        cls.francine = Partner.create({"name": "Francine Essai", "notice_email_consent": True})
        cls.gilles = Partner.create({
            "name": "Gilles Essai", "email": "gilles@essai.example", "notice_email_consent": True})
        cls.henri = Partner.create({"name": "Henri Essai", "street": "1 rue de l'Essai", "city": "Ville-Essai"})
        cls.persons = cls.alice | cls.bruno | cls.francine | cls.gilles | cls.henri
        for person in cls.persons:
            cls._membership(person, payment_state="paid", date_start=cls.start, date_end=cls.end)

        cls.member_org_membership = cls._org_membership(cls.member_org)
        cls.env["bf.membership.delegate"].create({
            "organization_id": cls.member_org.id, "partner_id": cls.carole.id, "date_from": cls.start})
        cls.orphan_org = Partner.create({"name": "Organisme Sans Délégué", "is_company": True})
        cls._org_membership(cls.orphan_org)

        cls.ines = Partner.create({"name": "Ines Honoraire"})
        cls._membership(cls.ines, cls.type_honorary, payment_state="exempt", date_start=cls.start)
        cls.jules = Partner.create({"name": "Jules Tardif"})
        cls._membership(cls.jules, payment_state="paid", date_start=cls.today, date_end=cls.end)
        cls.karine = Partner.create({"name": "Karine Grâce"})
        lapsed = cls._membership(
            cls.karine, payment_state="paid",
            date_start=cls.record_day - relativedelta(days=400),
            date_end=cls.record_day - relativedelta(days=5))
        lapsed.state = "expired"
        # Jamais payée, et échue hier par la passe quotidienne : sa période
        # couvre la date de référence, son état n'est plus « à payer ». Seul le
        # paiement l'écarte.
        cls.louis = Partner.create({"name": "Louis Impayé"})
        unpaid = cls._membership(cls.louis, date_start=cls.start, date_end=cls.record_day)
        unpaid.state = "expired"

        cls.expected_voters = cls.persons | cls.member_org | cls.orphan_org

    @classmethod
    def _org_membership(cls, org):
        membership = cls._membership(org, cls.type_org, date_start=cls.start, date_end=cls.end)
        membership.action_accept()
        membership.action_mark_paid()
        return membership

    def _assembly(self, **vals):
        values = {
            "name": "Assemblée générale annuelle (essai)",
            "kind": "annual",
            # Midi à Montréal : le même jour dans tous les fuseaux d'Amérique
            # et d'Europe, quel que soit celui de la personne qui fait l'essai.
            "date": datetime.combine(self.meeting_day, time(16, 0)),
            "record_date": self.record_day,
            "attachment_ids": [Command.create({
                "name": "États financiers (essai).pdf", "raw": b"%PDF-1.4 essai"})],
        }
        values.update(vals)
        return self.env["bf.membership.assembly"].create(values)

    def _convened(self, **vals):
        assembly = self._assembly(**vals)
        assembly.action_convene()
        return assembly

    def _opened(self, **vals):
        assembly = self._convened(**vals)
        assembly.action_open()
        return assembly

    def _line(self, assembly, partner):
        return assembly.voter_ids.filtered(lambda v: v.member_id == partner)

    def _attend(self, assembly, partners, mode="onsite"):
        for partner in partners:
            self._line(assembly, partner).attendance = mode

    def _proposal(self, assembly, **vals):
        values = {"assembly_id": assembly.id, "name": "Proposition (essai)"}
        values.update(vals)
        return self.env["bf.membership.assembly.proposal"].create(values)
