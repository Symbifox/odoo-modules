"""L'import vCard, éprouvé par l'assistant et non par le seul parseur.

Les fiches d'essai sont de vrais dialectes : un export d'iPhone, qui range ses
propriétés sous des préfixes de groupe, et un export Android 2.1, qui encode
ses accents en quoted-printable. Ce sont les deux formats sur lesquels la
version précédente perdait des coordonnées en silence.
"""
import base64

from odoo.tests import TransactionCase, tagged

APPLE = """BEGIN:VCARD\r
VERSION:3.0\r
PRODID:-//Apple Inc.//iPhone OS 17.5//EN\r
N:Marchetti;Solveig;;;\r
FN:Solveig Marchetti\r
ORG:Ateliers Les Deux Rives;Direction\r
TITLE:Directrice generale\r
item1.EMAIL;type=INTERNET;type=pref:smarchetti@deuxrives.test\r
item1.X-ABLabel:_$!<Work>!$_\r
item2.TEL;type=pref:+1 (819) 555-0142\r
item2.X-ABLabel:_$!<Work>!$_\r
TEL;type=CELL;type=VOICE:+1 819 555-9876\r
item3.ADR;type=WORK;type=pref:;Bureau 12;1250 rue King Ouest;Sherbrooke;QC;J1J 2B7;Canada\r
item4.URL;type=pref:https://deuxrives.test\r
END:VCARD\r
"""

ANDROID = """BEGIN:VCARD\r
VERSION:2.1\r
N;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:Ostiguy;Fr=C3=A9d=C3=A9rique;;;\r
FN;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:Fr=C3=A9d=C3=A9rique Ostiguy\r
TEL;CELL:+1 514 555-0199\r
TEL;FAX;WORK:+1 514 555-0101\r
EMAIL;INTERNET:fostiguy@exemple.test\r
END:VCARD\r
"""


def _upload(vcf):
    return base64.b64encode(vcf.encode("utf-8"))


@tagged("post_install", "-at_install")
class TestVcardImport(TransactionCase):

    def _run(self, vcf):
        wizard = self.env["bf.contact.vcard.wizard"].create({
            "vcf_file": _upload(vcf), "vcf_filename": "essai.vcf",
        })
        return wizard.action_import()

    def test_apple_export_keeps_grouped_properties(self):
        """Un export d'iPhone range courriel, téléphone et adresse sous
        « item1. » ; tout cela doit arriver sur la fiche."""
        self._run(APPLE)
        partner = self.env["res.partner"].search(
            [("email", "=", "smarchetti@deuxrives.test")])
        self.assertEqual(len(partner), 1)
        self.assertEqual(partner.name, "Solveig Marchetti")
        self.assertEqual(partner.phone, "+1 (819) 555-0142")
        self.assertEqual(partner.mobile, "+1 819 555-9876")
        self.assertEqual(partner.street, "1250 rue King Ouest")
        self.assertEqual(partner.street2, "Bureau 12")
        self.assertEqual(partner.city, "Sherbrooke")
        self.assertEqual(partner.zip, "J1J 2B7")
        self.assertEqual(partner.country_id.code, "CA")
        self.assertEqual(partner.state_id.code, "QC")
        self.assertEqual(partner.website, "https://deuxrives.test")
        self.assertEqual(partner.function, "Directrice generale")

    def test_quoted_printable_accents(self):
        """Les accents d'un export Android arrivent lisibles, et le fax
        ne se déguise pas en numéro de téléphone."""
        self._run(ANDROID)
        partner = self.env["res.partner"].search(
            [("email", "=", "fostiguy@exemple.test")])
        self.assertEqual(partner.name, "Frédérique Ostiguy")
        self.assertEqual(partner.mobile, "+1 514 555-0199")
        self.assertFalse(partner.phone)

    def test_known_company_becomes_the_parent(self):
        """Une société déjà au carnet accueille le contact au lieu d'hériter
        du courriel et du titre de son employé."""
        company = self.env["res.partner"].create({
            "name": "Ateliers Les Deux Rives", "is_company": True,
        })
        self._run(APPLE)
        partner = self.env["res.partner"].search(
            [("email", "=", "smarchetti@deuxrives.test")])
        self.assertEqual(partner.parent_id, company)
        self.assertFalse(company.email, "la société ne récolte pas le courriel")
        self.assertFalse(company.function)

    def test_existing_contact_is_filled_not_clobbered(self):
        """Sur un contact connu, l'import complète les vides et ne touche
        pas à ce qui est déjà renseigné."""
        existing = self.env["res.partner"].create({
            "name": "Solveig Marchetti",
            "email": "smarchetti@deuxrives.test",
            "phone": "+1 819 000-0000",
        })
        self._run(APPLE)
        self.assertEqual(existing.phone, "+1 819 000-0000")
        self.assertEqual(existing.mobile, "+1 819 555-9876")
        self.assertEqual(existing.city, "Sherbrooke")
        self.assertEqual(
            self.env["res.partner"].search_count(
                [("email", "=", "smarchetti@deuxrives.test")]), 1,
            "aucun doublon créé")

    def test_work_email_wins_and_the_other_is_logged(self):
        """Le courriel de travail va dans la fiche, le personnel est dit au
        chatter plutôt que jeté."""
        self._run("""BEGIN:VCARD\r
VERSION:3.0\r
FN:Anouk Delcourt\r
EMAIL;TYPE=INTERNET;TYPE=HOME:anouk.perso@gmail.com\r
EMAIL;TYPE=INTERNET;TYPE=WORK:anouk@delcourt.test\r
END:VCARD\r
""")
        partner = self.env["res.partner"].search([("name", "=", "Anouk Delcourt")])
        self.assertEqual(partner.email, "anouk@delcourt.test")
        bodies = "".join(partner.message_ids.mapped("body"))
        self.assertIn("anouk.perso@gmail.com", bodies)

    def test_photo_is_imported(self):
        pixel = base64.b64encode(base64.b64decode(
            "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRof"
            "Hh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAAB"
            "AAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q=="
        )).decode()
        self._run("BEGIN:VCARD\r\nFN:Photo Valide\r\nEMAIL:photo@exemple.test\r\n"
                  "PHOTO;ENCODING=B;TYPE=JPG:" + pixel + "\r\nEND:VCARD\r\n")
        partner = self.env["res.partner"].search([("email", "=", "photo@exemple.test")])
        self.assertTrue(partner.image_1920)

    def test_nameless_card_is_counted_not_created(self):
        """Une fiche sans nom ni courriel est comptée, pas transformée en
        contact vide."""
        action = self._run("""BEGIN:VCARD\r
VERSION:3.0\r
FN:Personne Valide\r
EMAIL:valide@exemple.test\r
END:VCARD\r
BEGIN:VCARD\r
VERSION:3.0\r
NOTE:ni nom ni courriel\r
END:VCARD\r
""")
        message = action["params"]["message"]
        self.assertIn("1 créé", message)
        self.assertIn("1 fiche(s) sans nom ni courriel", message)

    def test_linkedin_survives_both_lineages(self):
        """La ligne Blue Fox range le profil LinkedIn sur la fiche. La variante
        Symbifox publiée n'a pas ce champ, et l'import doit passer quand même
        plutôt que de lever sur un champ absent."""
        self._run("BEGIN:VCARD\r\nFN:Profil Social\r\nEMAIL:social@exemple.test\r\n"
                  "X-SOCIALPROFILE;type=linkedin:https://www.linkedin.com/in/social\r\n"
                  "END:VCARD\r\n")
        partner = self.env["res.partner"].search([("email", "=", "social@exemple.test")])
        self.assertEqual(len(partner), 1)
        if "x_linkedin_url" in self.env["res.partner"]._fields:
            self.assertEqual(partner.x_linkedin_url,
                             "https://www.linkedin.com/in/social")

    def test_empty_file_is_refused(self):
        from odoo.exceptions import UserError
        with self.assertRaises(UserError):
            self._run("ceci n'est pas une vCard")
