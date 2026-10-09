"""L'historique des courriels d'une fiche contact.

Ce qui compte ici, ce sont les bords de la définition : la copie (Cc) qui
manquait au bouton, la sous-chaîne qui ne doit rien ramasser, l'entreprise qui
compte ses personnes sans ramener la boîte entière de l'usager, et la boîte
d'un collègue qui reste fermée.
"""
import importlib.util
from pathlib import Path

from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import MobileApiCase


@tagged("post_install", "-at_install")
class TestContactHistorique(MobileApiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Partner = cls.env["res.partner"]
        cls.acme = Partner.create({"name": "Acme inc.", "is_company": True,
                                   "email": "info@acme.test"})
        cls.partner.parent_id = cls.acme
        cls.collegue = Partner.create({"name": "Collègue Acme", "email": "Lise.Exemple@ACME.test",
                                       "parent_id": cls.acme.id})
        cls.lettre = Partner.create({"name": "Une lettre", "email": "a@acme.test"})
        BfEmail = cls.env["bf.email"].with_user(cls.owner)
        cls.copie = BfEmail.create({
            "subject": "Ordre du jour", "direction": "out", "source": "imap",
            "user_id": cls.owner.id, "account_id": cls.account.id,
            "email_from": "owner@test.invalid",
            "email_to": "Quelqu'un <autre@ailleurs.test>",
            "email_cc": "\"Exemple, Lise\" <lise.exemple@acme.test>, data@acme.test",
            "date": "2026-09-01 12:00:00",
        })

    def _ids(self, partner):
        Email = self.as_owner()
        return set(Email.search(Email._contact_domain(partner)).ids)

    # -- la table des adresses ---------------------------------------------
    def test_les_adresses_sont_entieres_normalisees_et_par_role(self):
        lignes = {(p.address, p.role) for p in self.copie.sudo().participant_ids}
        self.assertEqual(lignes, {
            ("owner@test.invalid", "from"), ("autre@ailleurs.test", "to"),
            ("lise.exemple@acme.test", "cc"), ("data@acme.test", "cc")})

    def test_une_ecriture_des_adresses_refait_la_table(self):
        self.copie.sudo().write({"email_cc": "nouveau@acme.test"})
        self.assertEqual(
            set(self.copie.sudo().participant_ids.filtered(
                lambda p: p.role == "cc").mapped("address")),
            {"nouveau@acme.test"})

    def test_le_retro_remplissage_de_la_migration(self):
        self.env.cr.execute("DELETE FROM bf_email_participant")
        self.env["bf.email.participant"].invalidate_model()
        chemin = Path(__file__).parents[1] / "migrations" / "18.0.11.57.0" / "post-migrate.py"
        spec = importlib.util.spec_from_file_location("migration_11570", chemin)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.migrate(self.env.cr, "18.0.11.56.0")
        self.copie.invalidate_recordset(["participant_ids"])
        self.assertEqual(len(self.copie.sudo().participant_ids), 4)
        self.assertIn(self.copie.id, self._ids(self.collegue))

    # -- la définition -----------------------------------------------------
    def test_la_copie_compte(self):
        """Le bouton d'avant ne voyait que le `partner_id` : la copie manquait."""
        self.assertIn(self.copie.id, self._ids(self.collegue))

    def test_le_partner_id_compte_meme_sans_l_adresse(self):
        self.copie.sudo().partner_id = self.lettre
        self.assertIn(self.copie.id, self._ids(self.lettre))

    def test_une_sous_chaine_ne_ramasse_rien(self):
        """« a@acme.test » est dans « data@acme.test » : ce n'est pas la même adresse."""
        self.assertNotIn(self.copie.id, self._ids(self.lettre))

    def test_l_entreprise_compte_ses_personnes(self):
        self.assertIn(self.copie.id, self._ids(self.acme))
        self.assertNotIn(self.copie.id, self._ids(self.partner))

    def test_ma_propre_entreprise_ne_ramene_pas_toute_ma_boite(self):
        """L'usager est une personne de son entreprise : ses adresses à lui
        sont écartées, sinon la fiche de l'entreprise montrerait tout."""
        self.owner.partner_id.parent_id = self.acme
        self.outbound.sudo().partner_id = self.owner.partner_id
        self.assertNotIn(self.outbound.id, self._ids(self.acme))
        self.assertIn(self.outbound.id, self._ids(self.owner.partner_id))

    def test_mon_autre_fiche_sous_l_entreprise_ne_compte_pas(self):
        """Une fiche « personnel » qui porte un alias du
        compte, rangée sous l'entreprise, ramenait ses courriels par
        `partner_id`."""
        self.account.email_aliases = "moi@perso.test"
        perso = self.env["res.partner"].create({
            "name": "Moi (personnel)", "email": "Moi <MOI@perso.test>",
            "parent_id": self.acme.id})
        self.outbound.sudo().partner_id = perso
        self.assertNotIn(self.outbound.id, self._ids(self.acme))
        self.assertIn(self.outbound.id, self._ids(perso))

    def test_mon_adresse_ecrite_avec_mon_nom_est_ecartee(self):
        self.owner.partner_id.write({"email": "Propriétaire <Owner@Test.invalid>",
                                     "parent_id": self.acme.id})
        self.assertIn("owner@test.invalid", self.as_owner()._contact_self_addresses())
        self.assertNotIn(self.outbound.id, self._ids(self.acme))

    def test_la_boite_d_un_collegue_reste_fermee(self):
        self.foreign.sudo().write({"email_cc": "lise.exemple@acme.test"})
        self.assertNotIn(self.foreign.id, self._ids(self.collegue))
        adresses = self.env["bf.email.participant"].with_user(self.owner).search(
            [("email_id", "=", self.foreign.id)])
        self.assertFalse(adresses)
        with self.assertRaises(AccessError):
            self.foreign.sudo().participant_ids[:1].with_user(self.owner).read(["address"])

    # -- l'opérateur et le bouton ------------------------------------------
    def test_l_operateur_contact_par_numero_et_par_nom(self):
        Email = self.as_owner()
        par_numero = Email.search(
            Email._search_domain_from_query("contact:#%s" % self.collegue.id))
        par_nom = Email.search(Email._search_domain_from_query('contact:"Collègue Acme"'))
        self.assertIn(self.copie, par_numero)
        self.assertEqual(par_numero, par_nom)

    def test_une_fiche_introuvable_ne_rend_rien(self):
        Email = self.as_owner()
        self.assertFalse(Email.search(
            Email._search_domain_from_query("contact:personne-de-ce-nom")))

    def test_l_operateur_traverse_tous_les_dossiers(self):
        res = self.as_owner().inbox_get_messages(
            folder="all", search="contact:#%s" % self.acme.id)
        self.assertIn(self.copie.id, [m["id"] for m in res["messages"]])

    def test_tout_marquer_lu_sous_un_contact_ne_touche_que_lui(self):
        """Sans la ligne de recherche, « Tout
        marquer comme lu » sous « Courriels : X » vidait toute la boîte."""
        self.copie.sudo().status = "new"
        self.with_attachment.sudo().status = "new"
        res = self.as_owner().inbox_mark_folder_read(
            "all", search="contact:#%s" % self.collegue.id)
        self.assertEqual(res["marked"], 1)
        self.assertEqual(self.copie.status, "read")
        self.assertEqual(self.with_attachment.status, "new")

    def test_le_bouton_compte_ce_que_montre_le_panneau(self):
        """Le bouton ouvre le panneau sur `contact:#id` : il doit compter ce
        que cette recherche rend, dans « Tous les courriels »."""
        fiche = self.collegue.with_user(self.owner)
        action = fiche.action_view_bf_emails()
        self.assertEqual(action["tag"], "bf_email_contact_emails")
        self.assertEqual(action["params"]["contact_id"], self.collegue.id)
        res = self.as_owner().inbox_get_messages(
            folder="all", search="contact:#%s" % action["params"]["contact_id"])
        self.assertEqual(fiche.bf_email_count, res["total"])
        self.assertIn(self.copie.id, [m["id"] for m in res["messages"]])
