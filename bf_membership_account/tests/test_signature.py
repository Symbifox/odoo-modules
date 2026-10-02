import base64
import io

from PIL import Image

from odoo.exceptions import UserError
from odoo.tests import HttpCase, tagged

from .common import MembershipAccountCase, quiet_new_test_user


@tagged("post_install", "-at_install", "bf_membership_account")
class TestSignatureRoute(MembershipAccountCase, HttpCase):
    """🔴 La signature de la personne autorisée ne sort jamais par les routes
    de fichiers : téléchargée, elle suffirait à contrefaire un reçu."""

    def setUp(self):
        super().setUp()
        # Une session publique liée à la base de l'essai : sans elle, une
        # requête anonyme répond 404 à tout, et l'essai passerait pour rien.
        self.authenticate(None, None)

    def test_signature_refused_to_public_and_portal(self):
        buffer = io.BytesIO()
        Image.new("RGB", (60, 20), (12, 34, 56)).save(buffer, "PNG")
        self.company.membership_receipt_signature = base64.b64encode(buffer.getvalue())
        stored = base64.b64decode(self.company.sudo().membership_receipt_signature)
        quiet_new_test_user(self.env, login="portail_signature", groups="base.group_portal")
        self.env.flush_all()
        company_id = self.company.id
        urls = [
            "/web/image/res.company/%s/membership_receipt_signature" % company_id,
            "/web/image/res.company/%s/membership_receipt_signature?download=true" % company_id,
            "/web/content/res.company/%s/membership_receipt_signature" % company_id,
        ]
        for login in (None, "portail_signature"):
            self.authenticate(login, login)
            for url in urls:
                with self.subTest(login=login, url=url):
                    response = self.url_open(url)
                    self.assertNotIn(stored, response.content)
        # Le même appel sert la signature au responsable : le refus vient bien
        # du champ, pas d'une route fermée à tous.
        self.authenticate("resp_membres", "resp_membres")
        self.assertEqual(self.url_open(urls[0]).content, stored)


@tagged("post_install", "-at_install", "bf_membership_account")
class TestEmployerPortal(MembershipAccountCase, HttpCase):
    """🔴 Une personne rattachée à une entreprise : sa cotisation ne paraît
    pas au portail d'une ou d'un collègue de la même entreprise."""

    def setUp(self):
        super().setUp()
        self.authenticate(None, None)

    def test_colleague_on_the_portal_sees_no_membership_invoice(self):
        colleague = quiet_new_test_user(self.env, login="portail_collegue", groups="base.group_portal")
        colleague.partner_id.parent_id = self.member_org
        membership = self._membership(self.org_employee)
        try:
            with self.env.cr.savepoint():
                membership.with_user(self.agent).action_create_invoice()
        except UserError:
            pass
        self.env.flush_all()
        self.authenticate("portail_collegue", "portail_collegue")
        page = self.url_open("/my/invoices").text
        self.assertNotIn("INV/", page)
        self.assertNotIn("Cotisation", page)
        self.env.invalidate_all()
        self.assertFalse(self.env["account.move"].with_user(colleague).search([("move_type", "=", "out_invoice")]))

    def test_invoiced_person_is_not_attached_to_a_company(self):
        """🔴 Une personne facturée, rattachée ensuite à une entreprise : sa facture
        de cotisation passerait à l'entreprise, lisible et payable au portail de
        ses personnes. Refusé : sans le rôle Membres, d'un refus neutre ; avec le
        rôle, d'un refus qui dit pourquoi."""
        employer = self.env["res.partner"].create({"name": "Employeur (essai)", "is_company": True})
        colleague = quiet_new_test_user(self.env, login="portail_collegue_employeur", groups="base.group_portal")
        colleague.partner_id.parent_id = employer
        person = self.env["res.partner"].create({
            "name": "Personne Autonome (essai)", "street": "8, rue Autonome", "city": "Ville-Essai"})
        move = self._invoice(self._membership(person))
        contacts = quiet_new_test_user(self.env, login="gestion_contacts_parent",
                                       groups="base.group_user,base.group_partner_manager")
        with self.assertRaises(UserError) as caught:
            person.with_user(contacts).write({"parent_id": employer.id})
        message = str(caught.exception)
        self.assertIn("avec vos droits", message)
        for word in ("adhésion", "facture", "cotisation"):
            self.assertNotIn(word, message)
        agent_contacts = quiet_new_test_user(self.env, login="agent_contacts_parent",
                                             groups="bf_membership.group_membership_user,base.group_partner_manager")
        with self.assertRaises(UserError) as caught:
            person.with_user(agent_contacts).write({"parent_id": employer.id})
        self.assertIn("facture de cotisation", str(caught.exception))
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertFalse(person.parent_id)
        self.authenticate("portail_collegue_employeur", "portail_collegue_employeur")
        page = self.url_open("/my/invoices").text
        self.assertNotIn(move.name, page)
        self.env.invalidate_all()
        self.assertFalse(self.env["account.move"].with_user(colleague).search([("move_type", "=", "out_invoice")]))

    def test_fee_invoice_followers_stay_the_member_and_staff(self):
        """🔴 La règle du portail ouvre une facture au portail de l'entreprise de
        tout ABONNÉ. La comptabilité écrit à la personne de facturation depuis le
        fil de la facture (l'abonnement automatique l'inscrirait), abonne une
        personne déléguée ; ces personnes sont ensuite rattachées à une autre
        entreprise : son portail ne voit rien."""
        org_type = self.type_org.copy({"code": "ORGS", "admission": "automatic"})
        move = self._invoice(self._membership(self.member_org, org_type))
        billing = self.org_billing
        delegate = self.env["res.partner"].create({"name": "Personne déléguée (essai)", "parent_id": self.member_org.id})
        move.with_user(self.accountant).with_context(mail_post_autofollow=True).message_post(
            body="Votre facture de cotisation", partner_ids=[billing.id],
            message_type="comment", subtype_xmlid="mail.mt_comment")
        move.with_user(self.accountant).message_subscribe(partner_ids=[delegate.id])
        self.env.flush_all()
        self.env.invalidate_all()
        followers = move.message_partner_ids
        self.assertIn(self.member_org, followers, "Le membre lui-même reste abonné.")
        self.assertIn(self.accountant.partner_id, followers, "Les usagers internes aussi.")
        self.assertNotIn(billing, followers)
        self.assertNotIn(delegate, followers)
        third = self.env["res.partner"].create({"name": "Entreprise tierce (essai)", "is_company": True})
        portal = quiet_new_test_user(self.env, login="portail_tierce_abonnes", groups="base.group_portal")
        portal.partner_id.parent_id = third
        contacts = quiet_new_test_user(self.env, login="gestion_contacts_abonnes",
                                       groups="base.group_user,base.group_partner_manager")
        (billing | delegate).with_user(contacts).write({"parent_id": third.id})
        self.env.flush_all()
        self.authenticate("portail_tierce_abonnes", "portail_tierce_abonnes")
        self.assertNotIn(move.name, self.url_open("/my/invoices").text)
        self.env.invalidate_all()
        self.assertFalse(self.env["account.move"].with_user(portal).search([("move_type", "=", "out_invoice")]))
