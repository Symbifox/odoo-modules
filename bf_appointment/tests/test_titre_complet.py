"""La rencontre porte le même titre partout : agenda, courriels, pages, salle Talk.

L'agenda de l'organisateur et le .ics joint nommaient la rencontre par le nom
de la réservation (« Rencontre exploratoire - Organisation x Marque », ou le
titre écrit à la main d'un lien personnel). Les courriels, eux, la nommaient
par son type : objet « Rencontre flexible | Personne », ligne « Type » dans le
corps. La personne recevait un accusé qui ne disait pas de quelle rencontre il
s'agissait, et l'organisateur des avis qu'il ne retrouvait pas dans son
agenda.

Ces essais échouent sur 18.0.2.64.2 : l'objet rendu y est le nom du type.

🔴 Les pages à jeton et la salle Talk n'affichent pas ce titre-là pour une
réservation du formulaire public : la
fiche y est retrouvée par l'adresse tapée, sans preuve que la personne la
possède, et le navigateur part aussitôt vers la page du créneau avec le jeton.
Elles affichent `bf_page_title()`, le titre public bâti sur ce que la personne a
tapé. Voir `TestTitrePublicFormulaire`.
"""
import datetime
import html
import importlib.util
import json
import logging
import re
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode, urlsplit

import pytz

from odoo import Command, fields
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged
from odoo.tools import mute_logger

from odoo.addons.bf_appointment.controllers import main as ctrl

RACINE = Path(__file__).resolve().parent.parent
TITRE = "Revue des principes | Organisation Essai x Marque Essai"
TITRE_PUBLIC = "Rencontre flexible essai - Nom tapé Essai x Marque Essai"


def _migration():
    chemin = RACINE / "migrations" / "18.0.2.65.0" / "post-migration.py"
    spec = importlib.util.spec_from_file_location("bf_appointment_migration_2_65_0", chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _type_rdv(env, nom, slug):
    attendances = [
        Command.create({
            "name": "j%s" % d, "dayofweek": str(d), "hour_from": 0.0,
            "hour_to": 24.0, "day_period": "morning",
        })
        for d in range(7)
    ]
    calendrier = env["resource.calendar"].create({
        "name": "24/7 %s" % slug, "attendance_ids": attendances, "tz": "UTC"})
    ressource = env["resource.resource"].create({
        "name": "%s material" % slug, "calendar_id": calendrier.id,
        "resource_type": "material", "tz": "UTC"})
    combinaison = env["resource.booking.combination"].create({
        "resource_ids": [Command.set([ressource.id])]})
    return env["resource.booking.type"].create({
        "name": nom, "slug": slug,
        "duration": 1.0, "slot_duration": 1.0,
        "modifications_deadline": 0.0, "combination_assignment": "sorted",
        "resource_calendar_id": calendrier.id, "video_provider": "none",
        "requires_recording_consent": False,
        "sends_intake_acknowledgement": False,
        "is_public": True, "listed_on_landing": False,
        "combination_rel_ids": [
            Command.create({"sequence": 0, "combination_id": combinaison.id})],
    })


@tagged("post_install", "-at_install", "bf_appointment", "bf_appointment_titre")
class TestTitreComplet(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True,
                                       no_mail_to_attendees=True))
        cls.type_rdv = _type_rdv(cls.env, "Rencontre flexible essai", "titre-complet-essai")
        cls.personne = cls.env["res.partner"].create({
            "name": "Personne Essai", "email": "personne-titre@test.invalid",
            "lang": "fr_CA"})

    def setUp(self):
        super().setUp()
        patcher = patch("odoo.addons.mail.models.mail_mail.MailMail.send", lambda s, *a, **k: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _reservation(self, nom=TITRE):
        """Une réservation datée, comme le contrôleur la crée."""
        debut = fields.Datetime.context_timestamp(
            self.type_rdv, fields.Datetime.now()) + datetime.timedelta(hours=1)
        quand = self.type_rdv._bf_candidate_slots(
            debut, debut + datetime.timedelta(days=10), limit=1)[0]
        vals = {"name": nom} if nom else {}
        return self.type_rdv._bf_create_booking(
            quand.astimezone(pytz.utc).replace(tzinfo=None), partners=self.personne, vals=vals)

    # -- le titre ------------------------------------------------------------

    def test_le_titre_est_le_nom_de_la_reservation(self):
        self.assertEqual(self._reservation().bf_title(), TITRE)

    def test_le_nom_prime_sur_l_evenement(self):
        evenement = self.env["calendar.event"].create({
            "name": "Autre titre", "start": fields.Datetime.now(),
            "stop": fields.Datetime.now() + datetime.timedelta(hours=1)})
        booking = self.env["resource.booking"].new({
            "type_id": self.type_rdv.id, "name": TITRE, "meeting_id": evenement.id})
        self.assertEqual(booking.bf_title(), TITRE)

    def test_sans_nom_le_titre_est_celui_de_l_evenement(self):
        """Une réservation créée au backend sans nom : l'OCA nomme l'événement lui-même."""
        evenement = self.env["calendar.event"].create({
            "name": "Titre de l'agenda", "start": fields.Datetime.now(),
            "stop": fields.Datetime.now() + datetime.timedelta(hours=1)})
        booking = self.env["resource.booking"].new({
            "type_id": self.type_rdv.id, "name": False, "meeting_id": evenement.id})
        self.assertEqual(booking.bf_title(), "Titre de l'agenda")

    def test_sans_nom_ni_evenement_le_titre_est_compose(self):
        booking = self.env["resource.booking"].new({
            "type_id": self.type_rdv.id, "name": False,
            "partner_ids": [Command.set([self.personne.id])]})
        attendu = self.env["resource.booking"]._bf_build_title(
            self.type_rdv, partner=self.personne)
        self.assertEqual(booking.bf_title(), attendu)
        self.assertTrue(attendu.startswith("Rencontre flexible essai - Personne Essai"))

    def test_le_titre_n_est_pas_appelable_en_rpc(self):
        methode = type(self.env["resource.booking"]).bf_title
        self.assertTrue(getattr(methode, "_api_private", False))

    # -- le titre des pages à jeton -------------------------------------------

    def test_le_titre_de_page_est_le_titre_public(self):
        booking = self._reservation()
        booking.bf_public_title = TITRE_PUBLIC
        self.assertEqual(booking.bf_page_title(), TITRE_PUBLIC)
        # L'agenda, les courriels et le .ics gardent le titre complet.
        self.assertEqual(booking.bf_title(), TITRE)

    def test_sans_titre_public_la_page_a_le_titre_complet(self):
        """Lien personnel, réservation faite par l'équipe : le titre complet."""
        booking = self._reservation()
        self.assertFalse(booking.bf_public_title)
        self.assertEqual(booking.bf_page_title(), TITRE)
        booking.bf_public_title = "   "
        self.assertEqual(booking.bf_page_title(), TITRE)

    def test_le_titre_de_page_n_est_pas_appelable_en_rpc(self):
        methode = type(self.env["resource.booking"]).bf_page_title
        self.assertTrue(getattr(methode, "_api_private", False))

    def test_le_titre_public_ne_suit_pas_une_copie(self):
        self.assertFalse(self.env["resource.booking"]._fields["bf_public_title"].copy)

    # -- les courriels ---------------------------------------------------------

    def test_chaque_gabarit_a_le_titre_pour_objet(self):
        """Les seize gabarits, tels qu'en base : objet = titre, plus de nom de type."""
        booking = self._reservation()
        invite = self.env["resource.booking.guest"].create({
            "booking_id": booking.id, "name": "Invitée Essai",
            "email": "invitee-titre@test.invalid"})
        gabarits = self.env["ir.model.data"].search([
            ("module", "=", "bf_appointment"), ("model", "=", "mail.template")])
        self.assertEqual(len(gabarits), 16)
        for donnee in gabarits:
            gabarit = self.env["mail.template"].browse(donnee.res_id)
            with self.subTest(gabarit=donnee.name):
                self.assertNotIn("type_id.name", gabarit.subject or "")
                self.assertNotIn("type_id.name", gabarit.body_html or "")
                cible = invite if gabarit.model == "resource.booking.guest" else booking
                objet = gabarit._render_field("subject", cible.ids)[cible.id]
                self.assertIn(TITRE, objet)
                self.assertNotIn("Rencontre flexible essai", objet)

    def test_la_confirmation_nomme_la_rencontre_dans_le_corps(self):
        booking = self._reservation()
        gabarit = self.env.ref("bf_appointment.mail_template_appointment_confirmation")
        corps = gabarit._render_field("body_html", booking.ids)[booking.id]
        self.assertIn("Rencontre</td>", corps)
        self.assertIn(TITRE, corps)
        self.assertNotIn(">Type</td>", corps)
        self.assertNotIn("Rencontre flexible essai", corps)

    def test_l_ics_porte_le_meme_titre(self):
        booking = self._reservation()
        ics = booking._generate_ics_data()
        ics = ics.decode() if isinstance(ics, bytes) else ics
        self.assertIn("SUMMARY:Revue des principes | Organisation Essai x Marque Essai", ics)
        description = re.search(r"DESCRIPTION:(.*?)\r\n", ics).group(1)
        self.assertTrue(description.startswith("Revue des principes"), description)

    # -- la salle Talk ---------------------------------------------------------

    def test_la_salle_talk_porte_le_titre(self):
        """Lien personnel, réservation de l'équipe : sans titre public, le titre complet."""
        nom = self._nom_de_salle(self._reservation())
        self.assertTrue(nom.startswith(TITRE + " | "), nom)
        self.assertNotIn("Rencontre flexible essai", nom)

    def test_la_salle_talk_porte_le_titre_public(self):
        """Le lien de la salle part avec les pages à jeton : même titre qu'elles."""
        booking = self._reservation()
        booking.bf_public_title = TITRE_PUBLIC
        nom = self._nom_de_salle(booking)
        self.assertTrue(nom.startswith(TITRE_PUBLIC + " | "), nom)
        self.assertNotIn("Organisation Essai", nom)

    def _nom_de_salle(self, booking):
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("bf_appointment.nc_talk_base_url", "https://nc.test.invalid")
        ICP.set_param("bf_appointment.nc_talk_user", "fox-bot")
        ICP.set_param("bf_appointment.nc_talk_password_encrypted", "peu-importe")
        noms = []

        class Reponse:
            status_code = 200
            text = ""

            def json(self):
                return {"ocs": {"data": {"token": "salle123"}}}

            def raise_for_status(self):
                return None

        def faux_post(url, **kw):
            if url.endswith("/room"):
                noms.append((kw.get("data") or {}).get("roomName"))
            return Reponse()

        modele = type(booking)
        with patch.object(modele, "_decrypt_nc_talk_password", return_value="mdp"), \
                patch.object(modele, "_nc_talk_grant_moderation", return_value=None), \
                patch("requests.post", side_effect=faux_post):
            booking._generate_nc_talk_url()
        self.assertEqual(len(noms), 1)
        return noms[0]

    # -- la migration ----------------------------------------------------------

    def test_la_migration_remplace_l_objet_d_origine(self):
        m = _migration()
        self.assertEqual(
            m.remplacer_objet("{{ object.type_id.name }} | {{ object.partner_id.name }}"),
            ("{{ object.bf_title() }}", 1))
        self.assertEqual(
            m.remplacer_objet("Confirmez vos invités \u2014 {{ object.type_id.name }}")[0],
            "Confirmez vos invités : {{ object.bf_title() }}")
        self.assertEqual(
            m.remplacer_objet("Invitation: {{ object.booking_id.type_id.name }}")[0],
            "Invitation: {{ object.booking_id.bf_page_title() }}")

    def test_la_migration_garde_un_objet_retouche(self):
        m = _migration()
        retouche = ("Confirmé · {{ format_datetime(object.start, dt_format='EEEE d MMMM') }}"
                    " à {{ object.start_time_local }} \u2014 {{ object.type_id.name }}")
        neuf, n = m.remplacer_objet(retouche)
        self.assertEqual(n, 1)
        self.assertEqual(neuf, retouche.replace("object.type_id.name", "object.bf_title()"))

    def test_la_migration_etiquette_selon_la_langue_du_contenu(self):
        m = _migration()
        ligne = ('<td style="x">%s</td>\n    <td style="y"><t t-out="object.type_id.name or \'\'"/></td>')
        francais = "<p>Bonjour</p>" + ligne % "Type"
        anglais = "<p>Hello</p>" + ligne % "Type"
        accuse = "<p>Votre demande</p>" + ligne % "Type de rencontre"
        accuse_en = "<p>Your request</p>" + ligne % "Meeting type"
        self.assertIn('>Rencontre</td>\n    <td style="y"><t t-out="object.bf_title()"/>',
                      m.remplacer_corps(francais)[0])
        self.assertIn(">Meeting</td>", m.remplacer_corps(anglais)[0])
        self.assertIn(">Rencontre</td>", m.remplacer_corps(accuse)[0])
        self.assertIn(">Meeting</td>", m.remplacer_corps(accuse_en)[0])

    def test_la_migration_ne_touche_pas_une_autre_etiquette_type(self):
        m = _migration()
        corps = '<p>Bonjour</p><td>Type</td><td><t t-out="object.location"/></td>'
        self.assertEqual(m.remplacer_corps(corps), (corps, 0))

    def test_la_migration_ne_touche_pas_un_champ_voisin(self):
        m = _migration()
        voisin = "{{ object.type_id.name_court }} {{ object.type_id.name_get() }}"
        self.assertEqual(m.remplacer_objet(voisin), (voisin, 0))

    def test_la_migration_est_idempotente(self):
        m = _migration()
        source = (RACINE / "data" / "appointment_mail_templates.xml").read_text(encoding="utf-8")
        self.assertEqual(m.remplacer_objet(source)[1], 0)
        self.assertEqual(m.remplacer_corps(source)[1], 0)
        self.assertNotIn("type_id.name", source)

    # -- la migration : le titre public des anciennes réservations -------------

    def _ancienne(self, type_rdv, langue, **vals):
        """Une réservation d'avant 2.65.0, partenaire principal dans `langue`."""
        partenaire = self.env["res.partner"].create({
            "name": "Ancienne %s %s" % (type_rdv.slug, langue),
            "email": "ancienne-%s-%s@test.invalid" % (type_rdv.slug, langue)})
        booking = self.env["resource.booking"].create(dict(
            {"type_id": type_rdv.id, "partner_ids": [Command.set([partenaire.id])]}, **vals))
        self.env.flush_all()
        # En SQL : une langue que la base n'a pas activée (de_DE) reste lisible.
        self.env.cr.execute("UPDATE res_partner SET lang = %s WHERE id = %s",
                            (langue, partenaire.id))
        return booking

    def _types_traduits(self):
        noms = [
            {"en_US": "A en_US", "fr_CA": "A fr_CA", "en_CA": "A en_CA"},
            {"en_US": "B en_US", "de_DE": "B de_DE"},
            {"aa_AA": "", "de_DE": "C de_DE"},
        ]
        types = []
        for i, nom in enumerate(noms):
            type_rdv = _type_rdv(self.env, "Migration %d" % i, "titre-migration-%d" % i)
            self.env.flush_all()
            self.env.cr.execute("UPDATE resource_booking_type SET name = %s::jsonb WHERE id = %s",
                                (json.dumps(nom), type_rdv.id))
            types.append(type_rdv)
        return types

    def _migrer(self, version="18.0.2.64.2"):
        self.env.flush_all()
        _migration().migrate(self.env.cr, version)
        self.env.invalidate_all()

    def test_la_migration_pose_le_nom_du_type_sur_le_formulaire_public(self):
        """Comme les pages l'affichaient en 2.64.2, dans la langue de la personne."""
        a, b, c = self._types_traduits()
        langue = self._ancienne(a, "en_CA")
        sans_cle = self._ancienne(a, "de_DE")
        sans_fr = self._ancienne(b, "fr_CA")
        autre_cle = self._ancienne(c, "fr_CA")
        self._migrer()
        self.assertEqual(langue.bf_public_title, "A en_CA")
        self.assertEqual(sans_cle.bf_public_title, "A fr_CA")
        self.assertEqual(sans_fr.bf_public_title, "B en_US")
        self.assertEqual(autre_cle.bf_public_title, "C de_DE")
        # La page affiche ce nom, pas le titre bâti sur la fiche du carnet.
        self.assertEqual(langue.bf_page_title(), "A en_CA")

    def test_la_migration_laisse_les_liens_personnels_et_les_autres_sources(self):
        a = self._types_traduits()[0]
        lien = a._bf_create_onetime_link(self.personne)
        self.assertEqual(lien.bf_source, "onetime")
        sondage = self._ancienne(a, "fr_CA", bf_source="poll")
        deja = self._ancienne(a, "fr_CA", bf_public_title="Titre déjà posé")
        public = self._ancienne(a, "fr_CA")
        self._migrer()
        self.assertFalse(lien.bf_public_title)
        self.assertFalse(sondage.bf_public_title)
        self.assertEqual(deja.bf_public_title, "Titre déjà posé")
        self.assertEqual(public.bf_public_title, "A fr_CA")

    def test_la_migration_ne_remplit_rien_a_l_installation(self):
        a = self._types_traduits()[0]
        public = self._ancienne(a, "fr_CA")
        self._migrer(version=None)
        self.assertFalse(public.bf_public_title)


@tagged("post_install", "-at_install", "bf_appointment", "bf_appointment_titre")
class TestTitreCompletPages(HttpCase):
    """Le vrai chemin : la page du lien personnel, telle qu'un aperçu de lien la lit."""

    def setUp(self):
        super().setUp()
        if not self.env["ir.module.module"].sudo().search_count(
                [("name", "=", "website"), ("state", "=", "installed")]):
            self.skipTest("module `website` absent : les routes `website=True` ne sont pas routées")
        self.type_rdv = _type_rdv(self.env, "Rencontre flexible pages", "titre-complet-pages")
        self.personne = self.env["res.partner"].create({
            "name": "Personne Pages", "email": "personne-pages@test.invalid"})
        self.lien = self.type_rdv._bf_create_onetime_link(self.personne)
        self.lien.name = TITRE
        self.env.cr.flush()

    def test_la_page_du_lien_personnel_porte_le_titre(self):
        page = self.url_open("/appointment/b/%d/%s/schedule" % (
            self.lien.id, self.lien.access_token))
        self.assertEqual(page.status_code, 200)
        titre = re.search(r"<title>(.*?)</title>", page.text, re.S).group(1).strip()
        self.assertTrue(titre.startswith("Revue des principes | Organisation Essai x Marque Essai"),
                        titre)
        self.assertNotIn("Rencontre flexible pages", page.text)

    def test_la_page_de_confirmation_porte_le_titre(self):
        page = self.url_open("/appointment/b/%d/%s" % (self.lien.id, self.lien.access_token))
        self.assertEqual(page.status_code, 200)
        titre = re.search(r"<title>(.*?)</title>", page.text, re.S).group(1).strip()
        self.assertTrue(titre.startswith(TITRE), titre)
        corps = page.text.split("</head>", 1)[1]
        self.assertIn(TITRE, corps)
        self.assertNotIn("Rencontre flexible pages", page.text)

    def test_la_page_d_annulation_nomme_la_rencontre(self):
        """En GET, la page demande : elle nomme la rencontre et n'annule rien."""
        page = self.url_open("/appointment/b/%d/%s/cancel" % (self.lien.id, self.lien.access_token))
        self.assertEqual(page.status_code, 200)
        self.assertIn("<strong>%s</strong>" % TITRE, page.text)
        self.lien.invalidate_recordset()
        self.assertNotEqual(self.lien.state, "canceled")


@tagged("post_install", "-at_install", "bf_appointment", "bf_appointment_titre")
class TestTitrePublicFormulaire(HttpCase):
    """Le vrai chemin : un POST du formulaire public avec l'adresse d'une fiche au carnet.

    🔴 La fiche est retrouvée par l'adresse TAPÉE, sans preuve que la personne la
    possède, et le navigateur part aussitôt vers la page du créneau avec le jeton.
    Rien de la fiche ne doit s'y lire, ni sur les autres pages à jeton : ni sa
    société (« Société au carnet »), ni son nom (« Nom au carnet »). L'agenda et
    les courriels, qui partent à l'adresse, gardent le titre complet du carnet.
    """

    def setUp(self):
        super().setUp()
        if not self.env["ir.module.module"].sudo().search_count(
                [("name", "=", "website"), ("state", "=", "installed")]):
            self.skipTest("module `website` absent : les routes `website=True` ne sont pas routées")
        # Le limiteur du formulaire compte par IP, et tous les essais viennent de 127.0.0.1.
        ctrl._bucket_data.clear()
        self.addCleanup(ctrl._bucket_data.clear)
        self.type_rdv = _type_rdv(self.env, "Rencontre flexible formulaire", "titre-public-formulaire")
        societe = self.env["res.partner"].create({"name": "Société au carnet", "is_company": True})
        self.fiche = self.env["res.partner"].create({
            "name": "Nom au carnet", "email": "fiche-carnet@test.invalid",
            "parent_id": societe.id, "lang": "fr_CA"})
        self.env.cr.flush()

    def _reserver(self, **champs):
        page = self.url_open("/appointment/%s" % self.type_rdv.slug)
        self.assertEqual(page.status_code, 200)
        jeton = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page.text)
        self.assertTrue(jeton, "le formulaire doit porter son jeton CSRF")
        Modele = type(self.env["resource.booking"])
        avant = self.env["resource.booking"].search([("type_id", "=", self.type_rdv.id)])
        with patch.object(Modele, "_send_appointment_email", lambda *a, **k: None):
            reponse = self.url_open(
                "/appointment/%s/book" % self.type_rdv.slug,
                data=dict({
                    "csrf_token": jeton.group(1),
                    "email": "fiche-carnet@test.invalid",
                    "bf_consent": "on",
                }, **champs),
            )
        booking = self.env["resource.booking"].search(
            [("type_id", "=", self.type_rdv.id)]) - avant
        self.assertEqual(len(booking), 1, "le formulaire crée une réservation")
        self.assertEqual(booking.partner_id, self.fiche, "la fiche est bien retrouvée par l'adresse")
        self.assertIn("/appointment/b/%d/" % booking.id, reponse.url)
        self.assertIn("/schedule", reponse.url)
        return reponse, booking

    def _pages_sans_la_fiche(self, booking, creneau, tape):
        """Les quatre pages à jeton : ce qui a été tapé, rien de la fiche, pas d'index."""
        self.env["resource.booking.guest"].create({
            "booking_id": booking.id, "name": "Invitée Essai",
            "email": "invitee-formulaire@test.invalid"})
        self.env.cr.flush()
        base = "/appointment/b/%d/%s" % (booking.id, booking.access_token)
        pages = {
            "créneau": creneau,
            "confirmation": self.url_open(base),
            "annulation": self.url_open(base + "/cancel"),
            # La page des invités s'ouvre par la clé du courriel du demandeur.
            "invités": self.url_open(base + "/guests/confirm?key=" + booking.bf_guest_key()),
        }
        for nom, page in pages.items():
            with self.subTest(page=nom):
                self.assertEqual(page.status_code, 200)
                # « au carnet » : ni « Société au carnet » ni « Nom au carnet ».
                self.assertNotIn("au carnet", page.text)
                self.assertIn(tape, page.text)
                self.assertEqual(page.headers.get("X-Robots-Tag"), "noindex, nofollow")
        for nom in ("créneau", "confirmation"):
            with self.subTest(titre=nom):
                titre = re.search(r"<title>(.*?)</title>", pages[nom].text, re.S).group(1).strip()
                self.assertTrue(titre.startswith(booking.bf_public_title), titre)
                self.assertIn(tape, titre)
        # L'OCA n'a aucune traduction de « Bookings ». Le site sert
        # d'abord sa langue par défaut ; la page en français est demandée par le témoin
        # de langue du site, comme le sélecteur de langue le pose.
        self.assertRegex(pages["confirmation"].text, r'<a href="[^"]*/my/bookings[^"]*">Bookings</a>'
                         if re.search(r'<html lang="en', pages["confirmation"].text) else
                         r'<a href="[^"]*/my/bookings[^"]*">Rendez-vous</a>')
        self.opener.cookies.set("frontend_lang", "fr_CA")
        for nom, url in (("créneau", base + "/schedule"), ("confirmation", base)):
            with self.subTest(fil_d_ariane=nom):
                page = self.url_open(url)
                self.assertEqual(page.status_code, 200)
                self.assertRegex(page.text, r'<html lang="fr')
                self.assertNotIn(">Bookings<", page.text)
                self.assertRegex(page.text, r'<a href="[^"]*/my/bookings[^"]*">Rendez-vous</a>')
                self.assertNotIn("au carnet", page.text)

    # -- le portail de l'OCA, ouvert par le même jeton -------------------------

    def _portail(self, booking, suite="", **params):
        """La réponse de la route du portail, après la redirection de langue du site."""
        params["access_token"] = booking.access_token
        url = "/my/bookings/%d%s?%s" % (booking.id, suite, urlencode(params))
        for _saut in range(3):
            page = self.url_open(url, allow_redirects=False)
            lieu = page.headers.get("Location", "")
            if page.status_code not in (301, 302, 303) or "/my/bookings/" not in urlsplit(lieu).path:
                return page
            url = lieu
        return page

    def test_le_portail_de_l_oca_par_jeton_renvoie_aux_pages_du_module(self):
        """Avec un jeton, chaque route du portail renvoie à la page du module, sans agir.

        🔴 Sur 18.0.2.64.2, la fiche du portail montre le titre complet et les
        participants du carnet, et `/confirm` et `/cancel` agissent sur un GET.
        """
        reponse, booking = self._reserver(name="Nom tapé Essai")
        debut = fields.Datetime.context_timestamp(
            self.type_rdv, fields.Datetime.now()) + datetime.timedelta(hours=1)
        quand = self.type_rdv._bf_candidate_slots(
            debut, debut + datetime.timedelta(days=10), limit=1)[0]
        etat, depart = booking.state, booking.start
        base = "/appointment/b/%d/%s" % (booking.id, booking.access_token)
        for suite, params, arrivee in (
            ("", {}, base),
            ("/schedule", {}, base + "/schedule"),
            ("/schedule/2030/1", {}, base + "/schedule/2030/1"),
            ("/confirm", {"when": quand.isoformat()}, base),
            ("/cancel", {}, base + "/cancel"),
        ):
            with self.subTest(route=suite or "fiche"):
                page = self._portail(booking, suite, **params)
                self.assertIn(page.status_code, (301, 302, 303), page.text[:200])
                self.assertEqual(urlsplit(page.headers["Location"]).path, arrivee)
        booking.invalidate_recordset()
        self.assertEqual(booking.state, etat, "le portail de l'OCA ne confirme ni n'annule plus")
        self.assertEqual(booking.start, depart)
        # Et la page d'arrivée ne montre rien du carnet.
        page = self.url_open(self._portail(booking).headers["Location"])
        self.assertEqual(page.status_code, 200)
        self.assertNotIn("au carnet", page.text)

    def test_le_proprietaire_connecte_garde_le_portail_de_l_oca(self):
        """Ses propres droits lui ouvrent la réservation : le jeton des liens ne le renvoie pas.

        `get_portal_url()` ajoute le jeton à tous les liens du portail (liste, boutons,
        formulaire de confirmation) : les renvoyer briserait son parcours.
        """
        reponse, booking = self._reserver(name="Nom tapé Essai")
        usager = new_test_user(self.env, login="portail-proprio-26682", groups="base.group_portal")
        booking.message_subscribe(partner_ids=usager.partner_id.ids)
        self.env.cr.flush()
        self.authenticate("portail-proprio-26682", "portail-proprio-26682")
        for suite in ("", "/schedule"):
            with self.subTest(route=suite or "fiche"):
                page = self._portail(booking, suite)
                self.assertEqual(page.status_code, 200, page.headers.get("Location"))

    def test_l_invitation_d_un_invite_porte_le_titre_public(self):
        """🔴 L'invitation part à une adresse que le demandeur a tapée.

        La confirmation des invités ne demande que le jeton, que le formulaire remet
        aussitôt au navigateur : le titre bâti sur la fiche du carnet n'y a pas sa place.
        """
        reponse, booking = self._reserver(name="Nom tapé Essai")
        invite = self.env["resource.booking.guest"].create({
            "booking_id": booking.id, "name": "Invitée Essai",
            "email": "invitee-titre-public@test.invalid"})
        gabarit = self.env.ref("bf_appointment.mail_template_guest_invitation")
        for champ in ("subject", "body_html"):
            with self.subTest(champ=champ):
                rendu = str(gabarit._render_field(champ, invite.ids)[invite.id])
                self.assertIn("Nom tapé Essai", rendu)
                self.assertNotIn("Société au carnet", rendu)

    def test_la_source_de_l_invitation_porte_le_titre_public(self):
        """Le gabarit est `noupdate` : l'essai rendu lit la base, celui-ci la source.

        Une installation neuve charge la source telle quelle.
        """
        xml = (RACINE / "data" / "appointment_mail_templates.xml").read_text(encoding="utf-8")
        bloc = re.search(
            r'<record id="mail_template_guest_invitation".*?</record>', xml, re.S).group(0)
        self.assertEqual(bloc.count("object.booking_id.bf_page_title()"), 2)
        self.assertNotIn("bf_title()", bloc)

    def test_le_titre_public_est_dans_la_langue_de_la_page(self):
        """La langue de la fiche ne se lit pas sur la page."""
        if not self.env["res.lang"].search_count([("code", "=", "en_CA"), ("active", "=", True)]):
            self.skipTest("en_CA absent")
        self.fiche.lang = "en_CA"
        self.type_rdv.update_field_translations("name", {
            "fr_CA": "Rencontre flexible formulaire", "en_CA": "Flexible form meeting EN"})
        self.env.cr.flush()
        self.opener.cookies.set("frontend_lang", "fr_CA")
        reponse, booking = self._reserver(name="Nom tapé Essai")
        self.assertTrue(
            booking.bf_public_title.startswith("Rencontre flexible formulaire - Nom tapé Essai"),
            booking.bf_public_title)
        # L'agenda garde la langue de la fiche, comme avant.
        self.assertTrue(booking.name.startswith("Flexible form meeting EN"), booking.name)

    def test_sans_jeton_le_portail_de_l_oca_ne_change_pas(self):
        """Une personne connectée, sans jeton : le portail de l'OCA, selon ses droits."""
        reponse, booking = self._reserver(name="Nom tapé Essai")
        new_test_user(self.env, login="portail-essai-26682",
                      groups="base.group_user,resource_booking.group_manager")
        self.authenticate("portail-essai-26682", "portail-essai-26682")
        page = self.url_open("/my/bookings/%d" % booking.id, allow_redirects=False)
        self.assertEqual(page.status_code, 200)
        self.assertNotIn("/appointment/b/", page.headers.get("Location", ""))

    def test_le_nom_tape_et_jamais_la_fiche_retrouvee(self):
        """Le type ne recueille pas la société : une société postée quand même est ignorée."""
        self.assertFalse(self.type_rdv.collect_company)
        reponse, booking = self._reserver(name="Nom tapé Essai", company="Société ignorée")
        self.assertTrue(
            booking.bf_public_title.startswith("Rencontre flexible formulaire - Nom tapé Essai"),
            booking.bf_public_title)
        self.assertNotIn("Société ignorée", booking.bf_public_title)
        # L'agenda et les courriels gardent le titre complet, bâti sur le carnet.
        self.assertIn("Société au carnet", booking.name)
        self.assertIn("Société au carnet", booking.bf_title())
        self._pages_sans_la_fiche(booking, reponse, "Nom tapé Essai")

    def test_la_societe_tapee_quand_le_type_la_recueille(self):
        self.type_rdv.collect_company = True
        self.env.cr.flush()
        reponse, booking = self._reserver(name="Nom tapé Essai", company="Société tapée Essai")
        self.assertTrue(
            booking.bf_public_title.startswith("Rencontre flexible formulaire - Société tapée Essai"),
            booking.bf_public_title)
        self.assertNotIn("Nom tapé Essai", booking.bf_public_title)
        self.assertIn("Société au carnet", booking.name)
        self.assertIn("Société au carnet", booking.bf_title())
        self._pages_sans_la_fiche(booking, reponse, "Société tapée Essai")

    def test_les_pages_d_accueil_et_de_type_restent_indexables(self):
        for url in ("/appointment", "/appointment/%s" % self.type_rdv.slug):
            with self.subTest(url=url):
                page = self.url_open(url)
                self.assertEqual(page.status_code, 200)
                # Elles passent bien par `_apply_security_headers`...
                self.assertIn("Content-Security-Policy", page.headers)
                # ... qui ne les marque pas.
                self.assertNotIn("X-Robots-Tag", page.headers)


@tagged("post_install", "-at_install", "bf_appointment", "bf_appointment_titre")
class TestRenvois26641(TransactionCase):
    """Trois défauts de marque et de lisibilité, corrigés en 2.65.0."""

    def test_une_societe_neuve_n_a_aucune_marque_blue_fox(self):
        """Un client qui installe le module ne recueille pas de consentements au nom de Blue Fox."""
        societe = self.env["res.company"].create({"name": "Société neuve Essai"})
        for champ in ("name", "logo_url", "website_url", "privacy_url", "terms_url", "tagline"):
            with self.subTest(champ=champ):
                self.assertFalse(societe["appointment_brand_%s" % champ])

    def test_aucune_marque_blue_fox_dans_le_code(self):
        """Ni nom ni adresse de Blue Fox en repli dans le Python du module."""
        motif = re.compile(r"""["'](?:Blue Fox|https?://(?:www\.)?bluefoxconsultant\.com[^"']*)["']""")
        for fichier in sorted((RACINE / "models").glob("*.py")) + sorted(
                (RACINE / "controllers").glob("*.py")):
            with self.subTest(fichier=fichier.name):
                self.assertFalse(motif.findall(fichier.read_text(encoding="utf-8")))

    def test_la_description_du_type_prend_la_couleur_du_site(self):
        """Un blanc forcé disparaissait sur un site clair (fond blanc)."""
        source = (RACINE / "static/src/scss/appointment.scss").read_text(encoding="utf-8")
        bloc = re.search(r"^\.bf-description \{(.*?)^\}", source, re.S | re.M).group(1)
        self.assertNotRegex(bloc, r"255,\s*255,\s*255|#fff")
        self.assertIn("color: inherit", bloc)

    def test_le_fil_d_ariane_dit_rendez_vous(self):
        """La branche sans réservation (portail de l'OCA) ; l'autre est lue au vrai chemin."""
        for langue, attendu, absent in (("fr_CA", "Rendez-vous", "Bookings"),
                                        ("en_US", "Bookings", "Rendez-vous")):
            with self.subTest(langue=langue):
                fil = str(self.env["ir.qweb"].with_context(lang=langue)._render(
                    "portal.portal_breadcrumbs", {"page_name": "booking", "booking_sudo": False}))
                self.assertIn(attendu, fil)
                self.assertNotIn(absent, fil)


# ---------------------------------------------------------------------------
# Confirmation des invités par la clé, fil du portail fermé au jeton (2.65.0)
# ---------------------------------------------------------------------------

INVITEE = "invitee-cle@test.invalid"
NEUTRE = "bf-guests-link-invalid"


def _csrf(texte):
    trouve = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', texte)
    return trouve and trouve.group(1)


@tagged("post_install", "-at_install", "bf_appointment", "bf_appointment_titre")
class TestInvitesParLaCle(HttpCase):
    """🔴 Les invités ne se confirment que par la clé du courriel du demandeur.

    Le vrai chemin : le formulaire public, POSTé avec l'adresse d'une fiche du carnet
    (celle d'un tiers) et une invitée ; puis le créneau, POSTé sur la page où le
    navigateur a été envoyé avec le jeton. Sur 18.0.2.64.2, le jeton suffisait à
    faire partir l'invitation, au GET de la page puis au POST de son bouton.
    """

    _reserver = TestTitrePublicFormulaire._reserver

    def setUp(self):
        super().setUp()
        if not self.env["ir.module.module"].sudo().search_count(
                [("name", "=", "website"), ("state", "=", "installed")]):
            self.skipTest("module `website` absent : les routes `website=True` ne sont pas routées")
        ctrl._bucket_data.clear()
        self.addCleanup(ctrl._bucket_data.clear)
        self.type_rdv = _type_rdv(self.env, "Rencontre flexible invités", "invites-par-la-cle")
        self.type_rdv.write({"allow_guests": True, "max_guests": 3})
        societe = self.env["res.partner"].create({"name": "Société au carnet", "is_company": True})
        self.fiche = self.env["res.partner"].create({
            "name": "Nom au carnet", "email": "fiche-carnet@test.invalid",
            "parent_id": societe.id, "lang": "fr_CA"})
        self.env.cr.flush()

    # -- le chemin du visiteur ------------------------------------------------

    def _reserver_avec_invitee(self):
        """Formulaire public puis créneau : rend (réservation, invitée, page du créneau)."""
        reponse, booking = self._reserver(name="Nom tapé Essai", bf_guests=INVITEE)
        invitee = booking.guest_ids
        self.assertEqual(invitee.mapped("email"), [INVITEE])
        self.assertEqual(invitee.state, "pending")
        debut = fields.Datetime.context_timestamp(
            self.type_rdv, fields.Datetime.now()) + datetime.timedelta(hours=1)
        quand = self.type_rdv._bf_candidate_slots(
            debut, debut + datetime.timedelta(days=10), limit=1)[0]
        Modele = type(self.env["resource.booking"])
        with patch.object(Modele, "_send_appointment_email", lambda *a, **k: None):
            self.url_open(
                "/appointment/b/%d/%s/confirm" % (booking.id, booking.access_token),
                data={"csrf_token": _csrf(reponse.text), "when": quand.isoformat()},
            )
        booking.invalidate_recordset()
        self.assertTrue(booking.start, "le créneau est pris")
        return booking, invitee, reponse

    def _base(self, booking):
        return "/appointment/b/%d/%s/guests" % (booking.id, booking.access_token)

    def _poster(self, url, page_csrf, **champs):
        return self.url_open(url, data=dict({"csrf_token": page_csrf}, **champs))

    def _invitations(self, invitee):
        return self.env["mail.mail"].sudo().search([
            ("model", "=", "resource.booking.guest"), ("res_id", "in", invitee.ids)])

    def _demande(self, booking):
        demande = self.env["mail.mail"].sudo().search([
            ("model", "=", "resource.booking"), ("res_id", "=", booking.id),
            ("body_html", "ilike", "/guests/confirm")])
        self.assertEqual(len(demande), 1, "une demande de confirmation au demandeur")
        return demande

    def _sans_rien(self, page, booking):
        """La page neutre : rien de la réservation, du carnet ni des invités."""
        self.assertEqual(page.status_code, 200)
        self.assertIn(NEUTRE, page.text)
        for morceau in ("au carnet", "Nom tapé Essai", INVITEE, booking.bf_public_title,
                        "Rencontre flexible invités"):
            self.assertNotIn(morceau, page.text)
        self.assertEqual(page.headers.get("X-Robots-Tag"), "noindex, nofollow")

    def test_la_cle_ne_part_que_dans_le_courriel_du_demandeur(self):
        booking, invitee, page_creneau = self._reserver_avec_invitee()
        demande = self._demande(booking)
        cle = booking.bf_guest_key()
        self.assertIn("fiche-carnet@test.invalid", demande.email_to or "", "adressée au demandeur")
        self.assertEqual(html.unescape(demande.body_html).count("?key=" + cle), 2)
        # Ni la page du créneau, ni la confirmation, ni l'annulation ne la livrent.
        base = "/appointment/b/%d/%s" % (booking.id, booking.access_token)
        for url in (base, base + "/schedule", base + "/cancel"):
            with self.subTest(url=url):
                self.assertNotIn(cle, self.url_open(url).text)
        self.assertNotIn(cle, page_creneau.text)
        # Aucun autre courriel ne la porte, l'invitation comprise.
        autres = self.env["mail.mail"].sudo().search([("body_html", "ilike", cle)]) - demande
        self.assertFalse(autres)

    def test_le_jeton_seul_ne_fait_partir_aucune_invitation(self):
        """🔴 L'attaque : l'adresse d'un tiers au formulaire, puis le jeton seul."""
        booking, invitee, page_creneau = self._reserver_avec_invitee()
        jeton_csrf = _csrf(page_creneau.text)
        base = self._base(booking)
        for action in ("confirm", "decline"):
            with self.subTest(get=action):
                self._sans_rien(self.url_open("%s/%s" % (base, action)), booking)
            with self.subTest(post=action):
                self._sans_rien(self._poster("%s/%s" % (base, action), jeton_csrf), booking)
        for faux in ("0" * 64, booking.bf_guest_key()[:-1] + "x", "é" * 64, ""):
            with self.subTest(cle_fausse=faux[:8]):
                self._sans_rien(self.url_open("%s/confirm?%s" % (base, urlencode({"key": faux}))),
                                booking)
                self._sans_rien(self._poster(base + "/confirm", jeton_csrf, key=faux), booking)
        invitee.invalidate_recordset()
        self.assertEqual(invitee.state, "pending", "ni confirmée ni écartée")
        self.assertFalse(invitee.partner_id, "aucun contact créé")
        self.assertFalse(self._invitations(invitee), "aucune invitation n'est partie")

    def test_la_cle_d_une_autre_reservation_n_ouvre_rien(self):
        booking, invitee, page_creneau = self._reserver_avec_invitee()
        ctrl._bucket_data.clear()
        _reponse, autre = self._reserver(name="Autre Essai")
        self.assertNotEqual(autre.bf_guest_key(), booking.bf_guest_key())
        self._sans_rien(self._poster(self._base(booking) + "/confirm", _csrf(page_creneau.text),
                                     key=autre.bf_guest_key()), booking)
        invitee.invalidate_recordset()
        self.assertEqual(invitee.state, "pending")
        self.assertFalse(self._invitations(invitee))

    def _page_du_courriel(self, booking, action):
        """La page qu'ouvre le lien `action` du courriel adressé au demandeur."""
        lien = re.search(r'href="([^"]*/guests/%s\?[^"]*)"' % action, self._demande(booking).body_html)
        self.assertTrue(lien, "le courriel porte le lien « %s »" % action)
        url = urlsplit(html.unescape(lien.group(1)))
        page = self.url_open("%s?%s" % (url.path, url.query))
        self.assertEqual(page.status_code, 200)
        self.assertNotIn(NEUTRE, page.text)
        self.assertIn(INVITEE, page.text)
        self.assertNotIn("au carnet", page.text)
        return page

    def _soumettre(self, page, action):
        """Le bouton `action` de la page, tel qu'il est : son action et ses champs cachés."""
        formulaire = re.search(
            r'<form method="post"[^>]*\saction="([^"]*/guests/%s)">(.*?)</form>' % action,
            page.text, re.S)
        self.assertTrue(formulaire, "la page porte le bouton « %s »" % action)
        champs = dict(re.findall(r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', formulaire.group(2)))
        return champs, self.url_open(urlsplit(formulaire.group(1)).path, data=champs)

    def test_le_lien_du_courriel_confirme_les_invites(self):
        """Le demandeur, depuis sa boîte : le lien du courriel, puis le bouton de la page."""
        booking, invitee, _page = self._reserver_avec_invitee()
        page = self._page_du_courriel(booking, "confirm")
        invitee.invalidate_recordset()
        self.assertEqual(invitee.state, "pending", "le GET ne décide rien")
        champs, fait = self._soumettre(page, "confirm")
        self.assertEqual(champs.get("key"), booking.bf_guest_key(), "le POST porte la clé")
        self.assertEqual(fait.status_code, 200)
        self.assertNotIn(NEUTRE, fait.text)
        invitee.invalidate_recordset()
        self.assertEqual(invitee.state, "confirmed")
        self.assertEqual(len(self._invitations(invitee)), 1, "l'invitation est partie")

    def test_le_lien_du_courriel_ecarte_les_invites(self):
        booking, invitee, _page = self._reserver_avec_invitee()
        champs, fait = self._soumettre(self._page_du_courriel(booking, "decline"), "decline")
        self.assertEqual(champs.get("key"), booking.bf_guest_key(), "le POST porte la clé")
        self.assertNotIn(NEUTRE, fait.text)
        invitee.invalidate_recordset()
        self.assertEqual(invitee.state, "declined")
        self.assertFalse(self._invitations(invitee))

    # -- les demandes parties avant la pose -----------------------------------

    def _migrer(self):
        self.env.flush_all()
        _migration().migrate(self.env.cr, "18.0.2.64.2")
        self.env.invalidate_all()

    def test_une_demande_d_avant_la_pose_reste_confirmable(self):
        """Son lien n'a que le jeton : il reste valable pour les invités qu'elle visait."""
        booking, invitee, page_creneau = self._reserver_avec_invitee()
        self._migrer()
        self.assertTrue(invitee.bf_confirm_by_token)
        # Un invité ajouté APRÈS la pose n'est jamais confirmable par le jeton seul.
        tardive = self.env["resource.booking.guest"].create({
            "booking_id": booking.id, "email": "tardive@test.invalid"})
        self.env.cr.flush()
        page = self.url_open(self._base(booking) + "/confirm")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn(NEUTRE, page.text)
        self.assertIn(INVITEE, page.text)
        self.assertNotIn("tardive@test.invalid", page.text)
        fait = self._poster(self._base(booking) + "/confirm", _csrf(page.text))
        self.assertNotIn("tardive@test.invalid", fait.text, "seuls ceux que la demande visait")
        invitee.invalidate_recordset()
        tardive.invalidate_recordset()
        self.assertEqual(invitee.state, "confirmed")
        self.assertEqual(tardive.state, "pending")
        self.assertFalse(self._invitations(tardive))

    def test_une_demande_d_avant_la_pose_expire_avec_la_rencontre(self):
        booking, invitee, page_creneau = self._reserver_avec_invitee()
        self._migrer()
        self.assertTrue(invitee.bf_confirm_by_token)
        passe = fields.Datetime.now() - datetime.timedelta(days=2)
        self.env.cr.execute(
            "UPDATE resource_booking SET start = %s, stop = %s WHERE id = %s",
            (passe, passe + datetime.timedelta(hours=1), booking.id))
        self.env.invalidate_all()
        self._sans_rien(self.url_open(self._base(booking) + "/confirm"), booking)
        self._sans_rien(self._poster(self._base(booking) + "/confirm", _csrf(page_creneau.text)),
                        booking)
        invitee.invalidate_recordset()
        self.assertEqual(invitee.state, "pending")
        self.assertFalse(self._invitations(invitee))


@tagged("post_install", "-at_install", "bf_appointment", "bf_appointment_titre")
class TestMigrationInvites(TransactionCase):
    """La migration 18.0.2.65.0 : les liens de la demande en base, les demandes d'avant."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, tz="UTC"))
        cls.m = _migration()
        cls.type_rdv = _type_rdv(cls.env, "Rencontre flexible migration invités", "migration-invites")
        cls.personne = cls.env["res.partner"].create({
            "name": "Personne Migration", "email": "personne-migration@test.invalid"})
        xml = (RACINE / "data" / "appointment_mail_templates.xml").read_text(encoding="utf-8")
        cls.source = re.search(
            r'<record id="mail_template_guest_confirmation_request".*?<!\[CDATA\[(.*?)\]\]>',
            xml, re.S).group(1)
        cls.xml = xml

    def _avant(self):
        """Le corps tel que 2.64.2 le laissait : les liens n'ont que le jeton."""
        return self.source.replace("?key={{ object.bf_guest_key() }}", "")

    def test_la_source_porte_la_cle_dans_les_deux_liens(self):
        self.assertEqual(self.source.count("?key={{ object.bf_guest_key() }}\""), 2)
        self.assertNotIn('{{ object.access_token }}/guests/confirm"', self.source)
        self.assertNotIn('{{ object.access_token }}/guests/decline"', self.source)
        # La clé ne part que dans la demande adressée au demandeur.
        self.assertEqual(self.xml.count("bf_guest_key"), 2)

    def test_la_migration_pose_la_cle_dans_les_deux_liens(self):
        neuf, n = self.m.remplacer_liens_invites(self._avant())
        self.assertEqual(n, 2)
        self.assertEqual(neuf, self.source)

    def test_la_migration_est_idempotente_sur_les_liens(self):
        self.assertEqual(self.m.remplacer_liens_invites(self.source), (self.source, 0))

    def test_la_migration_laisse_un_gabarit_retouche(self):
        avant = self._avant()
        retouches = (
            avant.replace('{{ object.access_token }}/guests/decline"', "/appointment"),
            avant + '<a href="{{ object.access_token }}/guests/confirm">bis</a>',
            "<p>Corps refait à la main</p>",
        )
        for corps in retouches:
            with self.subTest(corps=corps[-40:]):
                self.assertEqual(self.m.remplacer_liens_invites(corps), (corps, 0))

    def _gabarit(self, corps):
        gabarit = self.env.ref("bf_appointment.mail_template_guest_confirmation_request")
        self.env.cr.execute("UPDATE mail_template SET body_html = %s::jsonb WHERE id = %s",
                            (json.dumps(corps), gabarit.id))
        return gabarit

    def _migrer(self, version="18.0.2.64.2"):
        self.env.flush_all()
        self.m.migrate(self.env.cr, version)
        self.env.invalidate_all()

    def test_la_migration_reecrit_chaque_langue_et_signale_la_retouchee(self):
        gabarit = self._gabarit({"en_US": self._avant(), "fr_CA": "<p>Retouché</p>"})
        with self.assertLogs(level=logging.WARNING) as journal:
            self._migrer()
        self.env.cr.execute("SELECT body_html FROM mail_template WHERE id = %s", (gabarit.id,))
        corps = self.env.cr.fetchone()[0]
        self.assertEqual(corps["en_US"].count("?key={{ object.bf_guest_key() }}"), 2)
        self.assertEqual(corps["fr_CA"], "<p>Retouché</p>")
        self.assertTrue(any("fr_CA" in ligne and "sans clé" in ligne for ligne in journal.output),
                        journal.output)

    def _reservation(self, start=True):
        debut = fields.Datetime.context_timestamp(
            self.type_rdv, fields.Datetime.now()) + datetime.timedelta(hours=1)
        if not start:
            return self.env["resource.booking"].create({
                "type_id": self.type_rdv.id, "partner_ids": [Command.set(self.personne.ids)]})
        quand = self.type_rdv._bf_candidate_slots(
            debut, debut + datetime.timedelta(days=10), limit=1)[0]
        return self.type_rdv._bf_create_booking(
            quand.astimezone(pytz.utc).replace(tzinfo=None), partners=self.personne)

    def test_la_migration_marque_les_demandes_deja_parties(self):
        Guest = self.env["resource.booking.guest"]
        avec = self._reservation()
        sans = self._reservation(start=False)
        self.assertFalse(sans.start)
        partie = Guest.create({"booking_id": avec.id, "email": "partie@test.invalid"})
        confirmee = Guest.create({"booking_id": avec.id, "email": "confirmee@test.invalid"})
        confirmee._bf_confirm()
        pas_partie = Guest.create({"booking_id": sans.id, "email": "pas-partie@test.invalid"})
        self._migrer()
        self.assertTrue(partie.bf_confirm_by_token)
        self.assertFalse(confirmee.bf_confirm_by_token)
        self.assertFalse(pas_partie.bf_confirm_by_token, "sa demande partira avec la clé")
        # Un invité créé ensuite ne l'est jamais.
        tardive = Guest.create({"booking_id": avec.id, "email": "tardive-m@test.invalid"})
        self.assertFalse(tardive.bf_confirm_by_token)

    def test_la_migration_ne_marque_rien_a_l_installation(self):
        partie = self.env["resource.booking.guest"].create({
            "booking_id": self._reservation().id, "email": "installation@test.invalid"})
        self._migrer(version=None)
        self.assertFalse(partie.bf_confirm_by_token)

    def test_la_cle_n_est_pas_appelable_en_rpc(self):
        methode = type(self.env["resource.booking"]).bf_guest_key
        self.assertTrue(getattr(methode, "_api_private", False))

    def test_la_cle_est_propre_a_la_reservation_et_stable(self):
        a, b = self._reservation(), self._reservation(start=False)
        self.assertEqual(a.bf_guest_key(), a.bf_guest_key())
        self.assertNotEqual(a.bf_guest_key(), b.bf_guest_key())
        self.assertNotIn(a.access_token or "-", a.bf_guest_key())
        self.assertGreaterEqual(len(a.bf_guest_key()), 64)


@tagged("post_install", "-at_install", "bf_appointment", "bf_appointment_titre")
class TestFilParLeJeton(HttpCase):
    """🔴 Le fil de la réservation ne s'ouvre plus par le jeton (ni par `hash`/`pid`).

    Sur 18.0.2.64.2, avec le jeton que le formulaire public remet au navigateur,
    `/portal/chatter_init` rendait le nom et l'avatar de la fiche retrouvée par
    l'adresse tapée (« Nom au carnet »), `/mail/chatter_fetch` les messages de
    l'équipe, et `/mail/message/post` publiait sous le nom de cette fiche.
    """

    _reserver = TestTitrePublicFormulaire._reserver

    def setUp(self):
        super().setUp()
        if not self.env["ir.module.module"].sudo().search_count(
                [("name", "=", "website"), ("state", "=", "installed")]):
            self.skipTest("module `website` absent : les routes `website=True` ne sont pas routées")
        ctrl._bucket_data.clear()
        self.addCleanup(ctrl._bucket_data.clear)
        self.type_rdv = _type_rdv(self.env, "Rencontre flexible fil", "fil-par-le-jeton")
        societe = self.env["res.partner"].create({"name": "Société au carnet", "is_company": True})
        self.fiche = self.env["res.partner"].create({
            "name": "Nom au carnet", "email": "fiche-carnet@test.invalid",
            "parent_id": societe.id, "lang": "fr_CA"})
        self.env.cr.flush()
        _reponse, self.booking = self._reserver(name="Nom tapé Essai")
        self.message = self.booking.message_post(
            body="Message de l'équipe Essai", message_type="comment",
            subtype_xmlid="mail.mt_comment")
        self.env.cr.flush()

    def _rpc(self, route, **params):
        """Rend le texte de la réponse JSON-RPC (résultat ou erreur), sans lever."""
        reponse = self.url_open(route, data=json.dumps({
            "id": 0, "jsonrpc": "2.0", "method": "call", "params": params,
        }), headers={"Content-Type": "application/json"})
        return reponse.text

    def _acces(self):
        signe = self.booking._sign_token(self.fiche.id)
        return {
            "jeton": {"token": self.booking.access_token},
            "signature": {"hash": signe, "pid": self.fiche.id},
        }

    def _fil(self, **acces):
        return {"thread_model": "resource.booking", "thread_id": self.booking.id, **acces}

    def test_le_jeton_n_ouvre_pas_le_fil(self):
        for nom, acces in self._acces().items():
            with self.subTest(acces=nom):
                init = self._rpc("/portal/chatter_init", **self._fil(**acces))
                self.assertNotIn("au carnet", init)
                self.assertNotIn("portal_partner", init)
                self.assertNotRegex(init, r'"hasReadAccess":\s*true')
                lu = self._rpc("/mail/chatter_fetch", **self._fil(**acces))
                self.assertNotIn("Message de l'équipe Essai", lu)
                donnees = self._rpc("/mail/thread/data", request_list=["messages"],
                                    **self._fil(**acces))
                self.assertNotIn("au carnet", donnees)
                with mute_logger("odoo.http"):
                    self._rpc("/mail/message/post", post_data={
                        "body": "Publié par %s" % nom, "message_type": "comment",
                        "subtype_xmlid": "mail.mt_comment"}, **self._fil(**acces))
                publie = self.env["mail.message"].sudo().search([
                    ("model", "=", "resource.booking"), ("res_id", "=", self.booking.id),
                    ("body", "ilike", "Publié par %s" % nom)])
                self.assertFalse(publie, "rien ne se publie par le %s" % nom)

    def test_le_jeton_ne_rend_pas_l_avatar_des_messages(self):
        url = "/mail/avatar/mail.message/%d/author_avatar/50x50" % self.message.id
        repli = self.url_open(url).content
        signe = self.booking._sign_token(self.fiche.id)
        for nom, requete in (
            ("jeton", "?access_token=%s" % self.booking.access_token),
            ("signature", "?_hash=%s&pid=%d" % (signe, self.fiche.id)),
        ):
            with self.subTest(acces=nom):
                self.assertEqual(self.url_open(url + requete).content, repli,
                                 "l'image de repli, pas l'avatar de l'auteur")

    def test_une_personne_connectee_avec_des_droits_garde_le_fil(self):
        new_test_user(self.env, login="fil-equipe-26682",
                      groups="base.group_user,resource_booking.group_manager")
        self.authenticate("fil-equipe-26682", "fil-equipe-26682")
        # Avec le jeton aussi : ses propres droits lui ouvrent le fil.
        fil = self._fil(token=self.booking.access_token)
        self.assertRegex(self._rpc("/portal/chatter_init", **fil), r'"hasReadAccess":\s*true')
        self.assertIn("Message de l'équipe Essai", self._rpc("/mail/chatter_fetch", **fil))
        self._rpc("/mail/message/post", post_data={
            "body": "Publié par l'équipe", "message_type": "comment",
            "subtype_xmlid": "mail.mt_comment"}, **fil)
        self.assertTrue(self.env["mail.message"].sudo().search([
            ("model", "=", "resource.booking"), ("res_id", "=", self.booking.id),
            ("body", "ilike", "Publié par l'équipe")]))
