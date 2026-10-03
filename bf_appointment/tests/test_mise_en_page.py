"""Les courriels de rendez-vous passent par la mise en page commune.

Les gabarits portaient leur propre coquille (fond, carte, en-tête au logo et au
titre, filet, pied de marque). Ils ne gardent que leur contenu ;
`bf_onboarding_base.bf_mail_layout`, que bluefox_branding remplace par la sienne,
les habille à l'envoi, au nom de la société du type de rendez-vous.
"""
import datetime
import html
import importlib.util
import re
from pathlib import Path
from unittest.mock import patch

import pytz

from odoo import Command, fields
from odoo.tests import TransactionCase, tagged

GABARITS = (
    "mail_template_intake_acknowledgement", "mail_template_appointment_confirmation",
    "mail_template_reminder_2d", "mail_template_reminder_1d", "mail_template_reminder_2h",
    "mail_template_reminder_1h", "mail_template_followup_immediate", "mail_template_followup_1h",
    "mail_template_followup_2h", "mail_template_organizer_new_booking",
    "mail_template_appointment_cancellation", "mail_template_organizer_reschedule",
    "mail_template_organizer_cancellation", "mail_template_guest_invitation",
    "mail_template_guest_confirmation_request",
)
COMMUNE = "bf_onboarding_base.bf_mail_layout"
# La carte de la mise en page commune (copie de secours et originale) : une, pas deux.
CARTE = "box-shadow:0 4px 24px"
# Ce que seule l'ancienne coquille portait, dans un courriel envoyé.
ANCIENNE = re.compile(r"border-radius:12px 12px 0 0|website/1/logo|Rendez-vous par|service@example\.com")
TRACES = ('width="600"', "border-radius:12px 12px 0 0", "appointment_brand_logo_url")
SURTITRE = "text-transform:uppercase; color:#6B7280;"
RACINE = Path(__file__).resolve().parent.parent


def _migration():
    chemin = RACINE / "migrations" / "18.0.2.64.0" / "post-migrate.py"
    spec = importlib.util.spec_from_file_location("bf_appointment_migration_2_64_0", chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _norme(corps):
    corps = re.sub(r"<!--.*?-->", "", html.unescape(corps or "").replace("\xa0", " "), flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r">\s+<", "><", corps)).strip()


@tagged("post_install", "-at_install", "bf_appointment", "bf_appointment_mise_en_page")
class TestMiseEnPage(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, tz="UTC"))
        cls.env.company.name = "Société Principale Essai"
        cls.societe = cls.env["res.company"].create({
            "name": "Société Rendez-vous Essai", "email": "bonjour@societe-rdv.invalid",
            "phone": "+1 514 555-0199"})
        att = [Command.create({"name": "j%s" % d, "dayofweek": str(d), "hour_from": 0.0,
                               "hour_to": 24.0, "day_period": "morning"}) for d in range(7)]
        cls.calendar = cls.env["resource.calendar"].create({
            "name": "24/7 mise en page", "attendance_ids": att, "tz": "UTC",
            "company_id": cls.societe.id})
        ressource = cls.env["resource.resource"].create({
            "name": "salle mise en page", "calendar_id": cls.calendar.id,
            "resource_type": "material", "tz": "UTC", "company_id": cls.societe.id})
        combo = cls.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([ressource.id])]})
        cls.booking_type = cls.env["resource.booking.type"].create({
            "name": "Type mise en page", "duration": 1.0, "slot_duration": 1.0,
            "modifications_deadline": 0.0, "combination_assignment": "sorted",
            "resource_calendar_id": cls.calendar.id, "is_public": True,
            "company_id": cls.societe.id,
            "combination_rel_ids": [Command.create({"sequence": 0, "combination_id": combo.id})]})
        cls.client = cls.env["res.partner"].create({
            "name": "Client Essai", "email": "client@test.invalid", "lang": "fr_CA"})

    def setUp(self):
        super().setUp()
        patcher = patch("odoo.addons.mail.models.mail_mail.MailMail.send", lambda s, *a, **k: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _booking(self):
        debut = fields.Datetime.context_timestamp(
            self.booking_type, fields.Datetime.now()) + datetime.timedelta(hours=1)
        quand = self.booking_type._bf_candidate_slots(
            debut, debut + datetime.timedelta(days=10), limit=1)[0]
        return self.booking_type._bf_create_booking(
            quand.astimezone(pytz.utc).replace(tzinfo=None), partners=self.client)

    def _envoi(self, xmlid, record):
        avant = self.env["mail.mail"].sudo().search([]).ids
        self.env.ref("bf_appointment." + xmlid).send_mail(record.id, force_send=False)
        courriel = self.env["mail.mail"].sudo().search([("id", "not in", avant)])
        self.assertEqual(len(courriel), 1)
        return courriel.body_html

    def _valeurs(self, template):
        self.env.flush_all()
        self.env.cr.execute("SELECT body_html FROM mail_template WHERE id = %s", [template.id])
        return self.env.cr.fetchone()[0] or {}

    def test_les_gabarits_pointent_la_mise_en_page_commune(self):
        for xmlid in GABARITS:
            with self.subTest(gabarit=xmlid):
                self.assertEqual(self.env.ref("bf_appointment." + xmlid).email_layout_xmlid, COMMUNE)

    def test_aucune_langue_stockee_ne_garde_sa_coquille(self):
        for xmlid in GABARITS:
            for lang, corps in self._valeurs(self.env.ref("bf_appointment." + xmlid)).items():
                with self.subTest(gabarit=xmlid, lang=lang):
                    for trace in TRACES:
                        self.assertNotIn(trace, corps)
                    self.assertNotIn("appointment_brand_", corps)
                    self.assertFalse(re.search(
                        r"(?<![\w-])(background|border-left|border-right):", corps))
                    self.assertIn(SURTITRE, corps)

    def test_la_confirmation_est_habillee_une_fois_au_nom_de_la_societe_du_type(self):
        corps = self._envoi("mail_template_appointment_confirmation", self._booking())
        self.assertEqual(corps.count(CARTE), 1, "une carte, la commune")
        self.assertFalse(ANCIENNE.search(corps))
        self.assertIn("Société Rendez-vous Essai", corps)
        self.assertNotIn("Société Principale Essai", corps,
                         "l'en-tête nomme la société du type, pas celle de l'envoyeur")

    def test_le_bouton_supplementaire_prend_la_couleur_de_la_mise_en_page(self):
        self.societe.write({"report_brand_primary": "#123456", "appointment_brand_primary": "#654321"})
        lien = [{"label": "Ordre du jour", "url": "https://exemple.invalid/odj", "help": ""}]
        with patch.object(type(self.env["resource.booking"]), "bf_extra_links", lambda s: lien):
            html = self._booking().bf_extra_cta_html()
        self.assertIn("background-color:#123456", html)
        self.assertNotIn("#654321", html)

    def test_les_mentions_du_pied_restent_dans_le_contenu(self):
        corps = self._envoi("mail_template_organizer_new_booking", self._booking())
        self.assertIn("Notification interne", corps)
        self.assertEqual(corps.count(CARTE), 1)

    def test_le_contact_ecarte_les_valeurs_factices(self):
        self.societe.write({"appointment_brand_support_email": "service@example.com",
                            "appointment_brand_support_phone": "+15555555555",
                            "appointment_brand_support_phone_display": "555-555-5555"})
        self.assertEqual(self.societe.bf_appointment_contact(), {
            "email": "bonjour@societe-rdv.invalid", "phone": "+15145550199",
            "phone_display": "+1 514 555-0199"})
        corps = self._envoi("mail_template_appointment_confirmation", self._booking())
        self.assertIn("mailto:bonjour@societe-rdv.invalid", corps)
        self.assertNotIn("555-555-5555", corps)

    def test_le_contact_garde_le_reglage_rendez_vous(self):
        self.societe.write({"appointment_brand_support_email": "aide@societe-rdv.invalid",
                            "appointment_brand_support_phone": "+15145550100",
                            "appointment_brand_support_phone_display": "514 555-0100"})
        corps = self._envoi("mail_template_appointment_confirmation", self._booking())
        self.assertIn("mailto:aide@societe-rdv.invalid", corps)
        self.assertIn("tel:+15145550100", corps)

    def test_sans_rien_a_citer_la_phrase_de_contact_se_tait(self):
        self.societe.write({"appointment_brand_support_email": False, "email": False,
                            "appointment_brand_support_phone": False,
                            "appointment_brand_support_phone_display": False, "phone": False})
        corps = self._envoi("mail_template_appointment_confirmation", self._booking())
        self.assertNotIn("mailto:", corps.split(CARTE, 1)[1].split("Divider", 1)[0])

    def test_un_invite_prend_la_societe_du_type(self):
        booking = self._booking()
        invite = self.env["resource.booking.guest"].new({"booking_id": booking.id})
        self.assertEqual(list(invite._mail_get_companies().values())[0], self.societe)
        self.assertEqual(booking._mail_get_companies()[booking.id], self.societe)

    def test_la_migration_rend_la_source(self):
        """L'outil de la migration, appliqué à l'ancien corps, rend la source neuve."""
        migration = _migration()
        avant = (RACINE / "tests" / "data" / "confirmation_avant.html").read_text(encoding="utf-8")
        nouveau, ok = migration.retirer_coquille(avant)
        self.assertTrue(ok)
        # La source, pas la base : les corps stockés d'un locataire peuvent replier
        # sur une autre couleur que la source.
        xml = (RACINE / "data" / "appointment_mail_templates.xml").read_text(encoding="utf-8")
        source = re.search(r'<record id="mail_template_appointment_confirmation".*?<!\[CDATA\[(.*?)\]\]>',
                           xml, re.S).group(1)
        self.assertEqual(_norme(nouveau), _norme(source))
        self.assertFalse(migration.a_une_coquille(nouveau))

    def test_la_migration_garde_la_mention_du_pied(self):
        migration = _migration()
        avant = (RACINE / "tests" / "data" / "nouvelle_reservation_avant.html").read_text(encoding="utf-8")
        nouveau, ok = migration.retirer_coquille(avant)
        self.assertTrue(ok)
        self.assertIn("Notification interne <t t-out=\"company.name\"/>", nouveau)
        self.assertNotIn("appointment_brand_name", nouveau)

    def test_la_migration_complete_un_preambule_absent(self):
        migration = _migration()
        avant = (RACINE / "tests" / "data" / "confirmation_avant.html").read_text(encoding="utf-8")
        sans = re.sub(r'<t t-set="(company|brand_primary|brand_dark)"[^>]*/>\s*', "", avant)
        nouveau, ok = migration.retirer_coquille(sans)
        self.assertTrue(ok)
        for nom in ("company", "brand_primary", "contact"):
            self.assertIn('t-set="%s"' % nom, nouveau)
        self.assertNotIn('t-set="brand_dark"', nouveau, "seulement ce que le contenu emploie")

    def test_la_migration_decoupe_l_ancienne_enveloppe_div(self):
        migration = _migration()
        avant = (RACINE / "tests" / "data" / "annulation_organisateur_div.html").read_text(encoding="utf-8")
        nouveau, ok = migration.retirer_coquille(avant)
        self.assertTrue(ok)
        self.assertIn(">Rendez-vous annulé</p>", nouveau)
        self.assertNotIn("max-width:600px", nouveau)
        self.assertNotIn("{{ brand_dark }}'", nouveau)
        self.assertIn("libre à nouveau", nouveau)

    def test_la_migration_decoupe_chaque_langue_et_pose_la_mise_en_page(self):
        migration = _migration()
        template = self.env.ref("bf_appointment.mail_template_appointment_confirmation")
        avant = (RACINE / "tests" / "data" / "confirmation_avant.html").read_text(encoding="utf-8")
        self.env.cr.execute(
            "UPDATE mail_template SET body_html = jsonb_build_object('en_US', %s, 'fr_CA', %s,"
            " 'en_CA', %s), email_layout_xmlid = NULL WHERE id = %s",
            [avant, avant, avant.replace("Bonjour", "Hello"), template.id])
        template.invalidate_recordset()
        migration.migrate(self.env.cr, "18.0.2.62.1")
        valeurs = self._valeurs(template)
        self.assertEqual(set(valeurs), {"en_US", "fr_CA", "en_CA"})
        for corps in valeurs.values():
            self.assertFalse(migration.a_une_coquille(corps))
        self.assertIn("Hello", valeurs["en_CA"])
        template.invalidate_recordset()
        self.assertEqual(template.email_layout_xmlid, COMMUNE)
        migration.migrate(self.env.cr, "18.0.2.62.1")
        self.assertEqual(self._valeurs(template), valeurs, "rejouée, elle ne retouche rien")

    def test_un_corps_refait_a_la_main_reste_tel_quel(self):
        migration = _migration()
        template = self.env.ref("bf_appointment.mail_template_reminder_1d")
        avant = (RACINE / "tests" / "data" / "confirmation_avant.html").read_text(encoding="utf-8")
        maison = '<div style="background-color:#f2f2f2;"><p>Notre rappel, notre habillage.</p></div>'
        self.env.cr.execute(
            "UPDATE mail_template SET body_html = jsonb_build_object('en_US', %s, 'fr_CA', %s),"
            " email_layout_xmlid = NULL WHERE id = %s", [avant, maison, template.id])
        template.invalidate_recordset()
        migration.migrate(self.env.cr, "18.0.2.62.1")
        self.assertEqual(self._valeurs(template), {"en_US": avant, "fr_CA": maison},
                         "tout ou rien : une langue refaite à la main garde le gabarit intact")
        template.invalidate_recordset()
        self.assertFalse(template.email_layout_xmlid)
