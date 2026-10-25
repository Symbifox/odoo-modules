# -*- coding: utf-8 -*-
"""Ce que ces essais protègent, dans l'ordre où ça ferait mal.

1. Aucun courriel de la séquence sans lien de désabonnement qui marche, et ce
   lien ne désabonne que sur un POST : un GET (passerelle, aperçu) n'écrit rien.
2. Une personne désabonnée ne reçoit plus RIEN, y compris ce que la tâche
   planifiée avait déjà en retard pour elle.
3. Aucun pixel ni lien suivi, et le rythme tient : un retard ne lâche pas les
   courriels en rafale.
4. Un courriel que le SMTP n'a pas pris n'avance pas la séquence.
"""

import email.generator
import io
import re
import time
from datetime import timedelta
from email.header import decode_header, make_header
from unittest.mock import patch

from odoo import fields
from odoo.addons.base.models.ir_mail_server import IrMailServer
from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tests.common import HttpCase
from odoo.tools import mute_logger

from ..controllers import main as ctl
from ..models import ir_mail_server as ims
from ..models.sequence import ATTENTE_MAX_HEURES, ESSAIS_MAX, _attente_heures


@tagged("post_install", "-at_install")
class TestSequenceAccueil(HttpCase):

    def setUp(self):
        super().setUp()
        ctl._bucket_data.clear()
        # 🔴 Le SMTP est coupé dans le `setUp` (voir test_signup.py) : un envoi qui
        # partirait pour de vrai ne se rappelle pas.
        self.envois = []
        self.smtp_en_panne = False
        self.tentatives = 0

        def faux_smtp(serveur, message, *a, **kw):
            self.tentatives += 1
            if self.smtp_en_panne:
                raise OSError("SMTP en panne (essai)")
            self.envois.append(message)
            return "essai"

        correctif = patch.object(IrMailServer, "send_email", faux_smtp)
        correctif.start()
        self.addCleanup(correctif.stop)
        domaine = self.env["mail.alias.domain"].search([], limit=1)
        if not domaine:
            domaine = self.env["mail.alias.domain"].create({
                "name": "example.com", "bounce_alias": "bounce",
                "catchall_alias": "catchall", "default_from": "notifications"})
        self.env.company.alias_domain_id = domaine
        self.liste = self.env["mailing.list"].create({"name": "Essai — séquence"})
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param(ctl.ICP_LIST, str(self.liste.id))
        icp.set_param(ctl.ICP_NOTIFY, "")
        icp.set_param(ctl.ICP_BASE_URL, "https://exemple.test")
        self.adresse = "essai.sequence@example.com"
        # Trois étapes par langue, une semaine entre chacune.
        for lang, sujets in (("fr", ("Un", "Deux", "Trois")), ("en", ("One", "Two", "Three"))):
            for rang, sujet in enumerate(sujets, start=1):
                self._etape(rang, 7 * (rang - 1), sujet, lang=lang)

    # ------------------------------------------------------------------ outils

    def _etape(self, rang, delai, sujet, lang="fr", corps=None):
        return self.env["bf.mailing.signup.step"].create({
            "list_id": self.liste.id, "lang": lang, "rank": rang, "delay_days": delai,
            "subject": sujet,
            "body_html": corps or (
                f'<html><body><p>Courrier {rang}</p>'
                f'<a href="{ctl.MARQUEUR_DESABONNEMENT}">Se désabonner</a></body></html>'),
        })

    def _inscription(self):
        contact = self.env["mailing.contact"].search(
            [("email_normalized", "=", self.adresse)], limit=1)
        return self.env["mailing.subscription"].search(
            [("contact_id", "=", contact.id), ("list_id", "=", self.liste.id)], limit=1)

    def _jeton_confirmation(self):
        from datetime import date
        return ctl._token(self.env, self.liste.id, self.adresse, date.today().toordinal())

    def _inscrire(self, lang="fr"):
        """Le chemin réel : formulaire, puis POST du bouton de confirmation."""
        self.url_open("/infolettre", data={"courriel": self.adresse, "lang": lang},
                      allow_redirects=False)
        self.envois.clear()             # la confirmation n'est pas la séquence
        return self.url_open("/infolettre/confirmer", data={
            "e": self.adresse, "j": self._jeton_confirmation(), "lang": lang},
            allow_redirects=False)

    def _sujets(self):
        return [str(make_header(decode_header(m["Subject"]))) for m in self.envois]

    def _html(self, message):
        return message.get_body(preferencelist=("html",)).get_content()

    def _url_desabo(self, lang="fr"):
        return ctl._url_desabonnement(self.env, self.liste.id, self.adresse, lang)

    def _vieillir(self, jours, sub=None):
        """Reculer la confirmation ET le dernier envoi de `jours` jours."""
        sub = sub or self._inscription()
        vals = {"bf_confirmed_at": sub.bf_confirmed_at - timedelta(days=jours)}
        if sub.bf_step_sent_at:
            vals["bf_step_sent_at"] = sub.bf_step_sent_at - timedelta(days=jours)
        sub.write(vals)

    def _reculer_tentative(self, heures):
        sub = self._inscription()
        sub.write({"bf_attempt_at": sub.bf_attempt_at - timedelta(hours=heures)})

    def _cron(self):
        self.env["bf.mailing.signup.step"]._cron_envoyer_sequence(auto_commit=False)

    # ------------------------------------------------------------------ étapes

    def test_une_etape_sans_lien_de_desabonnement_est_refusee(self):
        with self.assertRaises(ValidationError):
            self._etape(9, 70, "Sans lien", corps="<p>Rien pour se désabonner</p>")

    def test_une_etape_par_rang_et_par_langue(self):
        with mute_logger("odoo.sql_db"), self.assertRaises(Exception):
            with self.env.cr.savepoint():
                self._etape(1, 0, "Doublon")

    # ------------------------------------------------------------ premier courriel

    def test_la_confirmation_envoie_le_premier_courriel_tout_de_suite(self):
        r = self._inscrire()
        self.assertEqual(r.status_code, 303)
        sub = self._inscription()
        self.assertEqual((sub.bf_lang, sub.bf_step), ("fr", 1))
        self.assertTrue(sub.bf_confirmed_at)
        self.assertEqual(self._sujets(), ["Un"])

    def test_la_langue_du_formulaire_choisit_les_etapes(self):
        self._inscrire(lang="en")
        self.assertEqual(self._inscription().bf_lang, "en")
        self.assertEqual(self._sujets(), ["One"])

    def test_aucun_pixel_ni_lien_suivi_et_lien_de_desabonnement_dans_le_corps(self):
        self._inscrire()
        message = self.envois[0]
        corps = self._html(message)
        self.assertNotIn(ctl.MARQUEUR_DESABONNEMENT, corps)
        self.assertNotIn("data-odoo-tracking-email", corps)
        self.assertNotIn("utm_", corps)
        self.assertIsNone(message["X-Odoo-MailTracking-ID"])
        self.assertIsNone(message[ctl.ENTETE_SANS_SUIVI],
                          "l'en-tête interne ne doit pas sortir")
        url = self._url_desabo()
        self.assertIn(url.replace("&", "&amp;"), corps)

    def test_en_tetes_de_desabonnement_en_un_clic(self):
        self._inscrire()
        message = self.envois[0]
        self.assertEqual(message["List-Unsubscribe"], f"<{self._url_desabo()}>")
        self.assertEqual(message["List-Unsubscribe-Post"], "List-Unsubscribe=One-Click")

    def test_le_lien_de_confirmation_recharge_ne_relance_pas_la_sequence(self):
        self._inscrire()
        sub = self._inscription()
        date_initiale = sub.bf_confirmed_at
        self.envois.clear()
        self._vieillir(2)
        date_vieillie = self._inscription().bf_confirmed_at
        self.url_open("/infolettre/confirmer", data={
            "e": self.adresse, "j": self._jeton_confirmation(), "lang": "fr"},
            allow_redirects=False)
        sub = self._inscription()
        self.assertEqual(sub.bf_confirmed_at, date_vieillie)
        self.assertNotEqual(sub.bf_confirmed_at, date_initiale)
        self.assertEqual(sub.bf_step, 1)
        self.assertEqual(self.envois, [])

    def test_sans_etape_la_confirmation_reussit_et_rien_ne_part(self):
        self.env["bf.mailing.signup.step"].search([]).unlink()
        r = self._inscrire()
        self.assertEqual(r.headers["Location"], "/infolettre-confirme.html")
        sub = self._inscription()
        self.assertFalse(sub.opt_out)
        self.assertEqual(sub.bf_step, 0)
        self.assertEqual(self.envois, [])
        self._cron()
        self.assertEqual(self.envois, [])

    # --------------------------------------------------------------- le rythme

    def test_le_courriel_suivant_part_a_son_delai_pas_avant(self):
        self._inscrire()
        self.envois.clear()
        self._vieillir(6)
        self._cron()
        self.assertEqual(self.envois, [], "à 6 jours, le deuxième n'est pas dû")
        self._vieillir(1)
        self._cron()
        self.assertEqual(self._sujets(), ["Deux"])
        self.assertEqual(self._inscription().bf_step, 2)

    def test_un_retard_ne_lache_pas_les_courriels_en_rafale(self):
        self._inscrire()
        self.envois.clear()
        self._vieillir(40)              # serveur arrêté pendant des semaines
        self._cron()
        self.assertEqual(self._sujets(), ["Deux"], "un seul courriel par passage")
        self._cron()
        self.assertEqual(self._sujets(), ["Deux"], "le troisième attend l'écart prévu")
        self._vieillir(7)
        self._cron()
        self.assertEqual(self._sujets(), ["Deux", "Trois"])

    def test_apres_le_dernier_courriel_la_tache_ne_fait_plus_rien(self):
        self._inscrire()
        for _ in range(2):
            self._vieillir(7)
            self._cron()
        self.assertEqual(self._inscription().bf_step, 3)
        self.envois.clear()
        self._vieillir(30)
        self._cron()
        self.assertEqual(self.envois, [])

    def test_une_inscription_non_confirmee_par_le_formulaire_ne_recoit_rien(self):
        contact = self.env["mailing.contact"].create({"email": self.adresse})
        self.env["mailing.subscription"].create({
            "contact_id": contact.id, "list_id": self.liste.id, "opt_out": False})
        self._cron()
        self.assertEqual(self.envois, [])

    def test_une_adresse_sur_la_liste_noire_ne_recoit_rien(self):
        self._inscrire()
        self.envois.clear()
        self.env["mail.blacklist"].create({"email": self.adresse})
        self._vieillir(8)
        self._cron()
        self.assertEqual(self.envois, [])
        self.assertEqual(self._inscription().bf_step, 1)

    # -------------------------------------------------------------- les échecs

    def test_un_courriel_que_le_smtp_refuse_n_avance_pas_la_sequence(self):
        self._inscrire()
        self.envois.clear()
        self._vieillir(8)
        self.smtp_en_panne = True
        with mute_logger("odoo.addons.mail.models.mail_mail", "odoo.addons.bf_mailing_signup"):
            self._cron()
        sub = self._inscription()
        self.assertEqual((sub.bf_step, sub.bf_failures), (1, 1))
        self.assertFalse(self.env["mail.mail"].sudo().search([("email_to", "=", self.adresse)]),
                         "aucune ligne ne doit rester, la prochaine passe renvoie")
        self.smtp_en_panne = False
        self._cron()
        self.assertEqual(self.envois, [], "la reprise attend son délai d'une heure")
        self._reculer_tentative(2)
        self._cron()
        self.assertEqual(self._sujets(), ["Deux"])
        sub = self._inscription()
        self.assertEqual((sub.bf_step, sub.bf_failures), (2, 0))

    def test_le_premier_courriel_manque_est_repris_par_la_tache(self):
        self.smtp_en_panne = True
        with mute_logger("odoo.addons.mail.models.mail_mail", "odoo.addons.bf_mailing_signup"):
            self._inscrire()
        sub = self._inscription()
        self.assertFalse(sub.opt_out, "la confirmation tient malgré l'échec de l'envoi")
        self.assertEqual((sub.bf_step, sub.bf_failures), (0, 1))
        self.smtp_en_panne = False
        self._reculer_tentative(2)
        self._cron()
        self.assertEqual(self._sujets(), ["Un"])

    def test_des_echecs_repetes_abandonnent_l_inscription(self):
        self._inscrire()
        self.envois.clear()
        self._vieillir(8)
        self.smtp_en_panne = True
        with mute_logger("odoo.addons.mail.models.mail_mail", "odoo.addons.bf_mailing_signup"):
            self._cron()
            avant = self.tentatives
            self._cron()
            self.assertEqual(self.tentatives, avant, "pas de nouvel essai avant le délai")
            for _ in range(ESSAIS_MAX + 4):
                self._reculer_tentative(ATTENTE_MAX_HEURES + 1)
                self._cron()
        sub = self._inscription()
        self.assertEqual(sub.bf_failures, ESSAIS_MAX, "le nombre d'échecs prévu, puis plus d'essai")
        self.assertEqual(self.envois, [])
        self.smtp_en_panne = False
        self._reculer_tentative(100)
        self._cron()
        self.assertEqual(self.envois, [], "abandonnée : même un SMTP revenu ne la relance pas")
        sub.write({"bf_failures": 0})
        self._cron()
        self.assertEqual(self._sujets(), ["Deux"], "remise à zéro à la main : elle repart")

    def test_l_attente_double_puis_plafonne(self):
        self.assertEqual([_attente_heures(n) for n in range(1, 9)], [1, 2, 4, 8, 16, 24, 24, 24])

    def test_une_panne_de_trente_et_une_heures_n_abandonne_personne(self):
        """Avec cinq échecs pour 1+2+4+8 heures, une panne du serveur sortant de 15 heures abandonnait
        TOUTES les personnes dues pendant sa durée. La tâche passe aux quinze minutes : on simule les
        passages un à un."""
        self._inscrire()
        self.envois.clear()
        self._vieillir(8)
        self.smtp_en_panne = True
        with mute_logger("odoo.addons.mail.models.mail_mail", "odoo.addons.bf_mailing_signup"):
            self._cron()                                            # t = 0 : premier échec
            for _ in range(31 * 4):                                 # 31 heures, un passage par quart d'heure
                self._reculer_tentative(0.25)
                self._cron()
        sub = self._inscription()
        self.assertEqual(sub.bf_failures, 6, "échecs à 0, 1, 3, 7, 15 et 31 heures (attente 1, 2, 4, 8, 16)")
        self.assertLess(sub.bf_failures, ESSAIS_MAX, "pas abandonnée")
        self.smtp_en_panne = False
        self._reculer_tentative(25)
        self._cron()
        self.assertEqual(self._sujets(), ["Deux"], "le serveur revenu, la personne reçoit son courriel")

    def test_un_postprocess_interne_qui_leve_apres_l_envoi_ne_renvoie_pas_le_courriel(self):
        """Le cœur rappelle le crochet avec l'état `exception` quand un `_postprocess_sent_message`
        plus interne lève APRÈS un envoi réussi : le dernier état écrasait `sent`, et le courriel,
        déjà parti, repartait à chaque reprise (jusqu'à cinq copies)."""
        self._inscrire()
        self.envois.clear()
        self._vieillir(8)
        from odoo.addons.mail.models.mail_mail import MailMail as MailMailMail
        origine = MailMailMail._postprocess_sent_message
        appels = []

        def leve_une_fois(mails, *a, **kw):
            appels.append(1)
            if len(appels) == 1:
                raise RuntimeError("module interne en panne")
            return origine(mails, *a, **kw)

        with patch.object(MailMailMail, "_postprocess_sent_message", leve_une_fois), \
                mute_logger("odoo.addons.mail.models.mail_mail", "odoo.addons.bf_mailing_signup"):
            self._cron()
            self._cron()
        self.assertEqual(self._sujets(), ["Deux"], "un seul courriel parti, jamais deux")
        sub = self._inscription()
        self.assertEqual((sub.bf_step, sub.bf_failures), (2, 0))

    def test_sans_base_url_aucun_courriel_ne_part_et_rien_n_est_compte(self):
        self._inscrire()
        self.envois.clear()
        self._vieillir(8)
        self.env["ir.config_parameter"].sudo().set_param(ctl.ICP_BASE_URL, "")
        with mute_logger("odoo.addons.bf_mailing_signup"):
            self._cron()
        self.assertEqual(self.envois, [])
        sub = self._inscription()
        self.assertEqual((sub.bf_step, sub.bf_failures), (1, 0), "ce n'est pas l'échec d'une adresse")

    def test_l_expediteur_n_emprunte_pas_le_nom_de_l_usager_de_la_tache(self):
        racine = self.env.ref("base.partner_root")
        racine.write({"name": "Nom Bizarre", "email": "nombizarre@example.com"})
        self._inscrire()
        self.envois.clear()
        self._vieillir(8)
        self._cron()
        self.assertEqual(len(self.envois), 1)
        expediteur = str(self.envois[0]["From"])
        self.assertNotIn("Bizarre", expediteur)
        self.assertNotIn("nombizarre", expediteur)

    def test_une_note_qui_echoue_ne_fait_pas_renvoyer_le_courriel(self):
        self._inscrire()
        self.envois.clear()
        self._vieillir(8)
        contact = type(self.env["mailing.contact"])
        with patch.object(contact, "message_post", side_effect=RuntimeError("note en panne")), \
                mute_logger("odoo.addons.bf_mailing_signup"):
            self._cron()
            self._cron()
        self.assertEqual(self._sujets(), ["Deux"], "un seul envoi malgré la note qui lève")
        self.assertEqual(self._inscription().bf_step, 2)

    def test_une_etape_retiree_au_milieu_ne_bloque_pas_la_suite(self):
        self.env["bf.mailing.signup.step"].search([("lang", "=", "fr"), ("rank", "=", 2)]).unlink()
        self._inscrire()
        self.envois.clear()
        self._vieillir(14)
        self._cron()
        self.assertEqual(self._sujets(), ["Trois"])
        self.assertEqual(self._inscription().bf_step, 3)

    def test_une_liste_archivee_ne_recoit_rien(self):
        self._inscrire()
        self.envois.clear()
        self.liste.active = False
        self._vieillir(8)
        self._cron()
        self.assertEqual(self.envois, [])

    # ------------------------------------------------------------ désabonnement

    def test_le_get_du_lien_ne_desabonne_pas(self):
        self._inscrire()
        r = self.url_open(self._url_desabo().replace("https://exemple.test", ""),
                          allow_redirects=False)
        self.assertEqual(r.status_code, 200)
        self.assertIn("<form", r.text)
        self.assertIn("noindex", r.headers.get("X-Robots-Tag", ""))
        self.assertFalse(self._inscription().opt_out, "un GET (passerelle) n'écrit rien")

    def test_le_post_du_bouton_desabonne_et_la_sequence_s_arrete(self):
        self._inscrire()
        self.envois.clear()
        self._vieillir(8)               # le deuxième courriel était dû
        r = self.url_open(self._url_desabo().replace("https://exemple.test", ""),
                          data={"e": self.adresse}, allow_redirects=False)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(self._inscription().opt_out)
        self._cron()
        self.assertEqual(self.envois, [], "plus rien ne part à une personne désabonnée")
        contact = self._inscription().contact_id
        self.assertTrue(any("Désabonné" in (m.body or "") for m in contact.message_ids))

    def test_la_page_de_desabonnement_ne_s_accorde_pas_au_genre_de_la_personne(self):
        """« Vous êtes désabonné » s'accorde au masculin : ce qu'on écrit, nous, est épicène."""
        self._inscrire()
        chemin = self._url_desabo().replace("https://exemple.test", "")
        r = self.url_open(chemin, data={"x": "1"}, allow_redirects=False)
        self.assertEqual(r.status_code, 200)
        self.assertIn("Désabonnement confirmé", r.text)
        self.assertNotIn("êtes désabonné", r.text)

    def test_le_desabonnement_en_un_clic_du_client_de_messagerie(self):
        self._inscrire()
        r = self.url_open(self._url_desabo().replace("https://exemple.test", ""),
                          data={"List-Unsubscribe": "One-Click"}, allow_redirects=False)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(self._inscription().opt_out)

    def test_desabonner_deux_fois_est_sans_effet(self):
        self._inscrire()
        chemin = self._url_desabo().replace("https://exemple.test", "")
        self.url_open(chemin, data={"x": "1"}, allow_redirects=False)
        date_premiere = self._inscription().opt_out_datetime
        r = self.url_open(chemin, data={"x": "1"}, allow_redirects=False)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self._inscription().opt_out_datetime, date_premiere)

    def test_aucun_plafond_par_ip_sur_le_desabonnement(self):
        """Un fournisseur poste tous les clics uniques depuis quelques adresses."""
        self._inscrire()
        chemin = self._url_desabo().replace("https://exemple.test", "")
        for i in range(60):
            r = self.url_open(chemin, data={"List-Unsubscribe": "One-Click"},
                              allow_redirects=False)
            self.assertEqual(r.status_code, 200, f"POST n° {i + 1}")
        self.assertTrue(self._inscription().opt_out)

    def test_un_jeton_faux_ou_celui_de_la_confirmation_ne_desabonne_pas(self):
        self._inscrire()
        for jeton in ("0" * 32, self._jeton_confirmation(), "x", ""):
            r = self.url_open("/infolettre/desabonner", data={
                "e": self.adresse, "l": self.liste.id, "j": jeton, "lang": "fr"},
                allow_redirects=False)
            self.assertEqual(r.status_code, 303, jeton)
            self.assertEqual(r.headers["Location"], "/#infolettre")
        self.assertFalse(self._inscription().opt_out)

    def test_le_jeton_d_une_personne_ne_vaut_pas_pour_une_autre(self):
        self._inscrire()
        autre = "autre.personne@example.com"
        jeton = ctl._token_desabonnement(self.env, self.liste.id, self.adresse)
        r = self.url_open("/infolettre/desabonner", data={
            "e": autre, "j": jeton, "lang": "fr"}, allow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertFalse(self._inscription().opt_out)

    def test_list_unsubscribe_ne_part_pas_encode_sur_le_fil(self):
        """Ce que le serveur reçoit, pas l'objet en mémoire : Python plie un en-tête de plus
        de 78 caractères sans espace en mots encodés RFC 2047 (réproduit avant correction)."""
        self._inscrire()
        message = self.envois[0]
        brut = io.BytesIO()
        email.generator.BytesGenerator(
            brut, policy=message.policy.clone(utf8=False)).flatten(message, linesep="\r\n")
        lignes = brut.getvalue().decode("utf-8", errors="replace").split("\r\n")
        entetes = [ligne for ligne in lignes if ligne.lower().startswith("list-unsubscribe")]
        self.assertEqual(
            sorted(e.split(":")[0] for e in entetes), ["List-Unsubscribe", "List-Unsubscribe-Post"])
        unique = [e for e in entetes if e.startswith("List-Unsubscribe:")][0]
        self.assertEqual(unique, f"List-Unsubscribe: <{self._url_desabo()}>")
        self.assertGreater(len(unique), 100, "l'essai doit dépasser le seuil de pliage")
        self.assertNotIn("=?", unique)
        # pas de ligne de continuation juste après
        i = lignes.index(unique)
        self.assertFalse(lignes[i + 1].startswith((" ", "\t")))

    def test_un_desabonnement_efface_la_date_de_confirmation_peu_importe_le_chemin(self):
        self._inscrire()
        sub = self._inscription()
        self.assertTrue(sub.bf_confirmed_at)
        sub.write({"opt_out": True})                    # case cochée, page d'Odoo, import
        self.assertFalse(sub.bf_confirmed_at)

    def test_une_reinscription_sans_double_consentement_ne_relance_pas_la_sequence(self):
        """Route du site web, import ou geste d'administrateur : `opt_out` repasse à faux."""
        self._inscrire()
        self._vieillir(8)               # le deuxième courriel serait dû
        self.url_open(self._url_desabo().replace("https://exemple.test", ""),
                      data={"x": "1"}, allow_redirects=False)
        self.envois.clear()
        sub = self._inscription()
        self.assertFalse(sub.bf_confirmed_at, "le désabonnement efface la date")
        sub.write({"opt_out": False})
        self._cron()
        self.assertEqual(self.envois, [])
        self.assertEqual(sub.bf_step, 1)

    def test_le_desabonnement_atteint_les_fiches_en_double(self):
        """`mailing.contact` n'a aucune contrainte d'unicité sur le courriel."""
        self._inscrire()
        double = self.env["mailing.contact"].create({"email": self.adresse})
        self.env["mailing.subscription"].create({
            "contact_id": double.id, "list_id": self.liste.id, "opt_out": False})
        autre_liste = self.env["mailing.list"].create({"name": "Autre liste"})
        self.env["mailing.subscription"].create({
            "contact_id": double.id, "list_id": autre_liste.id, "opt_out": False})
        r = self.url_open(self._url_desabo().replace("https://exemple.test", ""),
                          data={"x": "1"}, allow_redirects=False)
        self.assertEqual(r.status_code, 200)
        subs = self.env["mailing.subscription"].search([
            ("list_id", "=", self.liste.id),
            ("contact_id.email_normalized", "=", self.adresse)])
        self.assertEqual(len(subs), 2)
        self.assertTrue(all(subs.mapped("opt_out")))
        autre = self.env["mailing.subscription"].search([("list_id", "=", autre_liste.id)])
        self.assertFalse(autre.opt_out, "une AUTRE liste n'est pas touchée")

    def test_le_lien_survit_au_changement_de_la_liste_du_parametre(self):
        """Un courriel de la LCAP doit désabonner 60 jours plus tard."""
        self._inscrire()
        chemin = self._url_desabo().replace("https://exemple.test", "")
        autre = self.env["mailing.list"].create({"name": "Nouvelle liste"})
        self.env["ir.config_parameter"].sudo().set_param(ctl.ICP_LIST, str(autre.id))
        r = self.url_open(chemin, data={"x": "1"}, allow_redirects=False)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(self._inscription().opt_out)

    # ------------------------------------------------- le consentement dit ce qui suit

    def _courriel_de_confirmation(self, lang="fr"):
        self.url_open("/infolettre", data={"courriel": self.adresse, "lang": lang},
                      allow_redirects=False)
        confirmations = [m for m in self.envois if "onfirm" in str(m["Subject"])]
        self.assertEqual(len(confirmations), 1)
        return self._html(confirmations[0])

    def test_la_confirmation_annonce_la_serie_avant_le_clic(self):
        corps = self._courriel_de_confirmation("fr")
        self.assertIn("vous recevrez 3 courriels d'accueil, un par semaine, puis les nouveautés", corps)

    def test_la_confirmation_annonce_la_serie_en_anglais(self):
        corps = self._courriel_de_confirmation("en")
        self.assertIn("you will receive 3 welcome emails, one a week, then the", corps)

    def test_la_page_du_bouton_annonce_aussi_la_serie(self):
        self.url_open("/infolettre", data={"courriel": self.adresse, "lang": "fr"}, allow_redirects=False)
        r = self.url_open(f"/infolettre/confirmer?e={self.adresse}&j={self._jeton_confirmation()}&lang=fr",
                          allow_redirects=False)
        self.assertEqual(r.status_code, 200)
        self.assertIn("vous recevrez 3 courriels d'accueil, un par semaine", r.text)

    def test_sans_etape_la_confirmation_ne_promet_rien(self):
        self.env["bf.mailing.signup.step"].search([]).unlink()
        corps = self._courriel_de_confirmation("fr")
        self.assertNotIn("courriels d'accueil", corps)
        self.assertNotIn("courriel d'accueil", corps)

    def test_un_rythme_irregulier_ne_promet_pas_une_semaine(self):
        self.env["bf.mailing.signup.step"].search([("lang", "=", "fr"), ("rank", "=", 3)]).write(
            {"delay_days": 30})
        corps = self._courriel_de_confirmation("fr")
        self.assertIn("3 courriels d'accueil, espacés dans le temps", corps)
        self.assertNotIn("un par semaine", corps)

    def test_une_seule_etape_se_dit_au_singulier(self):
        self.env["bf.mailing.signup.step"].search([("lang", "=", "fr"), ("rank", ">", 1)]).unlink()
        corps = self._courriel_de_confirmation("fr")
        self.assertIn("vous recevrez un courriel d'accueil, puis les nouveautés", corps)

    def test_la_phrase_vient_des_etapes_de_la_langue_du_formulaire(self):
        self.env["bf.mailing.signup.step"].search([("lang", "=", "en"), ("rank", ">", 2)]).unlink()
        self.assertIn("2 welcome emails", self._courriel_de_confirmation("en"))
        self.envois.clear()
        self.env["mailing.contact"].search([("email_normalized", "=", self.adresse)]).unlink()
        self.assertIn("3 courriels d'accueil", self._courriel_de_confirmation("fr"))

    def test_une_liste_en_chiffres_non_ascii_ne_fait_pas_de_500(self):
        """`"²".isdigit()` est vrai mais `int("²")` lève : un GET sans jeton rendait une 500."""
        for liste in ("²", "٣", "①", "%C2%B2"):
            r = self.url_open(f"/infolettre/desabonner?e={self.adresse}&l={liste}&j={'0' * 32}",
                              allow_redirects=False)
            self.assertEqual(r.status_code, 303, liste)

    def test_un_lien_valide_pour_une_adresse_modifiee_se_journalise_sans_oracle(self):
        self._inscrire()
        chemin = self._url_desabo().replace("https://exemple.test", "")
        self._inscription().contact_id.write({"email": "nouvelle.adresse@example.com"})
        with self.assertLogs("odoo.addons.bf_mailing_signup.controllers.main", "WARNING") as journal:
            r = self.url_open(chemin, data={"x": "1"}, allow_redirects=False)
        self.assertEqual(r.status_code, 200, "la page répond comme toujours")
        self.assertTrue(any("sans inscription" in m for m in journal.output))
        sub = self.env["mailing.subscription"].search([("list_id", "=", self.liste.id)], limit=1)
        self.assertFalse(sub.opt_out, "l'inscription sous la nouvelle adresse n'est pas touchée")

    def test_un_lien_dont_la_liste_est_changee_est_refuse(self):
        self._inscrire()
        autre = self.env["mailing.list"].create({"name": "Autre"})
        chemin = self._url_desabo().replace("https://exemple.test", "").replace(
            f"l={self.liste.id}", f"l={autre.id}")
        r = self.url_open(chemin, data={"x": "1"}, allow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertFalse(self._inscription().opt_out)

    def test_la_reinscription_apres_un_desabonnement_relance_la_sequence(self):
        self._inscrire()
        self.url_open(self._url_desabo().replace("https://exemple.test", ""),
                      data={"x": "1"}, allow_redirects=False)
        self.assertTrue(self._inscription().opt_out)
        # Une resoumission du formulaire ne réinscrit pas à elle seule...
        self.url_open("/infolettre", data={"courriel": self.adresse, "lang": "en"},
                      allow_redirects=False)
        self.assertTrue(self._inscription().opt_out)
        self.envois.clear()
        # ... seul le lien de confirmation le fait, et la séquence repart du début.
        self.url_open("/infolettre/confirmer", data={
            "e": self.adresse, "j": self._jeton_confirmation(), "lang": "en"},
            allow_redirects=False)
        sub = self._inscription()
        self.assertFalse(sub.opt_out)
        self.assertEqual((sub.bf_lang, sub.bf_step), ("en", 1))
        self.assertEqual(self._sujets(), ["One"])

    # ----------------------------------------------------------- sans étrangeté

    def test_aucun_courriel_de_sequence_n_est_garde_apres_l_envoi(self):
        self._inscrire()
        self.assertFalse(self.env["mail.mail"].sudo().search([("email_to", "=", self.adresse)]),
                         "la ligne mail.mail porte l'adresse : elle part avec l'envoi")

    def test_le_retrait_du_pixel_reste_lineaire_et_retire_un_vrai_pixel(self):
        """`build_email` est appelable par tout usager connecté (portail compris, par `call_kw`).
        L'expression qui cherchait l'attribut DANS la balise relisait la balise à chaque
        occurrence : `<img ` + l'attribut répété sans `>` coûtait 20 secondes pour 464 Ko.

        On chronomètre le retrait seul : `build_email` passe aussi par le module OCA
        `mail_tracking`, qui a son propre coût et que ce module ne maîtrise pas."""
        pixel = '<img src="https://t.example/p.gif" data-odoo-tracking-email="7" width="1" height="1">'
        message = self.env["ir.mail_server"].build_email(
            "a@example.com", ["b@example.com"], "s", f"<p>Bonjour</p>{pixel}",
            headers={ctl.ENTETE_SANS_SUIVI: "1"}, subtype="html")
        corps = message.get_body(preferencelist=("html",)).get_content()
        self.assertNotIn("data-odoo-tracking-email", corps)
        self.assertIn("Bonjour", corps)
        self.assertEqual(ims._retirer_pixel("x" + pixel + "y"), "xy")
        # attributs dans un autre ordre, guillemets simples, retour à la ligne dans la balise
        for variante in ("<img data-odoo-tracking-email='9' src='x'>",
                         '<img\nwidth="1"\ndata-odoo-tracking-email="9">'):
            self.assertEqual(ims._retirer_pixel("a" + variante + "b"), "ab", variante)
        autre = '<img src="https://exemple.test/logo.png" alt="logo">'
        self.assertEqual(ims._retirer_pixel(f"<p>{autre}</p>"), f"<p>{autre}</p>", "une image ordinaire reste")
        for malin in ("<img " * 100000,                                        # 500 Ko, aucun `>`
                      "<img " + 'data-odoo-tracking-email="1" ' * 16000,     # 464 Ko, une balise jamais fermée
                      "<img data-odoo-tracking-email=" * 16000):
            debut = time.monotonic()
            self.assertEqual(ims._retirer_pixel(malin), malin)
            self.assertLess(time.monotonic() - debut, 2, "le retrait du pixel doit être linéaire")

    def test_le_corps_envoye_ne_garde_aucun_marqueur_ni_gabarit_brut(self):
        self._inscrire()
        self.assertFalse(re.search(r"\{\{|\}\}", self._html(self.envois[0])))
