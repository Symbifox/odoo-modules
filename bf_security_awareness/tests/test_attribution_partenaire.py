"""L'attribution du résultat de campagne, réparée à la source.

🔴 Le correctif du jeton (un
signalement ne se reconnaît que dans SON PROPRE leurre) ne vaut que si le
résultat de la campagne naît bien sur le partenaire de l'employé. Il ne
naissait pas toujours là, et la cause est dans `_resolve_target_partners` :

1. la comparaison se faisait sur `email` BRUT et sensible à la casse — une
   `mailing.list` portant `Alice.X@Exemple.Test` ne retrouvait pas le
   partenaire dont l'adresse est `alice.x@exemple.test` et CRÉAIT un doublon ;
2. quand plusieurs partenaires portent l'adresse, `limit=1` avec l'ordre par
   défaut (`complete_name ASC`) rendait le contact d'une SOCIÉTÉ plutôt que le
   partenaire de l'usager interne.

Dans les deux cas, l'employé qui signale SON leurre avec SON jeton perdait son
crédit et faisait ouvrir un faux incident.

⚠️ Le correctif est ICI et non dans le filtre du jeton : `email` est dans
`SELF_WRITEABLE_FIELDS`, donc élargir ce filtre à `email_normalized` laisserait
un employé réécrire son adresse pour prendre celle d'un collègue.

⚠️ Ces essais vident `report_ntfy_url` : le chemin de triage pousse une alerte
ntfy, et une base copiée d'une production en porte les jetons.
"""
from odoo.tests import TransactionCase, new_test_user, tagged

TODO = "mail.mail_activity_data_todo"


@tagged("post_install", "-at_install")
class TestAttributionPartenaire(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param(
            "bf_security_awareness.report_ntfy_url", "")
        cls.Partner = cls.env["res.partner"]
        cls.modele = cls.env["bf.phishing.template"].create({
            "name": "Leurre attribution", "subject": "Votre relevé est prêt",
            "difficulty": "easy", "landing_mode": "credential"})

    # -- outillage -------------------------------------------------------------
    def _usager(self, suffixe, nom, adresse):
        return new_test_user(
            self.env, login="attr_%s" % suffixe, name=nom, email=adresse,
            groups="base.group_user")

    def _doublon_sous_societe(self, nom, adresse):
        """Le doublon le plus courant : le contact d'une société, qui trie AVANT
        le partenaire de l'usager (`complete_name ASC`)."""
        societe = self.Partner.create({"name": "ACME Inc", "is_company": True})
        return self.Partner.create(
            {"name": nom, "email": adresse, "parent_id": societe.id})

    def _campagne_sur_liste(self, nom_contact, adresse_dans_la_liste):
        liste = self.env["mailing.list"].create({"name": "Liste %s" % nom_contact})
        self.env["mailing.contact"].create({
            "name": nom_contact, "email": adresse_dans_la_liste,
            "list_ids": [(6, 0, liste.ids)]})
        return self.env["bf.phishing.campaign"].create({
            "name": "Campagne %s" % nom_contact,
            "template_id": self.modele.id,
            "mailing_list_ids": [(6, 0, liste.ids)],
        })

    def _porteurs(self, adresse):
        return self.Partner.search([("email_normalized", "=", adresse.lower())])

    # -- 1) la casse ----------------------------------------------------------
    def test_l_adresse_de_casse_differente_retrouve_le_partenaire_de_l_usager(self):
        """🔴 `Alice.X@Exemple.Test` dans la liste, `alice.x@exemple.test` au
        partenaire : la comparaison brute créait un doublon."""
        adresse = "alice.x.essai@exemple.test"
        usager = self._usager("casse", "Alice Casse", adresse)
        avant = len(self._porteurs(adresse))

        campagne = self._campagne_sur_liste("Alice Casse", "Alice.X.Essai@Exemple.Test")
        cibles = campagne._resolve_target_partners()

        self.assertEqual(
            cibles, usager.partner_id,
            "la liste doit viser le partenaire de l'usager, pas un doublon neuf")
        self.assertEqual(
            len(self._porteurs(adresse)), avant,
            "aucun partenaire ne doit être créé pour une adresse déjà connue")

    # -- 2) l'usager interne d'abord ------------------------------------------
    def test_le_partenaire_de_l_usager_interne_est_prefere_au_contact_de_la_societe(self):
        """🔴 `complete_name ASC` rendait « ACME Inc, Zoé … » avant « Zoé … »."""
        adresse = "zoe.essai@exemple.test"
        usager = self._usager("interne", "Zoé Exemple", adresse)
        doublon = self._doublon_sous_societe("Zoé Exemple", adresse)
        self.assertEqual(
            self.Partner.search(
                [("email_normalized", "=", adresse)], limit=1), doublon,
            "le témoin de l'ordre par défaut : la société passe devant")

        campagne = self._campagne_sur_liste("Zoé Exemple", adresse)
        cibles = campagne._resolve_target_partners()

        self.assertEqual(
            cibles, usager.partner_id,
            "le partenaire de l'usager interne doit être préféré au doublon")

    # -- 3) la conséquence mesurée, de bout en bout ---------------------------
    def test_l_employe_est_credite_pour_son_leurre_resolu_depuis_une_liste(self):
        """La régression mesurée par le relecteur, figée : l'employé honnête
        signale SON leurre avec SON jeton et doit être reconnu et crédité."""
        adresse = "bruno.essai@exemple.test"
        usager = self._usager("bout", "Bruno Boutenbout", adresse)
        self._doublon_sous_societe("Bruno Boutenbout", adresse)

        campagne = self._campagne_sur_liste(
            "Bruno Boutenbout", "Bruno.Essai@Exemple.Test")
        campagne.action_prepare()
        resultat = campagne.result_ids
        self.assertEqual(len(resultat), 1)
        self.assertEqual(
            resultat.partner_id, usager.partner_id,
            "le résultat doit naître sur le partenaire de l'employé")

        assistant = self.env["bf.report.phish.wizard"].with_user(usager).create({
            "category": "phishing",
            "subject": "Courriel suspect",
            "suspicious_link": "https://exemple.test/phish/%s" % resultat.token,
        })
        self.env.invalidate_all()
        assistant.action_submit()
        self.env.invalidate_all()
        signalement = self.env["bf.reported.phish"].sudo().search(
            [("reporter_id", "=", usager.partner_id.id)], order="id desc", limit=1)

        resultat.invalidate_recordset()
        self.assertEqual(signalement.state, "simulation",
                         "son propre leurre doit être reconnu")
        self.assertEqual(signalement.matched_result_id, resultat)
        self.assertTrue(resultat.sudo().reported, "son bon réflexe doit être crédité")
        self.assertFalse(
            signalement.activity_ids.filtered(
                lambda a: a.activity_type_id == self.env.ref(TODO)),
            "une simulation reconnue n'ouvre aucun faux incident")
