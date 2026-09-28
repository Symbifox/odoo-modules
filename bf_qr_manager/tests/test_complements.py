"""Ce que les autres essais ne touchent pas : réglages, formats, variantes, traduction."""
import base64
import os

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import HttpCase, TransactionCase, tagged

from .commun import monter


@tagged("post_install", "-at_install")
class TestReglages(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        monter(cls)

    def test_les_groupes_s_enregistrent_par_societe(self):
        autre = self.env["res.groups"].create({"name": "Techniciens (essai QR)"})
        page = self.env["bf.nfc.config"].with_user(self.gestion).create({})
        self.assertEqual(page.qr_groupe_ids, self.groupe_concierges)
        page.qr_groupe_ids = [(6, 0, autre.ids)]
        page.action_enregistrer()
        self.assertEqual(self.societe.bf_qr_groupe_ids, autre)

    def test_les_reglages_restent_a_la_gestion(self):
        with self.assertRaises(AccessError):
            self.env["bf.nfc.config"].with_user(self.concierge).create({})

    def test_types_suggeres_ne_sement_que_ce_qui_existe(self):
        page = self.env["bf.nfc.config"].with_user(self.gestion).create({})
        page.action_types_suggeres()
        modeles = self.env["bf.nfc.target.type"].search([]).mapped("model")
        for nom in ("bf.floorplan.element", "hosting.endpoint", "event.event", "bf.linkpage"):
            self.assertEqual(nom in modeles, nom in self.env, nom)

    def test_une_pastille_hors_lot_garde_la_porte_nfc(self):
        pastille = self.env["bf.nfc.tag"].create({
            "name": "NFC", "gesture_id": self.geste_open.id,
            "res_model": "res.partner", "res_id": self.partenaire.id})
        self.assertIn("/nfc/", pastille.url)

    def test_compteurs_du_lot(self):
        self.assertEqual((self.lot.count_total, self.lot.count_vierges, self.lot.count_associees),
                         (10, 10, 0))
        self.etiquettes[0].with_user(self.gestion)._associer(self.geste_open, cible=self.partenaire)
        self.etiquettes[1].active = False
        self.lot.invalidate_recordset()
        self.assertEqual((self.lot.count_total, self.lot.count_vierges, self.lot.count_associees),
                         (9, 8, 1))

    def test_l_assistant_d_association_de_la_fiche(self):
        tag = self.etiquettes[0]
        action = tag.with_user(self.concierge).action_associer_qr()
        assistant = self.env[action["res_model"]].with_user(self.concierge).with_context(
            action["context"]).create({"gesture_id": self.geste_url.id,
                                       "url": "https://symbifox.com", "qr_public": True})
        proposes = self.env["bf.nfc.gesture"].search(assistant._domaine_gestes())
        self.assertIn(self.geste_url, proposes)
        self.assertNotIn(self.env.ref("bf_qr_manager.gesture_vierge"), proposes)
        self.assertNotIn(self.env.ref("bf_nfc.gesture_cron"), proposes)
        assistant.action_associer()
        self.assertTrue(tag.qr_public)
        with self.assertRaises(UserError):
            tag.with_user(self.concierge).action_associer_qr()

    def test_la_gestion_voit_les_gestes_reserves(self):
        domaine = self.env["bf.qr.associer"].with_user(self.gestion)._domaine_gestes()
        self.assertIn(self.env.ref("bf_nfc.gesture_cron"), self.env["bf.nfc.gesture"].search(domaine))


@tagged("post_install", "-at_install")
class TestFormats(TransactionCase):

    def _format(self, **valeurs):
        base = {"name": "Essai", "papier": "letter", "largeur": 50, "hauteur": 50,
                "colonnes": 2, "lignes": 2, "marge_gauche": 10, "marge_haut": 10,
                "pas_x": 60, "pas_y": 60, "marge_interne": 2}
        base.update(valeurs)
        return self.env["bf.qr.label.format"].create(base)

    def test_feuille_sur_mesure(self):
        with self.assertRaises(ValidationError):
            self._format(papier="custom")
        fmt = self._format(papier="custom", page_largeur=150, page_hauteur=150)
        self.assertEqual(fmt._feuille(), (150, 150))

    def test_etiquettes_qui_se_chevauchent(self):
        with self.assertRaises(ValidationError):
            self._format(pas_x=40)

    def test_rond_et_texte(self):
        rond = self._format(forme="rond")
        self.assertAlmostEqual(rond._cote_qr(), 50 / 2 ** 0.5 - 4, places=3)
        self.assertLess(rond._cote_qr(avec_texte=True), rond._cote_qr())
        large = self._format(largeur=100, hauteur=30, pas_x=100, colonnes=1)
        self.assertTrue(large._texte_a_cote())
        self.assertFalse(rond._texte_a_cote())

    def test_position_repere_pdf(self):
        fmt = self._format()
        x, y = fmt._position(3)
        self.assertAlmostEqual(x, 70)
        self.assertAlmostEqual(y, 279.4 - 70 - 50, places=3)
        self.assertEqual(fmt._position(4), fmt._position(0))


@tagged("post_install", "-at_install")
class TestImpressionVariantes(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        monter(cls)

    def test_textes(self):
        tag = self.etiquettes[0]
        tag.with_user(self.gestion)._associer(self.geste_open, cible=self.partenaire)
        w = self.env["bf.qr.imprimer"].with_user(self.gestion).create({"tag_ids": [(6, 0, tag.ids)]})
        attendu = {"aucun": "", "reference": "TST-0001", "libelle": self.partenaire.display_name,
                   "les_deux": "TST-0001 · %s" % self.partenaire.display_name}
        for texte, valeur in attendu.items():
            w.texte = texte
            self.assertEqual(w._texte_de(tag), valeur)
        w.texte = "les_deux"
        self.assertEqual(w._texte_de(self.etiquettes[1]), "TST-0002")

    def test_pastille_signee_refusee(self):
        signee = self.env["bf.nfc.tag"].create({
            "name": "Signée", "gesture_id": self.geste_open.id, "sdm_enabled": True,
            "sdm_uid": "04DE5F1EACC041", "res_model": "res.partner", "res_id": self.partenaire.id})
        with self.assertRaisesRegex(UserError, "signée"):
            self.env["bf.qr.imprimer"].create({"tag_ids": [(6, 0, signee.ids)]})._pdf()


@tagged("post_install", "-at_install")
class TestImportVariantes(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        monter(cls)

    def _import(self, texte, batch=True):
        valeurs = {"fichier_nom": "a.csv", "fichier": base64.b64encode(texte.encode())}
        if batch:
            valeurs["batch_id"] = self.lot.id
        w = self.env["bf.qr.import"].with_user(self.gestion).create(valeurs)
        w.action_analyser()
        return w

    def test_plage_de_references_sans_lot(self):
        w = self._import("etiquette,type,fiche\nTST-0002..TST-0004,Contact,%s\n" % self.partenaire.id,
                         batch=False)
        self.assertEqual(w.count_etiquettes, 3, w.ligne_ids.mapped("message"))

    def test_numero_seul_sans_lot_refuse(self):
        w = self._import("etiquette,type,fiche\n2,Contact,%s\n" % self.partenaire.id, batch=False)
        self.assertIn("un numéro seul se lit dans un lot", w.ligne_ids.message)

    def test_geste_passage_et_public_ignore(self):
        w = self._import("etiquette,type,fiche,geste,public\n1,Contact,%s,passage,oui\n"
                         % self.partenaire.id)
        w.action_appliquer()
        tag = self.etiquettes[0]
        self.assertEqual(tag.gesture_id, self.geste_note)
        self.assertFalse(tag.qr_public)

    def test_geste_inconnu_et_plage_a_l_envers(self):
        w = self._import("etiquette,adresse,geste\n1,https://symbifox.com,danser\n5-3,https://a.b,\n")
        self.assertEqual(w.count_erreur, 2)
        self.assertIn("inconnu", w.ligne_ids[0].message)
        self.assertIn("envers", w.ligne_ids[1].message)

    def test_sans_colonne_etiquette(self):
        with self.assertRaises(UserError):
            self._import("type,fiche\nContact,1\n")

    def test_point_virgule_et_bom(self):
        texte = "﻿Étiquette;Type;Fiche\n1;Contact;%s\n" % self.partenaire.id
        w = self._import(texte)
        self.assertEqual(w.count_ok, 1, w.ligne_ids.mapped("message"))

    def test_retour_au_fichier(self):
        w = self._import("etiquette,type,fiche\n1,Contact,%s\n" % self.partenaire.id)
        w.action_retour()
        self.assertEqual(w.state, "saisie")
        self.assertFalse(w.ligne_ids)


@tagged("post_install", "-at_install")
class TestTraduction(TransactionCase):

    def test_le_catalogue_anglais_couvre_le_module(self):
        import polib
        dossier = os.path.join(os.path.dirname(__file__), "..", "i18n")
        po = polib.pofile(os.path.join(dossier, "en_CA.po"))
        # Les libellés en français que le module apporte ont tous leur anglais ; les
        # seuls restants sont ceux des Pastilles, que leur propre catalogue traduit.
        du_socle = {"Ce geste écrit", "Geste déclenché par une pastille NFC",
                    "Réglages des pastilles NFC"}
        manquants = [e.msgid for e in po if not e.msgstr and any(c in e.msgid for c in "éèàçêâ«»")
                     and e.msgid not in du_socle and not e.msgid.startswith("Coché dès que")]
        self.assertFalse(manquants)
        self.assertFalse([e.msgid for e in po if e.msgstr and "%" in e.msgid
                          and sorted(_formats(e.msgid)) != sorted(_formats(e.msgstr))])

    def test_le_menu_se_lit_en_anglais(self):
        if not self.env["res.lang"]._lang_get("en_CA"):
            self.skipTest("en_CA n'est pas chargé dans cette base")
        menu = self.env.ref("bf_qr_manager.menu_qr_root").with_context(lang="en_CA")
        self.assertEqual(menu.name, "QR codes")


def _formats(texte):
    import re
    return re.findall(r"%(?:\([a-z]+\))?[.0-9]*[sdf]", texte)


@tagged("post_install", "-at_install")
class TestPorteNfc(HttpCase):
    """Une étiquette vierge tapée par la porte /nfc/ du socle (application, lien direct)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        monter(cls)

    def test_la_gestion_est_menee_a_la_fiche(self):
        self.authenticate("qr_gestion", "qr_gestion_mdp")
        tag = self.etiquettes[0]
        r = self.url_open("/nfc/%s" % tag.code, allow_redirects=False)
        self.assertIn("/odoo/bf.nfc.tag/%s" % tag.id, r.headers.get("Location", ""))

    def test_l_interne_lit_un_refus_poli(self):
        self.authenticate("qr_interne", "qr_interne_mdp")
        r = self.url_open("/nfc/%s" % self.etiquettes[0].code, allow_redirects=False)
        self.assertEqual(r.status_code, 200)
        self.assertIn("pas encore associée", r.text)


@tagged("post_install", "-at_install")
class TestApplicationMobile(HttpCase):
    """L'application du socle ne voit ni les étiquettes vierges ni le geste « vierge »."""

    API = "/bf_nfc/mobile/v1"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        monter(cls)
        # Une vraie pastille, jamais tapée : triée APRÈS les vierges par nom
        # (« ZZ » > « TST »), c'est elle que la borne de 100 aurait fait tomber.
        cls.vraie = cls.env["bf.nfc.tag"].create({
            "name": "ZZ Porte du bureau", "gesture_id": cls.geste_open.id,
            "res_model": "res.partner", "res_id": cls.partenaire.id})
        lot = cls.env["bf.qr.batch"].with_user(cls.gestion).create({"prefixe": "MOB", "quantite": 120})
        lot.action_generer()

    def _apparier(self, login):
        import base64 as b64
        import hashlib
        from odoo.tests import new_test_user
        personne = new_test_user(self.env, login=login, groups="base.group_user,bf_nfc.group_nfc_manager")
        verif = "verificateur-qr-assez-long-pour-etre-serieux"
        defi = b64.urlsafe_b64encode(hashlib.sha256(verif.encode()).digest()).decode().rstrip("=")
        Device = self.env["bf.nfc.device"]
        _appareil, jeton = Device._exchange(Device._issue_pending(personne.id, challenge=defi), verif)
        return {"Authorization": "Bearer %s" % jeton}

    def test_mes_pastilles_sans_les_vierges(self):
        entetes = self._apparier("mobile-liste")
        codes = [t["code"] for t in self.url_open(self.API + "/pastilles", headers=entetes).json()["pastilles"]]
        self.assertIn(self.vraie.code, codes)
        vierges = set(self.env["bf.nfc.tag"].search([("qr_vierge", "=", True)]).mapped("code"))
        self.assertFalse(vierges & set(codes))

    def test_le_catalogue_ne_propose_pas_vierge(self):
        entetes = self._apparier("mobile-catalogue")
        gestes = [g["code"] for g in self.url_open(self.API + "/catalogue", headers=entetes).json()["gestes"]]
        self.assertIn("open", gestes)
        self.assertNotIn("qr_vierge", gestes)

    def test_le_site_montre_toujours_les_vierges(self):
        self.assertGreaterEqual(self.env["bf.nfc.tag"].search_count([("qr_vierge", "=", True)]), 120)
