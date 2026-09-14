"""À qui la tâche, chez le pair.

Le banc reprend le montage de `test_federation` : deux pairs qui pointent sur
l'instance elle-même. `peer_b` partage, `peer_a` reçoit. Ce qui s'éprouve ici
est la seule question traitée : l'émetteur propose une personne, et le
receveur décide, seul, ce qu'il en fait.
"""

from odoo import SUPERUSER_ID
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged

from ..models import transport
# Le module, pas la classe : une classe importée par son nom est une classe de plus
# pour le chargeur. L'héritage rejoue quand même la suite de base une fois ici, comme
# dans les essais de sécurité ; l'import évite seulement une passe de plus.
from . import test_federation as base


@tagged("post_install", "-at_install", "federation")
class TestFederationAssignee(base.TestFederation):

    def setUp(self):
        super().setUp()
        # Une personne DE CHEZ LE PAIR, telle qu'ICI on la connaît : un contact sous
        # l'organisation du pair. C'est tout ce que l'émetteur a, et c'est assez.
        self.contact_pair = self.env["res.partner"].create({
            "name": "Personne visée", "email": "visee@pair.example",
            "parent_id": self.peer_b.partner_id.id})
        # Et, chez le receveur, un compte interne à qui elle pourrait correspondre.
        self.visee = self.env["res.users"].create({
            "name": "Personne visée", "login": "visee.ici", "email": "visee@ici.example",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])]})

    def _apparier(self, courriel, partner, user=None):
        return self.env["federation.peer.identity"].create({
            "peer_id": self.peer_a.id, "remote_email": courriel,
            "remote_name": "Personne visée", "local_partner_id": partner.id,
            "local_user_id": user.id if user else False})

    def _partager(self, assignee=None, nom="Tâche adressée"):
        task = self.env["project.task"].create({
            "name": nom, "project_id": self.project.id,
            "federation_peer_id": self.peer_b.id,
            "federation_assignee_id": assignee.id if assignee else False})
        self._flush()
        return task, self._mirror_of(task)

    def _notes(self, record):
        return "\n".join(b or "" for b in record.message_ids.mapped("body"))

    def _assert_note_ligne_sans_compte(self, mirror):
        """La ligne existe : la note ne doit pas demander de l'ajouter, ce que la
        contrainte d'unicité refuserait."""
        notes = self._notes(mirror)
        self.assertIn("Ce courriel est apparié", notes)
        self.assertNotIn("ne figure pas", notes)

    def _portail(self, login):
        return self.env["res.users"].with_context(no_reset_password=True).create({
            "name": f"Portail {login}", "login": login, "email": f"{login}@pair.example",
            "groups_id": [(6, 0, [self.env.ref("base.group_portal").id])]})

    def _interne(self, login, **vals):
        return self.env["res.users"].create(dict({
            "name": f"Compte {login}", "login": login,
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])]}, **vals))

    # --- La carte ------------------------------------------------------------------
    def test_30_sans_destinataire_la_carte_ne_porte_rien_de_plus(self):
        """L'empreinte des liens vivants ne doit pas bouger pour rien."""
        task = self.env["project.task"].create({
            "name": "Sans destinataire", "project_id": self.project.id,
            "federation_peer_id": self.peer_b.id})
        Link = self.env["federation.link"]
        sans = task._federation_card()
        self.assertNotIn("assignee", sans)
        task.federation_assignee_id = self.contact_pair
        avec = task._federation_card()
        self.assertEqual(avec["assignee"], {"name": "Personne visée", "email": "visee@pair.example"})
        # La clé pèse dans l'empreinte : ré-adresser renvoie bien la carte. Et retirer
        # le destinataire rend exactement l'empreinte d'origine.
        self.assertNotEqual(Link._card_fingerprint(sans), Link._card_fingerprint(avec))
        task.federation_assignee_id = False
        self.assertEqual(Link._card_fingerprint(sans), Link._card_fingerprint(task._federation_card()))

    def test_31_un_destinataire_hors_du_pair_est_refuse(self):
        """Se tromper de pair n'est pas une coquille, c'est un incident."""
        etranger = self.env["res.partner"].create({"name": "Quelqu'un d'ailleurs", "email": "x@ailleurs.example"})
        with self.assertRaises(ValidationError):
            self.env["project.task"].create({
                "name": "Mal adressée", "project_id": self.project.id,
                "federation_peer_id": self.peer_b.id, "federation_assignee_id": etranger.id})

    def test_31b_un_contact_sorti_de_l_organisation_ne_part_plus(self):
        task = self.env["project.task"].create({
            "name": "Adressée puis sortie", "project_id": self.project.id,
            "federation_peer_id": self.peer_b.id, "federation_assignee_id": self.contact_pair.id})
        self.assertIn("assignee", task._federation_card())
        self.contact_pair.parent_id = False
        task.invalidate_recordset()
        self.assertNotIn("assignee", task._federation_card())

    # --- La résolution chez le receveur ---------------------------------------------
    def test_32_le_compte_apparie_recoit_la_tache(self):
        self._apparier("visee@pair.example", self.visee.partner_id)
        _task, mirror = self._partager(self.contact_pair)
        self.assertEqual(mirror.user_ids, self.visee)
        notes = self._notes(mirror)
        self.assertIn("elle est assignée à", notes)
        self.assertNotIn("personnes appariées", notes)

    def test_33_le_compte_explicite_prime_sur_le_contact(self):
        """Le contact et le compte ne sont pas le même objet : le second tranche."""
        autre = self._interne("autre.ici")
        self._apparier("visee@pair.example", self.visee.partner_id, user=autre)
        _task, mirror = self._partager(self.contact_pair)
        self.assertEqual(mirror.user_ids, autre)

    def test_33b_un_compte_explicite_devenu_invalide_ne_cede_pas_au_contact(self):
        autre = self._interne("autre.archive")
        self._apparier("visee@pair.example", self.visee.partner_id, user=autre)
        autre.active = False
        _task, mirror = self._partager(self.contact_pair)
        self.assertEqual(mirror.user_ids, self.receveur, "le repli, pas le compte du contact écarté")
        self._assert_note_ligne_sans_compte(mirror)

    def test_34_sans_appariement_la_tache_tombe_dans_le_repli_et_la_note_le_dit(self):
        _task, mirror = self._partager(self.contact_pair)
        self.assertEqual(mirror.user_ids, self.receveur)
        notes = self._notes(mirror)
        self.assertIn("visee@pair.example", notes)
        self.assertIn("ne figure pas dans les personnes appariées", notes)

    def test_34b_resolu_sur_le_compte_du_repli_ne_dit_pas_qu_il_manque_une_ligne(self):
        self._apparier("visee@pair.example", self.receveur.partner_id)
        _task, mirror = self._partager(self.contact_pair)
        self.assertEqual(mirror.user_ids, self.receveur)
        notes = self._notes(mirror)
        self.assertIn("elle est assignée à", notes)
        self.assertNotIn("ne figure pas", notes)

    def test_34c_un_repli_archive_ne_recoit_rien(self):
        self.receveur.active = False
        _task, mirror = self._partager(self.contact_pair)
        self.assertFalse(mirror.user_ids)
        self.assertIn("n'est assignée à personne", self._notes(mirror))

    def test_35_un_contact_sans_compte_interne_ne_recoit_rien(self):
        """Un contact apparié dont le seul compte est un portail reste un contact."""
        portail = self._portail("portail.pair")
        self.assertTrue(portail.share)
        self._apparier("visee@pair.example", portail.partner_id)
        _task, mirror = self._partager(self.contact_pair)
        self.assertEqual(mirror.user_ids, self.receveur)
        self._assert_note_ligne_sans_compte(mirror)

    def test_36_deux_comptes_sur_le_contact_ne_se_devinent_pas(self):
        """La règle d'Odoo pour `@nom` : un seul candidat, sinon rien."""
        partner = self.visee.partner_id
        self._interne("deuxieme.ici", partner_id=partner.id)
        self._apparier("visee@pair.example", partner)
        _task, mirror = self._partager(self.contact_pair)
        self.assertEqual(mirror.user_ids, self.receveur)
        self._assert_note_ligne_sans_compte(mirror)

    def test_37_l_appariement_ne_regarde_pas_la_casse(self):
        self._apparier("Visee@Pair.Example", self.visee.partner_id)
        _task, mirror = self._partager(self.contact_pair)
        self.assertEqual(mirror.user_ids, self.visee)

    def test_37b_un_courriel_n_a_qu_une_ligne_par_pair(self):
        self._apparier("visee@pair.example", self.visee.partner_id)
        with self.assertRaises(ValidationError):
            self._apparier("VISEE@pair.example", self.receveur.partner_id)

    # --- Ce que le réseau peut écrire ------------------------------------------------
    def test_37c_le_nom_propose_est_echappe(self):
        self.contact_pair.name = '<img src=x onerror="alert(1)">Personne'
        _task, mirror = self._partager(self.contact_pair)
        notes = self._notes(mirror)
        self.assertNotIn("<img", notes)
        self.assertIn("&lt;img", notes)

    def test_37d_sans_courriel_pas_de_proposition(self):
        peer = self.peer_a
        self.assertEqual(peer._proposed_assignee({"assignee": {"name": "Sans courriel"}}), ("", ""))
        self.assertEqual(peer._proposed_assignee({"assignee": "pas un objet"}), ("", ""))
        self.assertEqual(peer._proposed_assignee({"assignee": {"email": ["x@y.z"]}}), ("", ""))

    def test_37e_une_demi_paire_de_substitution_ne_fait_pas_tomber_l_ecriture(self):
        self.assertEqual(transport.clean_text("a\ud800b"), "ab")

    # --- Ce qui se passe APRÈS la naissance -----------------------------------------
    def test_38_readresser_se_dit_mais_ne_reassigne_pas(self):
        """Un miroir déjà né appartient à qui l'a pris."""
        self._apparier("visee@pair.example", self.visee.partner_id)
        task, mirror = self._partager(self.contact_pair)
        self.assertEqual(mirror.user_ids, self.visee)
        # le receveur reprend la tâche à son compte
        mirror.with_context(federation_inbound=True).write({"user_ids": [(6, 0, [self.receveur.id])]})
        # et l'émetteur ré-adresse
        autre_contact = self.env["res.partner"].create({
            "name": "Autre personne visée", "email": "autre.visee@pair.example",
            "parent_id": self.peer_b.partner_id.id})
        task.federation_assignee_id = autre_contact
        self._flush()
        mirror.invalidate_recordset()
        self.assertEqual(mirror.user_ids, self.receveur, "le miroir ne change pas de mains tout seul")
        self.assertIn("est maintenant adressé à Autre personne visée", self._notes(mirror))

    def test_39_la_meme_proposition_ne_se_redit_pas(self):
        self._apparier("visee@pair.example", self.visee.partner_id)
        task, mirror = self._partager(self.contact_pair)
        task.name = "Titre changé, même destinataire"
        self._flush()
        mirror.invalidate_recordset()
        self.assertEqual(mirror.name, "Titre changé, même destinataire", "la carte est bien repassée")
        self.assertNotIn("maintenant adressé", self._notes(mirror))

    def test_39a_un_genre_qui_n_assigne_pas_ne_pose_pas_de_note(self):
        self._apparier("visee@pair.example", self.visee.partner_id)
        task, mirror = self._partager()
        self.patch(type(self.env["project.task"]), "_federation_addressable", False)
        task.federation_assignee_id = self.contact_pair
        self._flush()
        mirror.invalidate_recordset()
        self.assertNotIn("maintenant adressé", self._notes(mirror))

    def test_39b_retirer_le_partage_retire_le_destinataire(self):
        task, _mirror = self._partager(self.contact_pair)
        task.federation_peer_id = False
        self.assertFalse(task.federation_assignee_id, "dé-fédérer ne doit pas buter sur la contrainte")

    def test_39c_un_destinataire_sans_pair_est_refuse(self):
        with self.assertRaises(ValidationError):
            self.env["project.task"].create({
                "name": "Adressée à personne", "project_id": self.project.id,
                "federation_assignee_id": self.contact_pair.id})

    def test_39d_adresser_demande_le_role_de_gestionnaire(self):
        task, _mirror = self._partager()
        utilisateur = self._interne("simple.utilisateur", groups_id=[(6, 0, [
            self.env.ref("base.group_user").id, self.env.ref("project.group_project_user").id])])
        self.assertFalse(utilisateur.has_group("project.group_project_manager"))
        # il peut écrire la tâche…
        task.with_user(utilisateur).write({"description": "<p>modifiée</p>"})
        # … mais pas l'adresser
        with self.assertRaises(AccessError):
            task.with_user(utilisateur).write({"federation_assignee_id": self.contact_pair.id})

    # --- Les gardes -----------------------------------------------------------------
    def test_40_le_repli_doit_etre_un_compte_interne_actif(self):
        portail = self._portail("portail.repli")
        with self.assertRaises(ValidationError):
            self.peer_a.write({"mirror_user_id": portail.id})

    def test_41_un_nom_saisi_ici_retrouve_la_fiche(self):
        maison = self.env["res.partner"].create({"name": "Maison Témoin", "is_company": True})
        peer = self.env["federation.peer"].create({"name": "Maison Témoin", "mirror_user_id": self.receveur.id})
        peer._ensure_partner(nom_saisi_ici=True)
        self.assertEqual(peer.partner_id, maison)
        self.assertIn("retrouvée par le nom saisi ici", self._notes(peer))

    def test_41b_un_nom_annonce_par_le_pair_ne_rattache_jamais(self):
        """Un pair qui se présente sous le nom d'un client n'hérite pas de sa fiche."""
        client = self.env["res.partner"].create({"name": "Client Usurpé", "is_company": True})
        peer = self.env["federation.peer"].create({"name": "Client Usurpé", "mirror_user_id": self.receveur.id})
        peer._ensure_partner()
        self.assertTrue(peer.partner_id)
        self.assertNotEqual(peer.partner_id, client)
        self.assertFalse(self.env["federation.peer"]._for_partner(client), "le client n'offre pas ce pair")

    def test_42_deux_homonymes_ne_se_devinent_pas(self):
        self.env["res.partner"].create({"name": "Maison Double", "is_company": True})
        self.env["res.partner"].create({"name": "Maison Double", "is_company": True})
        peer = self.env["federation.peer"].create({"name": "Maison Double", "mirror_user_id": self.receveur.id})
        peer._ensure_partner(nom_saisi_ici=True)
        self.assertEqual(self.env["res.partner"].search_count(
            [("name", "=", "Maison Double"), ("is_company", "=", True)]), 3,
            "devant deux candidats, on crée plutôt que de choisir au hasard")

    def test_42b_une_fiche_d_une_autre_societe_n_est_pas_candidate(self):
        autre_societe = self.env["res.company"].create({"name": "Société voisine"})
        self.env["res.partner"].create({"name": "Maison Voisine", "is_company": True,
                                        "company_id": autre_societe.id})
        peer = self.env["federation.peer"].create({"name": "Maison Voisine", "mirror_user_id": self.receveur.id})
        peer._ensure_partner(nom_saisi_ici=True)
        self.assertNotEqual(peer.partner_id.company_id, autre_societe)

    def test_42c_un_nom_laisse_vide_a_l_acceptation_ne_rattache_pas(self):
        """La faille là où elle se produit : le sorcier, nom vide, nom annoncé par le pair."""
        Peer = self.env["federation.peer"]
        annonce = self.env.company.name
        sonde = Peer.new({"name": annonce, "company_id": self.env.company.id})
        if not sonde._partner_candidates(annonce):
            self.env["res.partner"].create({"name": annonce, "is_company": True})
        cible = sonde._partner_candidates(annonce)
        self.assertEqual(len(cible), 1, "le montage doit offrir UN candidat, sinon l'essai ne prouve rien")
        inviteur = Peer.create({"name": "Inviteur C", "mirror_user_id": self.receveur.id})
        inviteur.action_generate_invitation()
        code = inviteur.sudo().invitation_code
        self.env["federation.accept.wizard"].create({
            "base_url": self.base_url(), "code": code, "mirror_user_id": self.admin.id}).action_accept()
        self.env.invalidate_all()
        invite = Peer.search([("name", "=", annonce), ("id", "not in", (self.peer_a | self.peer_b | inviteur).ids)],
                             order="id desc", limit=1)
        self.assertTrue(invite, "le pair invité porte le nom annoncé")
        self.assertTrue(invite.partner_id)
        self.assertNotEqual(invite.partner_id, cible, "un nom annoncé ne rattache jamais")

    def test_42d_changer_de_pair_retire_le_destinataire_meme_en_lot(self):
        pair_c = self.env["federation.peer"].create({
            "name": "Pair C", "mirror_user_id": self.admin.id, "state": "active"})
        self.project.federation_peer_ids = [(4, pair_c.id)]
        adressee = self.env["project.task"].create({
            "name": "Adressée", "project_id": self.project.id,
            "federation_peer_id": self.peer_b.id, "federation_assignee_id": self.contact_pair.id})
        simple = self.env["project.task"].create({
            "name": "Pas adressée", "project_id": self.project.id, "federation_peer_id": self.peer_b.id})
        # Une tâche déjà chez le pair C, adressée à une personne de C : elle GARDE son
        # destinataire. C'est pour elle que l'écriture se partage.
        maison_c = self.env["res.partner"].create({"name": "Organisation C", "is_company": True})
        pair_c.sudo().partner_id = maison_c
        personne_c = self.env["res.partner"].create({
            "name": "Personne de C", "email": "personne@c.example", "parent_id": maison_c.id})
        garde = self.env["project.task"].create({
            "name": "Déjà chez C", "project_id": self.project.id,
            "federation_peer_id": pair_c.id, "federation_assignee_id": personne_c.id})
        (adressee | simple | garde).write({"federation_peer_id": pair_c.id})
        self.assertEqual((adressee | simple | garde).mapped("federation_peer_id"), pair_c)
        self.assertFalse(adressee.federation_assignee_id)
        self.assertEqual(garde.federation_assignee_id, personne_c, "une tâche qui garde son pair garde son destinataire")

    def test_42e_un_non_gestionnaire_ne_cree_pas_une_tache_adressee(self):
        utilisateur = self._interne("createur.simple", groups_id=[(6, 0, [
            self.env.ref("base.group_user").id, self.env.ref("project.group_project_user").id])])
        with self.assertRaises(AccessError):
            self.env["project.task"].with_user(utilisateur).create({
                "name": "Adressée par un simple utilisateur", "project_id": self.project.id,
                "federation_assignee_id": self.contact_pair.id})

    def test_42e2_un_import_sans_destinataire_passe_pour_un_non_gestionnaire(self):
        utilisateur = self._interne("import.vide", groups_id=[(6, 0, [
            self.env.ref("base.group_user").id, self.env.ref("project.group_project_user").id])])
        task = self.env["project.task"].with_user(utilisateur).create({
            "name": "Importée", "project_id": self.project.id, "federation_assignee_id": False})
        self.assertTrue(task)

    def test_42e3_la_cle_de_contexte_ne_dispense_pas_du_role(self):
        """`federation_inbound` se pose depuis n'importe quel client RPC."""
        task, _mirror = self._partager()
        utilisateur = self._interne("contexte.pose", groups_id=[(6, 0, [
            self.env.ref("base.group_user").id, self.env.ref("project.group_project_user").id])])
        with self.assertRaises(AccessError):
            task.with_user(utilisateur).with_context(federation_inbound=True).write(
                {"federation_assignee_id": self.contact_pair.id})
        with self.assertRaises(AccessError):
            task.with_user(utilisateur).with_context(federation_inbound=True).write({"federation_peer_id": False})

    def test_42f_reecrire_la_meme_valeur_n_adresse_rien(self):
        task, _mirror = self._partager(self.contact_pair)
        utilisateur = self._interne("import.simple", groups_id=[(6, 0, [
            self.env.ref("base.group_user").id, self.env.ref("project.group_project_user").id])])
        task.with_user(utilisateur).write({"federation_assignee_id": self.contact_pair.id})
        self.assertEqual(task.federation_assignee_id, self.contact_pair)

    def test_42g_un_script_sous_le_superutilisateur_cree_un_pair(self):
        peer = self.env["federation.peer"].with_user(SUPERUSER_ID).create({"name": "Pair de script"})
        self.assertTrue(peer.mirror_user_id.active)
        self.assertFalse(peer.mirror_user_id.share)

    def test_43_changer_le_repli_suit_le_projet_miroir(self):
        self._partager()
        projet = self.peer_a.mirror_project_id
        self.assertTrue(projet)
        self.peer_a.write({"mirror_user_id": self.visee.id})
        self.assertEqual(projet.user_id, self.visee)
