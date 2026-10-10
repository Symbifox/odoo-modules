"""Journal d'humeur par HTTP : la page du téléphone, l'export, et le
verrou de Gen éprouvé par de VRAIS appels XML-RPC sous une clé d'API, comme le
MCP de Gen appelle Odoo. Données inventées.

Le verrou s'accroche au canal : la session web de la personne (écran) lit son
journal ; l'API (XML-RPC, clé) ne lit rien sans les deux portes, l'instance
puis la personne.
"""
import csv
import io
import json
import os
import secrets
import unittest
import xmlrpc.client
from datetime import timedelta

from odoo import fields
from odoo.tests import HttpCase, new_test_user, tagged

GROUPES = "base.group_user,bf_health.group_health_user"
PARAM = "bf_claude_chat.sensitive_open_scopes"


class _Base(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.a = new_test_user(cls.env, login="humeur_http_a", password="humeur_http_a",
                              groups=GROUPES, name="Personne A")
        cls.b = new_test_user(cls.env, login="humeur_http_b", password="humeur_http_b",
                              groups=GROUPES, name="Personne B")
        cls.cle_a = cls.env["res.users.apikeys"].with_user(cls.a).sudo()._generate(None, "gen-a", None)
        cls.cle_b = cls.env["res.users.apikeys"].with_user(cls.b).sudo()._generate(None, "gen-b", None)
        Act = cls.env["health.mood.activity"].with_user(cls.a)
        cls.sport = Act.create({"name": "SECRET-ACT-A", "icon": "🏃"})
        # Datée d'avant-hier : une saisie du jour empêcherait le rappel.
        cls.entree = cls.env["health.mood.entry"].with_user(cls.a).create(
            {"level": "2", "note": "SECRET-NOTE-A", "activity_ids": [(6, 0, cls.sport.ids)],
             "date": fields.Date.today() - timedelta(days=2)})
        cls.reglages = cls.env["health.mood.settings"].with_user(cls.a)._bf_mes_reglages()
        cls.vital = cls.env["health.vital"].with_user(cls.a).create(
            {"vital_type": "sleep_hours", "value": 6.5})

    def rpc(self, user, cle, model, method, *args, **kw):
        return self.xmlrpc_object.execute_kw(self.env.cr.dbname, user.id, cle, model, method,
                                             list(args), kw)

    def jsonrpc(self, user, cle, model, method, *args, **kw):
        """Le même appel par /jsonrpc, l'autre porte de l'API externe."""
        r = self.url_open("/jsonrpc", data=json.dumps({
            "jsonrpc": "2.0", "method": "call", "params": {
                "service": "object", "method": "execute_kw",
                "args": [self.env.cr.dbname, user.id, cle, model, method, list(args), kw]}}),
            headers={"Content-Type": "application/json"})
        return r.json()

    def json(self, route, params=None):
        r = self.url_open(route, data=json.dumps({"jsonrpc": "2.0", "method": "call",
                                                   "params": params or {}}),
                          headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 200)
        return r.json()

    def appel_web(self, model, method, args, kwargs=None):
        """Comme le client web : /web/dataset/call_kw sous la session (canal écran)."""
        corps = self.json("/web/dataset/call_kw/%s/%s" % (model, method),
                          {"model": model, "method": method, "args": args, "kwargs": kwargs or {}})
        return corps


@tagged("post_install", "-at_install", "humeur")
class TestPageTelephone(_Base):

    def test_page_exige_une_session(self):
        r = self.url_open("/healthy-fox/humeur", allow_redirects=False)
        self.assertIn(r.status_code, (302, 303))
        self.assertIn("/web/login", r.headers.get("Location", ""))

    def test_page_coquille_sans_donnee(self):
        self.authenticate("humeur_http_a", "humeur_http_a")
        r = self.url_open("/healthy-fox/humeur")
        self.assertEqual(r.status_code, 200)
        self.assertIn("no-cache", r.headers.get("Cache-Control", ""))
        self.assertIn('rel="manifest"', r.text)
        self.assertNotIn("SECRET", r.text, "la coquille gardée hors ligne ne porte rien de la personne")
        mots = r.text.split('id="i18n">', 1)[1].split("</script>", 1)[0]
        self.assertIn("question", json.loads(mots))

    def test_manifeste_et_agent(self):
        m = self.url_open("/healthy-fox/humeur/manifest.webmanifest").json()
        self.assertEqual(m["id"], "/healthy-fox/humeur")
        self.assertEqual(m["scope"], "/healthy-fox/humeur")
        self.assertEqual(m["display"], "standalone")
        usages = {(i["sizes"], i["purpose"]) for i in m["icons"]}
        self.assertTrue({("192x192", "any"), ("512x512", "any"), ("192x192", "maskable"),
                         ("512x512", "maskable")} <= usages)
        for icone in m["icons"]:
            self.assertEqual(self.url_open(icone["src"]).status_code, 200)
        sw = self.url_open("/healthy-fox/humeur/sw.js")
        self.assertEqual(sw.headers.get("Service-Worker-Allowed"), "/healthy-fox/humeur")
        self.assertIn("bf-healthy-fox-humeur-", sw.text)
        self.assertIn("k.startsWith(PREFIXE)", sw.text, "ne supprime que ses propres caches")

    def test_saisir_puis_rejouer_sans_doublon(self):
        self.authenticate("humeur_http_a", "humeur_http_a")
        etat = self.json("/healthy-fox/humeur/api/etat")["result"]
        self.assertEqual(etat["uid"], self.a.id)
        self.assertIn(self.sport.id, [x["id"] for x in etat["activities"]])
        uuid = secrets.token_hex(8)
        params = {"level": "4", "activity_ids": [self.sport.id], "note": "via téléphone",
                  "client_uuid": uuid}
        r1 = self.json("/healthy-fox/humeur/api/saisir", params)["result"]
        r2 = self.json("/healthy-fox/humeur/api/saisir", params)["result"]
        self.assertTrue(r1["ok"])
        self.assertEqual(r1["id"], r2["id"])
        self.assertTrue(r2.get("replayed"))
        entree = self.env["health.mood.entry"].sudo().browse(r1["id"])
        self.assertEqual(entree.create_uid, self.a)
        self.assertEqual(entree.activity_ids, self.sport)

    def test_saisie_hors_ligne_garde_son_jour(self):
        self.authenticate("humeur_http_a", "humeur_http_a")
        hier = fields.Date.to_string(fields.Date.context_today(self.env["health.mood.entry"]) - timedelta(days=1))
        r = self.json("/healthy-fox/humeur/api/saisir",
                      {"level": "3", "client_uuid": secrets.token_hex(8), "date": hier})["result"]
        self.assertEqual(fields.Date.to_string(self.env["health.mood.entry"].sudo().browse(r["id"]).date), hier)

    def test_b_ne_coche_pas_l_activite_de_a_par_la_page(self):
        self.authenticate("humeur_http_b", "humeur_http_b")
        r = self.json("/healthy-fox/humeur/api/saisir",
                      {"level": "3", "activity_ids": [self.sport.id], "client_uuid": "x1"})["result"]
        self.assertIn("error", r)
        self.assertFalse(self.env["health.mood.entry"].sudo().search([("create_uid", "=", self.b.id)]))
        etat = self.json("/healthy-fox/humeur/api/etat")["result"]
        self.assertNotIn("SECRET", json.dumps(etat))

    def test_cran_invalide_refuse(self):
        self.authenticate("humeur_http_a", "humeur_http_a")
        r = self.json("/healthy-fox/humeur/api/saisir", {"level": "9"})["result"]
        self.assertIn("error", r)

    def test_export_csv_de_la_personne_seule(self):
        self.env["health.mood.entry"].with_user(self.b).create({"level": "5", "note": "NOTE-DE-B"})
        self.authenticate("humeur_http_a", "humeur_http_a")
        r = self.url_open("/healthy-fox/humeur/export.csv")
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r.headers.get("Content-Disposition", ""))
        texte = r.content.decode("utf-8-sig")
        lignes = list(csv.reader(io.StringIO(texte)))
        self.assertEqual(len(lignes), 2, "l'en-tête et la saisie de A")
        self.assertIn("SECRET-NOTE-A", texte)
        self.assertIn("SECRET-ACT-A", texte)
        self.assertNotIn("NOTE-DE-B", texte)


@tagged("post_install", "-at_install", "humeur")
class TestPdfReel(_Base):
    """Le vrai rendu : wkhtmltopdf va chercher les feuilles de style au serveur
    d'essai, ce qu'un TransactionCase ne sert pas (il tient le curseur). La
    laisse de 60 s du banc tue un rendu bloqué."""

    def test_pdf_reel_wkhtmltopdf(self):
        port = self.http_port()
        self.env["ir.config_parameter"].sudo().set_param("report.url", "http://127.0.0.1:%s" % port)
        reglages = self.env["health.mood.settings"].with_user(self.a)._bf_mes_reglages()
        reglages.correlations_enabled = True
        fin = fields.Date.today()
        for i in range(10):
            self.env["health.mood.entry"].with_user(self.a).create(
                {"date": fin - timedelta(days=i), "level": str(1 + i % 5)})
        w = self.env["health.mood.report.wizard"].with_user(self.a).create(
            {"date_from": fin - timedelta(days=13), "date_to": fin})
        self.assertTrue(w.include_correlations, "activées par la personne : proposées au rapport")
        pdf, fmt = self.env["ir.actions.report"].with_user(self.a).with_context(
            force_report_rendering=True)._render_qweb_pdf("bf_health.report_mood_doctor", w.ids)
        self.assertEqual(fmt, "pdf")
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 5000)
        self.assertFalse(self.env["ir.attachment"].sudo().search(
            [("res_model", "=", "health.mood.report.wizard"), ("res_id", "=", w.id)]),
            "le PDF n'est jamais gardé en pièce jointe")
        # Pour le regarder au banc : BF_HEALTH_PDF_ESSAI=<chemin> (rien sinon).
        if os.environ.get("BF_HEALTH_PDF_ESSAI"):
            with open(os.environ["BF_HEALTH_PDF_ESSAI"], "wb") as f:
                f.write(pdf)


@tagged("post_install", "-at_install", "humeur", "verrou_gen")
class TestVerrouGen(_Base):
    """Le verrou de Gen par de vrais appels XML-RPC (canal API)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if "bf.gen.consent" not in cls.env:
            raise unittest.SkipTest("Gen (bf_claude_chat) n'est pas installé")
        # Le rappel de A : une activité qui lui est assignée.
        cls.reglages.with_user(cls.a).write({"reminder_time": 0.0})
        cls.env["health.mood.settings"]._cron_mood_reminders()
        cls.rappel = cls.env["mail.activity"].sudo().search([
            ("res_model", "=", "health.mood.settings"), ("user_id", "=", cls.a.id)], limit=1)
        # Une pièce jointe et un message de A sur sa fiche de réglages.
        cls.piece = cls.env["ir.attachment"].with_user(cls.a).create({
            "name": "SECRET-PJ.txt", "raw": b"secret", "res_model": "health.mood.entry",
            "res_id": cls.entree.id})
        cls.message = cls.reglages.with_user(cls.a).message_post(
            body="SECRET-MESSAGE-A", message_type="comment", subtype_xmlid="mail.mt_note")

    def ouvrir_instance(self, *portees):
        self.env["ir.config_parameter"].sudo().set_param(PARAM, ",".join(portees))

    def consentir_a_l_ecran(self, user, mdp, portee="bf_health.mood"):
        self.authenticate(user.login, mdp)
        return self.appel_web("bf.gen.consent", "create", [{"scope": portee}])

    def lecture_refusee(self, user, cle, model, rec_id, **ctx):
        kw = {"context": ctx} if ctx else {}
        self.assertEqual(self.rpc(user, cle, model, "search", [("id", "=", rec_id)], **kw), [])
        self.assertEqual(self.rpc(user, cle, model, "search_count", [], **kw), 0)
        groupes = self.rpc(user, cle, model, "read_group", [], ["id:count"], [], **kw)
        self.assertEqual(groupes[0]["__count"] if groupes else 0, 0)
        with self.assertRaises(xmlrpc.client.Fault):
            self.rpc(user, cle, model, "read", [rec_id], ["display_name"], **kw)

    # -- 1. Fermé par défaut -------------------------------------------------
    def test_ferme_sans_rien(self):
        for model, rec in (("health.mood.entry", self.entree), ("health.mood.activity", self.sport),
                           ("health.mood.settings", self.reglages), ("health.vital", self.vital)):
            with self.subTest(model=model):
                self.lecture_refusee(self.a, self.cle_a, model, rec.id)

    def test_ecran_lit_toujours(self):
        """Contre-épreuve : la session web de la personne lit son journal."""
        self.authenticate("humeur_http_a", "humeur_http_a")
        r = self.appel_web("health.mood.entry", "search_read", [[["id", "=", self.entree.id]], ["note"]])
        self.assertEqual(r["result"][0]["note"], "SECRET-NOTE-A")

    def test_ecriture_et_creation_refusees_par_l_api(self):
        self.ouvrir_instance("bf_health.mood")
        self.consentir_a_l_ecran(self.a, "humeur_http_a")
        for appel in (lambda: self.rpc(self.a, self.cle_a, "health.mood.entry", "create", {"level": "5"}),
                      lambda: self.rpc(self.a, self.cle_a, "health.mood.entry", "write",
                                       [self.entree.id], {"note": "écrit par Gen"}),
                      lambda: self.rpc(self.a, self.cle_a, "health.mood.entry", "unlink", [self.entree.id])):
            with self.assertRaises(xmlrpc.client.Fault):
                appel()
        self.assertEqual(self.entree.sudo().note, "SECRET-NOTE-A")

    # -- 2. Les deux portes --------------------------------------------------
    def test_consentement_seul_ne_suffit_pas(self):
        """Instance fermée : la personne ne peut même pas consentir, et un
        consentement présent (posé en superutilisateur) n'ouvre rien."""
        r = self.consentir_a_l_ecran(self.a, "humeur_http_a")
        self.assertIn("error", r)
        self.env["bf.gen.consent"].sudo().create({"user_id": self.a.id, "scope": "bf_health.mood"})
        self.lecture_refusee(self.a, self.cle_a, "health.mood.entry", self.entree.id)

    def test_instance_seule_ne_suffit_pas(self):
        self.ouvrir_instance("bf_health.mood", "bf_health")
        self.lecture_refusee(self.a, self.cle_a, "health.mood.entry", self.entree.id)

    def test_instance_et_consentement_ouvrent_la_lecture_puis_le_retrait_referme(self):
        self.ouvrir_instance("bf_health.mood")
        r = self.consentir_a_l_ecran(self.a, "humeur_http_a")
        self.assertNotIn("error", r)
        lu = self.rpc(self.a, self.cle_a, "health.mood.entry", "search_read",
                      [("id", "=", self.entree.id)], ["note"])
        self.assertEqual(lu[0]["note"], "SECRET-NOTE-A")
        # La santé reste fermée : une autre portée.
        self.lecture_refusee(self.a, self.cle_a, "health.vital", self.vital.id)
        # B, lui, ne lit toujours rien de A.
        self.assertEqual(self.rpc(self.b, self.cle_b, "health.mood.entry", "search",
                                  [("id", "=", self.entree.id)]), [])
        # Le retrait passe même par l'API, et referme.
        consent = self.rpc(self.a, self.cle_a, "bf.gen.consent", "search", [])
        self.rpc(self.a, self.cle_a, "bf.gen.consent", "unlink", consent)
        self.lecture_refusee(self.a, self.cle_a, "health.mood.entry", self.entree.id)

    def test_fermer_l_instance_referme_meme_avec_consentement(self):
        self.ouvrir_instance("bf_health.mood")
        self.consentir_a_l_ecran(self.a, "humeur_http_a")
        self.ouvrir_instance()
        self.lecture_refusee(self.a, self.cle_a, "health.mood.entry", self.entree.id)

    def test_gen_ne_se_donne_pas_le_consentement(self):
        self.ouvrir_instance("bf_health.mood")
        with self.assertRaises(xmlrpc.client.Fault):
            self.rpc(self.a, self.cle_a, "bf.gen.consent", "create", {"scope": "bf_health.mood"})
        self.assertFalse(self.env["bf.gen.consent"].sudo().search([("user_id", "=", self.a.id)]))

    def test_admin_ne_consent_pas_pour_autrui(self):
        self.ouvrir_instance("bf_health.mood")
        admin = new_test_user(self.env, login="humeur_http_admin", password="humeur_http_admin",
                              groups=GROUPES + ",base.group_system")
        self.authenticate("humeur_http_admin", "humeur_http_admin")
        self.appel_web("bf.gen.consent", "create", [{"scope": "bf_health.mood", "user_id": self.a.id}])
        consentements = self.env["bf.gen.consent"].sudo().search([])
        self.assertNotIn(self.a, consentements.user_id)
        self.assertIn(admin, consentements.user_id)
        r = self.appel_web("bf.gen.consent", "search_read", [[], ["user_id"]])
        self.assertEqual({c["user_id"][0] for c in r["result"]}, {admin.id},
                         "l'administrateur ne voit pas les consentements d'autrui")

    # -- 3. La conversation lancée depuis une fiche -------------------------
    def _tour(self, user, fiche, etat="pending"):
        """Un tour comme le prépare le contrôleur : conversation sur la fiche,
        message en cours, jeton dans la charge."""
        from odoo.addons.bf_claude_chat.models import gen_sensitive
        Session = self.env["claude.chat.session"].sudo()
        session = Session.create({"user_id": user.id, "name": "x", "origin": "web",
                                  "res_model": fiche._name, "res_id": fiche.id})
        pending = self.env["claude.chat.message"].sudo().create({
            "session_id": session.id, "role": "assistant", "content": "…", "state": etat,
            "runner_heartbeat": fields.Datetime.now()})
        charge = {}
        gen_sensitive.preparer_tour(self.env, session, pending, charge)
        return session, pending, charge

    def test_jeton_de_tour_ouvre_la_fiche_seule(self):
        self.ouvrir_instance("bf_health.mood")
        autre = self.env["health.mood.entry"].with_user(self.a).create({"level": "5", "note": "AUTRE"})
        session, pending, charge = self._tour(self.a, self.entree)
        jeton = charge["gen_grant"]["token"]
        self.assertTrue(session.gen_private)
        lu = self.rpc(self.a, self.cle_a, "health.mood.entry", "search_read", [], ["note"],
                      context={"bf_gen_grant": jeton})
        self.assertEqual([x["note"] for x in lu], ["SECRET-NOTE-A"], "cette fiche, et elle seule")
        ctx = {"bf_gen_grant": jeton}
        self.assertEqual(self.rpc(self.a, self.cle_a, "health.mood.entry", "search",
                                  [("id", "=", autre.id)], context=ctx), [])
        with self.assertRaises(xmlrpc.client.Fault):
            self.rpc(self.a, self.cle_a, "health.mood.entry", "read", [autre.id], ["note"], context=ctx)
        self.lecture_refusee(self.a, self.cle_a, "health.mood.activity", self.sport.id, bf_gen_grant=jeton)
        with self.assertRaises(xmlrpc.client.Fault):
            self.rpc(self.a, self.cle_a, "health.mood.entry", "write", [self.entree.id], {"note": "x"},
                     context={"bf_gen_grant": jeton})

    def test_jeton_refuse_ailleurs(self):
        self.ouvrir_instance("bf_health.mood")
        session, pending, charge = self._tour(self.a, self.entree)
        jeton = charge["gen_grant"]["token"]
        # Par B : le jeton est lié à la propriétaire de la conversation.
        self.assertEqual(self.rpc(self.b, self.cle_b, "health.mood.entry", "search",
                                  [("id", "=", self.entree.id)], context={"bf_gen_grant": jeton}), [])
        # Un jeton inventé.
        self.lecture_refusee(self.a, self.cle_a, "health.mood.entry", self.entree.id,
                             bf_gen_grant="x" * 43)
        # Le tour fini : le jeton ne vaut plus.
        pending.sudo().write({"state": "done"})
        self.lecture_refusee(self.a, self.cle_a, "health.mood.entry", self.entree.id, bf_gen_grant=jeton)

    def test_jeton_perime_sans_signe_de_vie(self):
        self.ouvrir_instance("bf_health.mood")
        session, pending, charge = self._tour(self.a, self.entree)
        pending.sudo().write({"runner_heartbeat": fields.Datetime.now() - timedelta(minutes=10)})
        self.lecture_refusee(self.a, self.cle_a, "health.mood.entry", self.entree.id,
                             bf_gen_grant=charge["gen_grant"]["token"])

    def test_pas_de_jeton_si_l_instance_est_fermee(self):
        session, pending, charge = self._tour(self.a, self.entree)
        self.assertNotIn("gen_grant", charge)
        self.assertTrue(session.gen_private, "privée quand même")

    def _tour_au_bureau(self, fiche):
        """Le vrai départ d'un tour au bureau (/claude-chat/stream), pont simulé :
        on relève ce que le pont aurait reçu."""
        from unittest.mock import patch
        from odoo.addons.bf_claude_chat.controllers import turns

        recu = []

        def faux_flux(socket_path, endpoint, payload, timeout, headers=None):
            recu.append(dict(payload))
            yield "event: done\ndata: {\"response\": \"fini\"}\n\n".encode()

        self.env["ir.config_parameter"].sudo().set_param("bf_claude_chat.enabled", "True")
        self.env["ir.config_parameter"].sudo().set_param("bf_ai_bridge.tenant", "essai")
        self.authenticate("humeur_http_a", "humeur_http_a")
        with patch.object(turns.transport, "stream", side_effect=faux_flux):
            self.url_open("/claude-chat/stream", data=json.dumps({
                "message": "Que dit mon journal ?", "context": {
                    "model": fiche._name, "res_id": fiche.id, "display_name": "x",
                    "view_type": "form", "url": "/odoo"}}),
                headers={"Content-Type": "application/json", "X-Claude-Stream": "1"}, timeout=30)
        session = self.env["claude.chat.session"].sudo().search(
            [("user_id", "=", self.a.id), ("res_model", "=", fiche._name)], order="id desc", limit=1)
        return session, recu

    def test_tour_au_bureau_depuis_une_fiche_porte_un_jeton(self):
        self.ouvrir_instance("bf_health.mood")
        session, recu = self._tour_au_bureau(self.entree)
        self.assertTrue(session, "la conversation est rattachée à la fiche (canal écran)")
        self.assertTrue(session.gen_private)
        self.assertTrue(recu, "le pont simulé n'a rien reçu : l'essai ne prouve rien")
        grant = recu[0].get("gen_grant") or {}
        self.assertEqual((grant.get("model"), grant.get("res_id")), ("health.mood.entry", self.entree.id))
        self.assertGreaterEqual(len(grant.get("token", "")), 40)
        # Le tour est fini : le jeton ne vaut plus.
        self.lecture_refusee(self.a, self.cle_a, "health.mood.entry", self.entree.id,
                             bf_gen_grant=grant["token"])

    def test_tour_au_bureau_instance_fermee_sans_jeton(self):
        session, recu = self._tour_au_bureau(self.entree)
        self.assertTrue(session.gen_private)
        self.assertTrue(recu)
        self.assertNotIn("gen_grant", recu[0])

    def test_gen_ne_rattache_pas_une_conversation_a_une_fiche_sensible(self):
        with self.assertRaises(xmlrpc.client.Fault):
            self.rpc(self.a, self.cle_a, "claude.chat.session", "create",
                     {"name": "x", "res_model": "health.mood.entry", "res_id": self.entree.id})
        with self.assertRaises(xmlrpc.client.Fault):
            self.rpc(self.a, self.cle_a, "claude.chat.session", "create", {"name": "x"},
                     context={"default_res_model": "health.mood.entry",
                              "default_res_id": self.entree.id})
        sid = self.rpc(self.a, self.cle_a, "claude.chat.session", "create", {"name": "x"})
        with self.assertRaises(xmlrpc.client.Fault):
            self.rpc(self.a, self.cle_a, "claude.chat.session", "write", [sid],
                     {"res_model": "health.mood.entry", "res_id": self.entree.id})

    def test_gen_ne_rend_pas_publique_une_conversation_privee(self):
        session, pending, charge = self._tour(self.a, self.entree)
        with self.assertRaises(xmlrpc.client.Fault):
            self.rpc(self.a, self.cle_a, "claude.chat.session", "write", [session.id],
                     {"gen_private": False})
        self.assertTrue(session.sudo().gen_private)

    # -- 4. Relecture adverse : les détours ----------------------------------
    def test_detour_activite_assignee(self):
        """Le rappel est assigné à A : sans le filtre compagnon, `list_activities`
        de Gen le rendrait (Odoo laisse lire une activité qui nous est assignée)."""
        self.assertTrue(self.rappel, "le rappel n'est pas posé, l'essai ne prouve rien")
        ids = self.rpc(self.a, self.cle_a, "mail.activity", "search", [])
        self.assertNotIn(self.rappel.id, ids)
        with self.assertRaises(xmlrpc.client.Fault):
            self.rpc(self.a, self.cle_a, "mail.activity", "read", [self.rappel.id], ["res_name", "summary"])

    def test_detour_piece_jointe(self):
        ids = self.rpc(self.a, self.cle_a, "ir.attachment", "search",
                       [("res_model", "=", "health.mood.entry")])
        self.assertEqual(ids, [])
        with self.assertRaises(xmlrpc.client.Fault):
            self.rpc(self.a, self.cle_a, "ir.attachment", "read", [self.piece.id], ["name", "datas"])

    def test_detour_message_dont_on_est_l_auteur(self):
        """Odoo laisse lire un message dont on est l'auteur, sans regarder le document."""
        ids = self.rpc(self.a, self.cle_a, "mail.message", "search",
                       [("model", "=", "health.mood.settings")])
        self.assertNotIn(self.message.id, ids)
        with self.assertRaises(xmlrpc.client.Fault):
            self.rpc(self.a, self.cle_a, "mail.message", "read", [self.message.id], ["body"])
        # Gen ne poste pas non plus sur la fiche.
        with self.assertRaises(xmlrpc.client.Fault):
            self.rpc(self.a, self.cle_a, "mail.message", "create", {
                "model": "health.mood.settings", "res_id": self.reglages.id, "body": "x",
                "message_type": "comment"})

    def test_detour_abonnes(self):
        ids = self.rpc(self.a, self.cle_a, "mail.followers", "search",
                       [("res_model", "=", "health.mood.settings")])
        self.assertEqual(ids, [])

    def test_detour_assistant_de_saisie(self):
        w = self.env["health.daily.log.wizard"].with_user(self.a).create(
            {"mood_level": "1", "mood_note": "SECRET-ASSISTANT"})
        self.ouvrir_instance("bf_health.mood", "bf_health")
        self.consentir_a_l_ecran(self.a, "humeur_http_a", "bf_health.mood")
        self.consentir_a_l_ecran(self.a, "humeur_http_a", "bf_health")
        self.assertEqual(self.rpc(self.a, self.cle_a, "health.daily.log.wizard", "search",
                                  [("id", "=", w.id)]), [], "un assistant n'est jamais ouvert à Gen")

    def test_detour_tableau_de_bord_en_sql(self):
        """Le tableau de bord lit la santé en SQL brut : il refuse lui-même.
        Par /jsonrpc aussi (XML-RPC ne sait pas porter ses valeurs vides)."""
        with self.assertRaises(xmlrpc.client.Fault):
            self.rpc(self.a, self.cle_a, "health.dashboard", "get_dashboard_data")
        ferme = self.jsonrpc(self.a, self.cle_a, "health.dashboard", "get_dashboard_data")
        self.assertIn("error", ferme)
        self.ouvrir_instance("bf_health")
        self.consentir_a_l_ecran(self.a, "humeur_http_a", "bf_health")
        ouvert = self.jsonrpc(self.a, self.cle_a, "health.dashboard", "get_dashboard_data")
        self.assertIn("med_compliance", ouvert.get("result", {}))

    def test_jsonrpc_meme_verrou(self):
        r = self.jsonrpc(self.a, self.cle_a, "health.mood.entry", "search_read",
                         [("id", "=", self.entree.id)], ["note"])
        self.assertEqual(r.get("result"), [])

    def test_detour_champ_calcule_des_reglages(self):
        """Les corrélations, la moyenne : calculées par l'ORM, donc fermées."""
        with self.assertRaises(xmlrpc.client.Fault):
            self.rpc(self.a, self.cle_a, "health.mood.settings", "read", [self.reglages.id],
                     ["correlation_html", "average_30"])

    def test_detour_nom_affiche_par_un_outil_de_gen(self):
        """`create_activity` et `get_record_followers` lisent d'abord le nom."""
        with self.assertRaises(xmlrpc.client.Fault):
            self.rpc(self.a, self.cle_a, "health.mood.entry", "read", [self.entree.id], ["display_name"])
        with self.assertRaises(xmlrpc.client.Fault):
            self.rpc(self.a, self.cle_a, "mail.activity", "create", {
                "res_model_id": self.env["ir.model"]._get_id("health.mood.settings"),
                "res_id": self.reglages.id, "summary": "x", "user_id": self.a.id})

    def test_detour_rapport_pdf_par_l_api(self):
        wid = None
        with self.assertRaises(xmlrpc.client.Fault):
            wid = self.rpc(self.a, self.cle_a, "health.mood.report.wizard", "create", {})
        self.assertIsNone(wid)

    def test_detour_onchange_sur_une_fiche_existante(self):
        """`onchange` avec l'id d'une fiche relit l'origine : rien ne doit revenir."""
        try:
            r = self.rpc(self.a, self.cle_a, "health.mood.entry", "onchange",
                         {"id": self.entree.id}, [], {"note": {}, "level": {},
                                                      "activity_ids": {"fields": {"display_name": {}}}})
        except xmlrpc.client.Fault:
            return
        self.assertNotIn("SECRET", repr(r))

    def test_detour_route_json_porteur(self):
        """La route `/json/1/` d'Odoo 18 lit une action sous une clé d'API
        (en-tête Authorization) : c'est aussi le canal API."""
        self.env["ir.config_parameter"].sudo().set_param("web.json.enabled", "1")
        self.a.sudo().groups_id = [(4, self.env.ref("base.group_allow_export").id)]
        r = self.url_open("/json/1/action-bf_health.health_mood_entry_action",
                          headers={"Authorization": "Bearer %s" % self.cle_a}, allow_redirects=True)
        self.assertNotIn("SECRET", r.text)
        self.ouvrir_instance("bf_health.mood")
        self.consentir_a_l_ecran(self.a, "humeur_http_a")
        self.opener.cookies.pop("session_id", None)
        r = self.url_open("/json/1/action-bf_health.health_mood_entry_action",
                          headers={"Authorization": "Bearer %s" % self.cle_a}, allow_redirects=True)
        self.assertIn("SECRET-NOTE-A", r.text, "contre-épreuve : ouvert, la route lit")

    def test_repli_sur_l_administrateur_ne_lit_pas_autrui(self):
        """Sans clé d'opérateur, Gen retombe sur l'administrateur. Même
        ouvert et consenti pour lui, il ne lit que SON journal."""
        admin = new_test_user(self.env, login="humeur_http_admin2", password="humeur_http_admin2",
                              groups=GROUPES + ",base.group_system")
        cle = self.env["res.users.apikeys"].with_user(admin).sudo()._generate(None, "gen-admin", None)
        self.ouvrir_instance("bf_health.mood")
        self.consentir_a_l_ecran(admin, "humeur_http_admin2")
        self.assertEqual(self.rpc(admin, cle, "health.mood.entry", "search",
                                  [("id", "=", self.entree.id)]), [])
