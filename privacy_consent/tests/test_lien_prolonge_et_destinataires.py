"""18.0.5.5.0 : le lien d'un courriel vaut, et il va aux bonnes personnes.

Décision du 2026-10-02 : chaque courriel qui porte le lien public le
prolonge de 90 jours, sans changer le jeton ; le lien neuf demandé depuis une page
échue, et le bouton « Envoyer lien portail », envoient un gabarit dont le texte suit
l'état du consentement, plus la demande.

Les rappels, l'avis d'expiration et les confirmations d'un mineur vont à ses
responsables, comme la demande, jamais à l'enfant.
"""
import importlib.util
import os
import re
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import Form, HttpCase, tagged

from .test_refusal_closes_link import RefusalFixture

RACINE = os.path.dirname(os.path.dirname(__file__))


def _migration(version):
    chemin = os.path.join(RACINE, "migrations", version, "post-migrate.py")
    spec = importlib.util.spec_from_file_location("pc_mig_" + version.replace(".", "_"), chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@tagged("post_install", "-at_install", "privacy_consent")
class TestLienProlongeEtDestinataires(HttpCase, RefusalFixture):

    def setUp(self):
        super().setUp()
        self._build_fixture()
        self.env["res.lang"]._activate_lang("fr_CA")
        self.partner.lang = "fr_CA"
        self.envois = []

    # --- outils ---------------------------------------------------------------

    def _consent(self, status="granted", **extra):
        vals = {"subject_partner_id": self.partner.id, "purpose_id": self.purpose.id,
                "notice_id": self.notice.id, "status": status}
        if status == "granted":
            vals["granted_at"] = fields.Datetime.now()
        vals.update(extra)
        return self.Consent.create(vals)

    def _echoir(self, consent):
        consent.sudo().access_token_expires_at = fields.Datetime.now() - timedelta(minutes=1)
        self.env.flush_all()

    def _capter(self):
        envois = self.envois

        def capter(mails, *a, **k):
            # Le module envoie puis efface son mail.mail : on le capte à l'envoi.
            envois.extend((m.email_to, m.subject or "", m.body_html or "") for m in mails)
            return True

        return patch.object(type(self.env["mail.mail"]), "send", autospec=True, side_effect=capter)

    def _echeance_dans_90_jours(self, consent):
        consent.invalidate_recordset()
        ecart = consent.access_token_expires_at - fields.Datetime.now()
        return timedelta(days=89) < ecart <= timedelta(days=90, minutes=1)

    def _mineur(self, adresse_enfant=True, adresses_responsables=(True, True)):
        responsables = self.env["res.partner"].create([
            {"name": f"Responsable {i}", "email": f"resp{i}@example.invalid" if a else False}
            for i, a in enumerate(adresses_responsables, 1)])
        enfant = self.env["res.partner"].create({
            "name": "Enfant Essai", "email": "enfant@example.invalid" if adresse_enfant else False,
            "is_minor_child": True, "legal_guardian_ids": [(6, 0, responsables.ids)]})
        return enfant, responsables

    def _csrf(self, html):
        trouve = re.search(r'name="csrf_token"\s+value="([^"]+)"', html) or re.search(
            r'value="([^"]+)"\s+name="csrf_token"', html)
        return trouve.group(1)

    # --- le lien prolongé ---------------------------------------------------

    def test_un_envoi_prolonge_le_lien_sans_changer_le_jeton(self):
        consent = self._consent()
        self._echoir(consent)
        jeton = consent.access_token
        with self._capter():
            consent._send_consent_link_email()
        self.assertTrue(self._echeance_dans_90_jours(consent))
        self.assertEqual(consent.access_token, jeton)
        self.assertEqual(len(self.envois), 1)
        self.assertIn(jeton, self.envois[0][2])

    def test_un_envoi_ne_raccourcit_pas_le_lien(self):
        consent = self._consent()
        loin = fields.Datetime.now() + timedelta(days=200)
        consent.sudo().access_token_expires_at = loin
        with self._capter():
            consent._send_consent_link_email()
        consent.invalidate_recordset()
        self.assertEqual(consent.access_token_expires_at, loin)

    def _composeur(self, consents, mode, template=None, body=None):
        ctx = {"default_model": "privacy.consent", "default_res_ids": consents.ids,
               "default_composition_mode": mode}
        if template:
            ctx["default_template_id"] = template.id
        vals = {"body": body} if body else {}
        return self.env["mail.compose.message"].with_context(**ctx).create(vals)

    def test_l_avis_d_expiration_ecrit_a_la_main_prolonge_le_lien(self):
        """L'avis ne part que par le composeur : depuis la fiche, et en lot depuis la liste."""
        avis = self.env.ref("privacy_consent.mail_template_consent_expiring")
        consent = self._consent()
        self._echoir(consent)
        jeton = consent.access_token
        composeur = self._composeur(consent, "comment", avis)
        self.assertIn(jeton, composeur.body, "le corps rendu porte le lien")
        composeur.action_send_mail()
        self.assertTrue(self._echeance_dans_90_jours(consent))
        self.assertEqual(consent.access_token, jeton)

        lot = self._consent() | self._consent()
        for c in lot:
            self._echoir(c)
        self._composeur(lot, "mass_mail", avis).action_send_mail()
        for c in lot:
            self.assertTrue(self._echeance_dans_90_jours(c))

    def test_un_message_sans_le_lien_ne_prolonge_rien(self):
        consent = self._consent()
        self._echoir(consent)
        self._composeur(consent, "comment", body="<p>Un mot sans lien.</p>").action_send_mail()
        consent.invalidate_recordset()
        self.assertTrue(consent._access_token_expired())

    # --- le gabarit du lien suit l'état -------------------------------------

    def test_le_lien_envoye_suit_l_etat(self):
        attendus = {
            "pending": "Répondre à cette demande",
            "granted": "Gérer mon consentement",
            "expired": "Renouveler mon consentement",
            "withdrawn": "Consulter mon consentement",
        }
        for status, bouton in attendus.items():
            with self.subTest(status=status):
                self.envois.clear()
                with self._capter():
                    self._consent(status)._send_consent_link_email()
                self.assertEqual(len(self.envois), 1)
                _a, sujet, corps = self.envois[0]
                self.assertTrue(sujet.startswith("Votre lien de consentement"), sujet)
                self.assertIn(bouton, corps)
                for autre in set(attendus.values()) - {bouton}:
                    self.assertNotIn(autre, corps)

    def test_le_lien_neuf_d_un_consentement_accorde_n_est_plus_une_demande(self):
        consent = self._consent("granted")
        self._echoir(consent)
        page = self.url_open(f"/privacy/consent/{consent.id}/{consent.access_token}").text
        self.assertIn("o_privacy_link_expired", page)
        with self._capter():
            self.url_open(f"/privacy/consent/{consent.id}/{consent.access_token}/new-link",
                          data={"csrf_token": self._csrf(page)})
        self.assertEqual(len(self.envois), 1)
        _a, sujet, corps = self.envois[0]
        self.assertNotIn("Demande de consentement", sujet)
        self.assertNotIn("Répondre à cette demande", corps)
        self.assertIn("Gérer mon consentement", corps)
        consent.invalidate_recordset()
        self.assertIn(consent.access_token, corps)

    def test_le_bouton_envoyer_lien_portail_suit_l_etat(self):
        consent = self._consent("granted")
        with self._capter():
            consent.action_send_portal_link()
        self.assertEqual(len(self.envois), 1)
        self.assertIn("Gérer mon consentement", self.envois[0][2])
        self.assertNotIn("Demande de consentement", self.envois[0][1])

    def test_le_lien_ne_parait_pas_au_chatter(self):
        consent = self._consent("granted")
        with self._capter():
            consent._send_consent_link_email()
        corps = "".join(consent.message_ids.mapped("body"))
        self.assertNotIn(consent.access_token, corps)
        self.assertIn('href="mailto:%s"' % self.partner.email, corps, "note en HTML, pas échappée")

    def test_un_echec_d_envoi_s_ecrit_comme_un_echec(self):
        """La note du chatter disait « envoyé » même quand le relais refusait le message."""
        consent = self._consent("pending", requested_at=fields.Datetime.now() - timedelta(days=5))
        self.env["privacy.email.sequence"].create({
            "name": "Rappel d'essai", "purpose_id": self.purpose.id, "sequence": 1,
            "days_after_previous": 1,
            "mail_template_id": self.env.ref("privacy_consent.mail_template_consent_reminder_1").id})

        def refuser(mails, *a, **k):
            mails.write({"state": "exception", "failure_reason": "relais injoignable (essai)"})
            return True

        with patch.object(type(self.env["mail.mail"]), "send", autospec=True, side_effect=refuser):
            self.Consent.cron_process_email_sequences()
        corps = "".join(consent.message_ids.mapped("body"))
        self.assertIn("NON envoyé", corps)
        self.assertIn("relais injoignable (essai)", corps)
        self.assertNotIn("Courriel de rappel n° 1 envoyé", corps)
        consent.invalidate_recordset()
        self.assertEqual(consent.reminder_count, 0, "un rappel qui n'est pas parti sera retenté")

    def test_le_gabarit_du_lien_suit_la_mise_en_page_commune(self):
        gabarit = self.env.ref("privacy_consent.mail_template_consent_link")
        self.assertEqual(gabarit.email_layout_xmlid, "bf_onboarding_base.bf_mail_layout")
        consent = self._consent("granted")
        page = "/privacy/consent/%s/%s" % (consent.id, consent.access_token)
        for lang, bonjour, surtitre in (("en_US", "Hello", "Your link"), ("fr_CA", "Bonjour", "Votre lien")):
            with self.subTest(lang=lang):
                corps = gabarit.with_context(privacy_contact_lang=lang, lang=lang)._render_field(
                    "body_html", consent.ids, compute_lang=True)[consent.id]
                self.assertIn(bonjour, corps)
                self.assertIn(">%s</p>" % surtitre, corps)
                self.assertEqual(corps.count(page), 1)
                self.assertNotIn("/my/privacy/", corps)

    # --- les responsables d'un mineur ---------------------------------------

    def test_les_rappels_d_un_mineur_vont_aux_responsables(self):
        enfant, responsables = self._mineur()
        consent = self._consent("pending", subject_partner_id=enfant.id,
                                requested_at=fields.Datetime.now() - timedelta(days=5))
        self.assertTrue(consent.is_minor)
        self.env["privacy.email.sequence"].create({
            "name": "Rappel d'essai", "purpose_id": self.purpose.id, "sequence": 1,
            "days_after_previous": 1,
            "mail_template_id": self.env.ref("privacy_consent.mail_template_consent_reminder_1").id})
        with self._capter():
            self.Consent.cron_process_email_sequences()
        self.assertEqual(sorted(e[0] for e in self.envois), sorted(responsables.mapped("email")))
        consent.invalidate_recordset()
        self.assertEqual(consent.reminder_count, 1)
        for _a, _s, corps in self.envois:
            self.assertIn("En tant que responsable de Enfant Essai", corps)
        self.assertNotIn(consent.access_token, "".join(consent.message_ids.mapped("body")))

    def test_un_rappel_sans_adresse_de_responsable_ne_part_pas_a_l_enfant(self):
        enfant, _responsables = self._mineur(adresses_responsables=(False,))
        consent = self._consent("pending", subject_partner_id=enfant.id,
                                requested_at=fields.Datetime.now() - timedelta(days=5))
        self.env["privacy.email.sequence"].create({
            "name": "Rappel d'essai", "purpose_id": self.purpose.id, "sequence": 1,
            "days_after_previous": 1,
            "mail_template_id": self.env.ref("privacy_consent.mail_template_consent_reminder_1").id})
        with self._capter():
            self.Consent.cron_process_email_sequences()
        self.assertEqual(self.envois, [])
        consent.invalidate_recordset()
        self.assertEqual(consent.reminder_count, 0, "rien n'est parti, rien n'est compté")

    def test_le_a_des_gabarits_designe_les_responsables(self):
        """L'avis d'expiration et la confirmation d'octroi partent à la main : le composeur
        propose le « À » du gabarit."""
        enfant, responsables = self._mineur()
        consent = self._consent("granted", subject_partner_id=enfant.id)
        for code in ("mail_template_consent_expiring", "mail_template_consent_granted_confirmation",
                     "mail_template_consent_link"):
            with self.subTest(gabarit=code):
                gabarit = self.env.ref("privacy_consent." + code)
                rendu = gabarit._render_field("partner_to", consent.ids)[consent.id]
                self.assertEqual(sorted(int(i) for i in rendu.split(",")), sorted(responsables.ids))
                # Par le formulaire, comme le navigateur : les destinataires y sont calculés
                # par onchange.
                composeur = Form(self.env["mail.compose.message"].with_context(
                    default_model="privacy.consent", default_res_ids=consent.ids,
                    default_composition_mode="comment", default_template_id=gabarit.id))
                self.assertEqual(sorted(p.id for p in composeur.partner_ids), sorted(responsables.ids))
        adulte = self._consent("granted")
        rendu = self.env.ref("privacy_consent.mail_template_consent_expiring")._render_field(
            "partner_to", adulte.ids)[adulte.id]
        self.assertEqual(rendu, str(self.partner.id))

    def test_la_confirmation_de_renouvellement_va_aux_responsables(self):
        enfant, responsables = self._mineur()
        consent = self._consent("expired", subject_partner_id=enfant.id)
        url = f"/privacy/consent/{consent.id}/{consent.access_token}/renew"
        csrf = self._csrf(self.url_open(url).text)
        with self._capter():
            self.url_open(url, data={"csrf_token": csrf, "notice_read": "1"})
        consent.invalidate_recordset()
        self.assertTrue(consent.renewed_to_id)
        self.assertEqual(sorted(e[0] for e in self.envois), sorted(responsables.mapped("email")))
        self.assertNotIn(enfant.email, [e[0] for e in self.envois])

    # --- migrations -------------------------------------------------------------

    def test_la_migration_repointe_le_a_des_six_gabarits(self):
        migration = _migration("18.0.5.5.0")
        gabarits = [self.env.ref("privacy_consent." + x) for x in migration.GABARITS]
        for gabarit in gabarits:
            self.env.cr.execute("UPDATE mail_template SET partner_to = %s WHERE id = %s",
                                [migration.ANCIEN, gabarit.id])
        maison = gabarits[0]
        self.env.cr.execute("UPDATE mail_template SET partner_to = %s WHERE id = %s",
                            ["{{ object.subject_partner_id.parent_id.id }}", maison.id])
        migration.migrate(self.env.cr, "18.0.5.4.0")
        migration.migrate(self.env.cr, "18.0.5.4.0")  # rejouée : rien de plus
        self.env.cr.execute("SELECT id, partner_to FROM mail_template WHERE id = ANY(%s)",
                            [[g.id for g in gabarits]])
        valeurs = dict(self.env.cr.fetchall())
        self.assertEqual(valeurs.pop(maison.id), "{{ object.subject_partner_id.parent_id.id }}")
        self.assertEqual(set(valeurs.values()), {migration.NOUVEAU})

    def test_le_filet_5_4_interpole_un_href_simple(self):
        """Un `href` simple vers les préférences n'interpolait pas `{{ object.id }}`."""
        migration = _migration("18.0.5.4.0")
        corps, _manques = migration.retoucher(
            "mail_template_consent_granted_confirmation",
            '<a href="https://exemple.invalid/my/privacy/preferences">Mes choix</a>')
        self.assertIn('t-attf-href="https://exemple.invalid' + migration.JETON + '"', corps)
        self.assertNotIn(' href=', corps)
