"""Audit TI parle anglais à l'anglophone et français au francophone.

Depuis 18.0.1.17.5, la source est anglaise et le français vient de fr_CA.po. Les
autres essais ne prouvent que la source : un catalogue vide ou abîmé les laisse
passer. Ceux-ci lisent le français que le catalogue LIVRÉ sert, par chaque chemin
du module : libellé de champ et de sélection, grille des 14 éléments (champs
devenus traduisibles), textes calculés du rapport (`env._`), SQL brut du tableau
de bord, et résumé d'activité écrit dans la langue de l'assigné.
"""
from odoo.tests.common import TransactionCase, tagged

CHAMPS_ELEMENT = ("name", "description", "best_practices")


@tagged("post_install", "-at_install")
class TestDeuxLangues(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env["res.lang"]._activate_lang("en_CA")
        # 🔴 Le français des éléments vit EN BASE, posé par la montée (migration ou
        # catalogue) : un catalogue abîmé ne l'en retire pas, et l'essai passerait sur
        # la base d'hier. On efface la clé fr_CA (transaction de l'essai, annulée à la
        # fin), puis on recharge le catalogue LIVRÉ, comme sur une base neuve.
        cls.element = cls.env.ref("audit_ti.element_1")
        for champ in CHAMPS_ELEMENT:
            cls.env.cr.execute(
                f"UPDATE audit_element SET {champ} = {champ} - 'fr_CA' WHERE id = %s",
                [cls.element.id])
        cls.element.invalidate_recordset(list(CHAMPS_ELEMENT))
        # Même chose pour le libellé et la sélection de l'état, et pour la vue du client.
        champ = cls.env["ir.model.fields"]._get("audit.client", "state")
        cls.env.cr.execute(
            "UPDATE ir_model_fields SET field_description = field_description - 'fr_CA' WHERE id = %s",
            [champ.id])
        cls.env.cr.execute(
            "UPDATE ir_model_fields_selection SET name = name - 'fr_CA' WHERE field_id = %s",
            [champ.id])
        vue = cls.env.ref("audit_ti.view_audit_client_form")
        cls.env.cr.execute("UPDATE ir_ui_view SET arch_db = arch_db - 'fr_CA' WHERE id = %s", [vue.id])
        cls.env.invalidate_all()
        cls.env["ir.module.module"]._load_module_terms(["audit_ti"], ["fr_CA"], overwrite=True)
        cls.env.registry.clear_all_caches()
        cls.client_ = cls.env["audit.client"].create({"name": "Client bilingue essai"})
        cls.supplier = cls.env["audit.supplier"].create({
            "name": "Fournisseur bilingue essai", "supplier_type": "cloud"})

    def test_libelles_du_modele(self):
        for lang, etat, livre in (("fr_CA", "État", "Livré"),
                                  ("en_US", "State", "Delivered"),
                                  ("en_CA", "State", "Delivered")):
            Client = self.env["audit.client"].with_context(lang=lang)
            champ = Client.fields_get(["state"])["state"]
            self.assertEqual(champ["string"], etat, lang)
            self.assertEqual(dict(champ["selection"])["delivered"], livre, lang)

    def test_le_bouton_livrer_suit_la_langue(self):
        Client = self.env["audit.client"]
        for lang, attendu, absent in (("fr_CA", "Marquer comme livré", "Mark as delivered"),
                                      ("en_CA", "Mark as delivered", "Marquer comme livré")):
            arch = Client.with_context(lang=lang).get_views(
                [(False, "form")])["views"]["form"]["arch"]
            self.assertIn(attendu, arch, lang)
            self.assertNotIn(absent, arch, lang)

    def test_la_grille_des_elements_suit_la_langue(self):
        fr = self.element.with_context(lang="fr_CA")
        en = self.element.with_context(lang="en_US")
        self.assertEqual(fr.name, "Politiques & Procédures TI")
        self.assertEqual(en.name, "IT policies & procedures")
        self.assertEqual(fr.description, "Politiques mots de passe, chiffrement, sauvegardes")
        self.assertIn("Politique de mots de passe documentée", fr.best_practices)
        self.assertNotIn("Documented password policy", fr.best_practices)
        self.assertIn("Documented password policy", en.best_practices)

    def test_le_rapport_suit_la_langue_de_qui_imprime(self):
        for lang, aucune, couvert in (("fr_CA", "Aucune action requise.", "Couvert"),
                                      ("en_US", "No action required.", "Covered")):
            client = self.client_.with_context(lang=lang)
            self.assertEqual(client._build_recommendation("green", self.env["audit.assessment"]), aucune)
            self.assertEqual(client._rag_label("green"), couvert)
            donnees = client._get_report_data()
            self.assertEqual(donnees["elements"][0]["name"],
                             self.element.with_context(lang=lang).name, lang)

    def test_le_tableau_de_bord_lit_le_nom_traduit(self):
        """Le SQL brut lisait la colonne : devenue jsonb, elle rendait un dict."""
        self.env["audit.watchpoint"].create({
            "client_id": self.client_.id, "element_id": self.element.id,
            "description": "Point bilingue essai", "priority": "high"})
        for lang, attendu in (("fr_CA", "Politiques & Procédures TI"),
                              ("en_US", "IT policies & procedures")):
            rangs = self.env["audit.dashboard"].with_context(lang=lang)._get_open_watchpoints()
            noms = {r["element_name"] for r in rangs if r["description"] == "Point bilingue essai"}
            self.assertEqual(noms, {attendu}, lang)

    def test_le_resume_d_activite_suit_la_langue_de_l_assigne(self):
        Users = self.env["res.users"].with_context(no_reset_password=True)
        for lang, attendu in (("fr_CA", "Suivi :"), ("en_CA", "Follow-up:")):
            assigne = Users.create({
                "name": f"Assigné {lang}", "login": f"audit_bilingue_{lang}",
                "email": f"audit.bilingue.{lang}@exemple.test", "lang": lang,
                "groups_id": [(6, 0, [self.env.ref("audit_ti.group_audit_user").id])]})
            # Créé par une session dans l'AUTRE langue : le résumé suit l'assigné.
            autre = "en_US" if lang == "fr_CA" else "fr_CA"
            point = self.env["audit.watchpoint"].with_context(lang=autre).create({
                "client_id": self.client_.id, "element_id": self.element.id,
                "description": f"Point {lang}", "priority": "low",
                "assigned_to": assigne.id})
            resume = point.activity_ids[:1].summary or ""
            self.assertTrue(resume.startswith(attendu), (lang, resume))
            nom = self.element.with_context(lang=lang).name
            self.assertIn(nom, resume, lang)
