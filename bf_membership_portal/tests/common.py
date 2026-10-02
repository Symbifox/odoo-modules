import re

from dateutil.relativedelta import relativedelta

from odoo.tests import HttpCase

from odoo.addons.bf_membership_account.tests.common import MembershipAccountCase, quiet_new_test_user


class MembershipPortalCase(MembershipAccountCase, HttpCase):
    """Le décor du greffon de facturation, vu du portail.

    Chaque personne du portail est un vrai usager portail dont le CONTACT est
    le membre : c'est le lien que le portail suit, jamais l'adresse courriel.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.type_auto_org = cls.type_org.copy({"code": "ORGA", "admission": "automatic"})

        cls.u_alice = cls._portal_user("portail_alice", "Alice Portail", "alice.portail@essai.example")
        cls.u_bruno = cls._portal_user("portail_bruno", "Bruno Portail", "bruno.portail@essai.example")
        cls.u_carole = cls._portal_user("portail_carole", "Carole Portail", "carole.portail@essai.example")
        cls.u_denis = cls._portal_user("portail_denis", "Denis Portail", "denis.portail@essai.example")
        cls.u_nobody = cls._portal_user("portail_personne", "Personne Portail", "personne@essai.example")
        cls.m_alice = cls._membership(cls.u_alice.partner_id, payment_state="paid", payment_source="cheque")
        cls.m_bruno = cls._membership(cls.u_bruno.partner_id, payment_state="paid", payment_source="cheque")
        cls.org = cls.env["res.partner"].create({
            "name": "Organisation des Portails (essai)", "is_company": True, "street": "5, rue de l'Organisation",
            "city": "Val-Exemple"})
        cls.m_org = cls._membership(cls.org, cls.type_auto_org, payment_state="paid", payment_source="cheque")
        Delegate = cls.env["bf.membership.delegate"]
        Delegate.create({"organization_id": cls.org.id, "partner_id": cls.u_carole.partner_id.id})
        Delegate.create({
            "organization_id": cls.org.id, "partner_id": cls.u_denis.partner_id.id,
            "date_from": cls.today - relativedelta(years=2),
            "date_to": cls.today - relativedelta(years=1)})

    def setUp(self):
        super().setUp()
        # Une session publique liée à la base de l'essai : sans elle, une requête
        # anonyme ne sait pas quelle base servir et répond 404 à tout, ce qui
        # ferait passer un essai qui attend un 404.
        self.authenticate(None, None)

    @classmethod
    def _portal_user(cls, login, name, email):
        user = quiet_new_test_user(cls.env, login=login, name=name, email=email, groups="base.group_portal")
        user.partner_id.write({"street": "10, rue du Portail", "city": "Ville-Essai"})
        return user

    def url_open(self, *args, **kwargs):
        """La requête lit la base, pas le cache de l'essai : on vide ce qui
        attend avant, et on oublie ce qu'on croyait savoir après."""
        self.env.flush_all()
        response = super().url_open(*args, **kwargs)
        self.env.invalidate_all()
        return response

    def _csrf(self, url):
        page = self.url_open(url).text
        match = re.search(r'name="csrf_token" value="([^"]+)"', page)
        self.assertTrue(match, "Aucun jeton CSRF sur %s" % url)
        return match.group(1)

    @staticmethod
    def _block(html, element_id):
        """Le texte d'un bloc de la page, espaces normalisés."""
        match = re.search(r'<div[^>]*id="%s"[^>]*>(.*?)</div>' % element_id, html, re.S)
        return " ".join(re.sub(r"<[^>]+>", " ", match.group(1)).split()) if match else None
