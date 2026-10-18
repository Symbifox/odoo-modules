"""Images distantes : préférence par usager et expéditeurs autorisés.

Règles : préférence PAR USAGER (l'instance reste le
défaut de qui n'a rien choisi), liste par expéditeur ET par domaine (domaine de
webmail public refusé), liste ignorée quand notre MX a noté `dmarc=fail`, et
les trois surfaces (formulaire, boîte, téléphone) plus le navigateur IMAP.
"""
import re

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .common import MobileApiCase

LIVE_REMOTE_SRC = r'[\s"\']src\s*=\s*["\']https?://'


@tagged("post_install", "-at_install")
class TestImagesAutorisees(MobileApiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Sender = cls.env["bf.email.image.sender"]
        # Un reçu ordinaire porte ses en-têtes, et notre MX y a écrit son
        # verdict : sans eux, la liste ne joue pas.
        cls.with_attachment.sudo().raw_headers = (
            "Authentication-Results: mx.ours.test; dmarc=pass "
            "header.from=fournisseur.test\n"
            "From: rapports@fournisseur.test\n")

    def _preview(self, rec=None, user=None, load_images=False):
        return self.env["bf.email"].with_user(user or self.owner).inbox_get_body(
            (rec or self.with_attachment).id, load_images=load_images)

    def _assert_parked(self, data):
        self.assertIsNone(re.search(LIVE_REMOTE_SRC, data["body_html"]),
                          "une source distante est restée active")
        self.assertGreaterEqual(data["blocked_images"], 1)

    def _assert_shown(self, data, reason):
        self.assertNotIn("data-blocked-src", data["body_html"])
        self.assertEqual(data["blocked_images"], 0)
        self.assertEqual(data["images_reason"], reason)

    def _allow(self, kind, rec=None, user=None):
        return self.env["bf.email"].with_user(user or self.owner) \
            .inbox_allow_remote_images((rec or self.with_attachment).id, kind)

    def _set_dmarc(self, rec, verdict):
        rec.sudo().raw_headers = (
            "Authentication-Results: mx.ours.test; dkim=pass; spf=pass; "
            "dmarc=%s header.from=fournisseur.test\n"
            "From: rapports@fournisseur.test\n" % verdict)

    # ------------------------------------------------------------------
    # Préférence par usager
    # ------------------------------------------------------------------
    def test_par_defaut_rien_ne_change(self):
        data = self._preview()
        self._assert_parked(data)
        self.assertEqual(data["images_reason"], "")
        self.assertEqual(data["images_sender"], "rapports@fournisseur.test")
        self.assertEqual(data["images_domain"], "fournisseur.test")
        self.assertTrue(data["images_domain_allowed"])

    def test_toujours_afficher_vaut_pour_moi_seul(self):
        self.owner.with_user(self.owner).write(
            {"bf_email_remote_images": "always"})
        self._assert_shown(self._preview(), "user")
        # L'instance bloque toujours pour les autres : la préférence de l'un
        # ne se lit pas chez l'autre.
        self.assertEqual(self.stranger.bf_email_remote_images, "instance")

    def test_toujours_bloquer_l_emporte_sur_l_instance_et_la_liste(self):
        self._allow("sender")
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_email.block_remote_images", "0")
        self.owner.with_user(self.owner).write(
            {"bf_email_remote_images": "block"})
        data = self._preview()
        self._assert_parked(data)
        # Rien à offrir : « Toujours pour… » ne changerait rien à l'écran.
        self.assertEqual(data["images_sender"], "")
        self.assertFalse(data["images_domain_allowed"])

    def test_selon_l_instance_suit_le_reglage(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_email.block_remote_images", "0")
        self._assert_shown(self._preview(), "instance")

    def test_la_preference_ne_s_ecrit_pas_chez_un_autre(self):
        with self.assertRaises(AccessError):
            self.stranger.with_user(self.owner).write(
                {"bf_email_remote_images": "always"})

    # ------------------------------------------------------------------
    # Liste d'autorisation
    # ------------------------------------------------------------------
    def test_autoriser_l_expediteur(self):
        self._allow("sender")
        row = self.Sender.sudo().search([("user_id", "=", self.owner.id)])
        self.assertEqual((row.kind, row.value),
                         ("sender", "rapports@fournisseur.test"))
        self._assert_shown(self._preview(), "sender")

    def test_un_domaine_sans_point_est_refuse(self):
        """« test » seul couvrirait tout « *.test » : un suffixe, pas un domaine."""
        with self.assertRaises(ValidationError):
            self.Sender.with_user(self.owner).create(
                {"kind": "domain", "value": "@Test"})

    def test_domaine_et_sous_domaine(self):
        self.Sender.with_user(self.owner).create(
            {"kind": "domain", "value": "FOURNISSEUR.test."})
        self._assert_shown(self._preview(), "domain")
        self.with_attachment.sudo().email_from = (
            "Alertes <alertes@mail.fournisseur.test>")
        self.with_attachment.invalidate_recordset()
        self._assert_shown(self._preview(), "domain")

    def test_un_domaine_voisin_ne_passe_pas(self):
        """« fournisseur.test » ne couvre pas « faux-fournisseur.test »."""
        self.Sender.with_user(self.owner).create(
            {"kind": "domain", "value": "fournisseur.test"})
        self.with_attachment.sudo().email_from = "x@faux-fournisseur.test"
        self.with_attachment.invalidate_recordset()
        self._assert_parked(self._preview())

    def test_l_adresse_exacte_seulement(self):
        self._allow("sender")
        self.with_attachment.sudo().email_from = "autre@fournisseur.test"
        self.with_attachment.invalidate_recordset()
        self._assert_parked(self._preview())

    def test_webmail_public_refuse_au_domaine(self):
        with self.assertRaises(ValidationError):
            self.Sender.with_user(self.owner).create(
                {"kind": "domain", "value": "gmail.com"})
        with self.assertRaises(ValidationError):
            self.Sender.with_user(self.owner).create(
                {"kind": "domain", "value": "eu.hotmail.com"})
        self.with_attachment.sudo().email_from = "Quelqu'un <a.b@gmail.com>"
        self.with_attachment.invalidate_recordset()
        data = self._preview()
        self.assertFalse(data["images_domain_allowed"])
        # L'adresse, elle, s'autorise.
        self._allow("sender")
        self._assert_shown(self._preview(), "sender")

    def test_la_liste_est_personnelle(self):
        self._allow("sender")
        other = self.with_attachment.sudo().copy({
            "user_id": self.stranger.id, "account_id": False,
            "imap_uid": "999", "message_id_header": "<copie@test.invalid>"})
        self._assert_parked(self._preview(other, user=self.stranger))
        self.assertFalse(self.Sender.with_user(self.stranger).search([]))

    def test_on_ne_donne_pas_sa_liste_a_un_autre(self):
        self._allow("sender")
        row = self.Sender.sudo().search([("user_id", "=", self.owner.id)])
        with self.assertRaises(AccessError):
            self.Sender.with_user(self.owner).browse(row.id).write(
                {"user_id": self.stranger.id})
        with self.assertRaises(AccessError):
            self.Sender.with_user(self.owner).create({
                "kind": "sender", "value": "x@y.test",
                "user_id": self.stranger.id})

    def test_autoriser_deux_fois_ne_double_pas(self):
        self._allow("sender")
        self._allow("sender")
        self.assertEqual(self.Sender.sudo().search_count(
            [("user_id", "=", self.owner.id)]), 1)

    def test_on_n_autorise_que_ce_qu_on_peut_lire(self):
        with self.assertRaises(AccessError):
            self._allow("sender", rec=self.foreign)

    def test_ne_plus_afficher_retire_adresse_et_domaine(self):
        self._allow("sender")
        self._allow("domain")
        self.env["bf.email"].with_user(self.owner) \
            .inbox_forget_remote_images(self.with_attachment.id)
        self.assertFalse(self.Sender.sudo().search(
            [("user_id", "=", self.owner.id)]))
        self._assert_parked(self._preview())

    # ------------------------------------------------------------------
    # Usurpation
    # ------------------------------------------------------------------
    def test_dmarc_fail_annule_la_liste(self):
        self._allow("sender")
        self._set_dmarc(self.with_attachment, "fail")
        data = self._preview()
        self._assert_parked(data)
        self.assertEqual(data["images_refused"], "dmarc")
        # Rien à offrir : l'autoriser de nouveau ne changerait rien.
        self.assertTrue(data["images_sender"])

    def test_dmarc_pass_et_en_tetes_sans_verdict_passent(self):
        """Nos propres envois ont des en-têtes mais pas d'Authentication-Results."""
        self._allow("sender")
        self._set_dmarc(self.with_attachment, "pass")
        self._assert_shown(self._preview(), "sender")
        self.with_attachment.sudo().raw_headers = (
            "Received: from mail.ours.test\nFrom: rapports@fournisseur.test\n")
        self._assert_shown(self._preview(), "sender")

    def test_sans_en_tetes_la_liste_ne_joue_pas(self):
        """Passerelle sans original, vieux import : rien ne dit d'où il vient."""
        self._allow("domain")
        for empty in (False, "", "   \n"):
            self.with_attachment.sudo().raw_headers = empty
            data = self._preview()
            self._assert_parked(data)
            self.assertEqual(data["images_refused"], "headers")
        # Le geste « cette fois » reste offert.
        data = self._preview(load_images=True)
        self._assert_shown(data, "once")

    def test_un_from_ambigu_ne_fait_pas_jouer_la_liste(self):
        """`email_normalize` lit `a@b@c` comme `a@b` : la décision, non."""
        self._allow("domain")
        for forged in ("rapports@fournisseur.test@evil.test",
                       "rapports@fournisseur.test <x@evil.test>",
                       "rapports@fournisseur.test, x@evil.test"):
            self.with_attachment.sudo().email_from = forged
            self.with_attachment.invalidate_recordset()
            data = self._preview()
            self._assert_parked(data)
            self.assertEqual(data["images_sender"], "")
            self.assertFalse(data["images_domain_allowed"])
            with self.assertRaises(UserError):
                self._allow("sender")
        # Un nom affiché qui contient une adresse ne trompe pas : c'est
        # l'adresse réelle qui compte.
        self.with_attachment.sudo().email_from = (
            '"rapports@fournisseur.test" <x@evil.test>')
        self.with_attachment.invalidate_recordset()
        data = self._preview()
        self._assert_parked(data)
        self.assertEqual(data["images_sender"], "x@evil.test")

    def test_un_suffixe_public_ne_s_autorise_pas(self):
        for suffix in ("co.nz", "com.au", "org.uk", "qc.ca", "govt.nz"):
            with self.assertRaises(ValidationError):
                self.Sender.with_user(self.owner).create(
                    {"kind": "domain", "value": suffix})
        # Un vrai domaine à deux lettres, lui, passe.
        self.Sender.with_user(self.owner).create(
            {"kind": "domain", "value": "bf.ca"})

    def test_seul_le_premier_verdict_compte(self):
        """Un `dmarc=pass` posé PLUS BAS par l'expéditeur ne couvre pas notre
        `dmarc=fail` ; un `dmarc=fail` voyageur ne condamne pas notre `pass`."""
        self._allow("sender")
        self.with_attachment.sudo().raw_headers = (
            "Authentication-Results: mx.ours.test; dmarc=fail\n"
            "Authentication-Results: forge.test; dmarc=pass\n")
        self._assert_parked(self._preview())
        self.with_attachment.sudo().raw_headers = (
            "Authentication-Results: mx.ours.test; dmarc=pass\n"
            "Authentication-Results: forge.test; dmarc=fail\n")
        self._assert_shown(self._preview(), "sender")

    def test_preference_toujours_ignore_dmarc(self):
        """« Toujours afficher » est un choix de l'usager, pas de la liste."""
        self._set_dmarc(self.with_attachment, "fail")
        self.owner.with_user(self.owner).write(
            {"bf_email_remote_images": "always"})
        self._assert_shown(self._preview(), "user")

    # ------------------------------------------------------------------
    # Images CSS
    # ------------------------------------------------------------------
    def test_les_fonds_css_distants_sont_parques_aussi(self):
        self.with_attachment.sudo().body_html = (
            '<table background="https://pisteur.test/fond.png">'
            '<tr><td style="background-image:url(\'https://pisteur.test/p.gif\')">'
            'x</td></tr></table>'
            '<div style="background: #fff url(https://pisteur.test/q.gif) no-repeat">'
            'y</div><div style="background:url(cid:locale)">z</div>')
        self.with_attachment.invalidate_recordset()
        data = self._preview()
        body = data["body_html"]
        # Plus aucune référence VIVANTE : il ne reste le pisteur que dans les
        # attributs `data-blocked-…`, qu'aucun navigateur ne charge.
        self.assertIsNone(re.search(r"url\(\s*['\"]?\s*https?://", body))
        self.assertIsNone(re.search(r'[\s"\']background\s*=\s*["\']https?://', body))
        self.assertIn('data-blocked-background="https://pisteur.test/fond.png"', body)
        self.assertEqual(data["blocked_images"], 3)
        self.assertIn("cid:locale", data["body_html"])
        self._allow("sender")
        data = self._preview()
        self.assertIn("url('https://pisteur.test/p.gif')", data["body_html"])

    # ------------------------------------------------------------------
    # Les autres surfaces
    # ------------------------------------------------------------------
    def test_le_formulaire_suit_la_liste(self):
        self._allow("sender")
        rec = self.with_attachment.with_user(self.owner)
        self.assertNotIn("data-blocked-src", rec.body_html_reading or "")
        self.assertEqual(rec.remote_images_reason, "sender")
        self.assertGreaterEqual(rec.remote_image_count, 1)

    def test_les_boutons_du_formulaire(self):
        rec = self.with_attachment.with_user(self.owner)
        action = rec.action_allow_remote_images_domain()
        self.assertEqual(action["res_id"], rec.id)
        self.assertNotIn("bf_load_images", action.get("context") or {})
        self.assertTrue(self.Sender.sudo().search(
            [("user_id", "=", self.owner.id), ("kind", "=", "domain")]))
        rec.action_forget_remote_images()
        self.assertFalse(self.Sender.sudo().search(
            [("user_id", "=", self.owner.id)]))

    def test_le_telephone_suit_la_liste(self):
        data = self.as_owner().get_mobile_message(self.with_attachment.id)
        self.assertEqual(data["images_reason"], "")
        self._allow("sender")
        data = self.as_owner().get_mobile_message(self.with_attachment.id)
        self.assertNotIn("data-blocked-src", data["body_html"])
        self.assertEqual(data["images_reason"], "sender")

    def test_le_telephone_autorise_et_oublie(self):
        data = self.as_owner().mobile_allow_remote_images(
            self.with_attachment.id, "domain")
        self.assertEqual(data["images_reason"], "domain")
        self.assertNotIn("data-blocked-src", data["body_html"])
        data = self.as_owner().mobile_forget_remote_images(
            self.with_attachment.id)
        self.assertEqual(data["images_reason"], "")
        self.assertIn("data-blocked-src", data["body_html"])

    def test_le_telephone_ne_touche_pas_au_courriel_d_un_autre(self):
        with self.assertRaises(UserError):
            self.as_owner().mobile_allow_remote_images(self.foreign.id, "sender")
        self.assertFalse(self.Sender.sudo().search(
            [("user_id", "=", self.owner.id)]))

    def test_meme_corps_partout(self):
        self._allow("sender")
        rec = self.with_attachment.with_user(self.owner)
        mobile = self.as_owner().get_mobile_message(rec.id)["body_html"]
        poste = self._preview()["body_html"]
        self.assertEqual(mobile, poste)
        self.assertEqual(poste, rec.body_html_reading)


@tagged("post_install", "-at_install")
class TestParcageParLArbre(MobileApiCase):
    """Le parcage parcourt l'arbre HTML.

    Chaque forme ci-dessous a été CHARGÉE par Chromium chez un pisteur, après
    l'assainisseur d'Odoo, avec un parcage par expressions régulières. On la
    passe ici par le même assainisseur.
    """

    def _park(self, html):
        from odoo.tools import html_sanitize
        from ..models.bf_email_images import park_remote_images
        return park_remote_images(html_sanitize(html))

    def _no_live_remote(self, html):
        from lxml import html as lxml_html
        from ..models.bf_email_images import css_is_remote, is_remote_url
        root = lxml_html.fragment_fromstring(html or "<p></p>", create_parent="div")
        for el in root.iter():
            if not isinstance(el.tag, str):
                continue
            for attr, value in el.attrib.items():
                if attr.startswith("data-blocked-"):
                    continue
                if attr == "style":
                    self.assertFalse(css_is_remote(value), html)
                elif attr in ("src", "poster", "background", "data", "lowsrc") or (
                        attr.endswith("href") and el.tag in ("img", "image", "link")):
                    self.assertFalse(is_remote_url(value), html)
                elif attr == "srcset":
                    self.assertNotIn("pisteur", value, html)

    def test_les_formes_qui_passaient(self):
        forms = [
            '<img src="https:\\\\pisteur.test/a.gif">',
            '<img src="\\\\pisteur.test/a.gif">',
            '<img src="/\\pisteur.test/a.gif">',
            '<img src=\'https://pisteur.test/a"b.gif\'>',
            '<img src="//pisteur.test/a.gif">',
            '<img srcset="https://pisteur.test/a.gif 1x, /local.gif 2x">',
            '<video poster="https://pisteur.test/a.gif"></video>',
            '<audio src="https://pisteur.test/a.mp3"></audio>',
            '<div style="background-image:u\\72l(https://pisteur.test/a.gif)">x</div>',
            '<div style="background-image:url(\\68ttps://pisteur.test/a.gif)">x</div>',
            '<div style="background-image:image-set(\'https://pisteur.test/a.gif\' 1x)">x</div>',
            '<div style="color:red;background-image:url(https://pisteur.test/a.gif">x</div>',
            '<table background="https://pisteur.test/a.gif"><tr><td>x</td></tr></table>',
        ]
        for form in forms:
            body, n = self._park(form)
            self.assertGreaterEqual(n, 1, form)
            self._no_live_remote(body)

    def test_le_texte_et_la_structure_ne_bougent_pas(self):
        """Un `url(` dans le TEXTE ne touche à rien (un parcage par expression régulière effaçait un tableau)."""
        html = ('<p>Format : url(//cdn/x</p><table><tr><td>Montant 120 $</td></tr>'
                '</table><p>(voir annexe)</p><img src="https://pisteur.test/a.gif">')
        body, n = self._park(html)
        self.assertEqual(n, 1)
        self.assertIn("Montant 120 $", body)
        self.assertIn("(voir annexe)", body)
        self.assertIn("Format : url(//cdn/x", body)
        html = '<a href="https://x.test/?u=url(https://y.test)">lien</a><p>Collez url(https://y.test) ici</p>'
        body, n = self._park(html)
        self.assertEqual(n, 0)
        self.assertIn("url(https://y.test)", body)

    def test_une_declaration_retiree_garde_ses_voisines(self):
        body, n = self._park(
            '<div style="color:red;background:url(https://pisteur.test/a.gif);'
            'font-weight:bold">x</div>')
        self.assertEqual(n, 1)
        self.assertIn("color:red", body.replace(" ", ""))
        self.assertIn("font-weight:bold", body.replace(" ", ""))

    def test_le_local_reste(self):
        html = ('<img src="cid:logo"><img src="/web/image/1">'
                '<div style="background:url(data:image/png;base64,AAAA)">x</div>')
        body, n = self._park(html)
        self.assertEqual(n, 0)
        self.assertEqual(str(body), str(self._park(html)[0]))
        self.assertIn("cid:logo", body)
        self.assertIn("/web/image/1", body)
        self.assertIn("data:image/png", body)

    def test_un_corps_sans_pisteur_revient_tel_quel(self):
        from ..models.bf_email_images import park_remote_images
        html = "<p>Bonjour&nbsp;<b>toi</b></p>"
        self.assertIs(park_remote_images(html)[0], html)

    def test_le_navigateur_imap_assainit_puis_parque(self):
        """Le corps IMAP arrive BRUT : `<link>`, `@import`, attribut sans guillemets."""
        from email.message import EmailMessage
        msg = EmailMessage()
        msg["From"] = "rapports@fournisseur.test"
        raw = ('<link rel="stylesheet" href="https://pisteur.test/a.css">'
               '<style>@import "https://pisteur.test/b.css";</style>'
               '<img src=https://pisteur.test/c.gif><object data="https://pisteur.test/d"></object>'
               '<p>texte</p>')
        Email = self.env["bf.email"].with_user(self.owner)
        body, blocked, reason, refused = Email._imap_browser_reading(raw, msg)
        self.assertEqual(reason, "")
        self.assertGreaterEqual(blocked, 1)
        self.assertNotIn("<link", body)
        self.assertNotIn("@import", body)
        self._no_live_remote(body)
        self.assertIn("texte", body)
        # Même décision que la boîte : un expéditeur autorisé, avec en-têtes.
        self.env["bf.email.image.sender"].with_user(self.owner).create(
            {"kind": "sender", "value": "rapports@fournisseur.test"})
        body, blocked, reason, refused = Email._imap_browser_reading(raw, msg)
        self.assertEqual((reason, blocked), ("sender", 0))


@tagged("post_install", "-at_install")
class TestFormesCssEtSvg(MobileApiCase):
    """Des formes CSS et SVG CHARGÉES par Chromium chez un pisteur après
    l'assainisseur et un parcage qui ne les connaissait pas."""

    def _park(self, html):
        from odoo.tools import html_sanitize
        from ..models.bf_email_images import park_remote_images
        return park_remote_images(html_sanitize(html))

    def test_un_commentaire_dans_une_chaine_ne_masque_rien(self):
        for html in (
            """<div style="background-image:url('/*'),url(http://pisteur.test/a.gif)">x</div>""",
            """<div style='background-image:url("/*"),url(http://pisteur.test/a.gif)'>x</div>""",
            r"""<div style="background-image:url('\2f\2a'),url(http://pisteur.test/a.gif)">x</div>""",
            """<div style="background-image:url('/*'),image-set('http://pisteur.test/a.gif' 1x)">x</div>""",
        ):
            body, n = self._park(html)
            self.assertGreaterEqual(n, 1, html)
            self.assertNotIn("pisteur.test", body, html)

    def test_une_variable_css_ne_cache_pas_l_url(self):
        for html in (
            """<div style="--x:'http://pisteur.test/a.gif';background-image:image-set(var(--x) 1x)">x</div>""",
            """<div style="--x:'http://pisteur.test/a.gif'"><p style="background-image:image-set(var(--x) 1x)">x</p></div>""",
            """<div style="--x:'http://pisteur.test/a.gif';background-image:-webkit-image-set(var(--x) 1x)">x</div>""",
        ):
            body, n = self._park(html)
            self.assertGreaterEqual(n, 1, html)
            self.assertNotIn("pisteur.test", body, html)

    def test_les_attributs_de_presentation_svg(self):
        for html in (
            '<svg width="20" height="20" filter="url(http://pisteur.test/f.svg#f)"></svg>',
            '<svg width="20" height="20" mask="url(http://pisteur.test/m.svg#m)"></svg>',
            '<svg width="20" height="20" cursor="url(http://pisteur.test/c.cur), auto"></svg>',
            '<svg><svg width="20" height="20" filter="url(http://pisteur.test/f2.svg#f)"></svg></svg>',
        ):
            body, n = self._park(html)
            self.assertGreaterEqual(n, 1, html)
            for attr in ("filter", "mask", "cursor"):
                self.assertNotIn(' %s="url(http' % attr, body, html)

    def test_le_texte_d_un_attribut_reste(self):
        body, n = self._park(
            '<img alt="voir url(http://x.test)" title="url(http://x.test)" src="cid:a">')
        self.assertEqual(n, 0)
        self.assertIn('alt="voir url(http://x.test)"', body)

    def test_la_cle_cette_fois_ne_suit_pas_le_pager(self):
        """« Afficher les images » ne vaut que pour CE courriel."""
        action = self.with_attachment.with_user(self.owner).action_load_remote_images()
        context = action["context"]
        self.assertEqual(context["bf_load_images"], self.with_attachment.id)
        self.inbound.sudo().body_html = '<img src="https://pisteur.test/p.gif">'
        other = self.inbound.with_user(self.owner).with_context(context)
        self.assertIn("data-blocked-src", other.body_html_reading or "")
        same = self.with_attachment.with_user(self.owner).with_context(context)
        self.assertNotIn("data-blocked-src", same.body_html_reading or "")

    def test_un_from_a_point_final_ne_joue_pas(self):
        from ..models.bf_email_images import strict_sender
        self.assertEqual(strict_sender("alertes@exemple.test."), "")
        self.assertEqual(strict_sender("Alertes <alertes@exemple.test>"),
                         "alertes@exemple.test")
