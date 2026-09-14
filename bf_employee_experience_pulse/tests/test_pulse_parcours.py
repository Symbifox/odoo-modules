"""Le parcours réel, joué dans un navigateur, par une personne CONNECTÉE.

C'est l'essai qui porte la raison d'être du module. Le piège qu'il vérifie
n'apparaît pas en `TransactionCase` : il tient au fait que le contrôleur
public d'Odoo lit la session, et une session n'existe que sur un vrai appel
HTTP.
"""

import html
import re

from odoo.tests.common import HttpCase, tagged

from .common import PulseCase

MOTIF_CSRF = re.compile(
    r'name="csrf_token"\s+value="([^"]+)"'
)


@tagged("post_install", "-at_install")
class TestPulseParcours(PulseCase, HttpCase):

    def setUp(self):
        super().setUp()
        self.campaign = self._campaign()
        self.campaign.action_open()
        self.invitation = self.campaign.invitation_ids[0]
        self.employe = self.invitation.employee_id
        self.utilisateur = self.env["res.users"].create({
            "name": self.employe.name,
            "login": "pulse-repondant",
            "password": "pulse-repondant-mdp",
            "company_ids": [(6, 0, [self.company.id])],
            "company_id": self.company.id,
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
        })
        self.employe.user_id = self.utilisateur
        self.env.cr.flush()

    def _jeton_csrf(self, page):
        trouve = MOTIF_CSRF.search(page)
        self.assertTrue(trouve, "le formulaire ne porte pas de jeton CSRF")
        return trouve.group(1)

    def test_une_personne_connectee_repond_sans_se_faire_nommer(self):
        """Le cœur du module.

        Le sondage natif d'Odoo aurait inscrit le partenaire, le courriel et
        le nom de cette personne, parce que son contrôleur public passe
        `request.env.user` à `_create_answer`. Ici, la session est ouverte et
        la réponse reste muette sur son auteur.
        """
        self.authenticate("pulse-repondant", "pulse-repondant-mdp")
        page = self.url_open("/pulse/%s" % self.invitation.token).text
        self.assertIn("Comment ça va", page)
        reponse = self.url_open(
            "/pulse/%s/repondre" % self.invitation.token,
            data={
                "csrf_token": self._jeton_csrf(page),
                "q%s" % self.question_scale.id: "6",
                "q%s" % self.question_text.id: "Les semaines sont longues.",
                "q%s" % self.question_enps.id: "9",
            },
        )
        self.assertEqual(reponse.status_code, 200)
        self.assertIn("C'est envoyé", reponse.text)

        self.invitation.invalidate_recordset()
        self.assertTrue(self.invitation.used)

        sas = self.env["bf.ex.pulse.staging"].sudo().search([
            ("campaign_id", "=", self.campaign.id),
        ])
        self.assertEqual(len(sas), 3)
        for ligne in sas:
            for champ in ligne._fields.values():
                if champ.relational and champ.comodel_name in (
                        "res.users", "res.partner", "hr.employee"):
                    self.fail(
                        "le sas porte un chemin vers une personne : %s"
                        % champ.name
                    )

        self.campaign._flush_staged(force=True)
        reponses = self.env["bf.ex.pulse.answer"].sudo().search([
            ("campaign_id", "=", self.campaign.id),
        ])
        self.assertEqual(len(reponses), 3)
        self.env.cr.execute(
            "SELECT * FROM bf_ex_pulse_answer WHERE campaign_id = %s LIMIT 1",
            (self.campaign.id,),
        )
        colonnes = {d[0] for d in self.env.cr.description}
        for interdit in ("create_uid", "write_uid", "create_date",
                         "write_date", "employee_id", "partner_id", "user_id",
                         "token"):
            self.assertNotIn(interdit, colonnes)

    def test_un_jeton_ne_sert_qu_une_fois(self):
        self.authenticate("pulse-repondant", "pulse-repondant-mdp")
        page = self.url_open("/pulse/%s" % self.invitation.token).text
        self.url_open(
            "/pulse/%s/repondre" % self.invitation.token,
            data={
                "csrf_token": self._jeton_csrf(page),
                "q%s" % self.question_scale.id: "6",
            },
        )
        deuxieme = self.url_open("/pulse/%s" % self.invitation.token)
        self.assertIn("déjà répondu", deuxieme.text)
        self.assertEqual(
            self.env["bf.ex.pulse.staging"].sudo().search_count(
                [("campaign_id", "=", self.campaign.id)]), 1,
        )

    def test_un_jeton_inconnu_n_ecrit_rien(self):
        # Authentifié : c'est le cas réel (un employé a une session ouverte),
        # et un appel sans session dépend de la résolution de base du serveur,
        # ce qui rend l'essai sensible au `dbfilter` du serveur, pas au code.
        self.authenticate("pulse-repondant", "pulse-repondant-mdp")
        avant = self.env["bf.ex.pulse.staging"].sudo().search_count([])
        page = self.url_open("/pulse/ce-jeton-n-existe-pas")
        self.assertEqual(page.status_code, 200)
        self.assertIn("n'est pas valide", page.text)
        self.assertEqual(
            self.env["bf.ex.pulse.staging"].sudo().search_count([]), avant,
        )

    def test_une_note_hors_bornes_est_ecartee(self):
        self.authenticate("pulse-repondant", "pulse-repondant-mdp")
        page = self.url_open("/pulse/%s" % self.invitation.token).text
        self.url_open(
            "/pulse/%s/repondre" % self.invitation.token,
            data={
                "csrf_token": self._jeton_csrf(page),
                "q%s" % self.question_scale.id: "42",
                "q%s" % self.question_enps.id: "7",
            },
        )
        notes = self.env["bf.ex.pulse.staging"].sudo().search([
            ("campaign_id", "=", self.campaign.id),
        ]).mapped("value_scale")
        self.assertEqual(notes, [7])

    def test_une_vague_fermee_ne_recoit_plus_rien(self):
        self.authenticate("pulse-repondant", "pulse-repondant-mdp")
        self.campaign.action_close()
        page = self.url_open("/pulse/%s" % self.invitation.token)
        self.assertIn("fermée", page.text)

    def test_contre_exemple_le_sondage_natif_nomme_le_repondant(self):
        """Le contre-exemple qui justifie tout le module.

        Si cet essai se met un jour à échouer, c'est qu'Odoo a corrigé son
        sondage, et la question de garder ce module se repose.
        """
        Module = self.env["ir.module.module"].sudo()
        survey = Module.search([("name", "=", "survey"),
                                ("state", "=", "installed")], limit=1)
        if not survey:
            self.skipTest("le module survey n'est pas installé ici")
        sondage = self.env["survey.survey"].sudo().create({
            "title": "Témoin : anonymat du natif",
            "access_mode": "public",
            "users_login_required": False,
        })
        reponse = sondage._create_answer(user=self.utilisateur)
        self.assertEqual(
            reponse.partner_id, self.utilisateur.partner_id,
            "le sondage natif n'inscrit plus le partenaire : à revérifier",
        )
        self.assertEqual(reponse.nickname, self.utilisateur.name)

    def test_la_note_zero_peut_etre_donnee(self):
        """🔴 Défaut trouvé en production, pas par un essai.

        QWeb RETIRE un attribut dont la valeur est fausse, et 0 est faux : le
        bouton 0 partait sans `value`, un navigateur envoyait « on » (norme
        HTML pour un bouton radio sans valeur), et le contrôleur jetait la
        réponse en silence. La note la plus NÉGATIVE de l'échelle était la
        seule impossible à donner, la personne n'était même pas comptée comme
        ayant répondu, et la relance continuait de la viser.

        Sur un sondage d'humeur, perdre systématiquement les pires réponses
        est le pire défaut possible : il remonte l'eNPS et tait exactement ce
        que le module existe pour entendre.

        L'essai lit la PAGE et rejoue ce que le navigateur enverrait, au lieu
        de poster une valeur choisie à la main : c'est le seul moyen de voir un
        attribut manquant.
        """
        self.authenticate("pulse-repondant", "pulse-repondant-mdp")
        page = self.url_open("/pulse/%s" % self.invitation.token).text

        bouton_zero = re.search(
            r'<input type="radio"[^>]*name="q%s"[^>]*value="0"[^>]*/?>'
            % self.question_scale.id, page)
        self.assertTrue(
            bouton_zero,
            "le bouton 0 n'a pas d'attribut value : un navigateur enverrait "
            "« on » et la réponse serait jetée",
        )

        reponse = self.url_open(
            "/pulse/%s/repondre" % self.invitation.token,
            data={
                "csrf_token": self._jeton_csrf(page),
                "q%s" % self.question_scale.id: "0",
                "q%s" % self.question_enps.id: "0",
            },
        )
        self.assertIn("C'est envoyé", reponse.text)
        self.invitation.invalidate_recordset()
        self.assertTrue(
            self.invitation.used,
            "une personne qui répond 0 n'est pas comptée comme ayant répondu",
        )
        self.campaign._flush_staged(force=True)
        notes = self.env["bf.ex.pulse.answer"].sudo().search([
            ("campaign_id", "=", self.campaign.id),
        ]).mapped("value_scale")
        self.assertEqual(sorted(notes), [0, 0], "les deux zéros sont perdus")

    def test_les_onze_boutons_de_l_echelle_portent_leur_valeur(self):
        """Le même défaut, vérifié sur toute l'échelle plutôt que sur un seul
        bouton : 0 était le seul cassé, mais rien ne garantissait que ce soit
        le seul."""
        self.authenticate("pulse-repondant", "pulse-repondant-mdp")
        page = self.url_open("/pulse/%s" % self.invitation.token).text
        for note in range(0, 11):
            with self.subTest(note=note):
                self.assertRegex(
                    page,
                    r'<input type="radio"[^>]*name="q%s"[^>]*value="%s"'
                    % (self.question_scale.id, note),
                )

    def test_la_page_porte_la_marque_et_pas_des_valeurs_en_dur(self):
        """Le branding se vérifie sur la page RENDUE, pas dans le gabarit.

        Signalé en revue : la première version servait du system-ui sans
        logo ni couleurs de marque, et le courriel ne ressemblait à rien de
        l'identité visuelle configurée.
        """
        self.authenticate("pulse-repondant", "pulse-repondant-mdp")
        page = self.url_open("/pulse/%s" % self.invitation.token).text

        # 🔴 Ne PAS se contenter de chercher le mot « Lexend » : il figure dans
        # l'appel de police, donc il survit à une déclaration remplacée par du
        # system-ui. Une mutation l'a prouvé. On vérifie CHAQUE déclaration.
        declarations = re.findall(r"font-family\s*:\s*([^;}]+)", page)
        self.assertTrue(declarations, "la page ne déclare aucune police")
        fautives = [d.strip() for d in declarations
                    if "lexend" not in d.lower() and "inherit" not in d.lower()]
        self.assertFalse(
            fautives,
            "des déclarations de police ne nomment pas la typo maison : %s"
            % fautives,
        )

        self.assertIn("/logo.png?company=", page, "la page n'affiche aucun logo")
        self.assertIn("--bf-accent", page)
        couleurs = self.env["bf.ex.pulse.marque"].couleurs(self.company)
        self.assertIn(couleurs["accent"], page)
        self.assertIn(couleurs["bouton"], page)

    def test_l_accent_ne_porte_jamais_de_texte_blanc(self):
        """🔴 Mesuré : blanc sur le bleu de marque rend 2,62:1, sous AA.

        L'accent brut tient le filet et les bordures; ce qui porte du texte
        prend la variante assombrie. L'essai vérifie le CALCUL, pour qu'un
        locataire avec un accent pâle soit servi correctement lui aussi.
        """
        from odoo.addons.bf_employee_experience_pulse.models.pulse_marque import (
            contraste,
        )
        Marque = self.env["bf.ex.pulse.marque"]
        for accent in ("#29ABE2", "#FFDD00", "#7FCDED", "#714B67"):
            with self.subTest(accent=accent):
                self.company.write({"primary_color": accent})
                couleurs = Marque.couleurs(self.company)
                self.assertGreaterEqual(
                    contraste("#FFFFFF", couleurs["bouton"]), 4.5,
                    "le bouton calculé depuis %s ne passe pas AA" % accent,
                )
                self.assertGreaterEqual(
                    contraste(couleurs["texte_sur_sombre"], couleurs["sombre"]), 4.5,
                    "le texte du bandeau ne passe pas AA",
                )

    def test_le_courriel_d_invitation_porte_la_marque(self):
        """Le gabarit rendu, pas le gabarit écrit."""
        gabarit = self.env.ref(
            "bf_employee_experience_pulse.mail_template_pulse_invite")
        corps = gabarit._render_field(
            "body_html", self.invitation.ids)[self.invitation.id]
        couleurs = self.env["bf.ex.pulse.marque"].couleurs(self.company)

        # Même règle que pour la page : chaque déclaration, pas le mot.
        # ⚠️ Déséchapper d'abord : le rendu écrit les apostrophes en `&#39;`,
        # qui se TERMINE par un point-virgule. Une coupe sur `;` tombait donc
        # au milieu de l'entité et rendait `&#39` en guise de police.
        lisible = html.unescape(corps)
        declarations = re.findall(r"font-family\s*:\s*([^;]+)", lisible)
        self.assertTrue(declarations, "le courriel ne déclare aucune police")
        fautives = [d.strip() for d in declarations if "lexend" not in d.lower()]
        self.assertFalse(
            fautives,
            "des déclarations de police du courriel ne nomment pas la typo "
            "maison : %s" % fautives,
        )

        self.assertIn("/logo.png?company=", corps, "le courriel n'a pas de logo")

        # Le bandeau, vérifié comme FOND d'une cellule et non comme couleur
        # présente quelque part : l'anthracite sert aussi au texte du corps,
        # donc sa seule présence ne prouve rien. Une mutation l'a prouvé.
        self.assertRegex(
            corps,
            r"background-color:\s*%s\s*;\s*padding:16px 24px" % re.escape(
                couleurs["sombre"]),
            "le bandeau du courriel n'est pas au fond anthracite",
        )
        self.assertRegex(
            corps,
            r"background-color:\s*%s\s*;\s*color:\s*#FFFFFF" % re.escape(
                couleurs["bouton"]),
            "le bouton n'emploie pas la variante lisible de l'accent",
        )
        self.assertIn("/pulse/%s" % self.invitation.token, corps)
        self.assertNotRegex(
            corps,
            r"background-color:\s*%s\s*;\s*color:\s*#FFFFFF" % re.escape(
                couleurs["accent"]),
            "du texte blanc est posé sur l'accent brut",
        )

    def test_le_gabarit_en_base_est_bien_celui_du_fichier(self):
        """🔴 Le XML peut dire vrai pendant que la base dit autre chose.

        Le gabarit vit dans un bloc `noupdate="1"` : une correction de son
        corps ne rejoint PAS une installation existante, et les courriels
        continuent de sortir avec l'ancien. Vécu en production sur ce
        module même. La migration `18.0.1.1.1` retire la paire
        enregistrement + `ir.model.data` avant le chargement des données.

        L'essai compare ce que la BASE porte à ce que le module promet, au lieu
        de relire le fichier.
        """
        gabarit = self.env.ref(
            "bf_employee_experience_pulse.mail_template_pulse_invite")
        corps = gabarit.body_html or ""
        self.assertIn(
            "Lexend", corps,
            "le gabarit en base n'est pas la version de marque : une migration "
            "manque, ou le noupdate a gagné",
        )
        # ⚠️ Le corps STOCKÉ porte l'expression QWeb, pas l'URL : `logo.png`
        # n'apparaît qu'au rendu. C'est l'essai du rendu qui vérifie l'URL.
        self.assertIn("marque['logo']", corps)
        self.assertIn("marque['sombre']", corps)

    def test_le_lien_du_courriel_ouvre_le_formulaire(self):
        """🔴 Lacune signalée en revue : les liens des boutons
        étaient-ils testés?

        Non, pas correctement. Les essais lisaient le jeton puis
        RECONSTRUISAIENT l'URL. Un href mal formé, une base erronée, un jeton
        tronqué ou une enveloppe de pisteur de clic seraient passés inaperçus,
        parce que la chaîne du courriel n'était jamais employée.

        Ici, le href est pris tel quel dans le corps RENDU, et c'est lui qu'on
        ouvre. Même famille de faute que le bouton 0 : poster ce qu'on croit
        que la page contient, au lieu de lire ce qu'elle porte vraiment.
        """
        # Authentifié : c'est le cas réel (un employé a une session ouverte),
        # et un appel sans session laisse le serveur résoudre la base tout
        # seul, ce qui rend 404 quand deux bases répondent au même `dbfilter`.
        self.authenticate("pulse-repondant", "pulse-repondant-mdp")

        gabarit = self.env.ref(
            "bf_employee_experience_pulse.mail_template_pulse_invite")
        corps = gabarit._render_field(
            "body_html", self.invitation.ids)[self.invitation.id]

        liens = re.findall(r'<a\b[^>]*href="([^"]+)"', corps)
        vers_pulse = [l for l in liens if "/pulse/" in l]
        self.assertTrue(vers_pulse, "le courriel ne porte aucun lien vers le pulse")
        url = html.unescape(vers_pulse[0])

        # Le lien doit être ABSOLU : un client mail n'a pas de base.
        self.assertTrue(
            url.startswith("http://") or url.startswith("https://"),
            "le lien du courriel est relatif, il ne s'ouvrira pas depuis un "
            "client mail : %s" % url,
        )
        self.assertNotIn("//pulse", url, "double barre dans le chemin")
        self.assertIn(self.invitation.token, url, "le jeton n'est pas entier")

        # Et on l'OUVRE, par son chemin, plutôt que d'en fabriquer un.
        # On garde le CHEMIN du lien reçu, sans le refabriquer : le nom
        # d'hôte d'essai n'est pas celui de la production, mais le chemin,
        # lui, est exactement celui que le courriel porte.
        chemin = "/" + url.split("/", 3)[-1]
        page = self.url_open(chemin)
        self.assertEqual(page.status_code, 200)
        self.assertIn("Comment ça va", page.text)

        # Puis on poste vers l'action que le formulaire déclare lui-même.
        action = re.search(r'<form[^>]*action="([^"]+)"', page.text)
        self.assertTrue(action, "le formulaire ne déclare pas d'action")
        reponse = self.url_open(
            html.unescape(action.group(1)),
            data={
                "csrf_token": self._jeton_csrf(page.text),
                "q%s" % self.question_scale.id: "0",
            },
        )
        self.assertIn("C'est envoyé", reponse.text)
        self.invitation.invalidate_recordset()
        self.assertTrue(self.invitation.used)

    def test_l_image_du_logo_est_une_url_absolue(self):
        """Une image relative ne s'affiche pas dans un client mail."""
        gabarit = self.env.ref(
            "bf_employee_experience_pulse.mail_template_pulse_invite")
        corps = gabarit._render_field(
            "body_html", self.invitation.ids)[self.invitation.id]
        images = [html.unescape(s) for s in
                  re.findall(r'<img\b[^>]*src="([^"]+)"', corps)]
        self.assertTrue(images, "le courriel ne porte aucune image")
        for src in images:
            with self.subTest(src=src):
                self.assertTrue(
                    src.startswith("http://") or src.startswith("https://")
                    or src.startswith("data:"),
                    "image relative dans un courriel : %s" % src,
                )
