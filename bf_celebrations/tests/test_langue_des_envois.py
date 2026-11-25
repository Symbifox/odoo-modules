# -*- coding: utf-8 -*-
"""ce qui part d'un travail planifié se lit dans la langue de qui le reçoit.

🔴 Relecture adverse de la vague A : depuis la bascule de la source en anglais, un travail planifié
(sans langue au contexte) rendait les gabarits et le nom des occasions dans la fente `en_US`, donc en
anglais, pour des francophones qui les lisaient en français avant. Ces essais tournent SANS langue
au contexte, comme le cron, et changent seulement la langue du destinataire ou de la société.
"""

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_celebrations")
class TestLangueDesEnvois(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for langue in ("fr_CA", "en_US"):
            cls.env["res.lang"]._activate_lang(langue)
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_celebrations"], ["fr_CA"], overwrite=True)
        cls.sans_langue = cls.env(context={})

    def _invitation_pour(self, langue, suffixe):
        usager = self.env["res.users"].create({
            "name": "Langue %s" % suffixe, "login": "cel_langue_%s@example.test" % suffixe,
            "email": "cel_langue_%s@example.test" % suffixe, "lang": langue,
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])]})
        self.env["hr.employee"].create({
            "name": "Langue %s" % suffixe, "user_id": usager.id,
            "work_email": "cel_langue_%s@example.test" % suffixe})
        self.sans_langue["bf.celebration.profile"]._cron_inviter(limite=100000)
        courriel = self.env["mail.mail"].search(
            [("email_to", "ilike", "cel_langue_%s@example.test" % suffixe)], limit=1)
        self.assertTrue(courriel, "aucune invitation partie : l'essai ne mesure rien")
        return courriel.body_html or ""

    def test_l_invitation_part_dans_la_langue_de_la_personne(self):
        corps_fr = self._invitation_pour("fr_CA", "fr")
        self.assertIn("Quatre réponses possibles", corps_fr)
        self.assertNotIn("Four possible answers", corps_fr)
        corps_en = self._invitation_pour("en_US", "en")
        self.assertIn("Four possible answers", corps_en)

    def test_le_nom_d_une_occasion_suit_la_langue_de_la_societe(self):
        societe = self.env.company
        for langue, attendu in (("fr_CA", "Anniversaire"), ("en_US", "Birthday")):
            societe.partner_id.lang = langue
            occasion = self.sans_langue["bf.celebration.occasion"].create({
                "occasion_type": "birthday", "person_name": "Essai %s" % langue,
                "company_id": societe.id})
            self.assertTrue(occasion.name.endswith("— %s" % attendu), occasion.name)

    def test_chaque_envoi_d_un_travail_planifie_porte_une_langue(self):
        """Les quatre chemins sans personne derrière l'écran : chaque `send_mail`, et le rendu de
        la carte souvenir, passent par `with_context(lang=…)`. Retirer la langue d'un seul de ces
        appels fait tomber l'essai (le comportement de deux d'entre eux est éprouvé plus haut)."""
        import ast
        from odoo.tools import file_open
        # Lu dans les fichiers du MODULE, pas par `inspect` : un pont qui surcharge la méthode
        # ferait lire sa surcharge à la place de notre code.
        chemins = {
            "celebration_profile": ("_cron_inviter", ("send_mail",)),
            "celebration_occasion": ("_cron_rappeler", ("send_mail",)),
            "celebration_board": ("_livrer", ("send_mail", "_pieces_souvenir")),
        }
        chemins_mince = ("celebration_board", "_cron_relancer_tableaux_minces", ("send_mail",))

        def verifier(fichier, methode, appels):
            modele = fichier
            with file_open("bf_celebrations/models/%s.py" % fichier) as f:
                arbre = ast.parse(f.read())
            fonctions = [n for n in ast.walk(arbre)
                         if isinstance(n, ast.FunctionDef) and n.name == methode]
            self.assertEqual(len(fonctions), 1, "%s.%s introuvable" % (fichier, methode))
            vus = 0
            for noeud in ast.walk(fonctions[0]):
                if isinstance(noeud, ast.Call) and isinstance(noeud.func, ast.Attribute) \
                        and noeud.func.attr in appels:
                    vus += 1
                    recepteur = noeud.func.value
                    avec_langue = isinstance(recepteur, ast.Call) \
                        and isinstance(recepteur.func, ast.Attribute) \
                        and recepteur.func.attr == "with_context" \
                        and any(k.arg == "lang" for k in recepteur.keywords)
                    self.assertTrue(avec_langue, "%s.%s : %s sans langue" % (modele, methode, noeud.func.attr))
            self.assertGreaterEqual(vus, len(appels), "%s.%s : appel introuvable" % (modele, methode))

        for modele, (methode, appels) in chemins.items():
            verifier(modele, methode, appels)
        verifier(*chemins_mince)


    def test_les_travaux_planifies_fixent_une_langue(self):
        """sans langue au contexte, le suivi des états écrit au chatter sortait en anglais."""
        import ast
        from odoo.tools import file_open
        for fichier, methodes in {'bf_celebrations/models/celebration_board.py': ['_cron_livrer', '_cron_relancer_tableaux_minces', '_cron_purger_livres'], 'bf_celebrations/models/celebration_occasion.py': ['_cron_generer', '_cron_rappeler'], 'bf_celebrations/models/celebration_profile.py': ['_cron_inviter']}.items():
            with file_open(fichier) as f:
                arbre = ast.parse(f.read())
            for methode in methodes:
                fonction = [n for n in ast.walk(arbre) if isinstance(n, ast.FunctionDef) and n.name == methode]
                self.assertEqual(len(fonction), 1, methode)
                source = ast.unparse(fonction[0])
                self.assertIn("if not self.env.lang:", source, methode)
                self.assertIn("get_lang(self.env).code", source, methode)
