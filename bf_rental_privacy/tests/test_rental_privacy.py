"""Ce que le registre dit du dossier locatif, et ce qu'il refuse de confondre.

Le module ne porte qu'un enregistrement de données. C'est justement pour ça
qu'il a besoin de tests : un module dont tout le contenu est une donnée n'a
aucun code qui échoue bruyamment. Une faute de frappe dans le `code`, un
`requires_consent` qui bascule, l'enregistrement qui ne se charge pas chez un
client — rien de tout cela ne se voit, et le registre annonce alors autre chose
que ce que la suite fait.

🔴 **L'idée centrale : DEUX finalités, jamais une.** Fondre le fondement de
l'art. 1974.1 dans le dossier locatif ordinaire reviendrait à annoncer « nom,
adresse et loyer » à une personne dont on enregistre en réalité qu'elle est
victime de violence. Le registre doit dire ce qui est collecté, pas une moyenne
de ce qui est collecté.
"""
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestRentalPrivacy(TransactionCase):

    LEASE = "bf_rental_privacy.purpose_rental_lease"
    GROUND = "bf_rental_privacy.purpose_rental_resiliation_ground"

    def test_the_two_purposes_are_registered(self):
        for xmlid in (self.LEASE, self.GROUND):
            purpose = self.env.ref(xmlid)
            self.assertTrue(purpose.name, xmlid)
            self.assertTrue(purpose.plain_language_summary, xmlid)

    def test_they_are_two_and_not_one(self):
        """🔴 Le cœur du module. Un registre qui annoncerait « le dossier
        locatif » couvrirait le motif de violence sans le nommer, et la personne
        lirait une description qui n'est pas la sienne."""
        self.assertNotEqual(self.env.ref(self.LEASE), self.env.ref(self.GROUND))
        self.assertNotEqual(self.env.ref(self.LEASE).code,
                            self.env.ref(self.GROUND).code)

    def test_neither_asks_for_consent(self):
        """⚠️ Ni l'un ni l'autre ne repose sur un consentement, et pour deux
        raisons différentes. Le dossier locatif tient au bail lui-même. Le
        fondement de résiliation tient à l'exercice d'un droit du locataire :
        lui demander de consentir à ce qu'on sache pourquoi il part
        laisserait croire qu'il peut refuser et partir quand même."""
        for xmlid in (self.LEASE, self.GROUND):
            purpose = self.env.ref(xmlid)
            self.assertFalse(purpose.requires_consent, xmlid)
            self.assertFalse(purpose.requires_express_opt_in, xmlid)

    def test_the_lease_summary_denies_what_the_file_does_not_hold(self):
        """Le résumé dit aussi ce qui n'est PAS collecté. Un locataire venu
        d'ailleurs s'attend à un dépôt de garantie et à une enquête de crédit :
        le registre lui dit qu'il n'y en a pas, plutôt que de se taire."""
        text = self.env.ref(self.LEASE).plain_language_summary.lower()
        self.assertIn("dépôt de garantie", text)
        self.assertIn("enquête de crédit", text)

    def test_the_ground_summary_says_the_attestation_is_not_kept(self):
        """🔴 La distinction qui protège la personne : le locateur conserve le
        FONDEMENT et le fait qu'une attestation a été reçue, pas le contenu de
        l'attestation ni les faits qui la soutiennent."""
        text = self.env.ref(self.GROUND).plain_language_summary.lower()
        self.assertIn("ne conserve pas le contenu", text)

    def test_the_ground_summary_explains_the_protection(self):
        """La personne doit lire ce qui la protège, pas seulement ce qu'on
        collecte : lecture réservée, rien au fil, rien par courriel, et la date
        qui reste visible."""
        text = self.env.ref(self.GROUND).plain_language_summary.lower()
        self.assertIn("courriel", text)
        self.assertIn("date", text)

    def test_the_records_are_noupdate(self):
        """⚠️ Le texte en langage clair est ce qu'on a MONTRÉ aux personnes. Une
        mise à jour du module qui le réécrirait changerait après coup ce
        qu'elles ont lu, sans que personne le sache."""
        for xmlid in (self.LEASE, self.GROUND):
            module, name = xmlid.split(".")
            data = self.env["ir.model.data"].search(
                [("module", "=", module), ("name", "=", name)], limit=1)
            self.assertTrue(data, xmlid)
            self.assertTrue(data.noupdate, xmlid)

    def test_the_bridge_installs_itself_when_both_sides_are_there(self):
        """Déclarer ces finalités dans bf_property_privacy les inscrirait au
        registre d'un syndicat qui ne loue rien."""
        module = self.env["ir.module.module"].search(
            [("name", "=", "bf_rental_privacy")], limit=1)
        self.assertTrue(module)
        self.assertEqual(module.state, "installed")
