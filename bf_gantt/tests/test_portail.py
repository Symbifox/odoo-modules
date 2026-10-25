# -*- coding: utf-8 -*-
"""Le portail, par de vraies requêtes HTTP.

C'est la surface qui sort de l'application : elle se contrôle en frappant les
adresses, pas en appelant des méthodes. Un contrôle qui appellerait
`_echeancier()` en Python passerait alors même que la route serait ouverte à
tous les vents.

⚠️ `HttpCase` roule dans une transaction que le serveur HTTP ne voit pas tant
qu'elle n'est pas commise : d'où les `flush` et l'usage de `url_open` après
création complète des enregistrements.

🔴 Et c'est le `dbfilter` de la configuration qui décide quelle base répond aux
requêtes HTTP, PAS le `-d` de la ligne de commande. Sur un serveur d'essai qui
en fixe un, il faut passer `--db-filter=^<base_de_test>$` : sans lui, toutes ces
requêtes atterrissent dans la base nommée par la configuration, où le module
n'est pas installé, et rendent un 404 qui ressemble à une route mal écrite.
"""
import json
from datetime import date, datetime

from odoo.tests.common import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install", "bf_gantt")
class TestPortail(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # ⚠️ Publier exige désormais le groupe de gestion, et le module ne
        # l'accorde à personne à l'installation, pas même à l'administrateur.
        # Les contrôles doivent donc le demander, comme un humain le ferait.
        cls.env.user.groups_id |= cls.env.ref("bf_gantt.group_bf_gantt_manager")
        cls.projet = cls.env["project.project"].create({
            "name": "Projet public",
            "allow_milestones": True,
        })
        cls.env["project.task"].create({
            "name": "Tâche visible au portail",
            "project_id": cls.projet.id,
            "planned_date_begin": datetime(2026, 9, 7, 9, 0),
            "date_deadline": datetime(2026, 9, 25, 17, 0),
        })
        cls.plan = cls.env["bf.gantt.plan"].create({
            "name": "Plan public",
            "item_ids": [(0, 0, {
                "name": "Ligne du plan",
                "date_start": date(2026, 9, 7),
                "date_end": date(2026, 9, 18),
            })],
        })

    def _jeton(self, projet=None):
        """Le jeton de l'échéancier (pas l'access_token du projet)."""
        projet = projet or self.projet
        projet._bf_gantt_assurer_le_jeton()
        return projet.bf_gantt_token

    def _url(self, kind, enregistrement, token=None, suffixe=""):
        base = "/mon/echeancier/%s/%s%s" % (kind, enregistrement.id, suffixe)
        return base + ("?access_token=%s" % token if token else "")

    # ------------------------------------------------------------- les refus

    def test_sans_token_la_page_renvoie_a_la_connexion(self):
        self.projet.action_bf_gantt_publier()
        self.env.flush_all()
        reponse = self.url_open(self._url("project", self.projet),
                                allow_redirects=False)
        self.assertIn(reponse.status_code, (301, 302, 303))

    def test_un_mauvais_token_ne_montre_rien(self):
        self.projet.action_bf_gantt_publier()
        self.env.flush_all()
        reponse = self.url_open(
            self._url("project", self.projet, token="pas-le-bon"),
            allow_redirects=False)
        self.assertIn(reponse.status_code, (301, 302, 303))

    def test_le_bon_token_sur_un_echeancier_non_publie_est_refuse(self):
        """🔴 Le token existe dès qu'on le demande ; publier est un autre geste."""
        self.projet._portal_ensure_token()
        self.projet.bf_gantt_published = False
        self.env.flush_all()
        reponse = self.url_open(
            self._url("project", self.projet, token=self._jeton()),
            allow_redirects=False)
        self.assertIn(reponse.status_code, (301, 302, 303))

    def test_un_genre_inconnu_ne_plante_pas(self):
        self.env.flush_all()
        reponse = self.url_open("/mon/echeancier/autrechose/1",
                                allow_redirects=False)
        self.assertLess(reponse.status_code, 500)

    # ------------------------------------------------------------ les succès

    def test_publie_avec_le_bon_token_la_page_montre_le_dessin(self):
        self.projet.action_bf_gantt_publier()
        self.env.flush_all()
        reponse = self.url_open(
            self._url("project", self.projet, token=self._jeton()))
        self.assertEqual(reponse.status_code, 200)
        corps = reponse.text
        self.assertIn("<svg", corps)
        self.assertIn("Projet public", corps)
        self.assertIn("Tâche visible au portail", corps)

    def test_le_pied_ne_double_pas_le_point_final_du_nom(self):
        """« servie par Boréal inc.. » : le nom de la société porte déjà son point."""
        self.projet.action_bf_gantt_publier()
        url = self._url("project", self.projet, token=self._jeton())
        # La page suit la langue du site : on regarde la ponctuation, pas la phrase.
        for nom, motif in (("Banc Gantt inc.", r"Banc Gantt inc\.</span>\s+[A-Z]"),
                           ("Banc Gantt", r"Banc Gantt</span>\.\s+[A-Z]")):
            (self.projet.company_id or self.env.company).name = nom
            self.env.flush_all()
            corps = self.url_open(url).text
            self.assertRegex(corps, motif)
            # Doublé : « inc.</span>. » ou « </span>. . »
            self.assertNotRegex(corps, r"Banc Gantt inc\.</span>\.")
            self.assertNotRegex(corps, r"Banc Gantt(?: inc\.)?</span>\.?\s+\.\s")

    def test_la_migration_retire_le_point_herite_de_l_ancienne_traduction(self):
        """La montée gardait la traduction « . Elle reflète… » : « Boréal. . Elle »."""
        import importlib.util
        from pathlib import Path
        chemin = Path(__file__).parents[1] / "migrations" / "18.0.1.6.1" / "post-migrate.py"
        spec = importlib.util.spec_from_file_location("bf_gantt_mig_1_6_1", chemin)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        vue = self.env.ref("bf_gantt.portail_echeancier")
        self.env.flush_all()
        self.env.cr.execute("SELECT arch_db FROM ir_ui_view WHERE id = %s", [vue.id])
        arch = self.env.cr.fetchone()[0]
        abime = {lang: texte.replace("It reflects the state", ". It reflects the state")
                 .replace("Elle reflète", ". Elle reflète") for lang, texte in arch.items()}
        self.env.cr.execute("UPDATE ir_ui_view SET arch_db = %s::jsonb WHERE id = %s",
                            [json.dumps(abime), vue.id])
        migration.migrate(self.env.cr, "18.0.1.6.0")
        self.env.cr.execute("SELECT arch_db FROM ir_ui_view WHERE id = %s", [vue.id])
        self.assertEqual(self.env.cr.fetchone()[0], arch)
        # Rejouable : rien ne bouge au second passage.
        migration.migrate(self.env.cr, "18.0.1.6.0")
        self.env.cr.execute("SELECT arch_db FROM ir_ui_view WHERE id = %s", [vue.id])
        self.assertEqual(self.env.cr.fetchone()[0], arch)

    def test_le_pied_francais_ne_double_pas_le_point(self):
        langue = self.env["res.lang"]._lang_get("fr_CA")
        if not langue:
            self.skipTest("fr_CA inactif sur cette base")
        self.projet.action_bf_gantt_publier()
        (self.projet.company_id or self.env.company).name = "Banc Gantt"
        self.env.flush_all()
        vue = self.env.ref("bf_gantt.portail_echeancier").with_context(lang="fr_CA")
        self.assertIn("Elle reflète", vue.arch_db)
        self.assertNotRegex(vue.arch_db, r"/>\s*\.\s+Elle reflète")

    def test_la_page_ne_laisse_pas_la_declaration_xml_dans_le_html(self):
        self.projet.action_bf_gantt_publier()
        self.env.flush_all()
        corps = self.url_open(
            self._url("project", self.projet,
                      token=self._jeton())).text
        self.assertNotIn("<?xml", corps)

    def test_un_plan_autonome_se_publie_pareil(self):
        self.plan.action_bf_gantt_publier()
        self.env.flush_all()
        reponse = self.url_open(
            self._url("plan", self.plan, token=self.plan.access_token))
        self.assertEqual(reponse.status_code, 200)
        self.assertIn("Ligne du plan", reponse.text)

    def test_les_cinq_fichiers_se_telechargent_avec_le_token(self):
        self.projet.action_bf_gantt_publier()
        self.env.flush_all()
        attendus = {
            "pdf": b"%PDF-",
            "png": b"\x89PNG",
            "svg": b"<?xml",
            "xlsx": b"PK",
            "mspdi": b"<?xml",
        }
        for format_, signature in attendus.items():
            reponse = self.url_open(self._url(
                "project", self.projet, token=self._jeton(),
                suffixe="/" + format_))
            self.assertEqual(reponse.status_code, 200, format_)
            self.assertTrue(reponse.content.startswith(signature), format_)
            self.assertIn("attachment", reponse.headers.get(
                "Content-Disposition", ""))

    def test_le_portail_ouvre_agrandi_et_le_zoom_suit_l_adresse(self):
        """Le tracé est calibré pour l'impression : à 1:1 il est illisible."""
        import re
        self.projet.action_bf_gantt_publier()
        self.env.flush_all()

        def boite(url):
            corps = self.url_open(url).text
            m = re.search(
                r'<svg[^>]*width="([\d.]+)" height="([\d.]+)" '
                r'viewBox="0 0 ([\d.]+) ([\d.]+)"', corps)
            self.assertTrue(m, "en-tête SVG absent de la page")
            l, h, vw, vh = (float(v) for v in m.groups())
            return l / vw

        base = self._url("project", self.projet, token=self._jeton())
        self.assertAlmostEqual(boite(base), 1.5, places=2)          # défaut
        self.assertAlmostEqual(boite(base + "&zoom=2.5"), 2.5, places=2)
        self.assertAlmostEqual(boite(base + "&zoom=1"), 1.0, places=2)
        # Une valeur absurde ne casse pas la page, elle retombe sur le défaut.
        self.assertAlmostEqual(boite(base + "&zoom=nawak"), 1.5, places=2)

    def test_un_fichier_sans_token_est_refuse(self):
        """Deviner l'adresse du PDF ne doit pas contourner la page."""
        self.projet.action_bf_gantt_publier()
        self.env.flush_all()
        reponse = self.url_open(self._url("project", self.projet,
                                          suffixe="/pdf"))
        # La route suit la langue du site : une redirection de langue peut venir d'abord.
        # Ce qui compte : la chaîne passe par l'espace client, et aucun PDF n'est servi.
        etapes = [r.headers.get("Location", "") for r in reponse.history]
        self.assertTrue(any(e.split("?")[0].rstrip("/").endswith("/my") for e in etapes), etapes)
        self.assertNotIn("application/pdf", reponse.headers.get("Content-Type", ""))

    def test_un_format_inconnu_ne_sert_rien(self):
        self.projet.action_bf_gantt_publier()
        self.env.flush_all()
        reponse = self.url_open(self._url(
            "project", self.projet, token=self._jeton(),
            suffixe="/exe"), allow_redirects=False)
        self.assertNotEqual(reponse.status_code, 200)

    def test_regenerer_le_token_coupe_l_ancienne_adresse(self):
        self.projet.action_bf_gantt_publier()
        ancien = self._jeton()
        self.projet.action_bf_gantt_regenerer_token()
        self.env.flush_all()
        self.assertNotEqual(ancien, self._jeton())
        reponse = self.url_open(
            self._url("project", self.projet, token=ancien),
            allow_redirects=False)
        self.assertIn(reponse.status_code, (301, 302, 303))

    # -------------------------------------------- le client qui a un compte

    def _client_portail(self, nom="Client Portail"):
        partenaire = self.env["res.partner"].create({"name": nom})
        usager = new_test_user(
            self.env, login="portail_%s" % partenaire.id,
            groups="base.group_portal", password="portail_%s" % partenaire.id)
        usager.partner_id = partenaire
        return usager, partenaire

    def test_le_client_voit_la_liste_de_ses_echeanciers(self):
        usager, partenaire = self._client_portail()
        self.plan.partner_id = partenaire
        self.plan.action_bf_gantt_publier()
        self.env.flush_all()

        self.authenticate(usager.login, usager.login)
        reponse = self.url_open("/my/echeanciers")
        self.assertEqual(reponse.status_code, 200)
        self.assertIn("Plan public", reponse.text)

    def test_le_client_ne_voit_pas_l_echeancier_d_un_autre(self):
        usager, _partenaire = self._client_portail("Client A")
        _autre_usager, autre = self._client_portail("Client B")
        self.plan.partner_id = autre
        self.plan.action_bf_gantt_publier()
        self.env.flush_all()

        self.authenticate(usager.login, usager.login)
        reponse = self.url_open("/my/echeanciers")
        self.assertEqual(reponse.status_code, 200)
        self.assertNotIn("Plan public", reponse.text)

    def test_le_client_ne_voit_pas_un_echeancier_non_publie(self):
        usager, partenaire = self._client_portail()
        self.plan.partner_id = partenaire
        self.plan.action_bf_gantt_depublier()
        self.env.flush_all()

        self.authenticate(usager.login, usager.login)
        reponse = self.url_open("/my/echeanciers")
        self.assertNotIn("Plan public", reponse.text)

    def test_la_regle_du_portail_tient_aussi_par_l_ORM(self):
        """Une liste filtrée dans le contrôleur ne prouve rien sans la règle."""
        usager, partenaire = self._client_portail()
        self.plan.partner_id = partenaire
        self.plan.action_bf_gantt_publier()
        autre = self.env["bf.gantt.plan"].create({
            "name": "Plan d'un autre client",
            "partner_id": self.env["res.partner"].create({"name": "Ailleurs"}).id,
            "portal_published": True,
        })
        self.env.flush_all()

        vus = self.env["bf.gantt.plan"].with_user(usager).search([]).mapped("name")
        self.assertIn("Plan public", vus)
        self.assertNotIn(autre.name, vus)

    def test_le_bouton_apparait_sur_la_page_projet_du_client(self):
        usager, partenaire = self._client_portail()
        self.projet.partner_id = partenaire
        self.projet.message_subscribe(partner_ids=[partenaire.id])
        self.projet.action_bf_gantt_publier()
        self.env.flush_all()

        self.authenticate(usager.login, usager.login)
        reponse = self.url_open("/my/projects/%s" % self.projet.id)
        self.assertEqual(reponse.status_code, 200)
        # La page suit la langue par défaut du site : fr_CA sur une base francophone.
        # bf_gantt ne dépend pas de `website` : sans lui, la page parle la source.
        francais = "website" in self.env and (
            self.env["website"].get_current_website().default_lang_id.code or "").startswith("fr")
        attendu = "Voir l'échéancier" if francais else "View the timeline"
        self.assertIn(attendu, reponse.text)

    def test_le_bouton_disparait_quand_l_echeancier_est_referme(self):
        usager, partenaire = self._client_portail()
        self.projet.partner_id = partenaire
        self.projet.message_subscribe(partner_ids=[partenaire.id])
        self.projet.action_bf_gantt_publier()
        self.projet.action_bf_gantt_depublier()
        self.env.flush_all()

        self.authenticate(usager.login, usager.login)
        reponse = self.url_open("/my/projects/%s" % self.projet.id)
        self.assertEqual(reponse.status_code, 200)
        # Dans les deux langues : une page rendue en anglais ne prouverait rien.
        self.assertNotIn("Voir l'échéancier", reponse.text)
        self.assertNotIn("View the timeline", reponse.text)

    def test_depublier_referme_sans_changer_le_token(self):
        self.projet.action_bf_gantt_publier()
        token = self._jeton()
        self.projet.action_bf_gantt_depublier()
        self.env.flush_all()
        self.assertEqual(token, self._jeton())
        reponse = self.url_open(self._url("project", self.projet, token=token),
                                allow_redirects=False)
        self.assertIn(reponse.status_code, (301, 302, 303))

    # ------------------------- 18.0.1.7.0 : un jeton propre à l'échéancier

    def test_l_access_token_du_projet_n_ouvre_plus_l_echeancier(self):
        self.projet.action_bf_gantt_publier()
        self.projet._portal_ensure_token()
        self.env.flush_all()
        reponse = self.url_open(
            self._url("project", self.projet, token=self.projet.access_token),
            allow_redirects=False)
        self.assertIn(reponse.status_code, (301, 302, 303))

    def test_publier_ne_frappe_pas_l_access_token_du_projet(self):
        projet = self.env["project.project"].create({"name": "Projet neuf"})
        projet.action_bf_gantt_publier()
        projet.action_bf_gantt_copier_lien()
        self.assertFalse(projet.access_token)
        self.assertTrue(projet.bf_gantt_token)

    def test_le_jeton_de_l_echeancier_n_ouvre_ni_le_projet_ni_ses_taches(self):
        """Le défaut d'origine : le lien de l'échéancier ouvrait chaque tâche."""
        self.projet.action_bf_gantt_publier()
        tache = self.projet.task_ids[:1]
        jeton = self._jeton()
        self.env.flush_all()
        self.assertEqual(self.url_open(
            self._url("project", self.projet, token=jeton)).status_code, 200)
        for adresse in ("/my/projects/%s" % self.projet.id,
                        "/my/projects/%s/task/%s" % (self.projet.id, tache.id),
                        "/my/tasks/%s" % tache.id):
            reponse = self.url_open("%s?access_token=%s" % (adresse, jeton),
                                    allow_redirects=False)
            self.assertNotEqual(reponse.status_code, 200, adresse)
            self.assertNotIn("Tâche visible au portail", reponse.text, adresse)

    def test_le_client_qui_suit_le_projet_l_ouvre_sans_jeton(self):
        usager, partenaire = self._client_portail()
        self.projet.privacy_visibility = "portal"
        self.projet.message_subscribe(partner_ids=[partenaire.id])
        self.projet.action_bf_gantt_publier()
        self.env.flush_all()
        self.authenticate(usager.login, usager.login)
        reponse = self.url_open("/mon/echeancier/project/%s" % self.projet.id,
                                allow_redirects=False)
        self.assertEqual(reponse.status_code, 200)

    def test_un_client_qui_ne_suit_pas_le_projet_est_refuse_sans_jeton(self):
        usager, _partenaire = self._client_portail("Client étranger")
        self.projet.action_bf_gantt_publier()
        self.env.flush_all()
        self.authenticate(usager.login, usager.login)
        reponse = self.url_open("/mon/echeancier/project/%s" % self.projet.id,
                                allow_redirects=False)
        self.assertIn(reponse.status_code, (301, 302, 303))

    def test_le_jeton_est_reserve_au_groupe_de_gestion(self):
        from odoo.exceptions import AccessError
        lecteur = new_test_user(self.env, login="lecteur_echeancier",
                                groups="project.group_project_manager")
        self.projet.action_bf_gantt_publier()
        with self.assertRaises(AccessError):
            self.projet.with_user(lecteur).read(["bf_gantt_token"])

    def test_la_migration_change_le_jeton_des_echeanciers_publies(self):
        import importlib.util
        from odoo.tools import file_path
        chemin = file_path("bf_gantt/migrations/18.0.1.7.0/post-migrate.py")
        spec = importlib.util.spec_from_file_location("bf_gantt_migration_170", chemin)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)

        publie = self.projet
        publie.bf_gantt_published = True
        publie._portal_ensure_token()
        # Publié puis dépublié en 1.6.1 : le lien distribué gardait son jeton.
        depublie = self.env["project.project"].create({"name": "Projet dépublié"})
        depublie.bf_gantt_published = True
        self.env.flush_all()
        depublie.bf_gantt_published = False
        depublie._portal_ensure_token()
        tache = self.env["project.task"].create({"name": "Tâche vue", "project_id": depublie.id})
        tache._portal_ensure_token()
        piece = self.env["ir.attachment"].create({
            "name": "piece.txt", "raw": b"x", "res_model": "project.task", "res_id": tache.id})
        piece.generate_access_token()
        jamais = self.env["project.project"].create({"name": "Projet jamais publié"})
        jamais._portal_ensure_token()
        tache_jamais = self.env["project.task"].create({"name": "Autre", "project_id": jamais.id})
        tache_jamais._portal_ensure_token()
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE project_project SET bf_gantt_published = NULL WHERE id = %s", (jamais.id,))
        gardes = (jamais.access_token, tache_jamais.access_token)

        migration.migrate(self.env.cr, "18.0.1.6.1")
        self.env.invalidate_all()

        self.assertFalse(publie.access_token)
        self.assertTrue(publie.bf_gantt_token)
        self.assertFalse(depublie.access_token)
        self.assertFalse(depublie.bf_gantt_token)
        self.assertFalse(tache.access_token)
        self.assertFalse(piece.access_token)
        self.assertEqual((jamais.access_token, tache_jamais.access_token), gardes)
