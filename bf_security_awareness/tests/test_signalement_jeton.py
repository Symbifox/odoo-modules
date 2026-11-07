"""Un signalement ne se reconnaît que dans SON PROPRE leurre.

🔴 La branche du jeton de `_detect_simulation` cherchait `token = <jeton>` sans
contrôler à qui le résultat appartient. Or `process()` lit ce résultat pour
écrire AILLEURS : il crédite son propriétaire (`register_report`) et ferme le
signalement en « simulation », ce qui saute toute la séquence de triage — ni
billet, ni activité, ni alerte à l'équipe.

Un employé qui soumettait le `/phish/<jeton>` d'un COLLÈGUE — obtenu en se
faisant simplement transférer le courriel suspect — créditait donc le résultat
du collègue et enterrait son propre signalement en silence.

Signaler le leurre de quelqu'un d'autre est légitime : ça part maintenant au
triage comme n'importe quel signalement.

⚠️ Ces essais vident `report_ntfy_url` : le chemin de triage pousse une alerte
ntfy, et une base copiée d'une production en porte les jetons, avec la sortie
internet ouverte. Sans cela, rejouer ces
essais enverrait de vraies notifications.
"""
from types import SimpleNamespace
from unittest.mock import patch

# Le double de `requests` se pose sur le NOM du module, pas sur le module
# `requests` lui-même : rien d'autre dans le processus n'est touché.
from odoo.addons.bf_security_awareness.models import (
    bf_reported_phish as module_signalement,
)
from odoo.tests import TransactionCase, new_test_user, tagged

TODO = "mail.mail_activity_data_todo"


@tagged("post_install", "-at_install")
class TestSignalementJeton(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param(
            "bf_security_awareness.report_ntfy_url", "")
        cls.employe = new_test_user(
            cls.env, login="employe_essai", name="Employé Signaleur",
            email="employe.essai@exemple.test", groups="base.group_user")
        cls.collegue_partenaire = cls.env["res.partner"].create({
            "name": "Collègue visé", "email": "collegue.essai@exemple.test"})
        cls.modele = cls.env["bf.phishing.template"].create({
            "name": "Leurre Essai", "subject": "Action requise sur votre compte",
            "difficulty": "easy", "landing_mode": "credential"})
        cls.campagne = cls.env["bf.phishing.campaign"].create({
            "name": "Campagne Essai", "template_id": cls.modele.id})

    def _resultat(self, partenaire):
        return self.env["bf.phishing.result"].create({
            "campaign_id": self.campagne.id,
            "partner_id": partenaire.id,
            "email": partenaire.email,
        })

    def _signaler(self, lien=None, sujet=None):
        assistant = self.env["bf.report.phish.wizard"].with_user(self.employe).create({
            "category": "phishing",
            "subject": sujet or "Courriel suspect",
            "suspicious_link": lien,
        })
        self.env.invalidate_all()
        assistant.action_submit()
        self.env.invalidate_all()
        return self.env["bf.reported.phish"].sudo().search(
            [("reporter_id", "=", self.employe.partner_id.id)], order="id desc", limit=1)

    def _relances(self, signalement):
        todo = self.env.ref(TODO)
        return signalement.activity_ids.filtered(lambda a: a.activity_type_id == todo)

    # -- le défaut -------------------------------------------------------------
    def test_le_jeton_d_un_collegue_ne_credite_personne_et_ouvre_le_triage(self):
        """🔴 Le défaut : le résultat du collègue était crédité et le triage sauté."""
        autrui = self._resultat(self.collegue_partenaire)
        jeton = autrui.token
        self.assertTrue(jeton, "le résultat doit porter un jeton")

        signalement = self._signaler(lien="https://exemple.test/phish/%s" % jeton)

        autrui.invalidate_recordset()
        self.assertFalse(autrui.sudo().reported,
                         "le résultat du collègue ne doit PAS être crédité")
        self.assertEqual(signalement.state, "triage",
                         "le signalement doit partir au triage")
        self.assertFalse(signalement.is_simulation)
        self.assertFalse(signalement.matched_result_id)
        self.assertTrue(self._relances(signalement),
                        "le triage doit poser son activité")

    # -- le chemin légitime, qui ne doit pas casser ---------------------------
    def test_son_propre_jeton_est_reconnu_comme_simulation(self):
        sien = self._resultat(self.employe.partner_id)
        signalement = self._signaler(lien="https://exemple.test/phish/%s" % sien.token)

        sien.invalidate_recordset()
        self.assertTrue(sien.sudo().reported, "son bon réflexe doit être crédité")
        self.assertEqual(signalement.state, "simulation")
        self.assertTrue(signalement.is_simulation)
        self.assertEqual(signalement.matched_result_id, sien)
        self.assertFalse(self._relances(signalement),
                         "une simulation reconnue n'ouvre pas de triage")

    def test_le_sujet_de_son_propre_leurre_est_reconnu_sans_jeton(self):
        """La branche heuristique, déjà bornée au signaleur, continue de marcher."""
        sien = self._resultat(self.employe.partner_id)
        signalement = self._signaler(sujet="Action requise sur votre compte")

        sien.invalidate_recordset()
        self.assertEqual(signalement.state, "simulation")
        self.assertEqual(signalement.matched_result_id, sien)

    # -- l'échappement des jokers ---------------------------------------------
    def test_un_sujet_reduit_a_un_joker_ne_correspond_a_aucun_leurre(self):
        """`%` et `_` sont des jokers pour `ilike` : un sujet « % » correspondait
        à n'importe quel leurre du signaleur."""
        self._resultat(self.employe.partner_id)
        for joker in ("%", "_"):
            with self.subTest(joker=joker):
                signalement = self._signaler(sujet=joker)
                self.assertEqual(
                    signalement.state, "triage",
                    "un sujet réduit à « %s » ne doit correspondre à rien" % joker)
                self.assertFalse(signalement.matched_result_id)

    def test_un_sujet_legitime_portant_un_joker_reste_reconnu(self):
        """Le sens INVERSE de l'échappement.

        L'échappement est mesuré juste dans les deux sens, mais rien ne le
        figeait : un échappement trop zélé — ou posé dans le mauvais ordre,
        l'antislash en dernier, qui échappe ses propres échappements — ferait
        cesser de reconnaître un leurre dont le sujet porte littéralement `_`
        ou `%`. Ce sont des caractères banals dans un sujet de courriel
        (« Facture 100% due_aujourdhui »), et ne plus les reconnaître renvoie
        au triage un employé qui méritait son crédit.
        """
        modele = self.env["bf.phishing.template"].create({
            "name": "Leurre joker Essai",
            "subject": "Facture 100% due_aujourdhui",
            "difficulty": "easy", "landing_mode": "credential"})
        campagne = self.env["bf.phishing.campaign"].create({
            "name": "Campagne joker Essai", "template_id": modele.id})
        sien = self.env["bf.phishing.result"].create({
            "campaign_id": campagne.id,
            "partner_id": self.employe.partner_id.id,
            "email": self.employe.partner_id.email,
        })
        for sujet in ("Facture 100% due_aujourdhui", "100% due_aujourdhui",
                      "due_aujourdhui"):
            with self.subTest(sujet=sujet):
                signalement = self._signaler(sujet=sujet)
                self.assertEqual(
                    signalement.state, "simulation",
                    "un sujet légitime portant « %% » ou « _ » doit rester reconnu")
                self.assertEqual(signalement.matched_result_id, sien)

    # -- le jeton d'autrui ne doit pas atterrir dans un billet ----------------
    def test_le_billet_ne_porte_pas_le_jeton_rapporte(self):
        """🔴 Le billet est lu par les agents d'ASSISTANCE, et `_ROBOT_UA` ne
        filtre pas un vrai navigateur : qui ouvre le `/phish/<jeton>` vivant
        d'un collègue enregistre un clic sur SON résultat et déclenche sa
        remédiation. Et c'est ce lot qui ouvre l'exposition : avant, signaler
        le leurre d'autrui était clos en « simulation », sans billet."""
        if "helpdesk.ticket" not in self.env:
            self.skipTest("pas de module d'assistance")
        autrui = self._resultat(self.collegue_partenaire)
        jeton = autrui.token
        lien = "https://exemple.test/phish/%s?utm=courriel" % jeton

        signalement = self._signaler(lien=lien)

        self.assertEqual(signalement.state, "triage")
        self.assertTrue(signalement.helpdesk_ticket_id, "le billet doit être créé")
        billet = self.env["helpdesk.ticket"].sudo().browse(
            signalement.helpdesk_ticket_id)
        description = str(billet.description or "")
        self.assertNotIn(jeton, description,
                         "le jeton vivant du collègue ne doit PAS être dans le billet")
        self.assertIn("/phish/", description,
                      "le triage doit voir qu'un lien /phish/ a été rapporté")
        self.assertIn("exemple.test", description, "le reste de l'URL est utile")
        self.assertIn("utm=courriel", description)
        # Le signalement lui-même garde le lien complet : il n'est lisible que
        # par les groupes de sensibilisation (`create`/`search` sont refusés à
        # `base.group_user`), pas par les agents d'assistance.
        self.assertIn(jeton, signalement.suspicious_link)

    # -- pas d'essai « sans signaleur » : l'état n'existe pas -----------------
    # `bf.reported.phish.reporter_id` est `required=True` (contrainte NOT NULL) :
    # un signalement sans signaleur ne peut pas être créé, et l'essai qui
    # prétendait le faire tombait sur la contrainte. La garde correspondante a
    # donc été retirée du code : le filtre `partner_id = reporter_id` suffit en
    # toutes circonstances, puisque `bf.phishing.result.partner_id` est lui aussi
    # requis — un `partner_id = False` ne correspond à aucun résultat.


@tagged("post_install", "-at_install")
class TestMasquageJetonTriage(TransactionCase):
    """🔴 Le jeton vivant d'un collègue ne doit atteindre AUCUNE sortie du
    triage, sous aucune des formes qu'un employé peut écrire.

    Un premier correctif ne masquait que le champ `suspicious_link`. Mesuré :
    **6 signalements sur 7** mettaient quand même le jeton dans la description
    du billet, parce que la description est COMPOSÉE de quatre champs que
    l'employé choisit librement dans l'assistant — son seul accès avec
    `base.group_user`. Le sujet fuyait en plus dans le TITRE du billet et dans
    le CORPS de l'alerte ntfy.

    Chaîne de dommage : qui ouvre ce lien depuis le billet déclenche
    `register_click` (`_ROBOT_UA` ne mord pas sur un vrai navigateur), le
    collègue est compté en échec, sa remédiation part, son profil se dégrade.

    ⚠️ `report_ntfy_url` est vidé au `setUpClass` comme dans la classe
    ci-dessus : le chemin de triage pousse une alerte, et une base copiée d'une
    production en porte les jetons, avec la sortie internet ouverte. Les
    deux essais qui ont BESOIN d'une URL la posent eux-mêmes en `.invalid`,
    pour la durée de leur transaction, ET remplacent `requests` par un double :
    rien ne sort, et le corps envoyé est lu dans le double.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param(
            "bf_security_awareness.report_ntfy_url", "")
        cls.employe = new_test_user(
            cls.env, login="employe_masque_essai", name="Employé Masque",
            email="employe.masque@exemple.test", groups="base.group_user")
        cls.collegue = cls.env["res.partner"].create({
            "name": "Collègue masqué", "email": "collegue.masque@exemple.test"})
        cls.modele = cls.env["bf.phishing.template"].create({
            "name": "Leurre masquage", "subject": "Relevé de compte",
            "difficulty": "easy", "landing_mode": "credential"})
        cls.campagne = cls.env["bf.phishing.campaign"].create({
            "name": "Campagne masquage", "template_id": cls.modele.id})

    def setUp(self):
        super().setUp()
        if "helpdesk.ticket" not in self.env:
            self.skipTest("pas de module d'assistance")
        self.resultat = self.env["bf.phishing.result"].create({
            "campaign_id": self.campagne.id,
            "partner_id": self.collegue.id,
            "email": self.collegue.email,
        })
        self.jeton = self.resultat.token
        self.assertEqual(len(self.jeton), 43, "jeton urlsafe de 43 caractères")

    def _billet(self, **valeurs):
        """Un signalement de l'employé, et le billet qu'il ouvre."""
        assistant = self.env["bf.report.phish.wizard"].with_user(
            self.employe).create(dict(
                {"category": "phishing", "subject": "Courriel suspect"},
                **valeurs))
        self.env.invalidate_all()
        assistant.action_submit()
        self.env.invalidate_all()
        signalement = self.env["bf.reported.phish"].sudo().search(
            [("reporter_id", "=", self.employe.partner_id.id)],
            order="id desc", limit=1)
        self.assertEqual(signalement.state, "triage",
                         "le leurre d'autrui doit partir au triage")
        self.assertTrue(signalement.helpdesk_ticket_id,
                        "le triage doit ouvrir son billet")
        billet = self.env["helpdesk.ticket"].sudo().browse(
            signalement.helpdesk_ticket_id)
        return signalement, str(billet.description or ""), str(billet.name or "")

    def _lien(self, chemin="/phish/"):
        return "https://exemple.test%s%s" % (chemin, self.jeton)

    # -- la description composée, champ par champ -----------------------------
    def test_le_jeton_dans_les_precisions_est_masque(self):
        """`details` : la fuite que le masquage d'un seul champ ne voyait pas."""
        _s, description, _t = self._billet(
            details="le lien était %s" % self._lien())
        self.assertNotIn(self.jeton, description)
        self.assertIn("/phish/", description, "la trace reste au triage")
        self.assertIn("exemple.test", description)

    def test_l_ancre_html_ne_garde_pas_le_href_vivant(self):
        """🔴 Un `<a href>` d'un seul clic, au texte trompeur, traverse
        l'assainisseur HTML intact : c'était un piège posé à l'agent."""
        piege = '<a href="%s">Facture Hydro-Québec</a>' % self._lien()
        _s, description, _t = self._billet(details=piege)
        self.assertNotIn(self.jeton, description)
        self.assertNotIn('href="%s"' % self._lien(), description)
        self.assertIn("Facture Hydro-Québec", description,
                      "le texte trompeur reste visible : l'agent doit le voir")

    def test_le_jeton_dans_l_adresse_rapportee_est_masque(self):
        _s, description, _t = self._billet(email_from=self._lien())
        self.assertNotIn(self.jeton, description)

    def test_le_jeton_dans_le_sujet_est_masque_dans_la_description(self):
        _s, description, _t = self._billet(subject="urgent %s" % self._lien())
        self.assertNotIn(self.jeton, description)

    # -- le titre du billet ---------------------------------------------------
    def test_le_titre_du_billet_ne_porte_pas_le_jeton(self):
        """Le titre est composé du sujet rapporté : il fuyait aussi."""
        _s, _description, titre = self._billet(
            subject="urgent %s" % self._lien())
        self.assertNotIn(self.jeton, titre)
        self.assertIn("/phish/", titre, "la trace reste dans le titre")

    # -- les formes du lien qui contournaient le masquage ---------------------
    def test_deux_phish_dans_le_lien_sont_tous_les_deux_masques(self):
        """Le second `/phish/` passait : le masquage ne coupait qu'une fois."""
        _s, description, _t = self._billet(
            suspicious_link="https://exemple.test/phish/LEURRE et %s"
            % self._lien())
        self.assertNotIn(self.jeton, description)

    def test_le_segment_point_ne_contourne_pas_le_masquage(self):
        """`/phish/./<jeton>` : le navigateur normalise le `.` avant d'émettre."""
        _s, description, _t = self._billet(
            suspicious_link=self._lien("/phish/./"))
        self.assertNotIn(self.jeton, description)

    def test_le_segment_vide_ne_contourne_pas_le_masquage(self):
        """`/phish//<jeton>` : la sortie anticipée sur un segment vide rendait
        le lien INCHANGÉ."""
        _s, description, _t = self._billet(
            suspicious_link=self._lien("/phish//"))
        self.assertNotIn(self.jeton, description)

    def test_la_casse_du_chemin_ne_contourne_pas_le_masquage(self):
        _s, description, _t = self._billet(
            suspicious_link=self._lien("/PHISH/"))
        self.assertNotIn(self.jeton, description)

    def test_le_separateur_encode_ne_contourne_pas_le_masquage(self):
        """`/phish%2F<jeton>` : werkzeug décode `%2F`, donc le lien CLIQUE."""
        _s, description, _t = self._billet(
            suspicious_link=self._lien("/phish%2F"))
        self.assertNotIn(self.jeton, description)

    # -- l'encodage HTML, qui contournait un masquage posé trop tôt ----------
    def test_l_entite_html_ne_contourne_pas_le_masquage(self):
        """🔴 La faille d'une CORRECTION : masquer avant l'assainisseur.

        Mesuré sur une base d'essai : `html_sanitize` DÉCODE les entités, donc
        `<a href="https://h.test&#47;phish&#47;<jeton>">Facture</a>` ressortait
        du champ en `href="https://h.test/phish/<jeton>"` — un lien vivant d'un
        seul clic — alors que le masquage, joué sur le texte encodé, n'y avait
        rien vu. `&#x2F;`, `&sol;`, `&#112;hish` et un jeton coupé en deux par
        `&#65;` faisaient de même. Énumérer les formes est sans fin : tout
        caractère s'encode. La garde est l'ORDRE — assainir, puis masquer.
        """
        coupe = "%s&#%d;%s" % (self.jeton[:20], ord(self.jeton[20]),
                               self.jeton[21:])
        formes = {
            "entité décimale": "https://exemple.test&#47;phish&#47;%s" % self.jeton,
            "entité hexadécimale": "https://exemple.test&#x2F;phish&#x2F;%s" % self.jeton,
            "entité nommée": "https://exemple.test&sol;phish&sol;%s" % self.jeton,
            "le mot phish encodé": "https://exemple.test/&#112;hish/%s" % self.jeton,
            "jeton coupé par une entité": "https://exemple.test/phish/%s" % coupe,
            # 🔴 Le cas qui exige l'ORDRE, et lui seul : séparateurs encodés
            # ET jeton coupé. Sur le texte ENCODÉ, le filet de la forme ne voit
            # aucun `/phish/` et le filet du jeton ne voit qu'un jeton coupé en
            # deux — aucun des deux ne mord. C'est l'assainisseur, joué AVANT,
            # qui rend le jeton lisible aux deux. Un mutant qui échange les deux
            # lignes a survécu à la campagne du 11-06 tant que ce cas manquait.
            "séparateur ET jeton encodés":
                "https://exemple.test&#47;phish&#47;%s" % coupe,
        }
        for nom, url in formes.items():
            with self.subTest(forme=nom):
                _s, description, _t = self._billet(
                    details='<a href="%s">Facture Hydro-Québec</a>' % url)
                self.assertNotIn(
                    self.jeton, description,
                    "la forme « %s » remettait un lien vivant dans le billet"
                    % nom)

    def test_le_jeton_en_parametre_de_requete_est_masque(self):
        """Le filet par JETON vivant, celui que la forme ne couvre pas :
        `/phish/?t=<jeton>` ne route vers rien, mais il se recopie à la main."""
        _s, description, _t = self._billet(
            suspicious_link="https://exemple.test/phish/?t=%s" % self.jeton)
        self.assertNotIn(self.jeton, description)

    def test_un_jeton_mort_n_est_pas_masque_hors_d_un_lien_phish(self):
        """Le sens INVERSE du filet : il est étroit à dessein. Une chaîne de 43
        caractères qui ne correspond à AUCUN résultat n'est pas un piège —
        `register_click` ne compte un clic que pour un jeton qui existe — et la
        masquer par précaution abîmerait le billet sans rien protéger."""
        faux = "z" * 43
        _s, description, _t = self._billet(details="identifiant %s" % faux)
        self.assertIn(faux, description,
                      "une chaîne qui ne correspond à rien doit rester lisible")

    # -- les deux corps d'alerte ntfy ----------------------------------------
    def _alerte(self, **valeurs):
        """Le signalement, et les corps ntfy qu'il a réellement poussés.

        ⚠️ L'URL est posée ici seulement, en `.invalid`, et `requests` est
        remplacé : une base copiée d'une production en porte les jetons."""
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_security_awareness.report_ntfy_url",
            "https://ntfy.essai.invalid/opsec")
        with patch.object(module_signalement, "requests") as faux:
            self._billet(**valeurs)
        appels = faux.post.call_args_list
        self.assertTrue(appels, "le chemin d'alerte doit avoir été joué")
        return [(a.kwargs.get("data") or b"").decode("utf-8") for a in appels]

    def test_le_corps_de_l_alerte_ntfy_ne_porte_pas_le_jeton(self):
        corps = self._alerte(subject="urgent %s" % self._lien())
        for texte in corps:
            self.assertNotIn(self.jeton, texte)
        self.assertTrue(any("/phish/" in t for t in corps),
                        "la trace reste dans l'alerte")

    def test_le_corps_de_l_alerte_de_retrait_ne_porte_pas_le_jeton(self):
        """🔴 Le SECOND corps ntfy du fichier (`_notify_security_team_clawback`)
        porte lui aussi le sujet rapporté, sur le MÊME canal opsec. Fermer un
        seul des deux aurait laissé la fuite ouverte derrière un rôle de plus.
        """
        signalement, _d, _t = self._billet(subject="urgent %s" % self._lien())
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_security_awareness.report_ntfy_url",
            "https://ntfy.essai.invalid/opsec")
        operation = SimpleNamespace(messages_removed=1, mailboxes_swept=2)
        with patch.object(module_signalement, "requests") as faux:
            signalement._notify_security_team_clawback(operation)
        appels = faux.post.call_args_list
        self.assertTrue(appels, "l'alerte de retrait doit avoir été poussée")
        for appel in appels:
            self.assertNotIn(
                self.jeton, (appel.kwargs.get("data") or b"").decode("utf-8"))


@tagged("post_install", "-at_install")
class TestMasquerJetonForme(TransactionCase):
    """Le filet de la FORME, pris SEUL.

    🔴 Pourquoi en unitaire et pas de bout en bout : les deux filets se
    recouvrent largement, et sur un jeton VIVANT celui du jeton rattrape tout ce
    que celui de la forme laisse passer. La campagne de mutations du 11-06 l'a
    montré — « une seule occurrence », « sensible à la casse » et « segments
    dégénérés ignorés » ont TOUS SURVÉCU à une suite qui ne les éprouvait que
    par un billet. Un essai par propriété de la forme, donc, sur la fonction
    elle-même, là où rien ne la rattrape.

    Ce que le filet de la forme apporte en propre : le jeton percent-encodé
    (`/phish/%4AETON…`), que werkzeug décode avant de router et qu'aucune
    recherche en base ne retrouverait, et la trace d'un lien `/phish/` dont le
    jeton ne correspond plus à rien.
    """

    MARQUE = "[M]"

    def _masquer(self, texte):
        return self.env["bf.reported.phish"]._masquer_jeton(texte, self.MARQUE)

    def test_la_forme_masque_toutes_les_occurrences(self):
        """Le second `/phish/` passait : le masquage ne coupait qu'une fois."""
        rendu = self._masquer(
            "https://a.test/phish/AAAAAAAAAA et https://b.test/phish/BBBBBBBBBB")
        self.assertNotIn("AAAAAAAAAA", rendu)
        self.assertNotIn("BBBBBBBBBB", rendu)
        self.assertEqual(rendu.count(self.MARQUE), 2)

    def test_la_forme_ignore_la_casse_du_chemin(self):
        rendu = self._masquer("https://h.test/PHISH/JETONJETONJETON")
        self.assertNotIn("JETONJETONJETON", rendu)
        self.assertIn("/PHISH/" + self.MARQUE, rendu,
                      "la casse écrite est gardée : c'est ce qui a été rapporté")

    def test_la_forme_traverse_les_segments_degeneres(self):
        """Un segment vide ou « . » faisait sortir le masquage par le haut, et
        le lien ressortait INCHANGÉ."""
        for chemin in ("/phish//", "/phish/./", "/phish/../", "/phish/.//"):
            with self.subTest(chemin=chemin):
                rendu = self._masquer("https://h.test%sJETONJETONJETON" % chemin)
                self.assertNotIn("JETONJETONJETON", rendu)

    def test_la_forme_traite_le_separateur_encode(self):
        """werkzeug décode `%2F` avant de router : le lien CLIQUE."""
        for chemin in ("/phish%2F", "/phish%2f", "/phish/%2E%2F"):
            with self.subTest(chemin=chemin):
                rendu = self._masquer("https://h.test%sJETONJETONJETON" % chemin)
                self.assertNotIn("JETONJETONJETON", rendu)

    def test_la_forme_garde_le_reste_de_l_url(self):
        """Le triage doit voir ce qui a été rapporté, sauf le jeton."""
        rendu = self._masquer(
            "https://h.test/phish/JETONJETONJETON/open.png?utm=courriel")
        self.assertNotIn("JETONJETONJETON", rendu)
        self.assertIn("h.test", rendu)
        self.assertIn("/open.png?utm=courriel", rendu)

    def test_la_forme_laisse_intact_ce_qui_ne_porte_pas_de_jeton(self):
        """Le sens INVERSE : ne pas abîmer un texte qui n'a rien à cacher."""
        for texte in ("https://h.test/phish/", "https://h.test/phish/   ",
                      "https://h.test/accueil", "", False):
            with self.subTest(texte=texte):
                self.assertEqual(self._masquer(texte), texte)
