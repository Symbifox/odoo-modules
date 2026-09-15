"""L'administration des pastilles, jouée dans les rôles qui s'en servent.

🔴 Le rôle compte plus que le reste. Les comptes de travail de la maison sont
administrateurs système et traversent ACL, règles et champs réservés sans les
éprouver. Ici, tout ce qui doit marcher est joué par un GESTIONNAIRE des pastilles
qui n'est pas administrateur, et tout ce qui doit être refusé par un interne
ordinaire.
"""
import json

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.bf_nfc.models import chiffrement

CLE_A = "00112233445566778899AABBCCDDEEFF"
CLE_B = "FFEEDDCCBBAA99887766554433221100"


@tagged("post_install", "-at_install")
class TestAdministration(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.gestion = new_test_user(cls.env, login="admin-pastilles",
                                    groups="base.group_user,bf_nfc.group_nfc_manager")
        cls.interne = new_test_user(cls.env, login="interne-pastilles", groups="base.group_user")
        cls.geste_open = cls.env.ref("bf_nfc.gesture_open")
        cls.contacts = cls.env["res.partner"].create([
            {"name": "Porte avant"}, {"name": "Porte arrière"}, {"name": "Local technique"}])

    # ------------------------------------------------------------------
    # La fiche visée, lisible
    # ------------------------------------------------------------------
    def test_les_types_par_defaut_sont_semes(self):
        self.assertIn("res.partner", self.env["bf.nfc.target.type"].search([]).mapped("model"))

    def test_la_fiche_visee_se_lit_et_s_ecrit_sans_nom_technique(self):
        tag = self.env["bf.nfc.tag"].with_user(self.gestion).create({
            "name": "Porte", "gesture_id": self.geste_open.id,
            "cible": "res.partner,%s" % self.contacts[0].id,
        })
        self.assertEqual((tag.res_model, tag.res_id), ("res.partner", self.contacts[0].id))
        tag.cible = self.contacts[1]
        self.assertEqual(tag.res_id, self.contacts[1].id)
        self.assertEqual(tag.cible, self.contacts[1])

    def test_une_fiche_supprimee_ne_fait_pas_tomber_la_pastille(self):
        perdu = self.env["res.partner"].create({"name": "Disparu"})
        tag = self.env["bf.nfc.tag"].create({
            "name": "Orpheline", "gesture_id": self.geste_open.id,
            "res_model": "res.partner", "res_id": perdu.id,
        })
        perdu.unlink()
        tag.invalidate_recordset(["cible"])
        self.assertFalse(tag.cible)

    def test_la_gestion_non_administratrice_ouvre_le_formulaire(self):
        """🔴 Sans droit sur ``ir.model`` : la sélection, les vues et le filtre du lot passent."""
        Tag = self.env["bf.nfc.tag"].with_user(self.gestion)
        selection = Tag.fields_get(["cible"])["cible"]["selection"]
        self.assertIn("res.partner", [cle for cle, _libelle in selection])
        Tag.get_views([(False, "form"), (False, "list"), (False, "search")])
        self.env["bf.nfc.gesture"].with_user(self.gestion).search(
            [("target_model", "=", "res.partner")])
        tag = Tag.create({"name": "Lue", "gesture_id": self.geste_open.id,
                          "res_model": "res.partner", "res_id": self.contacts[0].id})
        self.assertTrue(tag.read(["cible"])[0]["cible"])

    def test_un_type_reserve_n_est_pas_propose_a_un_interne(self):
        self.env["bf.nfc.target.type"].create({"model": "res.country", "gestion_seulement": True})
        self.assertIn("res.country", self.env["bf.nfc.tag"].with_user(self.gestion)._modeles_cibles())
        self.assertNotIn("res.country", self.env["bf.nfc.tag"].with_user(self.interne)._modeles_cibles())
        self.assertNotIn("ir.cron", self.env["bf.nfc.tag"].with_user(self.interne)._modeles_cibles())

    def test_un_traitement_reste_reserve_a_la_gestion(self):
        from odoo.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            self.env["bf.nfc.target.type"].create({"model": "ir.cron", "gestion_seulement": False})

    def test_un_interne_ne_modifie_pas_la_liste(self):
        with self.assertRaises(AccessError):
            self.env["bf.nfc.target.type"].with_user(self.interne).create({"model": "res.country"})

    # ------------------------------------------------------------------
    # Historique
    # ------------------------------------------------------------------
    def test_changer_le_geste_laisse_une_trace(self):
        tag = self.env["bf.nfc.tag"].with_user(self.gestion).create({
            "name": "Suivie", "gesture_id": self.geste_open.id,
            "res_model": "res.partner", "res_id": self.contacts[0].id,
        })
        # ⚠️ Deux pièges d'essai, et chacun rend « rien n'est tracé » à tort. Odoo ne
        # suit pas un enregistrement créé dans la MÊME transaction (la création
        # marque ses valeurs initiales à None), et le suivi ne s'écrit qu'au
        # pré-commit. On referme donc le cycle entre la création et le changement,
        # comme le feraient deux clics.
        self.env.flush_all()
        self.env.cr.precommit.run()
        tag.with_user(self.gestion).gesture_id = self.env.ref("bf_nfc.gesture_note")
        self.env.flush_all()
        self.env.cr.precommit.run()
        suivis = tag.sudo().message_ids.tracking_value_ids.filtered(lambda v: v.field_id.name == "gesture_id")
        self.assertTrue(suivis, "Le changement de geste n'a laissé aucune trace.")
        self.assertEqual(tag.sudo().message_ids[:1].author_id, self.gestion.partner_id)

    def test_un_tapotement_ne_remplit_pas_le_fil(self):
        self.env["ir.config_parameter"].sudo().set_param("bf_nfc.fenetre_doublon_secondes", "0")
        tag = self.env["bf.nfc.tag"].create({
            "name": "Silencieuse", "gesture_id": self.geste_open.id,
            "res_model": "res.partner", "res_id": self.contacts[0].id,
        })
        avant = len(tag.message_ids)
        for _essai in range(3):
            tag.with_user(self.gestion).taper("app")
        self.env.flush_all()
        self.assertEqual(len(tag.message_ids), avant)

    # ------------------------------------------------------------------
    # Réglages
    # ------------------------------------------------------------------
    def test_la_gestion_regle_sans_etre_administratrice(self):
        page = self.env["bf.nfc.config"].with_user(self.gestion).create({})
        page.write({"fenetre_doublon": 45, "differe_max_heures": 24})
        page.action_enregistrer()
        icp = self.env["ir.config_parameter"].sudo()
        self.assertEqual(icp.get_param("bf_nfc.fenetre_doublon_secondes"), "45")
        self.assertEqual(icp.get_param("bf_nfc.differe_max_heures"), "24")

    def test_un_interne_n_ouvre_pas_les_reglages(self):
        with self.assertRaises(AccessError):
            self.env["bf.nfc.config"].with_user(self.interne).create({})

    def test_les_schemas_restent_a_l_administrateur_systeme(self):
        """⚠️ Odoo laisse écrire le champ réservé sur l'assistant : c'est l'action qui l'ignore.

        L'essai joue donc ce qui protège vraiment, pas la barrière qu'on croirait là.
        """
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("bf_nfc.schemas_appariement", "com.bluefoxconsultant.pastilles://")
        page = self.env["bf.nfc.config"].with_user(self.gestion).sudo().create(
            {"schemas_appariement": "https://piege.example/"}).with_user(self.gestion)
        page.action_enregistrer()
        self.assertEqual(icp.get_param("bf_nfc.schemas_appariement"),
                         "com.bluefoxconsultant.pastilles://")

    def test_la_page_dit_ou_vit_la_cle_de_chiffrement(self):
        page = self.env["bf.nfc.config"].with_user(self.gestion).create({})
        self.assertIn(page.origine_cle, ("base", "absente", "environnement", "configuration"))
        if page.origine_cle in ("base", "absente"):
            self.assertIn("copie de la base", page.avertissement_cle)

    # ------------------------------------------------------------------
    # Clés des pastilles signées
    # ------------------------------------------------------------------
    def test_la_gestion_pose_les_cles_et_ne_peut_plus_les_lire(self):
        ligne = self.env["bf.nfc.sdm.key"].with_user(self.gestion)._poser(self.env.company, CLE_A, CLE_B)
        self.assertEqual(self.env["bf.nfc.sdm.key"]._cles_de(self.env.company), (CLE_A, CLE_B))
        with self.assertRaises(AccessError):
            ligne.with_user(self.gestion).read(["meta_key_enc"])
        self.assertEqual(ligne.with_user(self.gestion).state, "ok")

    def test_les_cles_ne_sont_pas_en_clair_en_base(self):
        self.env["bf.nfc.sdm.key"].with_user(self.gestion)._poser(self.env.company, CLE_A, CLE_B)
        self.env.flush_all()
        self.env.cr.execute("SELECT meta_key_enc, file_key_enc FROM bf_nfc_sdm_key")
        brut = json.dumps(self.env.cr.fetchall()).upper()
        self.assertNotIn(CLE_A, brut)
        self.assertNotIn(CLE_B, brut)

    def test_un_interne_ne_pose_pas_de_cle(self):
        with self.assertRaises(AccessError):
            self.env["bf.nfc.sdm.key"].with_user(self.interne)._poser(self.env.company, CLE_A, CLE_B)
        with self.assertRaises(AccessError):
            self.env["bf.nfc.sdm.key"].with_user(self.interne).search([])

    def test_une_cle_malformee_est_refusee(self):
        with self.assertRaises(UserError):
            self.env["bf.nfc.sdm.key"].with_user(self.gestion)._poser(self.env.company, "1234", CLE_B)

    def test_l_assistant_n_ecrit_jamais_la_cle_en_clair(self):
        """🔴 Le clair ne touche pas la table, même entre l'enregistrement et le clic.

        Le client web enregistre le formulaire dans une transaction, puis appelle le
        bouton dans une seconde : un assistant qui stockait la saisie gardait 32
        caractères hexadécimaux en base entre les deux, et pour de bon si la seconde
        échouait.
        """
        assistant = self.env["bf.nfc.sdm.key.wizard"].with_user(self.gestion).create(
            {"meta_key": CLE_A, "file_key": CLE_B})
        self.env.flush_all()
        self.env.cr.execute("SELECT * FROM bf_nfc_sdm_key_wizard WHERE id = %s", (assistant.id,))
        brut = json.dumps(self.env.cr.dictfetchall(), default=str).upper()
        self.assertNotIn(CLE_A, brut)
        self.assertNotIn(CLE_B, brut)
        # ⚠️ Le cache de la transaction rend encore ce que l'appelant vient d'écrire ;
        # c'est la relecture qui compte, et elle passe par le calcul, qui ne rend rien.
        assistant.invalidate_recordset(["meta_key", "file_key"])
        self.assertFalse(assistant.meta_key, "Une clé posée ne se relit pas, même dans l'assistant.")
        self.assertFalse(assistant.file_key)
        assistant.action_poser()
        self.assertFalse(assistant.exists())
        self.assertEqual(self.env["bf.nfc.sdm.key"]._cles_de(self.env.company), (CLE_A, CLE_B))

    def test_une_cle_malformee_est_refusee_des_la_saisie(self):
        with self.assertRaises(UserError):
            self.env["bf.nfc.sdm.key.wizard"].with_user(self.gestion).create({"meta_key": "1234"})

    def test_une_cle_de_chiffrement_remplacee_rend_les_cles_a_reposer(self):
        ligne = self.env["bf.nfc.sdm.key"].with_user(self.gestion)._poser(self.env.company, CLE_A, CLE_B)
        if chiffrement.origine_de_la_cle(self.env) != "base":
            self.skipTest("clé de chiffrement hors de la base sur ce banc")
        from cryptography.fernet import Fernet
        self.env["ir.config_parameter"].sudo().set_param(chiffrement.PARAM_BASE,
                                                         Fernet.generate_key().decode())
        ligne.invalidate_recordset(["state"])
        self.assertEqual(ligne.state, "illisibles")
        self.assertEqual(self.env["bf.nfc.sdm.key"]._cles_de(self.env.company), (None, None))

    def test_la_migration_n_efface_pas_une_paire_qu_elle_ne_peut_pas_reprendre(self):
        """🔴 Les 16 octets gravés en usine ne se relisent nulle part : une paire
        incomplète ou mal formée reste en place au lieu de disparaître."""
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("bf_nfc.sdm_meta_key", CLE_A)  # sans la clé de fichier
        self.assertEqual(self.env["bf.nfc.sdm.key"]._migrer_les_parametres(), 0)
        self.assertEqual(icp.get_param("bf_nfc.sdm_meta_key"), CLE_A)
        icp.set_param("bf_nfc.sdm_file_key", "pas-une-cle")
        self.assertEqual(self.env["bf.nfc.sdm.key"]._migrer_les_parametres(), 0)
        self.assertEqual(icp.get_param("bf_nfc.sdm_file_key"), "pas-une-cle")

    def test_la_migration_deplace_et_efface_les_anciens_parametres(self):
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("bf_nfc.sdm_meta_key", CLE_A)
        icp.set_param("bf_nfc.sdm_file_key", CLE_B)
        self.assertEqual(self.env["bf.nfc.sdm.key"]._migrer_les_parametres(),
                         len(self.env["res.company"].search([])))
        self.assertFalse(icp.get_param("bf_nfc.sdm_meta_key"))
        self.assertEqual(self.env["bf.nfc.sdm.key"]._cles_de(self.env.company), (CLE_A, CLE_B))

    # ------------------------------------------------------------------
    # Création en lot et lien retour
    # ------------------------------------------------------------------
    def _lot(self, fiches, utilisateur=None, **valeurs):
        return self.env["bf.nfc.tag.lot"].with_user(utilisateur or self.gestion).create(dict({
            "res_model": fiches._name, "res_ids": ",".join(map(str, fiches.ids)),
            "gesture_id": self.geste_open.id,
        }, **valeurs))

    def test_une_pastille_par_fiche_et_rien_en_double(self):
        self.env["bf.nfc.tag"].create({
            "name": "Déjà là", "gesture_id": self.geste_open.id,
            "res_model": "res.partner", "res_id": self.contacts[0].id,
        })
        lot = self._lot(self.contacts, prefixe="Ronde")
        self.assertEqual((lot.fiche_count, lot.deja_count), (3, 1))
        action = lot.action_creer()
        crees = self.env["bf.nfc.tag"].search(action["domain"])
        self.assertEqual(len(crees), 2)
        self.assertEqual(set(crees.mapped("res_id")), set(self.contacts[1:].ids))
        self.assertTrue(all(n.startswith("Ronde · ") for n in crees.mapped("name")))
        with self.assertRaises(UserError):
            self._lot(self.contacts).action_creer()

    def test_un_interne_ne_cree_pas_de_lot(self):
        with self.assertRaises(AccessError):
            self._lot(self.contacts, utilisateur=self.interne).action_creer()

    def test_le_lot_refuse_un_geste_d_un_autre_type_de_fiche(self):
        with self.assertRaises(UserError):
            self._lot(self.contacts, gesture_id=self.env.ref("bf_nfc.gesture_cron").id).action_creer()

    def test_la_fiche_compte_et_ouvre_ses_pastilles(self):
        self._lot(self.contacts[:2]).action_creer()
        contact = self.contacts[0].with_user(self.gestion)
        self.assertEqual(contact.nfc_tag_count, 1)
        action = contact.action_voir_pastilles()
        self.assertEqual(self.env["bf.nfc.tag"].search_count(action["domain"]), 1)
        self.assertEqual(self.contacts[2].nfc_tag_count, 0)
