"""Un lien de signature ne se lit que par l'assistant journalisé.

Deux voies laissaient un gestionnaire lire un lien sans trace au journal :
l'assistant transitoire d'un collègue (aucune règle ne le réservait à son
auteur) et les champs du signataire eux-mêmes (jeton et lien lisibles par tout
gestionnaire, par RPC ou par l'export). Les gestionnaires de ces essais ne
sont PAS administrateurs : un administrateur garde la lecture du jeton.

La relecture adverse en a trouvé trois de plus, antérieures : un jeton CHOISI à
la création du signataire (par la valeur, le contexte ou la copie), les champs
du parcours (état, image, code vérifié, IP) écrits à la main, et la révélation
d'un signataire d'une autre société.
"""
import base64
import io

from lxml import etree

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged

from .common import BaseNeuve

_JETON = ("access_token", "signing_url", "otp_hash")


def _pdf():
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.drawString(72, 720, "Document de test")
    c.showPage()
    c.save()
    return base64.b64encode(buf.getvalue())


def _png():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGBA", (200, 80), (0, 0, 0, 255)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue())


class _Usagers(BaseNeuve):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ref = lambda x: cls.env.ref(x).id  # noqa: E731
        # « Création de contact » : l'envoi crée le contact du signataire,
        # comme chez les vrais usagers.
        base = [ref("base.group_user"), ref("base.group_partner_manager")]

        def usager(login, groupes):
            return cls.env["res.users"].with_context(no_reset_password=True).create({
                "name": login, "login": login, "groups_id": [(6, 0, base + groupes)]})

        export = ref("base.group_allow_export")
        cls.gest_a = usager("gestionnaire-a@example.test", [ref("bf_sign.group_sign_manager"), export])
        cls.gest_b = usager("gestionnaire-b@example.test", [ref("bf_sign.group_sign_manager"), export])
        cls.preparateur = usager("preparateur@example.test", [ref("bf_sign.group_sign_user")])
        cls.employe = usager("employe@example.test", [])
        for u in (cls.gest_a, cls.gest_b, cls.preparateur, cls.employe):
            assert not u.has_group("base.group_system")

    def _demande(self, user, signataires=1):
        """Une demande en brouillon de ``user``, un pavé de signature par signataire."""
        req = self.env["bf.sign.request"].with_user(user).create({
            "document_file": _pdf(), "document_filename": "essai.pdf"})
        for i in range(signataires):
            s = self.env["bf.sign.signer"].with_user(user).create({
                "request_id": req.id, "name": "Signataire %d" % i,
                "email": "signataire%d@example.test" % i, "sequence": 10 + i})
            self.env["bf.sign.field"].with_user(user).create({
                "request_id": req.id, "signer_id": s.id, "field_type": "signature",
                "page": 1, "pos_x": 0.5, "pos_y": 0.8, "width": 0.25, "height": 0.08})
        return req


@tagged("post_install", "-at_install", "bf_sign")
class TestRevealLink(_Usagers, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Wizard = cls.env["bf.sign.reveal.link.wizard"]
        cls.req = cls.env["bf.sign.request"].create({
            "document_file": base64.b64encode(b"%PDF-1.4\n%%EOF\n"),
            "document_filename": "essai.pdf",
        })
        cls.signer = cls.env["bf.sign.signer"].create({
            "request_id": cls.req.id, "name": "Signataire", "email": "s@example.test"})

    def _reveler(self, user, signer=None):
        action = (signer or self.signer).with_user(user).action_reveal_signing_link()
        wizard = self.Wizard.with_user(user).browse(action["res_id"])
        wizard.action_confirm_reveal()
        return wizard

    def _revelations(self, req=None):
        return self.env["bf.sign.log"].search([
            ("request_id", "=", (req or self.req).id), ("event", "=", "link_revealed")])

    # ── l'assistant ───────────────────────────────────────────────────────────
    def test_auteur_obtient_le_lien_et_le_journal_le_dit(self):
        wizard = self._reveler(self.gest_a)
        self.assertEqual(wizard.read(["url"])[0]["url"], self.signer._signing_url())
        self.assertEqual(len(self._revelations()), 1)
        self.assertTrue(self.env["bf.sign.log"].search(
            [("request_id", "=", self.req.id)], order="id desc", limit=1).verify_chain())

    def test_un_autre_gestionnaire_ne_trouve_pas_l_assistant(self):
        wizard = self._reveler(self.gest_a)
        chez_b = self.Wizard.with_user(self.gest_b)
        self.assertFalse(chez_b.search([("id", "=", wizard.id)]))
        self.assertFalse(chez_b.search_read([], ["url"]))
        with self.assertRaises(AccessError):
            chez_b.browse(wizard.id).read(["url"])
        with self.assertRaises(AccessError):
            chez_b.browse(wizard.id).write({"url": False})
        # La lecture refusée n'a rien ajouté au journal, et rien ne manque.
        self.assertEqual(len(self._revelations()), 1)

    def test_un_administrateur_ne_lit_pas_non_plus_l_assistant_d_autrui(self):
        wizard = self._reveler(self.gest_a)
        admin = self.env.ref("base.user_admin")
        with self.assertRaises(AccessError):
            self.Wizard.with_user(admin).browse(wizard.id).read(["url"])

    def test_fermer_efface_le_lien(self):
        wizard = self._reveler(self.gest_a)
        action = wizard.action_close()
        self.assertEqual(action["type"], "ir.actions.act_window_close")
        self.assertFalse(wizard.read(["url"])[0]["url"])
        self.env.cr.execute(
            "select url from bf_sign_reveal_link_wizard where id = %s", [wizard.id])
        self.assertIsNone(self.env.cr.fetchone()[0])

    def test_le_bouton_fermer_de_la_vue_appelle_l_effacement(self):
        """Sans ce branchement, « Fermer » redevient un simple special=cancel."""
        arch = etree.fromstring(self.env.ref(
            "bf_sign.view_bf_sign_reveal_link_wizard_form").arch_db.encode())
        fermer = arch.xpath("//footer/button[@string='Fermer']")
        self.assertEqual(len(fermer), 1)
        self.assertEqual(fermer[0].get("name"), "action_close")
        self.assertEqual(fermer[0].get("type"), "object")
        self.assertIsNone(fermer[0].get("special"))

    def test_ouvrir_sans_confirmer_ne_revele_rien(self):
        action = self.signer.with_user(self.gest_a).action_reveal_signing_link()
        wizard = self.Wizard.with_user(self.gest_a).browse(action["res_id"])
        self.assertFalse(wizard.url)
        self.assertFalse(self._revelations())

    def test_un_preparateur_ne_revele_pas(self):
        """Même le signataire de sa propre demande, qu'il a le droit de lire."""
        sien = self._demande(self.preparateur, 1).signer_ids
        sien.with_user(self.preparateur).check_access("read")
        with self.assertRaises(UserError) as refus:
            sien.with_user(self.preparateur).action_reveal_signing_link()
        # La garde de groupe, pas l'ACL de l'assistant (AccessError hérite de UserError).
        self.assertNotIsInstance(refus.exception, AccessError)
        with self.assertRaises(AccessError):
            self.Wizard.with_user(self.preparateur).create({"signer_id": sien.id})
        self.assertFalse(self.Wizard.sudo().search([("signer_id", "=", sien.id)]))

    def test_revelation_refusee_hors_de_sa_societe(self):
        autre = self.env["res.company"].create({"name": "Société signature B"})
        req_b = self.env["bf.sign.request"].with_company(autre).create({
            "document_file": base64.b64encode(b"%PDF-1.4\n%%EOF\n"),
            "document_filename": "b.pdf", "company_id": autre.id})
        signer_b = self.env["bf.sign.signer"].create({
            "request_id": req_b.id, "name": "Signataire B", "email": "b@example.test"})
        self.assertNotIn(autre, self.gest_a.company_ids)
        with self.assertRaises(AccessError):
            signer_b.with_user(self.gest_a).action_reveal_signing_link()
        # Ni par un assistant fabriqué à la main avec ce signataire.
        wizard = self.Wizard.with_user(self.gest_a).create({"signer_id": signer_b.id})
        with self.assertRaises(AccessError):
            wizard.action_confirm_reveal()
        self.assertFalse(self._revelations(req_b))

    # ── les champs du signataire ────────────────────────────────────────────────
    def test_un_gestionnaire_ne_lit_pas_le_jeton(self):
        chez_a = self.signer.with_user(self.gest_a)
        for champ in _JETON:
            with self.assertRaises(AccessError, msg=champ):
                chez_a.read([champ])
        with self.assertRaises(AccessError):
            self.env["bf.sign.signer"].with_user(self.gest_a).search_read(
                [("id", "=", self.signer.id)], ["access_token"])
        # Ni par l'export, ni par un domaine qui deviendrait un oracle.
        with self.assertRaises(AccessError):
            chez_a.export_data(["access_token"])
        with self.assertRaises(AccessError):
            self.env["bf.sign.signer"].with_user(self.gest_a).search(
                [("access_token", "=like", "a%")])
        # Le reste de la fiche se lit toujours.
        self.assertEqual(chez_a.read(["name"])[0]["name"], "Signataire")
        self.assertNotIn("access_token", chez_a.fields_get())

    def test_un_administrateur_lit_toujours_le_jeton(self):
        admin = self.env.ref("base.user_admin")
        self.assertEqual(
            self.signer.with_user(admin).read(["access_token"])[0]["access_token"],
            self.signer.access_token)

    def test_la_revelation_marche_pour_un_gestionnaire_non_admin(self):
        """Le jeton lui est fermé, mais l'assistant le lit en sudo. Cache vidé
        d'abord : un jeton déjà en cache se lirait sans contrôle de groupe."""
        self.env.invalidate_all()
        wizard = self._reveler(self.gest_b)
        self.assertIn(self.signer.sudo().access_token, wizard.url)


@tagged("post_install", "-at_install", "bf_sign")
class TestJetonEtParcoursNonChoisis(_Usagers, TransactionCase):
    """Hors administrateur, ni le jeton ni les champs du parcours de signature
    ne se choisissent : sinon le préparateur signe à la place du destinataire."""

    _FORGE = {"access_token": "jeton-choisi", "state": "signed", "otp_verified": True,
              "signer_ip": "6.6.6.6", "consent_given": True,
              "signature_image": base64.b64encode(b"forge")}

    def _assert_vierge(self, signer):
        s = signer.sudo()
        self.assertNotEqual(s.access_token, "jeton-choisi")
        self.assertEqual(len(s.access_token or ""), 36)
        self.assertEqual(s.state, "pending")
        self.assertFalse(s.otp_verified)
        self.assertFalse(s.signer_ip)
        self.assertFalse(s.consent_given)
        self.assertFalse(s.signature_image)

    def test_creation_ignore_le_jeton_et_le_parcours_donnes(self):
        req = self._demande(self.preparateur, 0)
        for user in (self.preparateur, self.gest_a):
            s = self.env["bf.sign.signer"].with_user(user).create(dict(
                self._FORGE, request_id=req.id, name="Jean", email="jean@example.test"))
            self._assert_vierge(s)

    def test_creation_ignore_les_defauts_de_contexte(self):
        req = self._demande(self.preparateur, 0)
        ctx = {"default_" + k: v for k, v in self._FORGE.items()}
        s = self.env["bf.sign.signer"].with_user(self.preparateur).with_context(**ctx).create(
            {"request_id": req.id, "name": "Jean", "email": "jean@example.test"})
        self._assert_vierge(s)

    def test_copie_ignore_le_jeton_donne(self):
        original = self._demande(self.preparateur, 1).signer_ids
        copie = original.with_user(self.preparateur).copy(
            default={"access_token": "jeton-choisi", "state": "signed"})
        self._assert_vierge(copie)
        self.assertNotEqual(copie.sudo().access_token, original.sudo().access_token)

    def test_ecriture_du_parcours_refusee_en_brouillon(self):
        signer = self._demande(self.preparateur, 1).signer_ids
        with self.assertRaises(AccessError):
            signer.with_user(self.preparateur).write({"state": "signed"})
        self._assert_vierge(signer)

    def test_ecriture_du_parcours_refusee_apres_l_envoi(self):
        req = self._demande(self.preparateur, 2)
        req.with_user(self.preparateur).action_send()
        b = req.signer_ids[1]
        for user in (self.preparateur, self.gest_a):
            for champ, valeur in self._FORGE.items():
                with self.assertRaises(AccessError, msg="%s/%s" % (user.login, champ)):
                    b.with_user(user).write({champ: valeur})
        self.assertEqual(b.sudo().state, "pending")
        self.assertFalse(b.sudo().signature_image)

    def test_un_administrateur_ecrit_encore(self):
        """Les reprises (import de documents déjà signés) passent par un
        administrateur ; elles posent le jeton et l'état du document repris."""
        admin = self.env.ref("base.user_admin")
        req = self._demande(admin, 0)
        s = self.env["bf.sign.signer"].with_user(admin).create({
            "request_id": req.id, "name": "Repris", "email": "repris@example.test",
            "access_token": "jeton-de-reprise", "state": "signed"})
        self.assertEqual(s.access_token, "jeton-de-reprise")
        self.assertEqual(s.state, "signed")

    def _parcours(self, user):
        """Envoyer, relancer, annuler, remettre en brouillon, renvoyer."""
        req = self._demande(user, 2)
        req.with_user(user).action_send()
        self.assertEqual(req.state, "sent")
        premier_jeton = req.signer_ids[0].sudo().access_token
        req.signer_ids[0].with_user(user).action_resend_invitation()
        self.assertEqual(req.signer_ids[0].reminder_count, 1)
        req.with_user(user).action_cancel()
        req.with_user(user).action_reset_to_draft()
        self.assertEqual(req.state, "draft")
        self.assertNotEqual(req.signer_ids[0].sudo().access_token, premier_jeton)
        req.with_user(user).action_send()
        self.assertEqual(req.state, "sent")
        self.assertTrue(all(req.signer_ids.sudo().mapped("invited_on")))
        return req

    def test_parcours_d_un_preparateur(self):
        self.env.invalidate_all()
        self._parcours(self.preparateur)

    def test_parcours_d_un_gestionnaire_non_admin(self):
        self.env.invalidate_all()
        req = self._parcours(self.gest_a)
        action = req.signer_ids[0].with_user(self.gest_a).action_reveal_signing_link()
        wizard = self.env["bf.sign.reveal.link.wizard"].with_user(self.gest_a).browse(action["res_id"])
        wizard.action_confirm_reveal()
        self.assertIn(req.signer_ids[0].sudo().access_token, wizard.url)


@tagged("post_install", "-at_install", "bf_sign")
class TestPreuveNonForgee(_Usagers, TransactionCase):
    """Seconde relecture adverse : défauts personnels (ir.default), signataires et
    pavés déplacés, état et preuve de la demande, document changé après l'envoi,
    valeur d'un pavé réécrite. Hors administrateur, rien de cela ne s'écrit."""

    def _defaut(self, user, modele, champ, valeur):
        self.env["ir.default"].with_user(user).set(modele, champ, valeur, user_id=True)

    def test_ir_default_ne_fait_pas_naitre_un_signataire_signe(self):
        for champ, valeur in (("state", "signed"), ("consent_given", True),
                              ("signer_ip", "6.6.6.6"), ("otp_verified", True)):
            self._defaut(self.preparateur, "bf.sign.signer", champ, valeur)
        req = self._demande(self.preparateur, 0)
        s = self.env["bf.sign.signer"].with_user(self.preparateur).create(
            {"request_id": req.id, "name": "Jean", "email": "jean@example.test"}).sudo()
        self.assertEqual(s.state, "pending")
        self.assertFalse(s.consent_given)
        self.assertFalse(s.signer_ip)
        self.assertFalse(s.otp_verified)

    def test_ir_default_ne_fait_pas_naitre_une_demande_signee(self):
        self._defaut(self.preparateur, "bf.sign.request", "state", "signed")
        self._defaut(self.preparateur, "bf.sign.request", "verify_token", "choisi")
        req = self._demande(self.preparateur, 0).sudo()
        self.assertEqual(req.state, "draft")
        self.assertFalse(req.verify_token)

    def test_ir_default_ne_remplit_pas_un_pave(self):
        self._defaut(self.preparateur, "bf.sign.field", "filled_value", "forgé")
        req = self._demande(self.preparateur, 1)
        self.assertFalse(req.field_ids.sudo().filled_value)

    def test_creation_d_une_demande_ignore_l_etat_et_la_preuve(self):
        req = self.env["bf.sign.request"].with_user(self.preparateur).create({
            "document_file": base64.b64encode(b"%PDF-1.4\n%%EOF\n"), "document_filename": "x.pdf",
            "state": "signed", "verify_token": "choisi", "hash_signed": "0" * 64,
            "sealed": True}).sudo()
        self.assertEqual((req.state, req.verify_token, req.hash_signed, req.sealed),
                         ("draft", False, False, False))

    def test_etat_et_preuve_de_la_demande_refuses(self):
        req = self._demande(self.preparateur, 1)
        req.with_user(self.preparateur).action_send()
        for user in (self.preparateur, self.gest_a):
            for vals in ({"state": "signed"}, {"state": "draft"}, {"hash_signed": "0" * 64},
                         {"verify_token": "choisi"}, {"signed_on": "2026-01-01 00:00:00"},
                         {"sealed": True}, {"hash_original": "0" * 64},
                         {"tsa_token": _png()}, {"signed_attachment_id": False},
                         {"certificate_attachment_id": False}):
                with self.assertRaises(AccessError, msg="%s %s" % (user.login, vals)):
                    req.with_user(user).write(vals)
        self.assertEqual(req.sudo().state, "sent")
        self.assertFalse(req.sudo().verify_token)

    def test_document_et_conditions_figes_des_l_envoi(self):
        req = self._demande(self.preparateur, 1)
        # En brouillon, tout se règle encore.
        req.with_user(self.preparateur).write({"consent_text": "En brouillon", "require_signer_otp": True})
        req.with_user(self.preparateur).action_send()
        for vals in ({"document_file": _pdf()}, {"consent_text": "Changé"},
                     {"require_signer_otp": False}, {"signing_order": "sequential"},
                     {"verify_qr": True}, {"verify_qr_position": "tl"},
                     {"verify_qr_pages": "all"}, {"append_certificate": False}):
            with self.assertRaises(UserError, msg=str(vals)):
                req.with_user(self.preparateur).write(vals)
        # Ce qui n'engage pas le signataire se règle toujours.
        req.with_user(self.preparateur).write({"title": "Titre retouché"})

    def test_valeur_d_un_pave_refusee(self):
        req = self._demande(self.preparateur, 1)
        pave = req.field_ids
        with self.assertRaises(AccessError):
            pave.with_user(self.preparateur).write({"filled_value": "en brouillon"})
        req.with_user(self.preparateur).action_send()
        for user in (self.preparateur, self.gest_a):
            with self.assertRaises(AccessError):
                pave.with_user(user).write({"filled_value": "forgé"})
        self.assertFalse(pave.sudo().filled_value)

    def test_pave_cree_sans_valeur(self):
        req = self._demande(self.preparateur, 1)
        pave = self.env["bf.sign.field"].with_user(self.preparateur).create({
            "request_id": req.id, "signer_id": req.signer_ids.id, "field_type": "text",
            "filled_value": "forgé", "page": 1, "pos_x": 0.1, "pos_y": 0.1,
            "width": 0.2, "height": 0.05})
        self.assertFalse(pave.sudo().filled_value)

    def test_signataire_et_pave_ne_changent_pas_de_demande(self):
        autre = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "autre", "login": "autre-preparateur@example.test",
            "groups_id": [(6, 0, self.preparateur.groups_id.ids)]})
        envoyee_d_autrui = self._demande(autre, 1)
        envoyee_d_autrui.with_user(autre).action_send()
        brouillon_d_autrui = self._demande(autre, 0)
        mienne = self._demande(self.preparateur, 1)
        autre_brouillon_a_moi = self._demande(self.preparateur, 0)
        envoyee_a_moi = self._demande(self.preparateur, 1)
        envoyee_a_moi.with_user(self.preparateur).action_send()
        signer, pave = mienne.signer_ids, mienne.field_ids
        for cible in (envoyee_d_autrui, brouillon_d_autrui, envoyee_a_moi, autre_brouillon_a_moi):
            with self.assertRaises(AccessError, msg=cible.id):
                signer.with_user(self.preparateur).write({"request_id": cible.id})
            with self.assertRaises(AccessError, msg=cible.id):
                pave.with_user(self.preparateur).write({"request_id": cible.id})
        self.assertEqual(signer.request_id, mienne)
        self.assertEqual(pave.request_id, mienne)
        # Réécrire la même demande n'est pas un déplacement.
        signer.with_user(self.preparateur).write({"request_id": mienne.id, "name": "Renommé"})

    def test_ajout_a_une_demande_envoyee_par_le_contexte_refuse(self):
        req = self._demande(self.preparateur, 1)
        req.with_user(self.preparateur).action_send()
        ctx = {"default_request_id": req.id}
        with self.assertRaises(UserError) as refus:
            self.env["bf.sign.signer"].with_user(self.preparateur).with_context(**ctx).create(
                {"name": "Ajouté", "email": "ajoute@example.test"})
        self.assertNotIsInstance(refus.exception, (AccessError, ValidationError))
        with self.assertRaises(UserError) as refus:
            self.env["bf.sign.field"].with_user(self.preparateur).with_context(**ctx).create({
                "signer_id": req.signer_ids.id, "field_type": "text", "fill_mode": "fixed",
                "value_text": "Montant : 50 000 $", "page": 1, "pos_x": 0.1, "pos_y": 0.1,
                "width": 0.3, "height": 0.05})
        self.assertNotIsInstance(refus.exception, (AccessError, ValidationError))
        self.assertEqual(len(req.sudo().field_ids), 1)

    def _piece(self, modele, res_id, champ):
        return self.env["ir.attachment"].sudo().search([
            ("res_model", "=", modele), ("res_id", "=", res_id), ("res_field", "=", champ)])

    def test_pieces_de_preuve_par_ir_attachment(self):
        Att = self.env["ir.attachment"].with_user(self.preparateur)
        req = self._demande(self.preparateur, 1)
        # En brouillon, le document se remplace encore, y compris par sa pièce.
        self._piece("bf.sign.request", req.id, "document_file").with_user(
            self.preparateur).write({"datas": _pdf()})
        req.with_user(self.preparateur).action_send()
        doc = self._piece("bf.sign.request", req.id, "document_file").with_user(self.preparateur)
        with self.assertRaises(AccessError):
            doc.write({"datas": _pdf()})
        with self.assertRaises(AccessError):
            doc.unlink()
        signer = req.signer_ids
        signer.sudo().signature_image = _png()
        image = self._piece("bf.sign.signer", signer.id, "signature_image").with_user(self.preparateur)
        self.assertTrue(image)
        with self.assertRaises(AccessError):
            image.write({"datas": _png()})
        with self.assertRaises(AccessError):
            image.unlink()
        for modele, res_id, champ in (("bf.sign.signer", signer.id, "initials_image"),
                                      ("bf.sign.request", req.id, "tsa_token")):
            with self.assertRaises(AccessError, msg=champ):
                Att.create({"name": champ, "res_model": modele, "res_id": res_id,
                            "res_field": champ, "datas": _png()})
        # Le PDF signé et le certificat, pièces ordinaires de la demande.
        scelle = self.env["ir.attachment"].create({
            "name": "scellé.pdf", "res_model": "bf.sign.request", "res_id": req.id, "datas": _pdf()})
        req.sudo().signed_attachment_id = scelle
        with self.assertRaises(AccessError):
            scelle.with_user(self.preparateur).write({"datas": _pdf()})
        with self.assertRaises(AccessError):
            scelle.with_user(self.preparateur).unlink()
        # Détacher la pièce du document envoyé, ou la vider de son champ.
        with self.assertRaises(AccessError):
            doc.write({"res_field": False})
        with self.assertRaises(AccessError):
            doc.write({"res_id": self._demande(self.preparateur, 0).id})
        # Une pièce ordinaire jointe à la demande se gère toujours.
        note = Att.create({"name": "note.txt", "res_model": "bf.sign.request", "res_id": req.id,
                           "datas": base64.b64encode(b"note")})
        note.unlink()

    def test_pieces_de_preuve_par_les_chemins_de_cote(self):
        """Défauts de contexte, res_id en chaîne, pièce du composeur rattachée."""
        req = self._demande(self.preparateur, 1)
        req.with_user(self.preparateur).action_send()
        ctx = {"default_res_model": "bf.sign.request", "default_res_id": req.id,
               "default_res_field": "tsa_token"}
        # Le défaut de res_field est ignoré : la pièce naît sans champ, donc sans effet.
        for user in (self.preparateur, self.employe):
            piece = self.env["ir.attachment"].with_user(user).with_context(**ctx).create(
                {"name": "jeton", "datas": _png()})
            self.assertFalse(piece.sudo().res_field, user.login)
        vieille = self.env["ir.attachment"].with_user(self.preparateur).create(
            {"name": "vieille.txt", "datas": base64.b64encode(b"x")})
        for champ in ("tsa_token", "document_file"):
            with self.assertRaises(AccessError, msg=champ):
                vieille.write({"res_model": "bf.sign.request", "res_id": str(req.id),
                               "res_field": champ, "datas": _pdf()})
        # Le document du BROUILLON d'un autre : le res_id en chaîne fait sauter le
        # contrôle d'Odoo (exists() vide), la garde relit et contrôle l'écriture.
        autre = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "autre", "login": "autre-c@example.test",
            "groups_id": [(6, 0, self.preparateur.groups_id.ids)]})
        brouillon_d_autrui = self._demande(autre, 0)
        with self.assertRaises(AccessError):
            vieille.write({"res_model": "bf.sign.request", "res_id": str(brouillon_d_autrui.id),
                           "res_field": "document_file", "datas": _pdf()})
        with self.assertRaises(AccessError):
            self.env["ir.attachment"].with_user(self.preparateur).create({
                "name": "composeur.pdf", "res_model": "mail.compose.message", "res_id": 0,
                "res_field": "document_file", "datas": _pdf()})
        # À l'écriture aussi : une pièce du composeur ne prend pas un champ qu'il n'a pas.
        en_attente = self.env["ir.attachment"].with_user(self.preparateur).create({
            "name": "attente.pdf", "res_model": "mail.compose.message", "res_id": 0, "datas": _pdf()})
        with self.assertRaises(AccessError):
            en_attente.write({"res_field": "document_file"})
        self.assertFalse(self.env["ir.attachment"].sudo().search([
            ("res_model", "=", "bf.sign.request"), ("res_id", "=", req.id),
            ("res_field", "=", "tsa_token")]))
        self.assertTrue(req.sudo()._document_intact())

    def test_pieces_creees_en_sudo_sans_res_field_herite(self):
        """message_post crée ses pièces en sudo avec les défauts de l'appelant."""
        self.preparateur.email = "preparateur@example.test"
        req = self._demande(self.preparateur, 1)
        req.with_user(self.preparateur).action_send()
        chez_lui = req.with_user(self.preparateur)
        chez_lui.with_context(default_res_field="tsa_token").message_post(
            body="contexte", attachments=[("jeton1.bin", b"faux jeton")])
        self.env["ir.default"].with_user(self.preparateur).set(
            "ir.attachment", "res_field", "tsa_token", user_id=True)
        chez_lui.message_post(body="ir.default", attachments=[("jeton2.bin", b"faux jeton")])
        pieces = self.env["ir.attachment"].sudo().search([
            ("res_model", "=", "bf.sign.request"), ("res_id", "=", req.id),
            ("name", "in", ("jeton1.bin", "jeton2.bin"))])
        self.assertEqual(len(pieces), 2)
        self.assertFalse(any(pieces.mapped("res_field")))
        self.assertFalse(self._piece("bf.sign.request", req.id, "tsa_token"))

    def test_scelle_protege_meme_avec_un_res_field(self):
        req = self._demande(self.preparateur, 1)
        scelle = self.env["ir.attachment"].create({
            "name": "scellé.pdf", "res_model": "bf.sign.request", "res_id": req.id,
            "res_field": "consent_text", "datas": _pdf()})
        req.sudo().signed_attachment_id = scelle
        with self.assertRaises(AccessError):
            scelle.with_user(self.preparateur).unlink()

    def test_edition_d_un_message_ne_supprime_pas_une_piece_de_preuve(self):
        """Un interne lie n'importe quelle pièce par son id ; éditer le message
        la supprimait en sudo (_delete_and_notify)."""
        self.preparateur.email = "preparateur@example.test"
        req = self._demande(self.preparateur, 1)
        scelle = self.env["ir.attachment"].create({
            "name": "scellé.pdf", "res_model": "bf.sign.request", "res_id": req.id, "datas": _pdf()})
        req.sudo().signed_attachment_id = scelle
        # Comme le chatter de l'interface : message_post en sudo, sous l'uid de l'usager.
        fil = req.with_user(self.preparateur).sudo()
        msg = fil.message_post(body="pièce", attachment_ids=scelle.ids, message_type="comment")
        self.assertIn(scelle, msg.attachment_ids)
        with self.assertRaises(AccessError):
            fil._message_update_content(msg, "édité", attachment_ids=[])
        with self.assertRaises(AccessError):
            scelle.with_user(self.preparateur).sudo()._delete_and_notify()
        self.assertTrue(scelle.exists())

    def test_rattachement_en_sudo_refuse(self):
        """D'autres modules rattachent en sudo des pièces que l'usager désigne
        (réacheminement d'un courriel, signalement d'hameçonnage)."""
        req = self._demande(self.preparateur, 1)
        req.with_user(self.preparateur).action_send()
        contact = self.env["res.partner"].create({"name": "Ailleurs"})
        scelle = self.env["ir.attachment"].create({
            "name": "scellé.pdf", "res_model": "bf.sign.request", "res_id": req.id, "datas": _pdf()})
        req.sudo().signed_attachment_id = scelle
        certificat = self.env["ir.attachment"].create({
            "name": "certificat.pdf", "res_model": "bf.sign.request", "res_id": req.id, "datas": _pdf()})
        req.sudo().certificate_attachment_id = certificat
        signer = req.signer_ids
        signer.sudo().signature_image = _png()
        image = self._piece("bf.sign.signer", signer.id, "signature_image")
        for piece in (scelle, certificat, image, self._piece("bf.sign.request", req.id, "document_file")):
            with self.assertRaises(AccessError, msg=piece.name):
                piece.with_user(self.preparateur).sudo().write(
                    {"res_model": "res.partner", "res_id": contact.id})
            with self.assertRaises(AccessError, msg=piece.name + " (res_id seul)"):
                piece.with_user(self.preparateur).sudo().write({"res_id": contact.id})
        # Où la pièce arrive : un « document_file » venu d'un modèle de pavés, déposé
        # en sudo (comme le réacheminement d'un courriel) dans une demande envoyée.
        modele = self.env["bf.sign.field.template"].with_user(self.preparateur).create(
            {"name": "Modèle du préparateur"})
        depot = self.env["ir.attachment"].with_user(self.preparateur).create({
            "name": "autre.pdf", "res_model": "bf.sign.field.template", "res_id": modele.id,
            "res_field": "document_file", "datas": _pdf()})
        with self.assertRaises(AccessError):
            depot.with_user(self.preparateur).sudo().write(
                {"res_model": "bf.sign.request", "res_id": req.id})
        self.assertTrue(req.sudo()._document_intact())
        # Ni dans le brouillon d'un autre, même en sudo.
        autre = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "autre", "login": "autre-d@example.test",
            "groups_id": [(6, 0, self.preparateur.groups_id.ids)]})
        with self.assertRaises(AccessError):
            depot.with_user(self.preparateur).sudo().write(
                {"res_model": "bf.sign.request", "res_id": self._demande(autre, 0).id})
        # Le même dépôt dans son propre brouillon passe.
        brouillon = self._demande(self.preparateur, 0)
        depot.with_user(self.preparateur).sudo().write(
            {"res_model": "bf.sign.request", "res_id": brouillon.id})
        # Déplacé par un administrateur, le scellé reste reconnu par son id.
        scelle.write({"res_model": "res.partner", "res_id": contact.id})
        with self.assertRaises(AccessError):
            scelle.with_user(self.preparateur).sudo()._delete_and_notify()
        # Le chemin réel, quand le module de sensibilisation est installé (au banc
        # et en production ; la CI du dépôt public ne l'installe pas avec bf_sign).
        sensibilisation = self.env["ir.module.module"].sudo().search(
            [("name", "=", "bf_security_awareness"), ("state", "=", "installed")])
        self.assertEqual(bool(sensibilisation), "bf.report.phish.wizard" in self.env)
        if sensibilisation:
            assistant = self.env["bf.report.phish.wizard"].with_user(self.employe)
            vals = {"attachment_ids": [(6, 0, image.ids)]}
            categorie = assistant._fields.get("category")
            if categorie is not None:
                choix = categorie.selection
                vals["category"] = (choix(assistant) if callable(choix) else choix)[0][0]
            with self.assertRaises(AccessError):
                assistant.create(vals).action_submit()
        self.assertEqual((image.res_model, image.res_id), ("bf.sign.signer", signer.id))

    def test_cas_permis_de_la_garde_des_pieces(self):
        """La relecture laisse passer ce qui est légitime."""
        Att = self.env["ir.attachment"].with_user(self.preparateur)
        # Son propre brouillon, même avec un res_id en chaîne.
        mien = self._demande(self.preparateur, 0)
        piece = Att.create({"name": "v.pdf", "datas": _pdf()})
        piece.write({"res_model": "bf.sign.request", "res_id": str(mien.id),
                     "res_field": "document_file"})
        self.assertEqual(piece.sudo().res_id, mien.id)
        # Un champ homonyme d'un autre modèle n'est pas une pièce de preuve.
        modele = self.env["bf.sign.field.template"].with_user(self.preparateur).create(
            {"name": "Modèle du préparateur"})
        Att.create({"name": "modele.pdf", "res_model": "bf.sign.field.template",
                    "res_id": modele.id, "res_field": "document_file", "datas": _pdf()})

    def test_pieces_ordinaires_inchangees(self):
        """La garde ne touche que les pièces de preuve : le reste du locataire passe."""
        contact = self.env["res.partner"].create({"name": "Contact B"})
        self.employe.email = "employe@example.test"  # message_post exige une adresse d'auteur
        Att = self.env["ir.attachment"].with_user(self.employe)
        fiche = Att.create({"name": "fiche.txt", "res_model": "res.partner",
                            "res_id": contact.id, "datas": base64.b64encode(b"a")})
        fiche.write({"datas": base64.b64encode(b"b"), "name": "fiche2.txt"})
        en_attente = Att.create({"name": "jointe.txt", "res_model": "mail.compose.message",
                                 "res_id": 0, "datas": base64.b64encode(b"c")})
        contact.with_user(self.employe).message_post(body="note", attachment_ids=en_attente.ids)
        self.assertEqual((en_attente.sudo().res_model, en_attente.sudo().res_id),
                         ("res.partner", contact.id))
        en_attente.with_user(self.employe).sudo()._delete_and_notify()
        fiche.unlink()

    def _signer_comme_le_controleur(self, req, signer):
        req.sudo().register_signer_signature(
            signer.sudo(), _png().decode(), None, True, ip="1.2.3.4", user_agent="essai")

    def test_signature_refusee_si_le_document_a_change(self):
        req = self._demande(self.preparateur, 2)
        req.with_user(self.preparateur).write({"require_signer_otp": False})
        req.with_user(self.preparateur).action_send()
        premier, second = req.signer_ids
        self._signer_comme_le_controleur(req, premier)
        self.assertEqual(premier.state, "signed")
        # Un administrateur passe les gardes : la signature doit quand même refuser.
        req.sudo().write({"document_file": _pdf()})
        with self.assertRaisesRegex(UserError, "Le document a changé depuis son envoi"):
            self._signer_comme_le_controleur(req, second)
        self.assertEqual(second.state, "pending")
        self.assertFalse(second.sudo().signature_image)
        # Un renvoi ne recalcule pas l'empreinte pour maquiller le changement.
        req.with_user(self.preparateur).action_send()
        with self.assertRaisesRegex(UserError, "Le document a changé depuis son envoi"):
            self._signer_comme_le_controleur(req, second)

    def test_parcours_sudo_refuse_sur_la_demande_d_autrui(self):
        autre = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "autre", "login": "autre-b@example.test",
            "groups_id": [(6, 0, self.preparateur.groups_id.ids)]})
        req = self._demande(self.preparateur, 1)
        for action in ("action_send", "action_cancel", "action_reset_to_draft"):
            with self.assertRaises(AccessError, msg=action):
                getattr(req.with_user(autre), action)()
        self.assertEqual(req.sudo().state, "draft")

    def test_un_administrateur_garde_la_main(self):
        """Réparations et reprises passent par un administrateur."""
        admin = self.env.ref("base.user_admin")
        req = self._demande(self.preparateur, 1)
        req.with_user(self.preparateur).action_send()
        req.with_user(admin).write({"consent_text": "Corrigé par l'administrateur"})
        req.field_ids.with_user(admin).write({"filled_value": "reprise"})
        req.with_user(admin).write({"state": "cancelled"})
        self.assertEqual(req.state, "cancelled")
