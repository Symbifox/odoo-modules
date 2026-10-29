# -*- coding: utf-8 -*-
"""Les pronoms dans les cartes poussées vers Nextcloud."""

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_preferred_name")
class TestCartesNextcloud(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Config = cls.env["nextcloud.contacts.sync.config"]
        cls.personne = cls.env["res.partner"].create({
            "name": "Sam Exemple", "bf_pronouns": "iel", "bf_grammatical_gender": "neuter",
            "x_nc_contact_uid": "uid-essai"})

    def test_la_carte_porte_les_pronoms(self):
        carte = self.Config._partner_to_vcard(self.personne, uid="uid-essai")
        self.assertIn("PRONOUNS:iel", carte)
        self.assertIn("GRAMGENDER:neuter", carte)
        self.assertTrue(carte.rstrip().endswith("END:VCARD"))

    def test_rien_pour_qui_n_a_rien_donne(self):
        rien = self.env["res.partner"].create({"name": "Lou Exemple", "x_nc_contact_uid": "uid-lou"})
        carte = self.Config._partner_to_vcard(rien, uid="uid-lou")
        self.assertNotIn("PRONOUNS", carte)

    def test_changer_les_pronoms_marque_la_carte_a_repousser(self):
        etiquette = self.env["res.partner.category"].create({"name": "Exclus essai"})
        config = self.Config.create({
            "name": "Essai", "nextcloud_base_url": "https://nc.exemple.example",
            "carddav_path": "/remote.php/dav/addressbooks/users/x/contacts/",
            "nextcloud_user": "x", "exclude_tag_id": etiquette.id, "sync_direction": "odoo_to_nc"})
        self.personne.write({"x_nc_contacts_config_id": config.id, "x_carddav_etag": "e1",
                             "x_contact_sync_source": "odoo"} if "odoo" in dict(
            self.personne._fields["x_contact_sync_source"].selection) else
            {"x_nc_contacts_config_id": config.id, "x_carddav_etag": "e1"})
        self.personne.write({"ref": "témoin"})
        self.assertEqual(self.personne.x_carddav_etag, "e1", "témoin : un autre champ ne marque rien")
        self.personne.write({"bf_pronouns": "elle"})
        self.assertFalse(self.personne.x_carddav_etag)

    def test_une_fiche_de_source_nextcloud_n_est_pas_marquee(self):
        etiquette = self.env["res.partner.category"].create({"name": "Exclus essai bis"})
        config = self.Config.create({
            "name": "Essai bis", "nextcloud_base_url": "https://nc.exemple.example",
            "carddav_path": "/remote.php/dav/addressbooks/users/x/contacts/",
            "nextcloud_user": "x", "exclude_tag_id": etiquette.id})
        source = dict(self.personne._fields["x_contact_sync_source"].selection)
        if "nextcloud" not in source:
            self.skipTest("pas de source Nextcloud")
        self.personne.write({"x_nc_contacts_config_id": config.id, "x_carddav_etag": "e1",
                             "x_contact_sync_source": "nextcloud"})
        self.personne.write({"bf_pronouns": "elle"})
        self.assertEqual(self.personne.x_carddav_etag, "e1")

    def test_une_longue_ligne_est_pliee(self):
        from odoo.addons.bf_preferred_name.models.res_partner import PRONOUNS_MAX_LENGTH
        self.personne.bf_pronouns = ("é" * PRONOUNS_MAX_LENGTH)[:PRONOUNS_MAX_LENGTH]
        carte = self.Config._partner_to_vcard(self.personne, uid="uid-essai")
        for ligne in carte.replace("\r\n", "\n").split("\n"):
            self.assertLessEqual(len(ligne.encode("utf-8")), 75, ligne)

    def test_hors_du_sens_odoo_vers_nextcloud_rien_n_est_marque(self):
        # Marquer fait relire la carte de
        # Nextcloud, qui écrase la fiche.
        etiquette = self.env["res.partner.category"].create({"name": "Exclus essai ter"})
        for sens in ("bidirectional", "nc_to_odoo"):
            if sens not in dict(self.Config._fields["sync_direction"].selection):
                continue
            config = self.Config.create({
                "name": "Essai " + sens, "nextcloud_base_url": "https://nc.exemple.example",
                "carddav_path": "/remote.php/dav/addressbooks/users/x/contacts/",
                "nextcloud_user": "x", "exclude_tag_id": etiquette.id, "sync_direction": sens})
            self.personne.write({"x_nc_contacts_config_id": config.id, "x_carddav_etag": "e1"})
            self.personne.write({"bf_pronouns": "elle" if self.personne.bf_pronouns != "elle" else "iel"})
            self.assertEqual(self.personne.x_carddav_etag, "e1", sens)
