"""L'extension Thunderbird : appariement par Message-ID, contexte, mode code.

Chaque essai porte la valeur qui ferait passer une version fautive : un
Message-ID écrit de l'autre façon, la boîte d'un collègue, un bloc refusé par
les droits rendu comme un bloc vide, une échéance de report déjà passée, et un
appariement qui redirigerait là où on lui dit.
"""
import base64
import hashlib
import json
import re
import secrets
from datetime import datetime, timezone
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import HttpCase, tagged

from .common import build_rfc822

BASE = "/bf_email_management/mobile/v1"
ENTETE = "<thunder-1@test.invalid>"


def _pkce():
    verifier = secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("utf-8")).digest()).decode().rstrip("=")
    return verifier, challenge


@tagged("post_install", "-at_install")
class TestExtensionThunderbird(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # ⚠️ Les deux langues actives : la base de référence est en fr_CA, et
        # sans en_US actif `get_lang` ramène tout le monde au français. Mesuré :
        # la page de consentement sortait en français pour un usager en_US.
        for code in ("en_US", "fr_CA"):
            cls.env["res.lang"]._activate_lang(code)
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        groupe = cls.env.ref("base.group_user")
        cls.owner = Users.create({
            "name": "Porteur Thunderbird", "login": "ext.tb@test.invalid",
            "password": "ext.tb@test.invalid", "email": "ext.tb@test.invalid",
            "tz": "Pacific/Auckland", "lang": "en_US",
            "groups_id": [(6, 0, [groupe.id])],
        })
        cls.francophone = Users.create({
            "name": "Porteuse Francophone", "login": "ext.fr@test.invalid",
            "password": "ext.fr@test.invalid", "email": "ext.fr@test.invalid",
            "lang": "fr_CA", "groups_id": [(6, 0, [groupe.id])],
        })
        cls.voisin = Users.create({
            "name": "Voisine Thunderbird", "login": "ext.voisine@test.invalid",
            "password": "ext.voisine@test.invalid",
            "email": "ext.voisine@test.invalid",
            "groups_id": [(6, 0, [groupe.id])],
        })
        for user in (cls.owner, cls.francophone, cls.voisin):
            cls.env["bf.email.account"].create({
                "name": "Boîte %s" % user.name, "user_id": user.id,
                "host": "imap.test.invalid", "port": 993,
                "login": user.email, "password": "x", "state": "connected",
            })
        cls.client = cls.env["res.partner"].create({
            "name": "Cliente Essai", "email": "cliente@test.invalid",
            "is_company": True,
        })
        cls.email = cls._courriel(cls.owner, ENTETE, partner=cls.client)
        cls.device = cls.env["bf.email.mobile.device"]._issue(
            cls.owner.id, name="Thunderbird (linux)")
        cls.env.cr.flush()

    @classmethod
    def _courriel(cls, user, entete, partner=None, expediteur="cliente@test.invalid"):
        compte = cls.env["bf.email.account"].search(
            [("user_id", "=", user.id)], limit=1)
        return cls.env["bf.email"].with_user(user).create({
            "subject": "Soumission pour l'atelier",
            "email_from": expediteur, "email_to": user.email,
            "direction": "in", "status": "new", "source": "imap",
            "account_id": compte.id, "user_id": user.id, "imap_in_inbox": True,
            "message_id_header": entete, "date": "2026-11-12 20:00:00",
            "partner_id": partner.id if partner else False,
            "raw_rfc822": build_rfc822("Soumission pour l'atelier", expediteur,
                                       user.email, "Bonjour."),
        })

    # ------------------------------------------------------------ outils
    def _auth(self, device=None):
        return {"Authorization": "Bearer %s" % (device or self.device).device_token}

    def _ouvrir(self, entete, headers=None):
        return self.url_open(
            "%s/extension/open?header=%s" % (BASE, entete),
            headers=headers if headers is not None else self._auth(), timeout=30)

    # ------------------------------------------------------- appariement
    def test_les_deux_ecritures_du_message_id_trouvent_la_fiche(self):
        """Thunderbird rend « abc@x », Odoo stocke « <abc@x> »."""
        for ecriture in ("thunder-1@test.invalid", "<thunder-1@test.invalid>",
                         "%3Cthunder-1@test.invalid%3E"):
            with self.subTest(ecriture=ecriture):
                reponse = self._ouvrir(ecriture)
                self.assertEqual(reponse.status_code, 200)
                self.assertEqual(reponse.json()["email"]["id"], self.email.id)

    def test_la_boite_d_une_collegue_reste_invisible(self):
        """Le même courriel reçu par deux personnes existe deux fois : la
        contrainte d'unicité porte l'usager. Sans le filtre, un membre de
        `group_email_admin` ouvrirait la fiche d'autrui depuis son Thunderbird."""
        autre = self._courriel(self.voisin, ENTETE)
        self.env.cr.flush()
        trouve = self.env["bf.email"].with_user(self.owner)._extension_find(ENTETE)
        self.assertEqual(trouve, self.email)
        self.assertNotEqual(trouve, autre)
        vu_par_la_voisine = self.env["bf.email"].with_user(self.voisin)._extension_find(ENTETE)
        self.assertEqual(vu_par_la_voisine, autre)

    def test_un_entete_inconnu_est_un_404(self):
        reponse = self._ouvrir("jamais-vu@test.invalid")
        self.assertEqual(reponse.status_code, 404)
        self.assertEqual(reponse.json()["error"], "not_found")

    def test_un_entete_inutilisable_est_un_400_pas_un_404(self):
        """« Je t'ai mal demandé » ne doit pas se lire « ce courriel n'est pas
        dans Symbifox » : le panneau n'afficherait pas la même phrase."""
        for valeur in ("", "%20", "%3C%3E"):
            with self.subTest(valeur=valeur):
                reponse = self._ouvrir(valeur)
                self.assertEqual(reponse.status_code, 400)
                self.assertEqual(reponse.json()["error"], "missing_header")

    def test_ouvrir_le_panneau_ne_marque_pas_le_courriel_lu(self):
        """⛔ Le panneau s'ouvre sur un clic, pas sur l'affichage : c'est le
        `\\Seen` d'IMAP qui porte la lecture, pas la consultation d'un dossier."""
        avant = self._courriel(self.owner, "<thunder-4@test.invalid>")
        self.env.cr.flush()
        self.assertEqual(avant.status, "new")
        self._ouvrir("thunder-4@test.invalid")
        avant.invalidate_recordset()
        self.assertEqual(avant.status, "new", "la route a marqué le courriel lu")

    def test_un_entete_demesure_est_refuse_avant_la_base(self):
        """🔴 La RFC plafonne une ligne d'en-tête à 998 octets. Sans borne, un
        appelant authentifié pousse un mégaoctet dans le paramètre d'une requête
        SQL à chaque appel, pour un résultat toujours vide."""
        enorme = "a" * 1200 + "@test.invalid"
        self.assertEqual(
            self.env["bf.email"]._extension_header_forms(enorme), [])
        self.assertFalse(self.env["bf.email"]._extension_find(enorme))
        self.assertEqual(self._ouvrir(enorme).status_code, 404)

    def test_un_parametre_mal_forme_ne_sort_pas_en_500(self):
        """Un 500 dit à l'appelant de réessayer indéfiniment une requête fautive."""
        for brut in ("header=%00", "header=a&header=b", "header[]=x"):
            with self.subTest(brut=brut):
                reponse = self.url_open("%s/extension/open?%s" % (BASE, brut),
                                        headers=self._auth(), timeout=30)
                self.assertIn(reponse.status_code, (400, 404))

    def test_sans_jeton_la_route_refuse(self):
        self.assertEqual(self._ouvrir("thunder-1@test.invalid", headers={}).status_code, 401)

    # ------------------------------------------------------------ charge
    def test_les_trois_blocs_arrivent_en_un_seul_appel(self):
        """🔴 Un aller-retour, pas trois : chacun peut coûter plus de 200 ms."""
        data = self._ouvrir("thunder-1@test.invalid").json()
        self.assertEqual(set(data), {"email", "context", "config"})
        self.assertEqual(data["email"]["subject"], "Soumission pour l'atelier")
        self.assertIn("partner", data["context"])
        self.assertIn("snooze_presets", data["config"])
        self.assertEqual(data["config"]["user_name"], "Porteur Thunderbird")

    def test_les_reports_offerts_sont_tous_dans_l_avenir(self):
        """Le piège du lot : `until_ms` est une date ABSOLUE. Une config gardée
        proposerait « dans 1 heure » pour une heure passée, et le courriel
        reviendrait en boîte aussitôt reporté."""
        # ⚠️ `datetime.now(timezone.utc)` et non `fields.Datetime.now()` :
        # celui d'Odoo rend un datetime NAÏF, dont `.timestamp()` suppose le
        # fuseau du conteneur. L'essai passerait ou tomberait selon la machine.
        maintenant = datetime.now(timezone.utc).timestamp() * 1000
        presets = self._ouvrir("thunder-1@test.invalid").json()["config"]["snooze_presets"]
        self.assertTrue(presets)
        for preset in presets:
            self.assertGreater(preset["until_ms"], maintenant, preset["key"])

    def test_la_fiche_porte_une_adresse_web_ouvrable(self):
        """Un client de courriel n'est pas un navigateur : il n'a pas l'adresse
        de la fiche, il faut la lui donner."""
        data = self._ouvrir("thunder-1@test.invalid").json()
        self.assertRegex(data["email"]["url"], r"^https?://.+/odoo/bf\.email/%s$" % self.email.id)

    # ----------------------------------------------------------- contexte
    def test_un_bloc_refuse_est_nomme_et_pas_rendu_vide(self):
        """🔴 « Aucune tâche ouverte » et « je n'ai pas le droit de regarder »
        sont deux réponses opposées. Un panneau qui les confond fait chercher
        une facture qui existe."""
        courriel = self.email.with_user(self.owner)
        with patch.object(type(courriel), "_extension_tasks",
                          side_effect=AccessError("non")):
            contexte = courriel._extension_context()
        self.assertEqual(contexte["tasks"], [])
        self.assertIn("tasks", contexte["restricted"])

    def test_un_employe_sans_la_comptabilite_voit_le_refus_nomme(self):
        """Mesuré sur une base d'essai, pas supposé : un usager interne simple peut
        ne PAS avoir le droit de lire `account.move` ni `hour.bank.client`. Le
        panneau doit donc nommer ces deux blocs, et le reste s'afficher quand
        même."""
        contexte = self.email.with_user(self.owner)._extension_context()
        self.assertIn("invoices", contexte["restricted"])
        self.assertIn("hour_bank", contexte["restricted"])
        self.assertEqual(contexte["invoices"], [])
        self.assertIsNone(contexte["hour_bank"])
        # Le refus d'un bloc n'emporte pas les autres.
        self.assertIsNotNone(contexte["partner"])
        self.assertNotIn("tasks", contexte["restricted"])

    def test_le_correspondant_se_retrouve_par_son_adresse_sans_rattachement(self):
        """Un courriel sans `partner_id` a souvent quand même une fiche : c'est
        elle qui porte le dossier du client."""
        orphelin = self._courriel(self.owner, "<thunder-2@test.invalid>", partner=None)
        self.env.cr.flush()
        partner = orphelin.with_user(self.owner)._extension_partner()
        self.assertEqual(partner, self.client)

    def test_une_adresse_inconnue_ne_rend_aucun_client_sans_tomber(self):
        inconnu = self._courriel(self.owner, "<thunder-3@test.invalid>", partner=None,
                                 expediteur="personne-ici@test.invalid")
        self.env.cr.flush()
        contexte = inconnu.with_user(self.owner)._extension_context()
        self.assertIsNone(contexte["partner"])
        self.assertEqual(contexte["tasks"], [])
        self.assertEqual(contexte["restricted"], [])

    def test_une_tache_fermee_n_est_pas_une_tache_ouverte(self):
        projet = self.env["project.project"].create({"name": "Projet"})
        Task = self.env["project.task"]
        ouverte = Task.create({
            "name": "Préparer la soumission", "project_id": projet.id,
            "partner_id": self.client.id, "state": "01_in_progress",
        })
        Task.create({
            "name": "Vieille tâche finie", "project_id": projet.id,
            "partner_id": self.client.id, "state": "1_done",
        })
        self.env.cr.flush()
        taches = self.email.with_user(self.owner)._extension_tasks(self.client)
        noms = [t["name"] for t in taches]
        self.assertTrue(any("Préparer la soumission" in n for n in noms))
        self.assertFalse(any("Vieille tâche finie" in n for n in noms))
        self.assertTrue(all(t["url"] for t in taches))
        self.assertEqual([t["id"] for t in taches if "Préparer" in t["name"]], [ouverte.id])

    def test_les_taches_d_une_personne_de_l_entreprise_comptent(self):
        """Le dossier d'un client ne se range pas sous la seule fiche société."""
        personne = self.env["res.partner"].create({
            "name": "Marie Essai", "parent_id": self.client.id,
            "email": "marie@test.invalid",
        })
        projet = self.env["project.project"].create({"name": "Projet b"})
        self.env["project.task"].create({
            "name": "Rappeler Marie", "project_id": projet.id,
            "partner_id": personne.id, "state": "01_in_progress",
        })
        self.env.cr.flush()
        taches = self.email.with_user(self.owner)._extension_tasks(self.client)
        self.assertTrue(any("Rappeler Marie" in t["name"] for t in taches))

    def test_une_facture_payee_ou_brouillon_n_est_pas_une_impayee(self):
        Move = self.env["account.move"]
        # ⚠️ Mesuré sur une base d'essai : le compte de produits par défaut de cette base
        # (« Sales of Goods », 411100) est DÉPRÉCIÉ, et `action_post` refuse.
        # On en choisit un vivant plutôt que de laisser l'essai rouge sur une
        # propriété des données.
        compte = self.env["account.account"].search(
            [("deprecated", "=", False), ("account_type", "=", "income")], limit=1)
        if not compte:
            self.skipTest("aucun compte de produits vivant dans cette base")
        ligne = [(0, 0, {"name": "Atelier", "quantity": 1, "price_unit": 250,
                         "account_id": compte.id})]
        brouillon = Move.create({
            "move_type": "out_invoice", "partner_id": self.client.id,
            "invoice_date": "2026-11-01", "invoice_line_ids": ligne,
        })
        due = Move.create({
            "move_type": "out_invoice", "partner_id": self.client.id,
            "invoice_date": "2026-11-01", "invoice_line_ids": ligne,
        })
        due.action_post()
        self.env.cr.flush()
        # Lues avec les droits qu'il faut : l'usager simple n'a pas la
        # comptabilité, et c'est l'essai du refus qui mesure ce cas-là.
        lues = self.email.with_user(
            self.env.ref("base.user_admin"))._extension_invoices(self.client)
        ids = [f["id"] for f in lues]
        self.assertIn(due.id, ids, "une facture publiée et due doit paraître")
        self.assertNotIn(brouillon.id, ids, "un brouillon n'est pas une dette")
        impayee = next(f for f in lues if f["id"] == due.id)
        self.assertGreater(impayee["residual"], 0)
        self.assertTrue(impayee["currency"])
        self.assertTrue(impayee["url"])

    def test_la_banque_d_heures_suit_le_client_quand_on_a_le_droit_de_la_lire(self):
        if "hour.bank.client" not in self.env:
            self.skipTest("bf_hour_bank absent de cette instance")
        banque = self.env["hour.bank.client"].create({"partner_id": self.client.id})
        self.env.cr.flush()
        # Avec les droits (ici l'administrateur), le bloc arrive entier...
        lu = self.email.with_user(self.env.ref("base.user_admin"))._extension_hour_bank(
            self.client)
        self.assertEqual(lu["id"], banque.id)
        self.assertIn("balance", lu)
        self.assertTrue(lu["url"])
        # ... et sans les droits, c'est un refus nommé, pas un solde de zéro.
        contexte = self.email.with_user(self.owner)._extension_context()
        self.assertIsNone(contexte["hour_bank"])
        self.assertIn("hour_bank", contexte["restricted"])

    def test_le_dossier_ou_le_courriel_est_classe_est_nomme(self):
        projet = self.env["project.project"].create({"name": "Projet c"})
        tache = self.env["project.task"].create({
            "name": "Dossier du fil", "project_id": projet.id,
        })
        self.email.write({"res_model": "project.task", "res_id": tache.id})
        self.env.cr.flush()
        classe = self.email.with_user(self.owner)._extension_linked_dict()
        self.assertEqual(classe["model"], "project.task")
        self.assertEqual(classe["id"], tache.id)
        self.assertTrue(classe["url"].endswith("/odoo/project.task/%s" % tache.id))

    # ------------------------------------------------- appariement par code
    def _start(self, login="ext.tb@test.invalid", **extra):
        self.authenticate(login, login)
        params = {"mode": "code", "code_challenge_method": "S256", **extra}
        query = "&".join("%s=%s" % (k, v) for k, v in params.items())
        return self.url_open(BASE + "/auth/start?" + query, timeout=30,
                             allow_redirects=False)

    @staticmethod
    def _champs(page):
        return dict(re.findall(r'name="([a-z_]+)" value="([^"]*)"', page))

    def _repondre(self, champs, decision):
        return self.url_open(BASE + "/auth/consent",
                             data=dict(champs, decision=decision),
                             timeout=30, allow_redirects=False)

    def test_la_page_de_consentement_parle_la_langue_de_l_usager(self):
        """La chaîne neuve est traduite : la preuve que le `fr.po` est posé."""
        _v, defi = _pkce()
        page = self._start(code_challenge=defi,
                           login="ext.fr@test.invalid").text
        self.assertIn("Un client Symbifox demande l", page)
        self.assertIn('lang="fr"', page)

    def test_le_mode_code_affiche_le_consentement_sans_rediriger(self):
        _v, defi = _pkce()
        reponse = self._start(code_challenge=defi, device_name="Thunderbird%20(linux)")
        self.assertEqual(reponse.status_code, 200)
        self.assertFalse(reponse.headers.get("Location"))
        page = reponse.text
        self.assertIn("Thunderbird (linux)", page)
        self.assertIn("A Symbifox client wants to access your account", page)
        self.assertNotIn("Symbifox Mobile wants", page)
        champs = self._champs(page)
        self.assertEqual(champs["mode"], "code", "le POST doit rester en mode code")
        self.assertEqual(champs["redirect"], "")
        self.assertTrue(champs["csrf_token"])

    def test_le_code_s_affiche_puis_s_echange_contre_un_jeton(self):
        verif, defi = _pkce()
        champs = self._champs(self._start(code_challenge=defi).text)
        reponse = self._repondre(champs, "allow")
        self.assertEqual(reponse.status_code, 200, "aucune redirection en mode code")
        self.assertFalse(reponse.headers.get("Location"))
        code = re.search(r"<strong>([^<]+)</strong>", reponse.text)
        self.assertTrue(code, "la page doit porter le code")
        echange = self.url_open(
            BASE + "/auth/exchange",
            data=json.dumps({"code": code.group(1), "code_verifier": verif}).encode(),
            headers={"Content-Type": "application/json"}, timeout=30)
        self.assertEqual(echange.status_code, 200)
        self.assertTrue(echange.json()["token"])

    def test_un_code_ne_s_echange_pas_sans_le_bon_verificateur(self):
        """PKCE porte tout le poids ici : le code voyage par le presse-papier."""
        _verif, defi = _pkce()
        champs = self._champs(self._start(code_challenge=defi).text)
        code = re.search(r"<strong>([^<]+)</strong>",
                         self._repondre(champs, "allow").text).group(1)
        echange = self.url_open(
            BASE + "/auth/exchange",
            data=json.dumps({"code": code, "code_verifier": "pas-le-bon"}).encode(),
            headers={"Content-Type": "application/json"}, timeout=30)
        self.assertEqual(echange.status_code, 401)

    def test_le_mode_code_exige_toujours_pkce(self):
        reponse = self._start()
        self.assertEqual(reponse.status_code, 200)
        self.assertFalse(reponse.headers.get("Location"), "rien ne redirige en mode code")
        self.assertIn("PKCE", reponse.text)
        self.assertNotIn("<strong>", reponse.text, "aucun code n'a été émis")

    def test_le_mode_code_ignore_une_redirection_qu_on_lui_glisse(self):
        """Les deux ensemble n'ouvrent pas de redirection : le mode gagne."""
        _v, defi = _pkce()
        champs = self._champs(self._start(
            code_challenge=defi, redirect="https://malveillant.test/vol").text)
        self.assertEqual(champs["redirect"], "")
        reponse = self._repondre(champs, "allow")
        self.assertEqual(reponse.status_code, 200)
        self.assertNotIn("malveillant", reponse.text)
        self.assertFalse(reponse.headers.get("Location"))

    def test_un_refus_en_mode_code_n_emet_rien_et_ne_redirige_pas(self):
        _v, defi = _pkce()
        champs = self._champs(self._start(code_challenge=defi).text)
        avant = self.env["bf.email.mobile.device"].sudo().search_count(
            [("pending_code", "!=", False)])
        reponse = self._repondre(champs, "deny")
        self.assertEqual(reponse.status_code, 200)
        self.assertFalse(reponse.headers.get("Location"))
        self.assertNotIn("<strong>", reponse.text)
        self.assertEqual(
            self.env["bf.email.mobile.device"].sudo().search_count(
                [("pending_code", "!=", False)]), avant)

    AVERTISSEMENT = "never copy the code shown next, because someone asked you to"

    def test_le_mode_code_avertit_de_ne_rien_recopier_sur_demande(self):
        """N'importe qui peut fabriquer le lien du mode code avec son propre
        défi PKCE : la page doit dire, au moment de décider, de ne recopier le
        code que pour une application qu'on vient soi-même d'ouvrir."""
        _v, defi = _pkce()
        page = self._start(code_challenge=defi).text
        self.assertIn(self.AVERTISSEMENT, page)
        self.assertIn('class="warn" role="alert"', page)
        self.assertNotIn("app on your phone", page,
                         "la phrase du téléphone ne vaut pas pour un client de bureau")

    def test_l_avertissement_du_mode_code_est_traduit(self):
        _v, defi = _pkce()
        page = self._start(code_challenge=defi, login="ext.fr@test.invalid").text
        self.assertIn("ne recopiez jamais le code qui suit", page)

    def test_le_code_affiche_porte_l_avertissement(self):
        _v, defi = _pkce()
        champs = self._champs(self._start(code_challenge=defi).text)
        page = self._repondre(champs, "allow").text
        self.assertRegex(page, r"<strong>[^<]+</strong>", "le code doit être affiché")
        self.assertIn("Never give it to someone who asks for it.", page)

    def test_la_page_du_telephone_ne_porte_pas_l_avertissement(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_email_management.mobile_redirect_schemes", "odooinbox://")
        _v, defi = _pkce()
        self.authenticate("ext.tb@test.invalid", "ext.tb@test.invalid")
        page = self.url_open(
            "%s/auth/start?redirect=odooinbox://auth&state=x&code_challenge=%s"
            "&code_challenge_method=S256" % (BASE, defi),
            timeout=30, allow_redirects=False).text
        self.assertIn("app on your phone", page)
        self.assertNotIn(self.AVERTISSEMENT, page)
        self.assertNotIn('class="warn"', page)

    def test_la_redirection_du_telephone_marche_encore(self):
        """Le mode code ne doit rien changer au chemin de l'application."""
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_email_management.mobile_redirect_schemes", "odooinbox://")
        _v, defi = _pkce()
        self.authenticate("ext.tb@test.invalid", "ext.tb@test.invalid")
        reponse = self.url_open(
            "%s/auth/start?redirect=odooinbox://auth&state=x&code_challenge=%s"
            "&code_challenge_method=S256" % (BASE, defi),
            timeout=30, allow_redirects=False)
        self.assertEqual(reponse.status_code, 200)
        champs = self._champs(reponse.text)
        self.assertEqual(champs["mode"], "")
        self.assertEqual(champs["redirect"], "odooinbox://auth")
        suite = self._repondre(champs, "allow")
        self.assertEqual(suite.status_code, 303)
        self.assertIn("code=", suite.headers["Location"])

    # ------------------------------------------------- signatures
    def _identites(self, headers=None):
        return self.url_open(
            "%s/extension/identities" % BASE,
            headers=headers if headers is not None else self._auth(), timeout=30)

    def _poser_identite(self, user, email, **valeurs):
        return self.env["bf.email.identity"].sudo().create(dict({
            "user_id": user.id, "email": email, "name": user.name,
            "verified": True, "active": True,
        }, **valeurs))

    def test_les_identites_sortent_avec_leur_signature(self):
        self._poser_identite(self.owner, "porte-voix@test.invalid",
                             signature_html="<p>Porte-voix</p>")
        reponse = self._identites()
        self.assertEqual(reponse.status_code, 200)
        par_adresse = {i["email"]: i for i in reponse.json()["identities"]}
        self.assertIn("porte-voix@test.invalid", par_adresse)
        self.assertEqual(par_adresse["porte-voix@test.invalid"]["signature_html"],
                         "<p>Porte-voix</p>")

    def test_une_identite_non_verifiee_ne_descend_pas_sur_le_poste(self):
        """Elle poserait une signature pour une adresse que le serveur refuse.

        Le poste promettrait alors ce que Symbifox interdit : on peut écrire au
        nom de cette adresse dans Thunderbird, et l'envoi par Odoo serait rejeté.
        """
        self._poser_identite(self.owner, "pas-prouvee@test.invalid",
                             verified=False, signature_html="<p>Non</p>")
        adresses = [i["email"] for i in self._identites().json()["identities"]]
        self.assertNotIn("pas-prouvee@test.invalid", adresses)

    def test_une_identite_archivee_ne_descend_pas_non_plus(self):
        self._poser_identite(self.owner, "retiree@test.invalid", active=False)
        adresses = [i["email"] for i in self._identites().json()["identities"]]
        self.assertNotIn("retiree@test.invalid", adresses)

    def test_les_identites_d_une_collegue_restent_invisibles(self):
        """La boîte du voisin est déjà cloisonnée ; sa signature doit l'être."""
        self._poser_identite(self.voisin, "voisine-identite@test.invalid",
                             signature_html="<p>Voisine</p>")
        corps = self._identites().text
        self.assertNotIn("voisine-identite@test.invalid", corps)
        self.assertNotIn("Voisine", corps)

    def test_sans_signature_propre_l_identite_reprend_celle_du_compte(self):
        """Le repli de `_signature_for` : une identité neuve ne doit pas faire
        disparaître la signature que la personne avait déjà."""
        self.owner.sudo().signature = "<p>Signature du compte</p>"
        self._poser_identite(self.owner, "sans-la-sienne@test.invalid",
                             signature_html=False)
        par_adresse = {i["email"]: i for i in self._identites().json()["identities"]}
        self.assertEqual(par_adresse["sans-la-sienne@test.invalid"]["signature_html"],
                         "<p>Signature du compte</p>")

    def test_la_signature_propre_l_emporte_sur_celle_du_compte(self):
        self.owner.sudo().signature = "<p>Signature du compte</p>"
        self._poser_identite(self.owner, "la-sienne@test.invalid",
                             signature_html="<p>La sienne</p>")
        par_adresse = {i["email"]: i for i in self._identites().json()["identities"]}
        self.assertEqual(par_adresse["la-sienne@test.invalid"]["signature_html"],
                         "<p>La sienne</p>")

    def test_aucun_secret_ne_descend_avec_les_identites(self):
        """Le modèle porte `mail_server_id`, et un serveur sortant traîne des
        identifiants. La charge ne rend que ce qui s'affiche dans un courriel."""
        self._poser_identite(self.owner, "avec-serveur@test.invalid")
        permises = {"id", "email", "email_normalized", "name",
                    "signature_html", "is_default"}
        for identite in self._identites().json()["identities"]:
            self.assertEqual(set(identite) - permises, set(),
                             "une clé de trop dans la charge des identités")

    def test_l_adresse_normalisee_descend_pour_l_appariement(self):
        """Le poste apparie sur elle : Thunderbird écrit « Nom <A@B.COM> »."""
        self._poser_identite(self.owner, "Majuscules@TEST.invalid")
        par_id = {i["id"]: i for i in self._identites().json()["identities"]}
        ligne = next(i for i in par_id.values()
                     if i["email"] == "Majuscules@TEST.invalid")
        self.assertEqual(ligne["email_normalized"], "majuscules@test.invalid")

    def test_les_identites_refusent_un_appel_sans_jeton(self):
        self.assertEqual(self._identites(headers={}).status_code, 401)

    def test_la_signature_ne_voyage_pas_a_chaque_ouverture_du_panneau(self):
        """🔴 Route séparée à dessein : `/extension/open` s'appelle à chaque
        courriel affiché, et une vraie signature peut peser huit kilo-octets."""
        self.owner.sudo().signature = "<p>%s</p>" % ("x" * 4000)
        charge = self._ouvrir(ENTETE).json()
        self.assertNotIn("signature", json.dumps(charge["config"]))
        self.assertNotIn("xxxx", self._ouvrir(ENTETE).text)

    # ---------------------------------------------------------- les totaux
    # 🔴 Tout ce bloc existe pour un seul défaut, signalé en usage :
    # « ce client a plus de factures impayées que ce que je vois ».
    # Le panneau affichait « 5 » pour un client qui en avait dix-sept, parce que
    # le compte était `len(liste_tronquee)`. Les essais d'avant ne pouvaient pas
    # le voir : ils ne créaient jamais plus de factures que le plafond.
    def _porteur_comptable(self):
        """Le porteur du courriel, doté du droit de LIRE la comptabilité.

        🔴 Pas l'administrateur : `bf.email` porte un garde de propriété, et
        l'admin n'a pas le droit de lire le courriel d'un autre usager. Mesuré
        sur une base d'essai, en AccessError sur `self.partner_id` — le garde fait son
        travail, c'est l'essai qui se trompait de compte. Et l'inverse est tout
        aussi faux : le porteur SANS ce groupe est ce que mesure l'essai du bloc
        refusé, donc le droit s'ajoute ici, par essai, jamais dans le fixture
        de la classe.
        """
        self.owner.sudo().groups_id = [
            (4, self.env.ref("account.group_account_readonly").id)]
        return self.owner

    def _compte_de_produits(self):
        compte = self.env["account.account"].search(
            [("deprecated", "=", False), ("account_type", "=", "income")], limit=1)
        if not compte:
            self.skipTest("aucun compte de produits vivant dans cette base")
        return compte

    def _facture_due(self, montant=100, echeance="2026-12-31", devise=None):
        """Une facture de vente publiée et impayée, pour le client de l'essai."""
        valeurs = {
            "move_type": "out_invoice", "partner_id": self.client.id,
            "invoice_date": "2026-11-01", "invoice_date_due": echeance,
            "invoice_line_ids": [(0, 0, {
                "name": "Atelier", "quantity": 1, "price_unit": montant,
                "account_id": self._compte_de_produits().id})],
        }
        if devise:
            valeurs["currency_id"] = devise.id
        facture = self.env["account.move"].sudo().create(valeurs)
        facture.action_post()
        return facture

    def test_le_total_des_factures_ne_depend_pas_du_plafond_d_affichage(self):
        """L'essai qui aurait attrapé le défaut : plus de factures que le plafond.

        🔴 Le plafond est lu dans le module, pas recopié : un essai qui écrirait
        « 5 » resterait vert le jour où le plafond change, et c'est précisément
        la confusion entre le plafond et le compte qui a produit le défaut.
        """
        from ..models.bf_email_extension import LIGNES_MAX
        combien = LIGNES_MAX + 2
        for i in range(combien):
            self._facture_due(montant=100 + i, echeance="2026-12-%02d" % (10 + i))
        self.env.cr.flush()
        contexte = self.email.with_user(self._porteur_comptable())._extension_context()
        total = contexte["totals"]["invoices"]
        self.assertEqual(total["count"], combien,
                         "le total doit compter EN BASE, pas les lignes rendues")
        self.assertGreater(total["count"], LIGNES_MAX,
                           "l'essai ne mesure rien s'il reste sous le plafond")
        devise = total["currencies"][0]
        self.assertAlmostEqual(
            devise["residual"], sum(100 + i for i in range(combien)), places=2,
            msg="le montant dû doit porter sur TOUTES les factures")

    def test_le_total_depasse_meme_ce_qui_voyage(self):
        """Le compte reste exact quand la liste envoyée est plafonnée.

        Le plafond d'envoi est rabaissé au lieu de créer vingt-et-une factures :
        la mesure est la même et l'essai ne coûte pas une minute de plus.
        """
        for i in range(3):
            self._facture_due(montant=50, echeance="2026-12-%02d" % (10 + i))
        self.env.cr.flush()
        porteur = self._porteur_comptable()
        with patch("odoo.addons.bf_email_management.models.bf_email_extension"
                   ".LIGNES_ENVOYEES", 1):
            contexte = self.email.with_user(porteur)._extension_context()
        self.assertEqual(len(contexte["invoices"]), 1, "le plafond doit mordre")
        self.assertEqual(contexte["totals"]["invoices"]["count"], 3,
                         "le total ne doit RIEN devoir au plafond")

    def test_les_montants_ne_se_somment_pas_entre_devises(self):
        """🔴 Additionner deux devises rend un nombre qui n'existe pas.

        Et il s'afficherait avec l'air d'un total : la même faute qu'on corrige,
        en plus grave, parce qu'elle serait invisible à l'œil.
        """
        usd = self.env.ref("base.USD")
        if not usd.active:
            usd.sudo().active = True
        self._facture_due(montant=100)
        self._facture_due(montant=200, devise=usd)
        self.env.cr.flush()
        total = self.email.with_user(
            self._porteur_comptable())._extension_context()["totals"]["invoices"]
        par_devise = {d["currency"]: d for d in total["currencies"]}
        self.assertEqual(len(par_devise), 2, "deux devises, deux lignes")
        self.assertEqual(total["count"], 2, "les COMPTES, eux, s'additionnent")
        for cle, valeur in total.items():
            if cle == "currencies":
                continue
            self.assertNotIsInstance(
                valeur, float,
                f"`{cle}` est un montant hors devise : il ne doit pas exister")

    def test_une_facture_echue_est_dite_echue(self):
        """Le retard est calculé au SERVEUR : le poste n'est pas dans son fuseau.

        Un usager peut travailler à l'autre bout du monde ; « en retard » comparé à
        la date du poste rougit une facture due aujourd'hui.
        """
        passee = self._facture_due(montant=100, echeance="2020-01-15")
        venir = self._facture_due(montant=100, echeance="2099-01-15")
        self.env.cr.flush()
        contexte = self.email.with_user(self._porteur_comptable())._extension_context()
        par_id = {f["id"]: f for f in contexte["invoices"]}
        self.assertTrue(par_id[passee.id]["overdue"])
        self.assertFalse(par_id[venir.id]["overdue"])
        total = contexte["totals"]["invoices"]
        self.assertEqual(total["overdue_count"], 1)
        self.assertEqual(total["count"], 2)

    def test_une_tache_en_retard_est_comptee_a_part(self):
        """🔴 L'essai qui a trouvé le `TypeError`.

        `project.task.date_deadline` est un DATETIME en Odoo 18, pas une date :
        le comparer à `fields.Date.context_today` lève. Les essais d'avant ne
        posaient aucune échéance, donc rien ne l'avait vu, et le panneau serait
        tombé en 500 sur le premier client qui en a une — c'est-à-dire presque
        tous. Une échéance du JOUR mesure la seconde moitié de la règle : un module de
        fuseau les normalise à 06 h locale, et une tâche due aujourd'hui ne doit pas
        devenir rouge à 06 h 01.
        """
        projet = self.env["project.project"].create({"name": "Projet totaux"})
        Task = self.env["project.task"]
        tard = Task.create({"name": "En retard", "project_id": projet.id,
                            "partner_id": self.client.id,
                            "date_deadline": "2020-01-15 06:00:00"})
        plus_tard = Task.create({"name": "Plus tard", "project_id": projet.id,
                                 "partner_id": self.client.id,
                                 "date_deadline": "2099-01-15 06:00:00"})
        # Aujourd'hui dans le fuseau du porteur (Auckland), à 06 h locale,
        # exactement comme `bf_timezone` les pose.
        local = fields.Datetime.context_timestamp(
            self.email.with_user(self.owner), fields.Datetime.now())
        ce_matin = local.replace(hour=6, minute=0, second=0, microsecond=0)
        aujourd_hui = Task.create({
            "name": "Due aujourd'hui", "project_id": projet.id,
            "partner_id": self.client.id,
            "date_deadline": ce_matin.astimezone(timezone.utc).replace(tzinfo=None),
        })
        Task.create({"name": "Sans échéance", "project_id": projet.id,
                     "partner_id": self.client.id})
        self.env.cr.flush()
        contexte = self.email.with_user(self.owner)._extension_context()
        total = contexte["totals"]["tasks"]
        self.assertEqual(total["count"], 4)
        self.assertEqual(total["overdue_count"], 1,
                         "ni l'échéance absente ni celle du jour ne sont des retards")
        par_id = {t["id"]: t for t in contexte["tasks"]}
        self.assertTrue(par_id[tard.id]["overdue"])
        self.assertFalse(par_id[plus_tard.id]["overdue"])
        self.assertFalse(par_id[aujourd_hui.id]["overdue"],
                         "une tâche due aujourd'hui n'est pas en retard")
        # 🔴 Le compte et les drapeaux doivent lire le MÊME seuil : sinon le
        # titre dit « 1 en retard » au-dessus d'une liste sans ligne marquée.
        self.assertEqual(
            sum(1 for t in contexte["tasks"] if t["overdue"]), total["overdue_count"],
            "le compte des retards et les drapeaux des lignes divergent")

    def test_un_total_refuse_n_accompagne_pas_une_liste_vide(self):
        """🔴 Le défaut d'aujourd'hui à l'envers, et il serait pire.

        Un total posé quand la lecture a échoué afficherait « 17 factures » en
        titre d'une liste vide : le panneau dirait au client qu'il doit de
        l'argent sans pouvoir nommer une seule facture.
        """
        from ..models.bf_email_extension import BfEmail
        with patch.object(BfEmail, "_extension_invoices_total",
                          side_effect=AccessError("non")):
            contexte = self.email.with_user(self.owner)._extension_context()
        self.assertNotIn("invoices", contexte["totals"])
        self.assertEqual(contexte["invoices"], [])
        self.assertIn("invoices", contexte["restricted"])

    def test_la_liste_et_le_total_lisent_le_meme_domaine(self):
        """La garde structurelle : un seul domaine, pas deux copies.

        Deux copies ne divergent pas le jour où on les écrit, elles divergent
        celui où l'une des deux change. Le domaine est donc détourné une fois,
        et les DEUX lectures doivent suivre.
        """
        from ..models.bf_email_extension import BfEmail
        self._facture_due(montant=100)
        self.env.cr.flush()
        porteur = self._porteur_comptable()
        avant = self.email.with_user(porteur)._extension_context()
        self.assertGreaterEqual(avant["totals"]["invoices"]["count"], 1)
        with patch.object(BfEmail, "_extension_invoices_domain",
                          return_value=[("id", "=", 0)]):
            apres = self.email.with_user(porteur)._extension_context()
        self.assertEqual(apres["invoices"], [], "la liste doit suivre le domaine")
        self.assertEqual(apres["totals"]["invoices"]["count"], 0,
                         "le total aussi : sinon il a sa propre copie du domaine")

    # ------------------------------------------------ l'appariement en lot
    def _resoudre(self, entetes, headers=None):
        return self.url_open(
            "%s/extension/resolve" % BASE,
            data=json.dumps({"headers": entetes}),
            headers={**(headers if headers is not None else self._auth()),
                     "Content-Type": "application/json"}, timeout=30)

    def test_le_lot_apparie_plusieurs_entetes_en_un_appel(self):
        """🔴 UN aller-retour, et c'est toute la raison d'être de la route.

        Vingt appels à `/extension/open` coûteraient plusieurs secondes de latence
        pure depuis un poste lointain, et vingt fois onze kilo-octets de contexte
        client dont la sélection multiple n'a aucun usage.
        """
        deuxieme = self._courriel(self.owner, "<thunder-lot-2@test.invalid>",
                                  partner=self.client)
        self.env.cr.flush()
        # ⚠️ Les deux écritures du même en-tête : Thunderbird rend « a@b »,
        # Odoo stocke « <a@b> ». Les deux doivent apparier.
        rep = self._resoudre([ENTETE.strip("<>"), "<thunder-lot-2@test.invalid>"])
        self.assertEqual(rep.status_code, 200)
        par_id = {c["id"]: c for c in rep.json()["emails"]}
        self.assertIn(self.email.id, par_id)
        self.assertIn(deuxieme.id, par_id)
        # L'en-tête rendu est celui QU'ON A DEMANDÉ : c'est la clé avec laquelle
        # le poste retrouvera son message, pas la forme stockée par Odoo.
        self.assertEqual(par_id[self.email.id]["header"], ENTETE.strip("<>"))

    def test_le_lot_ne_rend_pas_le_courriel_d_un_collegue(self):
        autre = self._courriel(self.voisin, "<thunder-lot-voisine@test.invalid>")
        self.env.cr.flush()
        rendus = self._resoudre(["<thunder-lot-voisine@test.invalid>"]).json()["emails"]
        self.assertEqual(rendus, [], "le garde de propriété vaut aussi en lot")
        self.assertTrue(autre.exists())

    def _administrateur_courriel(self):
        """Un membre de `group_email_admin` : la règle de lecture totale lui
        ouvre toutes les boîtes dans l'ORM, et c'est elle qu'on met à l'essai."""
        admin = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Administratrice Courriel", "login": "ext.admin@test.invalid",
            "password": "ext.admin@test.invalid", "email": "ext.admin@test.invalid",
            "groups_id": [(6, 0, [
                self.env.ref("base.group_user").id,
                self.env.ref("bf_email_management.group_email_admin").id,
            ])],
        })
        self.env["bf.email.account"].create({
            "name": "Boîte admin", "user_id": admin.id,
            "host": "imap.test.invalid", "port": 993,
            "login": admin.email, "password": "x", "state": "connected",
        })
        return admin

    def test_le_lot_d_un_administrateur_ne_rend_que_sa_copie(self):
        """Le même Message-ID dans deux boîtes : l'administrateur courriel
        reçoit sa fiche, jamais celle de sa collègue."""
        admin = self._administrateur_courriel()
        entete = "<thunder-lot-admin@test.invalid>"
        sienne = self._courriel(admin, entete)
        collegue = self._courriel(self.voisin, entete)
        self.env.cr.flush()
        # La règle de lecture totale est bien là : sans elle, l'essai ne
        # mesurerait rien.
        self.assertTrue(collegue.with_user(admin).has_access("read"))
        rendu = self.env["bf.email"].with_user(admin).extension_resolve([entete])
        self.assertEqual([c["id"] for c in rendu["emails"]], [sienne.id])

    def test_le_lot_d_un_administrateur_ignore_la_seule_copie_d_une_collegue(self):
        admin = self._administrateur_courriel()
        entete = "<thunder-lot-admin-seule@test.invalid>"
        collegue = self._courriel(self.voisin, entete)
        self.env.cr.flush()
        self.assertTrue(collegue.with_user(admin).has_access("read"))
        rendu = self.env["bf.email"].with_user(admin).extension_resolve([entete])
        self.assertEqual(rendu["emails"], [])

    def test_le_lot_compte_les_retards_du_client(self):
        self._facture_due(montant=100, echeance="2020-01-15")
        self._facture_due(montant=100, echeance="2099-01-15")
        self.env.cr.flush()
        porteur = self._porteur_comptable()
        rendu = self.email.with_user(porteur).extension_resolve([ENTETE])["emails"]
        self.assertEqual(len(rendu), 1)
        self.assertEqual(rendu[0]["overdue_invoices"], 1)
        self.assertEqual(rendu[0]["overdue_tasks"], 0)
        self.assertFalse(rendu[0]["partial"])

    def test_un_compte_refuse_rend_None_et_le_dit(self):
        """🔴 Jamais zéro pour « je n'ai pas pu regarder ».

        La pastille du bouton est un CRI : crier « 0 » quand la comptabilité
        est refusée est la faute que la 18.0.11.73.0 corrige ailleurs, et elle
        serait pire ici, parce qu'aucun écran ne l'explique.
        """
        self._facture_due(montant=100, echeance="2020-01-15")
        self.env.cr.flush()
        # Le porteur SANS le groupe comptable : c'est le cas réel.
        rendu = self.email.with_user(self.owner).extension_resolve([ENTETE])["emails"]
        self.assertEqual(len(rendu), 1)
        self.assertIsNone(rendu[0]["overdue_invoices"])
        self.assertTrue(rendu[0]["partial"], "le refus doit être AVOUÉ")
        self.assertIsNotNone(rendu[0]["overdue_tasks"],
                             "le bloc non refusé garde son compte")

    def test_le_lot_ne_compte_qu_une_fois_par_client(self):
        """Dix courriels du même correspondant ne coûtent pas dix comptages."""
        entetes = [ENTETE]
        for i in range(4):
            entete = "<thunder-meme-client-%d@test.invalid>" % i
            self._courriel(self.owner, entete, partner=self.client)
            entetes.append(entete)
        self.env.cr.flush()
        porteur = self._porteur_comptable()
        from ..models.bf_email_extension import BfEmail
        vrai = BfEmail._extension_retards
        appels = []

        def compter(self, client):
            appels.append(client.id if client else 0)
            return vrai(self, client)

        with patch.object(BfEmail, "_extension_retards", compter):
            rendu = self.email.with_user(porteur).extension_resolve(entetes)
        self.assertEqual(len(rendu["emails"]), 5)
        self.assertEqual(len(appels), 1,
                         "un seul comptage pour cinq courriels du même client")

    def test_le_lot_est_borne(self):
        from ..models.bf_email_extension import LOT_MAX
        trop = ["<rien-%d@test.invalid>" % i for i in range(LOT_MAX + 50)]
        # Ne lève pas, et ne va pas chercher mille en-têtes en base : la borne
        # est une garde, chaque en-tête devenant un paramètre de requête SQL.
        rendu = self.email.with_user(self.owner).extension_resolve(trop)
        self.assertEqual(rendu["emails"], [])

    def test_le_lot_refuse_ce_qui_n_est_pas_une_liste(self):
        rep = self.url_open(
            "%s/extension/resolve" % BASE, data=json.dumps({"headers": "un seul"}),
            headers={**self._auth(), "Content-Type": "application/json"}, timeout=30)
        self.assertEqual(rep.status_code, 400)
        self.assertEqual(self._resoudre([], headers={}).status_code, 401)

    def test_le_lot_porte_ses_echeances_de_report(self):
        """🔴 Recalculées à CHAQUE appel, parce qu'elles sont absolues.

        Un client qui les garderait proposerait demain « dans 1 heure » pour
        une heure passée, et le courriel reviendrait en boîte à l'instant même
        où on le reporte. Même raison que dans `_extension_config`.
        """
        charge = self._resoudre([ENTETE]).json()
        reports = charge.get("snooze_presets") or []
        self.assertTrue(reports, "la fenêtre de sélection n'a pas d'autre source")
        maintenant = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        for report in reports:
            self.assertGreater(report["until_ms"], maintenant,
                               "une échéance déjà passée ferait revenir le "
                               "courriel au moment du report")
        # Et une réponse VIDE les porte aussi : la fenêtre s'ouvre parfois sur
        # une sélection que Symbifox ne connaît pas, et le tiroir doit exister.
        vide = self._resoudre(["<jamais-vu@test.invalid>"]).json()
        self.assertEqual(vide["emails"], [])
        self.assertTrue(vide.get("snooze_presets"))

    def test_le_lot_ne_marque_rien_comme_lu(self):
        """⛔ C'est une lecture. La pastille se peint à CHAQUE courriel affiché."""
        self.email.status = "new"
        self.env.cr.flush()
        self._resoudre([ENTETE])
        self.email.invalidate_recordset()
        self.assertEqual(self.email.status, "new")

    def test_les_totaux_voyagent_par_la_route(self):
        """Ce que le panneau lit vraiment, pas ce que la méthode rend."""
        self._facture_due(montant=100)
        self.env.cr.flush()
        charge = self._ouvrir(ENTETE).json()
        self.assertIn("totals", charge["context"],
                      "la clé doit traverser la sérialisation JSON")
        self.assertIn("tasks", charge["context"]["totals"])
