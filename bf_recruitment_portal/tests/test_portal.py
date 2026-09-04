# Part of bf_recruitment_portal. Voir LICENSE.
"""Ce qu'on prouve : la bonne personne voit la bonne chose au bon moment.

Trois invariants, et chacun a son contrôle qui DISCRIMINE :
  * avant la décision, aucune appréciation ne sort ;
  * après la décision, le cahier sort sans le nom des évaluateurs ;
  * un jeton qui ne correspond pas n'ouvre rien.
"""

import re
from unittest.mock import patch

from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestPortailCandidat(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref("base.main_company")
        cls.recruteur = cls.env["res.users"].create({
            "name": "Anouk Lemieux", "login": "recruteur_portal",
            "email": "anouk@exemple.invalid",
            "groups_id": [(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref("hr_recruitment.group_hr_recruitment_user").id,
            ])],
        })
        cls.panel = cls.env["res.users"].create({
            "name": "Bruno Panelliste", "login": "panel_portal",
            "email": "bruno@exemple.invalid",
            "groups_id": [(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref("hr_recruitment.group_hr_recruitment_interviewer").id,
            ])],
        })
        cls.job = cls.env["hr.job"].create({
            "name": "Conseiller TI", "company_id": cls.company.id})
        cls.guide = cls.env["bf.interview.guide"].create({
            "name": "Grille portail", "round_type": "technique", "scale_max": 5,
            "company_id": cls.company.id,
            "criterion_ids": [(0, 0, {"name": "Diagnostic", "weight": 1.0})],
        })
        cls.guide.action_publish()
        cls.candidate = cls.env["hr.candidate"].create({
            "partner_name": "Camille Sanschagrin",
            "email_from": "camille@exemple.invalid", "company_id": cls.company.id})
        cls.applicant = cls.env["hr.applicant"].create({
            "candidate_id": cls.candidate.id, "job_id": cls.job.id,
            "company_id": cls.company.id, "user_id": cls.recruteur.id})
        cls.interview = cls.env["bf.interview"].create({
            "applicant_id": cls.applicant.id, "guide_id": cls.guide.id,
            "company_id": cls.company.id,
            "interviewer_ids": [(6, 0, [cls.panel.id])],
            "date_start": "2026-08-25 14:00:00"})
        note = cls.interview.rating_line_ids
        note.with_user(cls.panel).write({"score": 4, "comment": "Explique clairement."})
        cls.interview.with_user(cls.panel).action_submit()
        cls.interview.action_mark_held()

    def setUp(self):
        super().setUp()
        # ⚠️ Les limiteurs du contrôleur sont des globales de module, partagées
        # entre tous les tests du passage. Sans cette remise à zéro, le repos de
        # 30 s posé par un test refuse l'envoi du suivant, et l'échec accuse le
        # code au lieu d'accuser l'ordre des tests. Un test qui dépend de l'état
        # d'un limiteur ne teste plus ce qu'il croit tester.
        from odoo.addons.bf_recruitment_portal.controllers import portal as _p
        for limiteur in (_p._echecs, _p._envois, _p._repos):
            limiteur._data.clear()

    def _decider(self):
        motif = self.env.ref("hr_recruitment.email_template_data_applicant_refuse")
        raison = self.env["hr.applicant.refuse.reason"].search(
            [("template_id", "=", motif.id)], limit=1)
        self.applicant.with_user(self.recruteur).write({
            "refuse_reason_id": raison.id,
            "decision_note": "Habilitation absente. Le reste du dossier est solide.",
            "date_closed": "2026-08-28 16:00:00",
        })
        self.applicant.invalidate_recordset()

    def _url(self, suffixe=""):
        jeton = self.applicant.sudo()._portal_ensure_token()
        return "/my/candidature/%s%s?access_token=%s" % (self.applicant.id, suffixe, jeton)

    # ------------------------------------------------------------------

    def test_before_the_decision_nothing_is_disclosed(self):
        """🔴 Le garde du module. Avant la décision, aucune appréciation."""
        self.assertFalse(self.applicant._portal_decision_taken())
        self.assertEqual(self.applicant._portal_interviews(), [])
        self.assertEqual(self.applicant._portal_summary()["decision_note"], "")

        reponse = self.url_open(self._url())
        self.assertEqual(reponse.status_code, 200)
        self.assertIn("à l'étude", reponse.text)
        self.assertNotIn("Explique clairement", reponse.text)
        self.assertNotIn(self.panel.name, reponse.text)

    def test_the_book_is_refused_before_the_decision(self):
        """Servir le cahier ici contournerait le garde du modèle."""
        reponse = self.url_open(self._url("/cahier"), allow_redirects=False)
        self.assertIn(reponse.status_code, (301, 302, 303))
        self.assertNotIn("application/pdf", reponse.headers.get("Content-Type", ""))

    def test_after_the_decision_the_file_opens(self):
        self._decider()
        self.assertTrue(self.applicant._portal_decision_taken())
        seances = self.applicant._portal_interviews()
        self.assertEqual(len(seances), 1)

        reponse = self.url_open(self._url())
        self.assertEqual(reponse.status_code, 200)
        self.assertIn("Habilitation absente", reponse.text)
        self.assertIn("Non retenue", reponse.text)

    def test_the_page_never_names_an_evaluator(self):
        """🔴 Le nom des évaluateurs porte sur des TIERS.

        La liste blanche du modèle ne rend qu'un effectif de panel. Ce contrôle
        tombe si quelqu'un passe l'enregistrement au gabarit.
        """
        self._decider()
        reponse = self.url_open(self._url())
        self.assertNotIn(self.panel.name, reponse.text)
        self.assertNotIn("Explique clairement", reponse.text)
        for seance in self.applicant._portal_interviews():
            self.assertNotIn("user_id", seance)
            self.assertIn("panel_size", seance)

    def test_the_book_downloads_after_the_decision(self):
        self._decider()
        reponse = self.url_open(self._url("/cahier"))
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse.headers.get("Content-Type"), "application/pdf")
        self.assertGreater(len(reponse.content), 1000)

    def test_a_wrong_token_opens_nothing(self):
        """Le contrôle qui prouve que le jeton discrimine."""
        self._decider()
        reponse = self.url_open(self._url_mauvais_jeton(), allow_redirects=False)
        self.assertEqual(reponse.status_code, 404)
        self.assertNotIn("Habilitation absente", reponse.text or "")
        self.assertIn("ne mène plus à rien", reponse.text or "")

    def test_no_token_at_all_opens_nothing(self):
        self._decider()
        reponse = self.url_open(
            "/my/candidature/%s" % self.applicant.id, allow_redirects=False)
        self.assertEqual(reponse.status_code, 404)
        self.assertNotIn("Habilitation absente", reponse.text or "")

    # ------------------------------------------------------------------
    # Le lien qui ne mène plus à rien
    # ------------------------------------------------------------------

    def _url_mauvais_jeton(self, applicant_id=None):
        return "/my/candidature/%s?access_token=%s" % (
            applicant_id or self.applicant.id, "0" * 32)

    def test_a_dead_link_lands_on_a_page_and_not_on_a_login_form(self):
        """🔴 Le défaut que ce correctif ferme.

        Avant, l'échec d'accès redirigeait vers `/my`, qui pour un visiteur
        sans compte est la page de CONNEXION. Le lien de la lettre de refus se
        lisait donc comme un compte cassé plutôt que comme une conservation
        arrivée à son terme.
        """
        reponse = self.url_open(self._url_mauvais_jeton(), allow_redirects=False)
        corps = reponse.text or ""
        self.assertEqual(reponse.status_code, 404)
        self.assertIn("ne mène plus à rien", corps)
        self.assertIn("calendrier de conservation", corps)
        self.assertNotIn(
            'name="login"', corps,
            "La page ne doit pas être un formulaire de connexion.",
        )

    def test_the_dead_link_page_names_nobody(self):
        self._decider()
        corps = self.url_open(
            self._url_mauvais_jeton(), allow_redirects=False).text or ""
        for nom in (self.candidate.partner_name, self.panel.name,
                    self.recruteur.name, "Habilitation absente"):
            self.assertNotIn(nom, corps)

    def test_a_destroyed_file_answers_exactly_like_a_wrong_token(self):
        """🔴 L'anti-oracle, et c'est ce qui fait la valeur de la page.

        Servir « ce dossier a été détruit » pour un identifiant qui a existé, et
        autre chose pour un identifiant inventé, dirait à qui essaie des numéros
        lesquels ont porté une candidature. Les deux réponses doivent être
        indiscernables, corps compris.
        """
        invente = 2 ** 30
        faux = self.url_open(self._url_mauvais_jeton(invente), allow_redirects=False)
        vrai = self.url_open(self._url_mauvais_jeton(), allow_redirects=False)
        self.assertEqual(faux.status_code, vrai.status_code)

        # ⚠️ Deux choses diffèrent entre deux réponses sans rien dire de
        # l'identifiant demandé, et il faut neutraliser les DEUX :
        #   * l'URL canonique et l'`og:url`, que la mise en page renvoie telles
        #     qu'on les a demandées, donc telles que le visiteur les a écrites ;
        #   * le **jeton CSRF**, qui change d'une requête à l'autre.
        # 🔴 Le jeton a rendu ce test INSTABLE : il est passé plusieurs fois de
        # suite, puis il est tombé sur la démo. Un test qui compare des corps
        # HTTP bruts compare aussi tout ce qui varie sans rien vouloir dire.
        # 🔴 Et on normalise le CHEMIN, pas le NOMBRE. Un `replace(str(id))`
        # aveugle a fait tomber ce test une fois sur trois : l'identifiant est
        # court, et il apparaît au milieu du numéro de version d'un paquet
        # d'assets (`/6575968/web.__assets…`), que le remplacement coupait en
        # deux. Le test accusait alors la page d'être un oracle, pour un chiffre
        # qui n'avait aucun rapport avec elle.
        def _comparable(reponse, _identifiant=None):
            corps = reponse.text or ""
            corps = re.sub(r"/my/candidature/\d+", "/my/candidature/<id>", corps)
            return re.sub(r'csrf_token: "[^"]*"', 'csrf_token: "<jeton>"', corps)

        # ⚠️ La garde du normaliseur : s'il devenait trop gourmand, il rendrait
        # deux chaînes vides égales et ce contrôle passerait sans rien prouver.
        self.assertIn(
            "ne mène plus à rien", _comparable(vrai),
            "Le normaliseur a mangé la page : la comparaison ne prouve plus rien.",
        )
        self.assertEqual(
            _comparable(faux),
            _comparable(vrai),
            "Un identifiant qui a existé et un identifiant inventé doivent "
            "rendre la MÊME page, sinon la page est un oracle.",
        )

    def test_the_book_route_lands_on_the_same_page(self):
        """La lettre de refus envoie aussi vers le cahier : les deux routes
        doivent se comporter pareil quand le dossier n'est plus là."""
        reponse = self.url_open(
            "/my/candidature/%s/cahier?access_token=%s"
            % (2 ** 30, "0" * 32), allow_redirects=False)
        self.assertEqual(reponse.status_code, 404)
        self.assertIn("ne mène plus à rien", reponse.text or "")

    def test_the_second_door_is_an_invitation(self):
        """⚠️ L'instance refuse les inscriptions libres. L'invitation passe.

        `signup_prepare()` pose un jeton d'inscription sur le partenaire, ce
        qui fonctionne même avec `auth_signup.allow_uninvited = False`. Sans
        ça, le bouton « Créer mon compte » mènerait à un refus.
        """
        self.env["ir.config_parameter"].sudo().set_param(
            "auth_signup.allow_uninvited", "False")
        # 🔴 Le PREMIER clic est le seul qui compte. `signup_prepare()` écrit
        # `signup_type` de façon persistante : sans cette remise à zéro, le
        # contrôle passerait sur la seule foi d'un appel antérieur, et le
        # défaut (la porte qui mène à la connexion) resterait invisible.
        self.applicant.sudo().partner_id.write({"signup_type": False})
        url = self.applicant._portal_signup_url()
        self.assertTrue(url, "aucune invitation produite")
        self.assertIn("/web/signup", url)
        self.assertIn("token=", url,
                      "sans jeton, l'invitation vaut une inscription libre, "
                      "que l'instance refuse")

    def test_the_access_url_is_the_candidate_one(self):
        self.assertEqual(
            self.applicant.access_url, "/my/candidature/%s" % self.applicant.id)

    # ------------------------------------------------------------------
    # 🔴 Les deux fonctions qui répondent « y a-t-il une décision »
    # ------------------------------------------------------------------

    def test_the_state_never_says_under_review_once_a_decision_is_taken(self):
        """L'invariant que le défaut violait.

        `_portal_decision_taken()` lit trois signaux, `_portal_state()` n'en
        lisait que deux. Une personne embauchée sans dossier d'employé passait
        donc le garde, voyait ses séances et téléchargeait son cahier, pendant
        que la page lui disait « À l'étude ». On éprouve les huit combinaisons
        plutôt que le seul cas qu'on a rencontré.
        """
        motif = self.env.ref("hr_recruitment.email_template_data_applicant_refuse")
        raison = self.env["hr.applicant.refuse.reason"].search(
            [("template_id", "=", motif.id)], limit=1)
        employe = self.env["hr.employee"].create({
            "name": "Employée créée depuis la candidature",
            "company_id": self.company.id,
        })
        combinaisons = [
            {},
            {"decision_date": "2026-08-28 16:00:00"},
            {"date_closed": "2026-08-28 16:00:00"},
            {"refuse_reason_id": raison.id,
             "decision_note": "Motif écrit, exigé après une entrevue tenue."},
            {"employee_id": employe.id},
            {"date_closed": "2026-08-28 16:00:00", "employee_id": employe.id},
        ]
        for valeurs in combinaisons:
            app = self.applicant.sudo().copy({"active": True})
            app.write(dict(valeurs))
            app.invalidate_recordset()
            if app._portal_decision_taken():
                self.assertNotEqual(
                    app._portal_state(), "en_cours",
                    "Décision prise et page qui annonce « À l'étude » : les "
                    "deux fonctions se contredisent, pour %s" % valeurs,
                )
            else:
                self.assertEqual(app._portal_state(), "en_cours", str(valeurs))

    def test_a_hire_without_an_employee_record_reads_as_hired(self):
        """Le cas exact rencontré sur la démo : embauche saisie, dossier
        d'employé pas encore créé."""
        self.applicant.sudo().write({"date_closed": "2026-08-28 16:00:00"})
        self.applicant.invalidate_recordset()
        self.assertTrue(self.applicant._portal_decision_taken())
        self.assertEqual(self.applicant._portal_state(), "retenue")
        self.assertEqual(
            self.applicant._portal_summary()["state_label"], "Retenue")

    # ------------------------------------------------------------------
    # Les deux interrupteurs du locataire
    # ------------------------------------------------------------------

    def _company(self, **valeurs):
        self.company.sudo().write(valeurs)
        self.applicant.invalidate_recordset()

    def test_the_book_button_disappears_when_the_tenant_turns_it_off(self):
        self._decider()
        self._company(recruitment_portal_book_enabled=False)
        corps = self.url_open(self._url(), allow_redirects=False).text or ""
        self.assertNotIn("/cahier", corps)
        self.assertIn("ne se télécharge pas", corps)
        self.assertIn(
            "Non retenue", corps,
            "La décision et le motif restent servis : l'interrupteur retire le "
            "libre-service, pas le droit d'accès.",
        )

    def test_hiding_the_button_is_not_access_control(self):
        """🔴 Le contrôle qui compte. Un bouton caché se contourne en tapant
        l'adresse ; la ROUTE doit refuser aussi."""
        self._decider()
        self._company(recruitment_portal_book_enabled=False)
        reponse = self.url_open(self._url("/cahier"), allow_redirects=False)
        self.assertNotEqual(
            reponse.status_code, 200,
            "Le cahier est servi alors que le locataire l'a décroché : le "
            "gabarit cachait le bouton et la route ne gardait rien.",
        )
        self.assertNotIn("pdf", (reponse.headers.get("Content-Type") or "").lower())

    def test_the_book_still_downloads_when_the_tenant_leaves_it_on(self):
        """La paire. Sans elle, un module qui refuse TOUJOURS passerait."""
        self._decider()
        self._company(recruitment_portal_book_enabled=True)
        reponse = self.url_open(self._url("/cahier"), allow_redirects=False)
        self.assertEqual(reponse.status_code, 200)
        self.assertIn("pdf", (reponse.headers.get("Content-Type") or "").lower())

    # ------------------------------------------------------------------
    # Le code à usage unique
    # ------------------------------------------------------------------

    def test_without_the_setting_the_link_opens_straight_away(self):
        self._decider()
        self._company(recruitment_portal_otp_required=False)
        corps = self.url_open(self._url(), allow_redirects=False).text or ""
        self.assertNotIn("Un code pour ouvrir votre dossier", corps)

    def test_with_the_setting_the_link_alone_opens_nothing(self):
        self._decider()
        self._company(recruitment_portal_otp_required=True)
        reponse = self.url_open(self._url(), allow_redirects=False)
        corps = reponse.text or ""
        self.assertEqual(
            reponse.status_code, 200,
            "🔴 La barrière RENVOIE une page. Rediriger vers la page qu'elle "
            "garde boucle à l'infini.",
        )
        self.assertIn("Un code pour ouvrir votre dossier", corps)
        self.assertNotIn(
            "Habilitation absente", corps,
            "Le motif de la décision ne doit pas fuiter par la page du code.",
        )

    def test_the_gate_also_holds_the_book(self):
        self._decider()
        self._company(recruitment_portal_otp_required=True)
        reponse = self.url_open(self._url("/cahier"), allow_redirects=False)
        self.assertNotIn("pdf", (reponse.headers.get("Content-Type") or "").lower())

    def test_the_code_page_never_publishes_the_whole_address(self):
        """⚠️ Publier l'adresse entière la donnerait à qui détient un lien qui a
        fuité, c'est-à-dire exactement la personne contre qui le code existe."""
        self._decider()
        self._company(recruitment_portal_otp_required=True)
        corps = self.url_open(self._url(), allow_redirects=False).text or ""
        self.assertNotIn(self.candidate.email_from, corps)

    def test_the_hint_shows_enough_to_recognise_and_no_more(self):
        indice = self.applicant.sudo()._portal_otp_indice()
        self.assertTrue(indice.endswith("@exemple.invalid"))
        self.assertNotIn("camille", indice)

    def _demander_un_code(self):
        """Passe par la VRAIE route d'envoi, en interceptant le courriel.

        ⚠️ On remplace `_portal_otp_email` plutôt que de laisser partir un
        message : une suite de tests qui écrit au relais SMTP est une suite qui
        envoie du courrier à chaque passage. Et l'interception est le seul
        moyen d'obtenir le code, que `_portal_otp_send` ne rend jamais, à
        dessein.
        """
        captures = []

        def _capturer(self_rec, adresse, code):
            captures.append((adresse, code))

        with patch.object(type(self.applicant), "_portal_otp_email",
                          _capturer, create=False):
            # ⚠️ `data={"envoyer": "1"}` et NON `data={}` : `url_open` fait
            # `if data or files` pour choisir POST, et un dictionnaire vide est
            # faux. Une requête partie en GET se heurte au 405 de la route, et
            # l'échec accuse le contrôleur plutôt que l'appel.
            reponse = self.url_open(self._url("/code"), data={"envoyer": "1"},
                                    allow_redirects=False)
        self.assertTrue(captures, "Aucun code produit (HTTP %s)." % reponse.status_code)
        return captures[0][1], reponse

    def test_a_wrong_code_opens_nothing_and_the_right_one_opens(self):
        """La paire qui prouve que le code DISCRIMINE."""
        self._decider()
        self._company(recruitment_portal_otp_required=True)
        code, page = self._demander_un_code()
        self.assertNotIn(
            code, page.text or "",
            "🔴 Le code ne doit JAMAIS s'afficher sur la page qui le demande.",
        )

        faux = self.url_open(self._url("/verifier"), data={"code": "000000"},
                             allow_redirects=False)
        self.assertNotEqual(faux.status_code, 303)
        self.assertIn("ne correspond pas", faux.text or "")

        bon = self.url_open(self._url("/verifier"), data={"code": code},
                            allow_redirects=False)
        self.assertEqual(bon.status_code, 303, "Le bon code doit ouvrir.")
        ouverte = self.url_open(self._url(), allow_redirects=False)
        self.assertIn("Non retenue", ouverte.text or "")
        self.assertNotIn("Un code pour ouvrir", ouverte.text or "")

    def test_the_code_is_sent_to_the_file_address_and_not_a_supplied_one(self):
        """⚠️ L'adresse ne se choisit pas : sinon la route devient un service
        d'envoi vers l'adresse de son choix, sous notre nom."""
        self._decider()
        self._company(recruitment_portal_otp_required=True)
        captures = []

        def _capturer(self_rec, adresse, code):
            captures.append(adresse)

        with patch.object(type(self.applicant), "_portal_otp_email",
                          _capturer, create=False):
            self.url_open(self._url("/code"),
                          data={"email": "ailleurs@exemple.invalid"},
                          allow_redirects=False)
        self.assertEqual(captures, [self.candidate.email_from])

    def test_the_hash_is_keyed_not_a_bare_digest(self):
        """🔴 Un code de six chiffres condensé sans clé se retrouve en une
        seconde. `bf_securetransfer` a déjà payé cette leçon."""
        import hashlib
        Modele = self.env["hr.applicant"]
        empreinte = Modele._portal_otp_hash("123456")
        self.assertNotEqual(
            empreinte, hashlib.sha256(b"123456").hexdigest())
        self.assertNotEqual(
            empreinte,
            hashlib.sha256(b"bf_recruitment_portal_otp:123456").hexdigest())
        self.assertEqual(empreinte, Modele._portal_otp_hash("123456"))
        self.assertNotEqual(empreinte, Modele._portal_otp_hash("123457"))

