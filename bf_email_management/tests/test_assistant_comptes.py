"""L'assistant d'ajout d'un compte courriel.

Les contrôles qui tranchent, et ce qu'ils empêchent :

* `test_xoauth2_suit_le_vecteur_publie_par_microsoft` : l'encodeur SASL est
  comparé, octet pour octet, à l'exemple publié par Microsoft. Sans ce
  contrôle, un seul séparateur de travers donnerait « échec
  d'authentification », qu'on irait chercher du côté des permissions pendant
  des heures.
* `test_le_mode_dauthentification_ne_vient_pas_du_capability` : le serveur de
  Microsoft ANNONCE `AUTH=PLAIN` et refuse le mot de passe. Un assistant qui
  croit l'annonce promet une connexion impossible.
* `test_un_compte_neuf_ne_reorganise_pas_la_boite` : les 55 courriels sortis
  de l'INBOX d'un client le 2026-09-20 sont venus d'un défaut coché.
* `test_le_serveur_sortant_exige_un_filtre_dexpediteur` : un serveur sortant
  sans `from_filter` devient candidat pour toutes les adresses du locataire.
"""

import base64
import imaplib
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged

from ..models import bf_email_imap
from ..models import bf_email_autoconfig as autoconf

CHEMIN = "odoo.addons.bf_email_management.models.bf_email_autoconfig.BfEmailAutoconfig"

# Un vrai extrait de l'ISPDB, capturé le 2026-09-20 sur
# https://autoconfig.thunderbird.net/v1.1/gmail.com
ISPDB_GMAIL = """<?xml version="1.0" encoding="UTF-8"?>
<clientConfig version="1.1">
  <emailProvider id="googlemail.com">
    <domain>gmail.com</domain>
    <incomingServer type="imap">
      <hostname>imap.gmail.com</hostname>
      <port>993</port>
      <socketType>SSL</socketType>
      <authentication>OAuth2</authentication>
      <username>%EMAILADDRESS%</username>
    </incomingServer>
    <outgoingServer type="smtp">
      <hostname>smtp.gmail.com</hostname>
      <port>465</port>
      <socketType>SSL</socketType>
      <authentication>OAuth2</authentication>
      <username>%EMAILADDRESS%</username>
    </outgoingServer>
  </emailProvider>
</clientConfig>"""

# Le même format, mais annonçant un mot de passe en clair sur un hôte
# Microsoft : c'est le mensonge que l'assistant ne doit pas croire.
ISPDB_MENTEUR = ISPDB_GMAIL.replace(
    "imap.gmail.com", "outlook.office365.com").replace("OAuth2", "password-cleartext")


@tagged("post_install", "-at_install")
class TestAutoconfig(TransactionCase):

    def _decouvrir(self, adresse, reponses=None, mx=None, sonde=False):
        """Joue la cascade sans toucher au réseau."""
        reponses = reponses or {}

        def faux_get(self_, url, delai=None):
            for motif, corps in reponses.items():
                if motif in url:
                    return corps
            return ""

        def faux_mx(self_, domaine):
            return list(mx or [])

        def faux_srv(self_, nom):
            return False

        def faux_parle(self_, hote, port=993):
            return bool(sonde) and hote.startswith(sonde)

        with patch(CHEMIN + "._get", faux_get), \
             patch(CHEMIN + "._mx", faux_mx), \
             patch(CHEMIN + "._srv", faux_srv), \
             patch(CHEMIN + "._parle_imap", faux_parle), \
             patch(CHEMIN + "._resout", lambda self_, h: True):
            return self.env["bf.email.autoconfig"].decouvrir(adresse)

    # -- les voies ------------------------------------------------------
    def test_la_configuration_publiee_par_le_domaine_gagne(self):
        trouve = self._decouvrir(
            "usager@exemple.test",
            {"autoconfig.exemple.test": ISPDB_GMAIL.replace(
                "imap.gmail.com", "imap.exemple.test")})
        self.assertEqual(trouve["source"], "autoconfig")
        self.assertEqual(trouve["imap"]["host"], "imap.exemple.test")
        self.assertEqual(trouve["imap"]["port"], 993)

    def test_le_domaine_dentreprise_se_trouve_par_son_mx(self):
        """🔴 La voie qui décide : sans elle, 42 % des domaines rendent rien.

        Un domaine chez Google Workspace ne publie ni autoconfig, ni
        .well-known, ni SRV, et n'est pas dans l'ISPDB. Seul le MX le nomme.
        """
        trouve = self._decouvrir(
            "personne@entreprise.test",
            {"thunderbird.net/v1.1/google.com": ISPDB_GMAIL},
            mx=["aspmx.l.google.com"])
        self.assertEqual(trouve["source"], "mx_ispdb")
        self.assertEqual(trouve["imap"]["host"], "imap.gmail.com")
        self.assertEqual(trouve["fournisseur"], "google")

    def test_migadu_passe_par_notre_table_faute_dispdb(self):
        """🔴 Mesuré : l'ISPDB rend 404 sur `migadu.com`."""
        trouve = self._decouvrir("moi@chez-nous.test", {}, mx=["aspmx1.migadu.com"])
        self.assertEqual(trouve["source"], "empreinte_mx")
        self.assertEqual(trouve["imap"]["host"], "imap.migadu.com")
        self.assertEqual(trouve["auth"], "password")

    def test_la_sonde_exige_un_accueil_imap_pas_un_enregistrement_dns(self):
        """⚠️ 34 domaines ont un `imap.` qui résout, 12 répondent."""
        muet = self._decouvrir("moi@muet.test", {}, mx=[], sonde=False)
        self.assertFalse(muet["source"])
        parlant = self._decouvrir("moi@parlant.test", {}, mx=[], sonde="imap.")
        self.assertEqual(parlant["source"], "sonde")
        self.assertEqual(parlant["imap"]["host"], "imap.parlant.test")

    def test_une_adresse_sans_domaine_ne_casse_rien(self):
        for mauvaise in ("", "pas-une-adresse", "a@b", "a@localhost"):
            trouve = self.env["bf.email.autoconfig"].decouvrir(mauvaise)
            self.assertFalse(trouve["source"])

    # -- l'authentification ---------------------------------------------
    def test_le_mode_dauthentification_ne_vient_pas_du_capability(self):
        """🔴 `outlook.office365.com` annonce AUTH=PLAIN et refuse le mot de passe.

        Ici le XML lui-même annonce « password-cleartext ». L'assistant doit
        quand même conclure OAuth, parce que la décision se prend sur le
        FOURNISSEUR, pas sur ce qui est annoncé.
        """
        trouve = self._decouvrir(
            "moi@boite.test", {"autoconfig.boite.test": ISPDB_MENTEUR})
        self.assertEqual(trouve["imap"]["auth_annonce"], "password-cleartext")
        self.assertEqual(trouve["fournisseur"], "microsoft")
        self.assertEqual(trouve["auth"], "oauth")
        self.assertEqual(trouve["oauth"], "microsoft")

    def test_google_reste_au_mot_de_passe_dapplication(self):
        trouve = self._decouvrir(
            "moi@gmail.com", {"thunderbird.net/v1.1/gmail.com": ISPDB_GMAIL})
        self.assertEqual(trouve["auth"], "app_password")
        self.assertTrue(trouve["aide_url"])

    # -- les garde-fous --------------------------------------------------
    def test_aucune_requete_vers_une_adresse_interne(self):
        """La cascade fabrique des URL depuis une chaîne de l'usager."""
        modele = self.env["bf.email.autoconfig"]
        for url in ("https://127.0.0.1/mail/config-v1.1.xml",
                    "https://169.254.169.254/latest/meta-data",
                    "file:///etc/passwd"):
            self.assertEqual(modele._get(url), "")

    def test_le_budget_de_temps_est_borne(self):
        """Une voie lente ne doit pas figer l'écran : le total est borné."""
        self.assertLessEqual(autoconf.DELAI_ETAPE * 3, autoconf.DELAI_TOTAL)
        self.assertLess(autoconf.DELAI_WELLKNOWN, autoconf.DELAI_ETAPE)


@tagged("post_install", "-at_install")
class TestXoauth2(TransactionCase):

    def test_xoauth2_suit_le_vecteur_publie_par_microsoft(self):
        """✅ Le vecteur de la documentation Microsoft, octet pour octet.

        Sans un vecteur publié, un encodeur faux se contente d'échouer à
        l'authentification, et on cherche la panne du mauvais côté.
        """
        attendu = ("dXNlcj10ZXN0QGNvbnRvc28ub25taWNyb3NvZnQuY29tAWF1dGg9QmVhcmVy"
                   "IEV3QkFBbDNCQUFVRkZwVUFvN0ozVmUwYmpMQldaV0NjbFJDM0VvQUEBAQ==")
        rendu = self.env["bf.email.oauth"].chaine_xoauth2(
            "test@contoso.onmicrosoft.com",
            "EwBAAl3BAAUFFpUAo7J3Ve0bjLBWZWCclRC3EoAA")
        self.assertEqual(rendu, attendu)
        # Et la chaîne décodée porte bien les deux octets 0x01 de séparation.
        clair = base64.b64decode(rendu).decode()
        self.assertEqual(clair.count("\x01"), 3)
        self.assertTrue(clair.endswith("\x01\x01"))

    def test_letat_signe_refuse_le_compte_dun_autre(self):
        Compte = self.env["bf.email.account"]
        mien = Compte.create({
            "name": "mien", "host": "imap.test", "login": "mien@test.invalid",
            "password": "x", "user_id": self.env.user.id})
        autre = Compte.create({
            "name": "autre", "host": "imap.test", "login": "autre@test.invalid",
            "password": "x", "user_id": self.env.user.id})
        Oauth = self.env["bf.email.oauth"]
        etat = Oauth._etat(mien)
        self.assertTrue(Oauth._verifier_etat(etat, mien))
        self.assertFalse(Oauth._verifier_etat(etat, autre))
        self.assertFalse(Oauth._verifier_etat('{"compte": %s}' % mien.id, mien))
        self.assertFalse(Oauth._verifier_etat("pas du json", mien))

    def test_le_jeton_se_renouvelle_avant_son_expiration(self):
        compte = self.env["bf.email.account"].create({
            "name": "outlook", "host": "outlook.office365.com",
            "login": "moi@entreprise.test", "auth_mode": "xoauth2",
            "oauth_provider": "microsoft", "oauth_refresh_token": "rafraichir-moi",
            "oauth_access_token": "vieux", "oauth_expiration": 0,
            "user_id": self.env.user.id})
        appels = []

        def faux_rafraichir(self_, fournisseur, jeton):
            appels.append((fournisseur, jeton))
            return {"access_token": "neuf", "expiration": 2 ** 31 - 1,
                    "refresh_token": False}

        with patch("odoo.addons.bf_email_management.models.bf_email_oauth."
                   "BfEmailOauth.rafraichir", faux_rafraichir):
            self.assertEqual(compte._jeton_acces(), "neuf")
            # Le second appel ne redemande rien : le jeton est encore bon.
            self.assertEqual(compte._jeton_acces(), "neuf")
        self.assertEqual(len(appels), 1)
        # ⚠️ Google ne redonne pas de refresh_token au renouvellement :
        # l'écraser avec un False perdrait le lien pour toujours.
        self.assertEqual(compte.oauth_refresh_token, "rafraichir-moi")

    def test_un_compte_oauth_sans_consentement_le_dit(self):
        compte = self.env["bf.email.account"].create({
            "name": "outlook", "host": "outlook.office365.com",
            "login": "moi@entreprise.test", "auth_mode": "xoauth2",
            "oauth_provider": "microsoft", "user_id": self.env.user.id})
        with self.assertRaises(UserError):
            compte._jeton_acces()


@tagged("post_install", "-at_install")
class TestMessagesEtGardes(TransactionCase):

    def test_le_message_derreur_nest_plus_une_chaine_doctets(self):
        """Vu en production : `b'[AUTHENTICATIONFAILED] …'` dans `last_error`."""
        erreur = imaplib.IMAP4.error(b"[AUTHENTICATIONFAILED] Authentication failed.")
        texte = bf_email_imap._texte_de_l_erreur(erreur)
        self.assertNotIn("b'", texte)
        self.assertTrue(texte.startswith("[AUTHENTICATIONFAILED]"))

    def test_un_compte_sans_moyen_dentrer_est_refuse(self):
        with self.assertRaises(ValidationError):
            self.env["bf.email.account"].create({
                "name": "sans rien", "host": "imap.test",
                "login": "x@test.invalid", "user_id": self.env.user.id})
        with self.assertRaises(ValidationError):
            self.env["bf.email.account"].create({
                "name": "oauth sans fournisseur", "host": "imap.test",
                "login": "y@test.invalid", "auth_mode": "xoauth2",
                "user_id": self.env.user.id})

    def test_un_employe_ne_peut_pas_creer_un_serveur_sortant(self):
        """C'est POUR ÇA que l'assistant passe en sudo, et rien d'autre."""
        employe = self.env["res.users"].create({
            "name": "Employé", "login": "employe.25862@test.invalid",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])]})
        with self.assertRaises(AccessError):
            self.env["ir.mail_server"].with_user(employe).create({
                "name": "x", "smtp_host": "smtp.test"})

    def test_le_serveur_sortant_exige_un_filtre_dexpediteur(self):
        compte = self.env["bf.email.account"].create({
            "name": "compte", "host": "outlook.office365.com",
            "login": "moi@entreprise.test", "auth_mode": "xoauth2",
            "oauth_provider": "microsoft", "user_id": self.env.user.id})
        with self.assertRaises(ValidationError):
            self.env["ir.mail_server"].create({
                "name": "sans filtre", "smtp_host": "smtp.outlook.com",
                "smtp_authentication": "bf_compte", "bf_account_id": compte.id,
                "from_filter": False})

    def test_le_serveur_fabrique_porte_ladresse_du_compte(self):
        compte = self.env["bf.email.account"].create({
            "name": "compte", "host": "outlook.office365.com",
            "login": "Moi@Entreprise.test", "auth_mode": "xoauth2",
            "oauth_provider": "microsoft", "user_id": self.env.user.id})
        serveur = self.env["ir.mail_server"]._bf_assurer_pour_compte(
            compte, "smtp.outlook.com", 587, "STARTTLS")
        self.assertEqual(serveur.from_filter, "moi@entreprise.test")
        self.assertEqual(serveur.smtp_authentication, "bf_compte")
        self.assertFalse(serveur.smtp_pass)
        self.assertEqual(serveur.smtp_encryption, "starttls")
        # Idempotent : deux passages ne font pas deux serveurs.
        encore = self.env["ir.mail_server"]._bf_assurer_pour_compte(
            compte, "smtp.outlook.com", 587, "STARTTLS")
        self.assertEqual(encore, serveur)


@tagged("post_install", "-at_install")
class TestAssistant(TransactionCase):

    def _assistant(self, **valeurs):
        return self.env["bf.email.account.setup"].create(valeurs)

    def test_la_detection_remplit_lassistant(self):
        assistant = self._assistant(email="moi@entreprise.test")
        faux = {
            "adresse": "moi@entreprise.test", "domaine": "entreprise.test",
            "source": "mx_ispdb", "fournisseur": "microsoft",
            "libelle_fournisseur": "Microsoft 365 / Outlook.com",
            "imap": {"host": "outlook.office365.com", "port": 993, "socket": "SSL"},
            "smtp": {"host": "smtp.outlook.com", "port": 587, "socket": "STARTTLS"},
            "auth": "oauth", "oauth": "microsoft", "aide_url": False,
            "duree_ms": 431, "essais": [], "mx": [],
        }
        with patch("odoo.addons.bf_email_management.models.bf_email_autoconfig."
                   "BfEmailAutoconfig.decouvrir", lambda self_, a: faux):
            assistant.action_detecter()
        self.assertEqual(assistant.state, "detecte")
        self.assertEqual(assistant.imap_host, "outlook.office365.com")
        self.assertEqual(assistant.auth_mode, "oauth")
        # ⚠️ Le message doit dire que le mot de passe ne marchera JAMAIS, pas
        # seulement afficher un serveur trouvé.
        self.assertIn("Basic authentication is disabled", assistant.message)
        self.assertFalse(assistant.oauth_pret)
        # Et sans inscription d'application, le bouton refuse d'ouvrir.
        with self.assertRaises(UserError):
            assistant.action_lier_oauth()


    def test_le_dialogue_porte_un_titre_et_nomme_l_adresse(self):
        """🔴 Deux défauts vus sur une capture destinée au guide public.

        Une action sans `name` fait titrer le dialogue « Odoo », et l'écran
        de détection montrait des serveurs sans jamais redire de quelle
        adresse il parlait. Les deux sautent aux yeux en image et à personne
        en lisant le code.
        """
        assistant = self._assistant(email="moi@chez-nous.test")
        faux = {"adresse": "moi@chez-nous.test", "domaine": "chez-nous.test",
                "source": "empreinte_mx", "fournisseur": "migadu",
                "libelle_fournisseur": "Migadu",
                "imap": {"host": "imap.migadu.com", "port": 993, "socket": "SSL"},
                "smtp": {"host": "smtp.migadu.com", "port": 465, "socket": "SSL"},
                "auth": "password", "oauth": False, "aide_url": False,
                "duree_ms": 305, "essais": [], "mx": []}
        with patch("odoo.addons.bf_email_management.models.bf_email_autoconfig."
                   "BfEmailAutoconfig.decouvrir", lambda self_, a: faux):
            action = assistant.action_detecter()
        self.assertTrue(action.get("name"), "le dialogue retomberait sur « Odoo »")
        self.assertIn("moi@chez-nous.test", assistant.message)

    def test_un_domaine_muet_bascule_a_la_saisie_manuelle(self):
        assistant = self._assistant(email="moi@muet.test")
        vide = {"adresse": "moi@muet.test", "domaine": "muet.test",
                "source": False, "imap": False, "smtp": False, "auth": "password",
                "duree_ms": 12, "essais": [], "mx": []}
        with patch("odoo.addons.bf_email_management.models.bf_email_autoconfig."
                   "BfEmailAutoconfig.decouvrir", lambda self_, a: vide):
            assistant.action_detecter()
        self.assertEqual(assistant.state, "manuel")

    def test_rien_nest_ecrit_quand_la_connexion_echoue(self):
        """L'essai IMAP passe AVANT la création, pas après."""
        assistant = self._assistant(
            email="moi@chez-nous.test", state="detecte",
            imap_host="imap.migadu.com", imap_port=993, password="faux")
        avant = self.env["bf.email.account"].search_count([])

        def refus(*a, **kw):
            raise bf_email_imap.ImapConnectionError(
                "IMAP login failed for moi on imap.migadu.com: Authentication failed")

        with patch.object(bf_email_imap, "open_connection", refus):
            with self.assertRaises(UserError):
                assistant.action_brancher()
        self.assertEqual(self.env["bf.email.account"].search_count([]), avant)

    def test_le_refus_de_microsoft_est_traduit_en_phrase_utile(self):
        assistant = self._assistant(email="moi@entreprise.test", state="detecte",
                                    imap_host="outlook.office365.com",
                                    password="peu importe")

        def refus(*a, **kw):
            raise bf_email_imap.ImapConnectionError(
                "IMAP login failed for moi on outlook.office365.com: "
                "Basic authentication is disabled.")

        with patch.object(bf_email_imap, "open_connection", refus):
            with self.assertRaises(UserError) as capture:
                assistant.action_brancher()
        self.assertIn("OAuth", str(capture.exception))

    def test_un_compte_neuf_ne_reorganise_pas_la_boite(self):
        """🔴 55 courriels réels sont sortis d'une INBOX le 2026-09-20.

        La cause : `writeback_archive` vrai par défaut, plus les règles
        livrées d'office qui posent `set_handled`. L'assistant ne doit jamais
        naître avec ce réglage armé.
        """
        assistant = self._assistant(
            email="moi@chez-nous.test", state="detecte",
            imap_host="imap.migadu.com", imap_port=993, password="bon",
            smtp_host="smtp.migadu.com", smtp_port=465, smtp_security="SSL")

        class FausseSession:
            def logout(self_):
                return None

        with patch.object(bf_email_imap, "open_connection",
                          lambda *a, **kw: FausseSession()), \
             patch("odoo.addons.bf_email_management.models.bf_email_account."
                   "BfEmailAccount._semer_le_filigrane", lambda self_, jours=7: True):
            assistant.action_brancher()

        compte = assistant.account_id
        self.assertTrue(compte)
        self.assertFalse(compte.writeback_archive)
        self.assertEqual(compte.auth_mode, "password")
        self.assertEqual(compte.state, "connected")
        # L'identité d'expédition et le serveur sortant suivent, avec le
        # filtre verrouillé sur l'adresse du compte.
        identite = self.env["bf.email.identity"].search([
            ("email_normalized", "=", "moi@chez-nous.test")])
        self.assertTrue(identite)
        serveur = self.env["ir.mail_server"].search([
            ("from_filter", "=ilike", "moi@chez-nous.test")])
        self.assertEqual(len(serveur), 1)
        # Depuis la revue de sécurité : les DEUX modes empruntent le secret au
        # compte, le serveur n'en garde aucune copie.
        self.assertEqual(serveur.smtp_authentication, "bf_compte")
        self.assertFalse(serveur.smtp_pass)

    def test_le_filigrane_saute_lhistorique(self):
        """Un compte neuf a `last_uid_inbox = 0`, donc il rejouerait TOUT."""
        compte = self.env["bf.email.account"].create({
            "name": "compte", "host": "imap.test", "login": "moi@test.invalid",
            "password": "x", "user_id": self.env.user.id})
        self.assertEqual(compte.last_uid_inbox, 0)

        class FausseSession:
            def logout(self_):
                return None

        with patch.object(bf_email_imap, "open_connection",
                          lambda *a, **kw: FausseSession()), \
             patch.object(bf_email_imap, "select_folder", lambda *a, **kw: True), \
             patch.object(bf_email_imap, "search_uids_in_range",
                          lambda conn, date_from=None, date_to=None:
                              [900, 901, 902] if date_from else [1, 2, 900, 902]), \
             patch("odoo.addons.bf_email_management.models.bf_email_account."
                   "BfEmailAccount._get_imap_folders", lambda self_, force=False: []):
            compte._semer_le_filigrane()
        self.assertEqual(compte.last_uid_inbox, 899)

@tagged("post_install", "-at_install")
class TestAssistantEnEmploye(TransactionCase):
    """🔴 Le parcours joué dans le rôle visé, pas en administrateur.

    Un assistant qui marche en admin et tombe en employé ne sert à rien : la
    personne qui branche sa boîte n'est jamais administratrice. Et c'est
    précisément là que ça casse, parce que l'assistant touche trois choses
    interdites au groupe *User* : `ir.mail_server` (création refusée),
    `ir.config_parameter` (lecture refusée) et les identités d'expédition
    des autres.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.employe = cls.env["res.users"].create({
            "name": "Employée ordinaire",
            "login": "employe.assistant.25862@test.invalid",
            "email": "employe.assistant.25862@test.invalid",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })

    def test_une_employee_branche_sa_boite_de_bout_en_bout(self):
        assistant = self.env["bf.email.account.setup"].with_user(
            self.employe).create({
                "email": "elle@chez-nous.test", "state": "detecte",
                "imap_host": "imap.migadu.com", "imap_port": 993,
                "password": "mot-de-passe-d-application",
                "smtp_host": "smtp.migadu.com", "smtp_port": 465,
                "smtp_security": "SSL"})

        class FausseSession:
            def logout(self_):
                return None

        with patch.object(bf_email_imap, "open_connection",
                          lambda *a, **kw: FausseSession()), \
             patch("odoo.addons.bf_email_management.models.bf_email_account."
                   "BfEmailAccount._semer_le_filigrane", lambda self_, jours=7: True):
            assistant.action_brancher()

        compte = assistant.account_id
        self.assertEqual(compte.user_id, self.employe)
        self.assertFalse(compte.writeback_archive)
        # Le serveur sortant existe, alors qu'elle n'a pas le droit d'en créer.
        serveur = self.env["ir.mail_server"].search([
            ("from_filter", "=ilike", "elle@chez-nous.test")])
        self.assertEqual(len(serveur), 1)
        self.assertEqual(serveur.from_filter, "elle@chez-nous.test")
        # Et elle ne peut toujours pas le modifier elle-même.
        with self.assertRaises(AccessError):
            serveur.with_user(self.employe).write({"from_filter": False})

    def test_la_detection_marche_sans_droits_dadministration(self):
        """La cascade lit des ICP et appelle `bf.email.oauth` : en sudo, sinon
        un employé se prend un `AccessError` sur un réglage d'instance."""
        faux = {"adresse": "elle@entreprise.test", "domaine": "entreprise.test",
                "source": "mx_ispdb", "fournisseur": "microsoft",
                "libelle_fournisseur": "Microsoft 365 / Outlook.com",
                "imap": {"host": "outlook.office365.com", "port": 993,
                         "socket": "SSL"},
                "smtp": False, "auth": "oauth", "oauth": "microsoft",
                "aide_url": False, "duree_ms": 12, "essais": [], "mx": []}
        assistant = self.env["bf.email.account.setup"].with_user(
            self.employe).create({"email": "elle@entreprise.test"})
        with patch("odoo.addons.bf_email_management.models.bf_email_autoconfig."
                   "BfEmailAutoconfig.decouvrir", lambda self_, a: faux):
            assistant.action_detecter()
        self.assertEqual(assistant.auth_mode, "oauth")
        self.assertFalse(assistant.oauth_pret)

    def test_une_employee_ne_voit_pas_le_jeton_dune_autre(self):
        autre = self.env["res.users"].create({
            "name": "Quelqu'un d'autre", "login": "autre.25862@test.invalid",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])]})
        compte = self.env["bf.email.account"].create({
            "name": "boîte de l'autre", "host": "outlook.office365.com",
            "login": "autre@entreprise.test", "auth_mode": "xoauth2",
            "oauth_provider": "microsoft", "oauth_refresh_token": "secret",
            "user_id": autre.id})
        # La règle d'enregistrement borne la lecture au propriétaire : le
        # compte se comporte comme s'il n'existait pas.
        self.assertFalse(
            self.env["bf.email.account"].with_user(self.employe).search(
                [("id", "=", compte.id)]))


@tagged("post_install", "-at_install")
class TestSecurite(TransactionCase):
    """Revue de sécurité du lot, demandée avant le déploiement multi-locataires.

    Chaque essai ici correspond à une faiblesse MESURÉE sur la production le
    2026-09-20, pas à une inquiétude théorique.
    """

    def test_un_hote_interne_est_refuse(self):
        """🔴 L'assistant et le bouton « Tester » étaient un scanner interne.

        Mesuré depuis un compte employé ordinaire sur la production : le refus
        distinguait `Connection refused` (port fermé) d'une erreur TLS (port
        ouvert), et nommait même le service.
        """
        for hote in ("127.0.0.1", "localhost", "10.0.0.1", "169.254.169.254",
                     "base-interne"):
            permis, motif = bf_email_imap.hote_est_joignable(hote)
            self.assertFalse(permis, f"{hote} devrait être refusé")
            self.assertIn(motif, ("interne", "introuvable"))
        with self.assertRaises(bf_email_imap.ImapConnectionError) as capture:
            bf_email_imap.open_connection("127.0.0.1", 5432, "x", "y", timeout=2)
        self.assertIn("refusé", str(capture.exception))

    def test_la_surface_rpc_des_modeles_abstraits_est_fermee(self):
        """🔴 Un AbstractModel n'a NI table NI contrôle d'accès.

        Aucune règle d'enregistrement, aucune ligne d'`ir.model.access` ne se
        déclenche quand `call_kw` atteint un modèle abstrait : une méthode
        sans tiret bas y est appelable par n'importe quel usager interne
        depuis la console de son navigateur. Deux d'entre elles font signer
        une demande de jeton par le secret client de l'instance.
        """
        attendues = {
            "bf.email.oauth": ["est_configure", "url_de_consentement",
                               "echanger_le_code", "rafraichir",
                               "chaine_xoauth2"],
            "bf.email.autoconfig": ["decouvrir"],
        }
        for modele, methodes in attendues.items():
            for nom in methodes:
                fonction = getattr(type(self.env[modele]), nom)
                self.assertTrue(
                    getattr(fonction, "_api_private", False),
                    f"{modele}.{nom} est appelable par RPC")
        # Et aucune AUTRE méthode publique ne doit apparaître sans qu'on
        # l'ait décidée : c'est la liste qui doit bouger, pas le silence.
        for modele in attendues:
            publiques = [
                nom for nom in dir(type(self.env[modele]))
                if not nom.startswith("_")
                and callable(getattr(type(self.env[modele]), nom, None))
                and getattr(getattr(type(self.env[modele]), nom).__func__
                            if hasattr(getattr(type(self.env[modele]), nom), "__func__")
                            else getattr(type(self.env[modele]), nom),
                            "__module__", "").startswith("odoo.addons.bf_email")
            ]
            inconnues = set(publiques) - set(attendues[modele])
            self.assertFalse(
                inconnues, f"{modele} : méthode publique non décidée {inconnues}")

    def test_un_hote_public_passe_la_garde(self):
        """La garde ne doit pas fermer la porte aux serveurs réels.

        Les quatre hôtes employés par nos locataires sont publics ; si la
        garde les refusait, elle couperait le courrier de tout le monde.
        """
        permis, _motif = bf_email_imap.hote_est_joignable("imap.migadu.com")
        self.assertTrue(permis)

    def test_le_serveur_sortant_ne_recopie_aucun_secret(self):
        """🔴 `smtp_pass` est lisible de tout administrateur des réglages.

        Le mot de passe du compte, lui, n'est lisible que de son propriétaire
        (ir.rule, sans dérogation admin). Le recopier élargissait le cercle.
        """
        compte = self.env["bf.email.account"].create({
            "name": "compte", "host": "imap.migadu.com",
            "login": "moi@chez-nous.test", "password": "secret-du-proprietaire",
            "user_id": self.env.user.id})
        serveur = self.env["ir.mail_server"]._bf_assurer_pour_compte(
            compte, "smtp.migadu.com", 465, "SSL")
        self.assertFalse(serveur.smtp_pass)
        self.assertEqual(serveur.smtp_authentication, "bf_compte")
        self.assertEqual(serveur.bf_account_id, compte)
        # Et le secret n'apparaît nulle part dans la ligne du serveur.
        valeurs = serveur.read()[0]
        self.assertNotIn("secret-du-proprietaire",
                         " ".join(str(v) for v in valeurs.values()))

    def test_le_mot_de_passe_ne_dort_pas_dans_lassistant(self):
        """🔴 Une ligne d'assistant vit une heure par défaut, en clair."""
        self.assertLessEqual(
            self.env["bf.email.account.setup"]._transient_max_hours, 0.5)
        assistant = self.env["bf.email.account.setup"].create({
            "email": "moi@chez-nous.test", "state": "manuel",
            "imap_host": "imap.migadu.com", "password": "mot-de-passe-refuse"})

        def refus(*a, **kw):
            raise bf_email_imap.ImapConnectionError("Authentication failed")

        sql_joue = []
        registre = assistant.pool
        vrai_curseur = registre.cursor

        class CurseurEspion:
            def __enter__(self_):
                self_.cr = vrai_curseur()
                self_.cr.__enter__()
                execute = self_.cr.execute

                def espion(requete, params=None):
                    sql_joue.append(str(requete))
                    return execute(requete, params)

                self_.cr.execute = espion
                return self_.cr

            def __exit__(self_, *a):
                return self_.cr.__exit__(*a)

        with patch.object(bf_email_imap, "open_connection", refus), \
             patch.object(type(registre), "cursor",
                          lambda *a, **kw: CurseurEspion()):
            with self.assertRaises(UserError):
                assistant.action_brancher()
        # 🔴 L'effacement ne peut PAS être vérifié dans la transaction du
        # test : `assertRaises` d'Odoo ouvre un point de sauvegarde et annule
        # le bloc, exactement comme la requête HTTP annule tout quand une
        # `UserError` remonte. C'est d'ailleurs le défaut que le correctif
        # ferme. On vérifie donc que l'effacement passe par un curseur
        # SÉPARÉ, seul moyen qu'il survive à cette annulation.
        self.assertIn("UPDATE bf_email_account_setup SET password = NULL",
                      " ".join(sql_joue),
                      "l'effacement n'a pas été écrit hors de la transaction")

    def test_un_etat_oauth_perime_est_refuse(self):
        """🔴 Un état sans borne de temps se rejoue des mois plus tard."""
        compte = self.env["bf.email.account"].create({
            "name": "outlook", "host": "outlook.office365.com",
            "login": "moi@entreprise.test", "auth_mode": "xoauth2",
            "oauth_provider": "microsoft", "user_id": self.env.user.id})
        Oauth = self.env["bf.email.oauth"]
        import time as _t
        self.assertTrue(Oauth._verifier_etat(Oauth._etat(compte), compte))
        vieux = Oauth._etat(compte, horodatage=int(_t.time()) - 3600)
        self.assertFalse(Oauth._verifier_etat(vieux, compte),
                         "un état d'il y a une heure doit être refusé")
        # Et l'horodatage est DANS la signature : le rajeunir ne suffit pas.
        import json as _j
        trafique = _j.loads(vieux)
        trafique["t"] = int(_t.time())
        self.assertFalse(Oauth._verifier_etat(_j.dumps(trafique), compte))

    def test_le_resolveur_lit_les_pointeurs_de_compression(self):
        """Le client DNS maison doit suivre la compression (RFC 1035).

        Vecteur fabriqué à la main : un nom suivi d'un pointeur vers lui.
        Sans ce contrôle, un MX compressé (le cas courant) se lirait tronqué
        et la voie qui porte 42 points de couverture tomberait en silence.
        """
        Auto = self.env["bf.email.autoconfig"]
        paquet = (b"\x00" * 12
                  + b"\x07example\x03com\x00"      # position 12
                  + b"\xc0\x0c")                    # pointeur vers 12
        nom, suite = Auto._nom_dns(paquet, 12)
        self.assertEqual(nom, "example.com")
        nom2, _ = Auto._nom_dns(paquet, len(paquet) - 2)
        self.assertEqual(nom2, "example.com")
        self.assertEqual(suite, len(paquet) - 2)
        # Une boucle de pointeurs ne doit pas faire tourner le serveur.
        boucle = b"\x00" * 12 + b"\xc0\x0c"
        self.assertEqual(Auto._nom_dns(boucle, 12)[0], "")
