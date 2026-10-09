"""Le RPRP désigné d'une organisation : un seul stockage, une adresse jamais devinée."""
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestRprpDesigne(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Partner = cls.env["res.partner"]
        cls.org = Partner.create({"name": "Client Essai", "is_company": True, "email": "info@client.invalid"})
        cls.dg = Partner.create({"name": "Directrice", "parent_id": cls.org.id, "email": "dg@client.invalid"})
        cls.employe = Partner.create({"name": "Employé", "parent_id": cls.org.id})

    def test_adresse_designee_l_emporte(self):
        self.org.write({"privacy_officer_partner_id": self.dg.id,
                        "privacy_officer_email": "rprp@client.invalid"})
        officer, email = self.org._privacy_officer_address()
        self.assertEqual(officer, self.dg)
        self.assertEqual(email, "rprp@client.invalid")

    def test_aucune_adresse_n_est_devinee(self):
        self.assertEqual(self.org._privacy_officer_address()[1], "",
                         "le courriel général de l'organisation n'est pas une adresse désignée")
        self.org.privacy_officer_partner_id = self.dg
        self.assertEqual(self.org._privacy_officer_address(), (self.dg, ""),
                         "ni le courriel de la fiche de la personne, que tout gestionnaire de contacts modifie")
        self.org.privacy_officer_email = "rprp@client.invalid"
        self.dg.email = "detourne@ailleurs.invalid"
        self.org.privacy_officer_partner_id = self.dg  # réécrire la même personne
        self.assertEqual(self.org._privacy_officer_address()[1], "rprp@client.invalid",
                         "corriger la fiche de la personne ne déroute pas les avis")

    def test_la_designation_se_lit_sur_l_organisation_elle_meme(self):
        """Pas par la société mère : rattacher le client A sous le client B ne déroute pas ses avis."""
        self.org.write({"privacy_officer_partner_id": self.dg.id,
                        "privacy_officer_email": "rprp@client.invalid"})
        self.assertEqual(self.employe._privacy_officer_address()[1], "")
        autre = self.env["res.partner"].create({"name": "Client A", "is_company": True})
        autre.write({"is_company": False, "parent_id": self.org.id})
        self.assertEqual(autre._privacy_officer_address()[1], "")

    def test_adresse_invalide_refusee(self):
        with self.assertRaises(ValidationError):
            self.org.privacy_officer_email = "pas une adresse"

    def test_la_societe_lit_et_ecrit_le_partenaire(self):
        company = self.env.company
        company.privacy_officer_email = "rprp@societe.invalid"
        self.assertEqual(company.partner_id.privacy_officer_email, "rprp@societe.invalid")

    def test_courriel_du_responsable_de_la_societe(self):
        company = self.env.company
        self.env["ir.config_parameter"].sudo().set_param(
            "privacy_consent.privacy_officer_email", "ancien@societe.invalid")
        consent = self.env["privacy.consent"]
        self.assertEqual(consent._privacy_officer_email(), "ancien@societe.invalid")
        company.privacy_officer_email = "rprp@societe.invalid"
        self.assertEqual(consent._privacy_officer_email(), "rprp@societe.invalid",
                         "l'adresse désignée passe devant le paramètre hérité")

    def test_reserve_au_groupe_vie_privee(self):
        """Une adresse qui reçoit des avis légaux ne se change pas depuis n'importe quel poste."""
        commis = self.env["res.users"].create({
            "name": "Commis", "login": "commis-essai@essai.invalid",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("base.group_partner_manager").id])]})
        org = self.org.with_user(commis)
        org.name  # la fiche reste lisible
        with self.assertRaises(AccessError):
            org.privacy_officer_email = "detourne@ailleurs.invalid"
        with self.assertRaises(AccessError):
            org.read(["privacy_officer_email"])
        # Le calcul de l'adresse, lui, sert les autres modules quel que soit l'appelant.
        self.org.privacy_officer_email = "rprp@client.invalid"
        self.assertEqual(org._privacy_officer_address()[1], "rprp@client.invalid")

    def test_l_utilisateur_vie_privee_lit_mais_ne_designe_pas(self):
        lecteur = self.env["res.users"].create({
            "name": "Utilisateur VP", "login": "uvp-essai@essai.invalid",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("base.group_partner_manager").id,
                                  self.env.ref("privacy_consent.group_privacy_user").id])]})
        self.org.privacy_officer_email = "rprp@client.invalid"
        org = self.org.with_user(lecteur)
        self.assertEqual(org.privacy_officer_email, "rprp@client.invalid")
        with self.assertRaises(AccessError):
            org.privacy_officer_email = "moi@client.invalid"
        with self.assertRaises(AccessError):
            org.privacy_officer_partner_id = self.employe
        gestionnaire = self.env["res.users"].create({
            "name": "Gestionnaire VP", "login": "gvp2-essai@essai.invalid",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("base.group_partner_manager").id,
                                  self.env.ref("privacy_consent.group_privacy_manager").id])]})
        self.org.with_user(gestionnaire).privacy_officer_email = "rprp2@client.invalid"
        self.assertEqual(self.org.privacy_officer_email, "rprp2@client.invalid")


    def test_creer_et_dupliquer_un_contact_sans_etre_gestionnaire(self):
        """Le formulaire envoie tous ses champs à la création, `False` compris : la garde ne
        refuse qu'une vraie désignation, et la désignation ne se recopie pas."""
        utilisateur = self.env["res.users"].create({
            "name": "Utilisateur VP 2", "login": "uvp2-essai@essai.invalid",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("base.group_partner_manager").id,
                                  self.env.ref("privacy_consent.group_privacy_user").id])]})
        Partner = self.env["res.partner"].with_user(utilisateur)
        neuf = Partner.create({"name": "Contact neuf", "privacy_officer_partner_id": False,
                               "privacy_officer_email": False, "privacy_officer_public_url": False})
        self.assertTrue(neuf)
        self.org.privacy_officer_email = "rprp@client.invalid"
        copie = self.org.with_user(utilisateur).copy()
        self.assertFalse(copie.privacy_officer_email, "une désignation ne se duplique pas")
        self.org.with_user(utilisateur).write({"privacy_officer_email": "rprp@client.invalid",
                                               "name": "Client Essai renommé"})
        with self.assertRaises(AccessError):
            Partner.create({"name": "Forcé", "privacy_officer_email": "moi@client.invalid"})


    def test_une_organisation_ne_nait_pas_designee_par_defaut(self):
        utilisateur = self.env["res.users"].create({
            "name": "Utilisateur VP 3", "login": "uvp3-essai@essai.invalid",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("base.group_partner_manager").id,
                                  self.env.ref("privacy_consent.group_privacy_user").id])]})
        with self.assertRaises(AccessError):
            self.env["res.partner"].with_user(utilisateur).with_context(
                default_privacy_officer_email="moi@client.invalid").create({"name": "Org forcée", "is_company": True})


    def test_la_fusion_ne_deplace_pas_une_designation(self):
        createur = self.env["res.users"].create({
            "name": "Création de contacts 2", "login": "cc2-essai@essai.invalid",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("base.group_partner_manager").id])]})
        self.org.write({"privacy_officer_partner_id": self.dg.id, "privacy_officer_email": "rprp@client.invalid"})
        doublon = self.env["res.partner"].create({"name": "Directrice (doublon)", "email": self.dg.email})
        wizard = self.env["base.partner.merge.automatic.wizard"].with_user(createur)
        with self.assertRaisesRegex(UserError, "responsable de la protection"):
            wizard._merge([self.dg.id, doublon.id], doublon)


    def test_la_fusion_voit_aussi_les_organisations_archivees(self):
        createur = self.env["res.users"].create({
            "name": "Création de contacts 5", "login": "cc5-essai@essai.invalid",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("base.group_partner_manager").id])]})
        self.org.write({"privacy_officer_partner_id": self.dg.id})  # désignée, sans adresse
        self.org.active = False
        doublon = self.env["res.partner"].create({"name": "Directrice bis", "email": self.dg.email})
        with self.assertRaisesRegex(UserError, "responsable de la protection"):
            self.env["base.partner.merge.automatic.wizard"].with_user(createur)._merge(
                [self.dg.id, doublon.id], doublon)
