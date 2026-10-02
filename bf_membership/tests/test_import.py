import base64

from dateutil.relativedelta import relativedelta

from odoo.tests import tagged

from .common import MembershipCase

# Les dates sont posées au lancement, relativement à l'exercice en cours :
# un CSV daté en dur casserait l'essai le jour où l'exercice change.
CSV = """Prénom;Nom;Courriel;Catégorie;Date de début;Date de fin;Montant;Payé;Identifiant;Code postal
Alice;Essai;ALICE@essai.example;REG;{cs};{ce};70,00 $;Oui;Z-1;A1A 1A1
Gilles;Nouveau;gilles@essai.example;REG;{cs};{ce};70;Oui;Z-2;A1A 2B2
Huguette;Ancienne;huguette@essai.example;REG;{os};{oe};70;Oui;Z-3;A1A 3C3
Ivan;Impayé;ivan@essai.example;REG;{cs};{ce};70;{ivan_paid};Z-4;A1A 4D4
;;;REG;{cs};;;;Z-5;
"""


@tagged("post_install", "-at_install", "bf_membership")
class TestImport(MembershipCase):

    def _csv(self, ivan_paid="Non"):
        cur_end = self.type_person._period_end(self.today)
        cur_start = cur_end - relativedelta(years=1, days=-1)
        old_end = cur_start - relativedelta(years=1, days=1)
        old_start = old_end - relativedelta(years=1, days=-1)
        return CSV.format(cs=cur_start, ce=cur_end, os=old_start, oe=old_end, ivan_paid=ivan_paid)

    def _wizard(self, content=None, source="Zeffy essai"):
        content = content or self._csv()
        return self.env["bf.membership.import"].create({
            "file": base64.b64encode(content.encode("utf-8")),
            "filename": "membres.csv",
            "source": source,
            "type_id": self.type_person.id,
        })

    def _imported(self, source="Zeffy essai"):
        return self.env["bf.membership"].search([("source", "=", source)])

    def test_preview_writes_nothing(self):
        partners_before = self.env["res.partner"].search_count([])
        wizard = self._wizard()
        wizard.action_preview()
        self.assertEqual(wizard.state, "previewed")
        self.assertIn("Aperçu", wizard.report_html)
        self.assertFalse(self._imported())
        self.assertEqual(self.env["res.partner"].search_count([]), partners_before)
        self.assertFalse(self.alice.member_number, "L'aperçu ne consomme aucun numéro.")

    def test_import_matches_creates_and_reports(self):
        wizard = self._wizard()
        wizard.action_import()
        memberships = self._imported()
        self.assertEqual(len(memberships), 4, "La ligne sans nom est écartée.")
        self.assertIn(self.alice, memberships.partner_id, "Rapproché par courriel, casse ignorée.")
        self.assertEqual(self.env["res.partner"].search_count([("email", "=ilike", "alice@essai.example")]), 1)
        gilles = memberships.filtered(lambda m: m.partner_id.name == "Gilles Nouveau")
        self.assertEqual(gilles.state, "active")
        self.assertEqual(gilles.payment_source, "platform")
        ivan = memberships.filtered(lambda m: m.partner_id.name == "Ivan Impayé")
        self.assertEqual(ivan.state, "waiting")
        self.assertIn("Ligne 6", wizard.report_html)

    def test_expired_rows_never_trigger_reminders(self):
        self.company.membership_reminders = True
        self._wizard().action_import()
        huguette = self._imported().filtered(lambda m: m.partner_id.name == "Huguette Ancienne")
        self.assertEqual(huguette.state, "expired")
        self.assertEqual(huguette.reminder_stage, "done")
        self.env["bf.membership"]._cron_daily()
        mails = self.env["mail.mail"].search([("recipient_ids", "in", huguette.partner_id.ids)])
        self.assertFalse(mails)

    def test_reimport_updates_instead_of_duplicating(self):
        self._wizard().action_import()
        self._wizard(self._csv(ivan_paid="Oui")).action_import()
        memberships = self._imported()
        self.assertEqual(len(memberships), 4)
        ivan = memberships.filtered(lambda m: m.partner_id.name == "Ivan Impayé")
        self.assertEqual(ivan.payment_state, "paid")
        self.assertEqual(ivan.state, "active")

    def test_same_period_from_another_list_is_a_duplicate(self):
        self._membership(self.alice, payment_state="paid", date_start=self._day(days=-1))
        wizard = self._wizard(source="Excel trésorière")
        wizard.action_import()
        alice_rows = self._imported("Excel trésorière").filtered(lambda m: m.partner_id == self.alice)
        self.assertFalse(alice_rows, "Une adhésion en double a été créée pour la même période.")
        self.assertIn("Déjà au registre pour cette période", wizard.report_html)

    def test_ambiguous_email_is_a_conflict(self):
        self.env["res.partner"].create({"name": "Alice Doublon", "email": "alice@essai.example"})
        wizard = self._wizard()
        wizard.action_import()
        self.assertNotIn(self.alice, self._imported().partner_id)
        self.assertIn("2 contacts ont le courriel", wizard.report_html)

    def test_underscore_in_email_is_not_a_wildcard(self):
        """« jean_paul » ne rapproche pas « jeanXpaul » : `_` est un joker de LIKE."""
        lookalike = self.env["res.partner"].create({"name": "Jean X Paul", "email": "jeanXpaul@essai.example"})
        content = ("Prénom;Nom;Courriel;Catégorie;Identifiant\n"
                   "Jean;Paul;jean_paul@essai.example;REG;U-1\n")
        self._wizard(content, source="Soulignés").action_import()
        imported = self._imported("Soulignés")
        self.assertEqual(len(imported), 1)
        self.assertNotEqual(imported.partner_id, lookalike)
        self.assertEqual(imported.partner_id.email, "jean_paul@essai.example")
