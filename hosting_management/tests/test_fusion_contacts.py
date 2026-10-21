# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestFusionContacts(TransactionCase):
    """La fusion de contacts ne déplace pas de services d'hébergement sans un gestionnaire."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Partner = cls.env["res.partner"]
        cls.client = Partner.create({"name": "Client Fusion Essai", "is_company": True})
        cls.doublon = Partner.create({"name": "Client Fusion Essai (doublon)", "is_company": True})
        logiciel = cls.env["hosting.software"].create({
            "name": "Logiciel Fusion", "code": "logiciel-fusion", "software_type": "self_hosted"})
        cls.service = cls.env["hosting.service"].create({
            "name": "Nuage Doublon", "software_id": logiciel.id, "partner_id": cls.doublon.id,
            "state": "active"})
        cls.createur = new_test_user(cls.env, login="essai_fusion_contacts",
                                     groups="base.group_user,base.group_partner_manager,base.group_erp_manager")
        cls.usager = new_test_user(
            cls.env, login="essai_fusion_usager",
            groups="base.group_user,base.group_partner_manager,base.group_erp_manager,"
                   "hosting_management.group_hosting_user")
        cls.gestion = new_test_user(
            cls.env, login="essai_fusion_gestion",
            groups="base.group_user,base.group_system,hosting_management.group_hosting_manager")
        # En pratique, seuls les administrateurs fusionnent : Odoo lit en route des champs
        # réservés (`signup_type`, `credit_limit`, visiteurs du site). La garde arrête donc
        # surtout un administrateur sans le groupe d'hébergement.
        cls.admin = new_test_user(cls.env, login="essai_fusion_admin",
                                  groups="base.group_user,base.group_system,base.group_partner_manager")

    def _assistant(self, user):
        return self.env["base.partner.merge.automatic.wizard"].with_user(user)

    def _refusee(self, user, dst):
        try:
            self._assistant(user)._merge((self.client | self.doublon).ids, dst_partner=dst)
        except UserError as erreur:
            self.assertRegex(str(erreur), "hosting|hébergement")
        else:
            self.fail("la fusion aurait dû être refusée")
        self.assertTrue(self.doublon.exists())
        self.assertEqual(self.service.partner_id, self.doublon)

    def test_la_fiche_qui_porte_un_service_ne_disparait_pas(self):
        self.assertFalse(self.admin.has_group("hosting_management.group_hosting_manager"))
        for user in (self.createur, self.usager, self.admin):
            with self.subTest(user=user.login):
                self._refusee(user, self.client)

    def test_un_service_archive_ou_annule_compte_aussi(self):
        self.service.write({"state": "cancelled"})
        self._refusee(self.createur, self.client)
        if "active" in self.service._fields:
            self.service.active = False
            self._refusee(self.createur, self.client)

    def test_le_bouton_de_l_assistant_aussi(self):
        assistant = self._assistant(self.admin).create({
            "partner_ids": [(6, 0, (self.client | self.doublon).ids)],
            "dst_partner_id": self.client.id,
        })
        try:
            assistant.action_merge()
        except UserError:
            pass
        else:
            self.fail("le bouton Fusionner aurait dû refuser")
        self.assertTrue(self.doublon.exists())

    # Une fusion permise va au bout ailleurs que dans la garde : Odoo y lit des champs réservés
    # selon les modules installés. On éprouve donc la garde elle-même, sur les mêmes fiches.
    def _permise(self, user, dst):
        self._assistant(user)._hosting_garde_fusion((self.client | self.doublon).ids, dst)

    def test_la_fiche_qui_porte_un_service_peut_rester(self):
        self._permise(self.admin, self.doublon)
        self._permise(self.createur, self.doublon)

    def test_le_gestionnaire_fusionne(self):
        self._permise(self.gestion, self.client)

    def _vieillir(self, fiche):
        self.env.flush_all()
        self.env.cr.execute("UPDATE res_partner SET create_date = create_date - interval '1 day' "
                            "WHERE id = %s", [fiche.id])
        self.env.invalidate_all()

    def test_sans_destination_la_regle_suit_le_choix_d_odoo(self):
        Assistant = self._assistant(self.admin)
        paire = (self.client | self.doublon).ids
        self._vieillir(self.doublon)
        self.assertEqual(Assistant._get_ordered_partner(paire)[-1], self.doublon)
        self._permise(self.admin, None)
        self._vieillir(self.client)
        self._vieillir(self.client)
        self.assertEqual(Assistant._get_ordered_partner(paire)[-1], self.client)
        self._refusee(self.admin, None)

    def test_les_contacts_d_une_source_comptent(self):
        """Une société sans service, dont un contact en porte : Odoo réécrit aussi le
        `parent_id` du contact, et ses services changeraient de client."""
        vide = self.env["res.partner"].create({"name": "Société Vide Essai", "is_company": True})
        employe = self.env["res.partner"].create({"name": "Employé Essai", "parent_id": vide.id})
        self.service.partner_id = employe
        with self.assertRaisesRegex(UserError, "hosting|hébergement"):
            self._assistant(self.admin)._hosting_garde_fusion((self.client | vide).ids, self.client)
        self.assertEqual(employe.parent_id, vide)

    def test_la_destination_ne_change_pas_de_client(self):
        """Une personne sans parent porte des services et reste ; la source est contact d'un
        autre client : Odoo donnerait ce parent à la destination."""
        personne = self.env["res.partner"].create({"name": "Personne Essai"})
        self.service.partner_id = personne
        contact = self.env["res.partner"].create({"name": "Contact Essai", "parent_id": self.client.id})
        with self.assertRaisesRegex(UserError, "hosting|hébergement"):
            self._assistant(self.admin)._hosting_garde_fusion((personne | contact).ids, personne)
        self.assertFalse(personne.parent_id)
        # Même parent des deux côtés : le client ne change pas, la fusion passe la garde.
        personne.parent_id = self.client
        self._assistant(self.admin)._hosting_garde_fusion((personne | contact).ids, personne)
        # Une source société ferait de la destination, contact du client, une société à part :
        # ses services quitteraient le client. Refus.
        with self.assertRaisesRegex(UserError, "hosting|hébergement"):
            self._assistant(self.admin)._hosting_garde_fusion((personne | self.doublon).ids, personne)

    def test_une_destination_sans_service_peut_changer_de_client(self):
        personne = self.env["res.partner"].create({"name": "Personne Sans Service"})
        contact = self.env["res.partner"].create({"name": "Contact Sans Service", "parent_id": self.client.id})
        self._assistant(self.admin)._hosting_garde_fusion((personne | contact).ids, personne)

    def test_un_contact_sans_service_ne_change_rien(self):
        autre = self.env["res.partner"].create({"name": "Fiche Sans Service", "is_company": True})
        self._assistant(self.createur)._hosting_garde_fusion((self.client | autre).ids, self.client)

    def test_le_refus_parle_la_langue_de_l_usager(self):
        if "fr_CA" not in dict(self.env["res.lang"].get_installed()):
            self.skipTest("fr_CA inactive")
        assistant = self._assistant(self.createur).with_context(lang="fr_CA")
        with self.assertRaisesRegex(UserError, "gestionnaire d'hébergement"):
            assistant._merge((self.client | self.doublon).ids, dst_partner=self.client)

    def test_pas_de_boucle_de_parents(self):
        """Une source archivée, contact d'un contact de la destination : Odoo ne la voit pas
        et donnerait à la destination l'un de ses propres contacts pour parent."""
        tete = self.env["res.partner"].create({"name": "Tête Essai"})
        milieu = self.env["res.partner"].create({"name": "Milieu Essai", "parent_id": tete.id})
        archive = self.env["res.partner"].create({"name": "Archivé Essai", "parent_id": milieu.id})
        archive.active = False
        with self.assertRaisesRegex(UserError, "own contacts|propres contacts"):
            self._assistant(self.admin)._hosting_garde_fusion((tete | archive).ids, tete)
        self.assertFalse(tete.parent_id)
        # Un contact archivé fusionné dans son propre parent : pas de boucle (Odoo ignore un
        # parent égal à la destination), la garde laisse passer.
        direct = self.env["res.partner"].create({"name": "Direct Essai", "parent_id": tete.id})
        direct.active = False
        self._assistant(self.admin)._hosting_garde_fusion((tete | direct).ids, tete)

    def test_tout_fusionner_automatiquement(self):
        """Le bouton global retire en SQL le statut de société des filiales : refusé quand une
        filiale porte des services. (On n'éprouve que la garde : le bouton valide en base.)"""
        Assistant = self._assistant(self.admin)
        filiale = self.env["res.partner"].create({
            "name": "Filiale Essai", "is_company": True, "parent_id": self.client.id})
        self.service.partner_id = filiale
        with self.assertRaisesRegex(UserError, "hosting|hébergement"):
            Assistant._hosting_garde_mise_a_jour_globale()
        self._assistant(self.gestion)._hosting_garde_mise_a_jour_globale()
