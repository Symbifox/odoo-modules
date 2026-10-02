from odoo.tests import tagged

from .common import MembershipPortalCase

DIRECTORY = "/membres/repertoire"


@tagged("post_install", "-at_install", "bf_membership_portal")
class TestDirectory(MembershipPortalCase):

    def setUp(self):
        super().setUp()
        # En règle et consentant : paraît.
        self.u_alice.partner_id.write({"directory_consent": True, "city": "Lac-Alice"})
        # En règle sans consentement : ne paraît pas.
        self.u_bruno.partner_id.write({"directory_consent": False, "city": "Bourg-Exemple"})
        # En grâce et consentant : paraît.
        self.grace = self.env["res.partner"].create({
            "name": "Gaston Grâce", "city": "Mont-Exemple", "email": "gaston@essai.example", "directory_consent": True})
        self._membership(self.grace, payment_state="paid", payment_source="cheque",
                         date_start=self._day(years=-1), date_end=self._day(days=-5)).state = "expired"
        # Ancien membre consentant : ne paraît plus.
        self.former = self.env["res.partner"].create({
            "name": "Fernande Ancienne", "city": "Pointe-Exemple", "directory_consent": True})
        self._membership(self.former, payment_state="paid", payment_source="cheque",
                         date_start=self._day(years=-3), date_end=self._day(years=-2)).state = "expired"
        # Demande en cours, consentante : ne paraît pas.
        self.pending = self.env["res.partner"].create({
            "name": "Paulette Attente", "city": "Rive-Exemple", "directory_consent": True})
        self._membership(self.pending)
        self._recompute_status(self.grace | self.former | self.pending | self.u_alice.partner_id
                               | self.u_bruno.partner_id)

    def test_closed_by_default(self):
        self.assertEqual(self.env.company.membership_directory, "closed")
        self.assertEqual(self.url_open(DIRECTORY).status_code, 404)
        self.authenticate("portail_alice", "portail_alice")
        self.assertEqual(self.url_open(DIRECTORY).status_code, 404)
        # Le même appel répond dès que l'organisme ouvre le répertoire : le 404
        # vient bien du réglage.
        self.env.company.membership_directory = "public"
        self.assertEqual(self.url_open(DIRECTORY).status_code, 200)

    def test_public_shows_only_consenting_members_name_and_city(self):
        self.env.company.membership_directory = "public"
        response = self.url_open(DIRECTORY)
        self.assertEqual(response.status_code, 200)
        page = response.text
        for shown in ("Alice Portail", "Lac-Alice", "Gaston Grâce", "Mont-Exemple"):
            self.assertIn(shown, page)
        for hidden in ("Bruno Portail", "Fernande Ancienne", "Paulette Attente", "Organisation des Portails",
                       "gaston@essai.example", "alice.portail@essai.example", "10, rue du Portail"):
            self.assertNotIn(hidden, page)

    def test_members_only_mode(self):
        self.env.company.membership_directory = "members"
        response = self.url_open(DIRECTORY, allow_redirects=False)
        self.assertIn(response.status_code, (302, 303))
        self.assertIn("/web/login", response.headers["Location"])
        self.authenticate("portail_personne", "portail_personne")
        self.assertEqual(self.url_open(DIRECTORY).status_code, 404)
        self.authenticate("portail_alice", "portail_alice")
        self.assertIn("Alice Portail", self.url_open(DIRECTORY).text)
        self.authenticate("portail_carole", "portail_carole")
        self.assertEqual(self.url_open(DIRECTORY).status_code, 200,
                         "La déléguée d'une organisation en règle y a accès.")

    def test_directory_reads_the_status_of_its_own_company(self):
        """🔴 Base à deux sociétés : le statut vient des seules adhésions de la
        société du répertoire, jamais du statut toutes sociétés confondues."""
        other = self.env["res.company"].create({"name": "Seconde société (essai)"})
        other_type = self.type_person.copy({"company_id": other.id, "code": "REGB"})
        both = self.env["res.partner"].create({
            "name": "Personne Deux Sociétés", "city": "Val-Témoin", "directory_consent": True})
        # Ancienne membre de la société du répertoire, en règle dans l'autre.
        self._membership(both, payment_state="paid", payment_source="cheque",
                         date_start=self._day(years=-3), date_end=self._day(years=-2)).state = "expired"
        self.env["bf.membership"].create({
            "partner_id": both.id, "type_id": other_type.id, "company_id": other.id,
            "payment_state": "paid", "payment_source": "cheque"})
        self._recompute_status(both)
        self.assertEqual(both.member_status, "member", "En règle, toutes sociétés confondues.")
        self.env.company.membership_directory = "public"
        self.assertNotIn("Personne Deux Sociétés", self.url_open(DIRECTORY).text)
        # Une personne en règle ici n'ouvre pas le répertoire « membres
        # connectés » de l'autre société.
        other.membership_directory = "members"
        self.u_alice.write({"company_ids": [(4, other.id)], "company_id": other.id})
        self.authenticate("portail_alice", "portail_alice")
        self.assertEqual(self.url_open(DIRECTORY).status_code, 404)
