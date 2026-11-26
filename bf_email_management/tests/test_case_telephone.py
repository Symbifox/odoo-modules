"""La case « Fil cité attendu » au téléphone.

Le lot précédent avait fermé un cul-de-sac à moitié : il REFUSE la sauvegarde quand
une retouche depuis le téléphone effacerait le fil cité, ce qui protège la donnée,
mais la case qui débloque n'était exposée qu'au formulaire du poste et le texte du
refus proposait une commande Python — injouable depuis un téléphone.

🔴 L'essai qui porte tout le lot est `test_decocher_et_reecrire_dans_le_meme_appel` :
c'est le geste réel de quelqu'un qui décoche puis sauvegarde, et si la garde relisait
l'ancienne valeur du drapeau, décocher ne débloquerait rien.
"""
from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import HttpCase, TransactionCase, tagged

from .test_mobile_compositeur_http import BASE, MobileHttpMixin

MARQUEUR_SIGNATURE = "o_bf_signature"
SIGNATURE = f'<div class="{MARQUEUR_SIGNATURE}">Alex Exemple</div>'
CITATION = ('<div class="bf_citation"><br/><p>Le lundi 3 novembre 2026, '
            'a@b.test a écrit :</p><blockquote>bonjour</blockquote></div>')


@tagged("post_install", "-at_install")
class TestCaseTelephone(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.dossier = cls.env["res.partner"].create(
            {"name": "Dossier d'essai"})
        cls.destinataire = cls.env["res.partner"].create(
            {"name": "Destinataire", "email": "destinataire3@essai.invalid"})
        cls.Api = cls.env["bf.email"]

    def _brouillon(self, corps, attendue=True, objet="Re: question"):
        """Un brouillon tel que le poste le pose, et que le téléphone peut voir.

        ⚠️ `author_id` = l'usager courant, `bf_is_draft`, `is_note=False` : les trois
        clauses de `_mobile_draft_domain`. Sans elles, `mobile_draft` ne le trouve pas
        et l'essai mesurerait l'absence du brouillon, pas la garde.
        """
        return self.env["mail.scheduled.message"].create({
            "subject": objet,
            "body": corps,
            "model": "res.partner",
            "res_id": self.dossier.id,
            "partner_ids": [(6, 0, self.destinataire.ids)],
            "scheduled_date": "2031-01-01 12:00:00",
            "author_id": self.env.user.partner_id.id,
            "is_note": False,
            "bf_is_draft": True,
            "bf_citation_attendue": attendue,
        })

    # ------------------------------------------------------------------ la lecture
    def test_la_case_est_dans_la_charge_utile(self):
        """Le téléphone doit VOIR la case, sinon il ne peut pas la montrer."""
        marque = self._brouillon("<p>Merci.</p>" + SIGNATURE + CITATION)
        libre = self._brouillon("<p>Bonjour.</p>" + SIGNATURE, attendue=False,
                                objet="Offre de service")
        self.assertIs(self.Api.mobile_draft(marque.id)["citation_attendue"], True)
        self.assertIs(self.Api.mobile_draft(libre.id)["citation_attendue"], False)

    # ------------------------------------------------------------------ la bascule
    def test_decocher_depuis_le_telephone(self):
        marque = self._brouillon("<p>Merci.</p>" + SIGNATURE + CITATION)
        self.Api.mobile_draft_save(marque.id, citation_attendue=False)
        self.assertFalse(marque.bf_citation_attendue)
        self.assertIs(self.Api.mobile_draft(marque.id)["citation_attendue"], False)

    def test_recocher_depuis_le_telephone(self):
        libre = self._brouillon("<p>Bonjour.</p>" + SIGNATURE, attendue=False,
                                objet="Offre de service")
        self.Api.mobile_draft_save(libre.id, citation_attendue=True)
        self.assertTrue(libre.bf_citation_attendue)

    def test_ne_rien_envoyer_ne_touche_pas_la_case(self):
        """`None` veut dire « je n'y touche pas », comme pour les autres champs."""
        marque = self._brouillon("<p>Merci.</p>" + SIGNATURE + CITATION)
        self.Api.mobile_draft_save(marque.id, subject="Objet retouché")
        self.assertEqual(marque.subject, "Objet retouché")
        self.assertTrue(marque.bf_citation_attendue, "la case a bougé toute seule")

    # ------------------------------------------------------------------ le piège
    def test_decocher_et_reecrire_dans_le_meme_appel(self):
        """🔴 Le geste réel : décocher, puis sauvegarder son texte.

        La garde de la version précédente refuse une retouche qui efface la citation. Si elle relisait
        l'ancienne valeur du drapeau, ce seul appel serait refusé — et décocher depuis
        le téléphone ne débloquerait RIEN, ce qui est exactement le cul-de-sac que ce
        lot est censé fermer.
        """
        marque = self._brouillon("<p>Merci.</p>" + SIGNATURE + CITATION)
        self.Api.mobile_draft_save(
            marque.id, body="Une phrase de plus.", citation_attendue=False)
        self.assertFalse(marque.bf_citation_attendue)
        self.assertIn("Une phrase de plus.", marque.body)
        self.assertNotIn("bf_citation", marque.body, "le montage est faux")

    def test_sans_decocher_la_garde_refuse_toujours(self):
        """Le témoin négatif du précédent : la garde de la version précédente tient toujours."""
        marque = self._brouillon("<p>Merci.</p>" + SIGNATURE + CITATION)
        with self.assertRaises(UserError) as capture:
            self.Api.mobile_draft_save(marque.id, body="Une phrase de plus.")
        self.assertIn("effacerait", str(capture.exception))
        self.assertIn("Fil cité attendu", str(capture.exception),
                      "le refus doit nommer la case à décocher")
        self.assertIn("bf_citation", marque.body, "la citation a été détruite")

    def test_decocher_ouvre_vraiment_l_envoi(self):
        """De bout en bout : décocher au téléphone, et le brouillon part.

        🔴 C'est ce qui prouve que la case au téléphone DÉBLOQUE, et pas seulement
        qu'elle s'écrit. Sans cet essai, le lot pourrait exposer une case décorative.
        """
        marque = self._brouillon("<p>Merci.</p>" + SIGNATURE)
        with self.assertRaises(UserError):
            marque.post_message()
        self.Api.mobile_draft_save(marque.id, citation_attendue=False)
        marque.post_message()
        self.assertFalse(marque.exists(), "le brouillon décoché n'est pas parti")

    def test_un_envoi_programme_nest_pas_basculable(self):
        """La bascule ne doit pas atteindre ce que le téléphone ne montre pas.

        ⚠️ Mesuré, et pas supposé : la porte s'applique AUSSI à un envoi programmé
        marqué, et `_mobile_draft_domain` ne montre que les brouillons. Le lot
        élargit la surface d'écriture, donc il lui faut son témoin négatif.
        """
        programme = self._brouillon("<p>Merci.</p>" + SIGNATURE + CITATION)
        programme.bf_is_draft = False
        with self.assertRaises(UserError):
            self.Api.mobile_draft_save(programme.id, citation_attendue=False)
        self.assertTrue(programme.bf_citation_attendue, "la case a bougé")

    def test_une_note_nest_pas_basculable(self):
        """Une note marquée serait impostable à jamais : le téléphone n'y touche pas."""
        note = self._brouillon("<p>Note.</p>")
        note.is_note = True
        with self.assertRaises(UserError):
            self.Api.mobile_draft_save(note.id, citation_attendue=False)
        self.assertTrue(note.bf_citation_attendue, "la case a bougé")

    def test_la_case_est_aussi_dans_la_LISTE(self):
        """La liste la porte, pas seulement le brouillon complet.

        C'est par la liste que l'app badge un brouillon bloqué sans l'ouvrir.
        """
        marque = self._brouillon("<p>Merci.</p>" + SIGNATURE + CITATION)
        lignes = self.Api.mobile_drafts()["drafts"]
        vue = next((d for d in lignes if d["id"] == marque.id), None)
        self.assertIsNotNone(vue, "le brouillon n'est pas dans la liste")
        self.assertIs(vue["citation_attendue"], True)

    # --------------------------------------------- ce que le lot ne change pas
    def test_un_brouillon_d_un_autre_reste_invisible(self):
        """La bascule passe par `_mobile_draft_browse`, donc par son contrôle d'accès.

        ⚠️ Mesuré, et pas supposé : exposer un champ de plus ne doit pas ouvrir une
        route sur les brouillons d'autrui.
        """
        autre = self.env["res.users"].create({
            "name": "Collègue", "login": "collegue.case@essai.invalid",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("base.group_partner_manager").id])],
        })
        sien = self.env["mail.scheduled.message"].with_user(autre).create({
            "subject": "Re: question", "body": "<p>x</p>" + SIGNATURE + CITATION,
            "model": "res.partner", "res_id": self.dossier.id,
            "partner_ids": [(6, 0, self.destinataire.ids)],
            "scheduled_date": "2031-01-01 12:00:00",
            "author_id": autre.partner_id.id, "is_note": False,
            "bf_is_draft": True, "bf_citation_attendue": True,
        })
        with self.assertRaises(UserError):
            self.Api.with_user(self.env.user).mobile_draft_save(
                sien.id, citation_attendue=False)
        self.assertTrue(sien.bf_citation_attendue, "la case d'un autre a été touchée")

    def test_la_date_du_brouillon_ne_bouge_pas(self):
        """Une bascule n'est pas un envoi : la sentinelle ne bouge pas."""
        marque = self._brouillon("<p>Merci.</p>" + SIGNATURE + CITATION)
        avant = marque.scheduled_date
        self.Api.mobile_draft_save(marque.id, citation_attendue=False)
        self.assertEqual(marque.scheduled_date, avant)
        self.assertTrue(marque.bf_is_draft, "le brouillon a cessé d'en être un")
        self.assertGreater(marque.scheduled_date, fields.Datetime.now(),
                           "la sentinelle est passée dans le passé")


@tagged("post_install", "-at_install")
class TestCaseTelephoneHttp(MobileHttpMixin, HttpCase):
    """La case, par la ROUTE, et pas seulement par le modèle.

    🔴 Pourquoi cet essai existe : le contrôleur de `/draft/save` construit une liste
    de paramètres EXPLICITE. Un paramètre ajouté au modèle sans l'ajouter là n'est
    jamais atteint depuis le téléphone — la case aurait été décorative, et aucun essai
    du modèle ne l'aurait montré. C'est ce que ce lot a failli livrer.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner = cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Porteur Case",
            "login": "mobile.case@test.invalid",
            "email": "case@test.invalid",
            "tz": "America/Montreal",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.account = cls.env["bf.email.account"].create({
            "name": "Boîte case", "user_id": cls.owner.id,
            "host": "imap.test.invalid", "port": 993,
            "login": "case@test.invalid", "password": "x",
            "state": "connected",
        })
        Identity = cls.env["bf.email.identity"].sudo()
        Identity.search([("user_id", "=", cls.owner.id)]).unlink()
        cls.identite = Identity.create({
            "user_id": cls.owner.id, "name": "Porteur Case",
            "email": "case@test.invalid", "verified": True,
            "is_default": True, "account_id": cls.account.id,
        })
        # La fiche porteuse appartient à l'auteur : `mail.scheduled.message` refuse la
        # création quand l'auteur ne peut pas écrire dessus.
        # ⚠️ `date`, `source`, `account_id`, `user_id` : `bf.email` a une contrainte
        # NOT NULL sur la date, et le montage mimé sans eux tombait en setUpClass.
        cls.porteuse = cls.env["bf.email"].with_user(cls.owner).create({
            "subject": "Sujet case", "email_from": "Tiers <tiers@test.invalid>",
            "email_to": "case@test.invalid",
            "direction": "in", "status": "new",
            "source": "imap", "account_id": cls.account.id,
            "user_id": cls.owner.id, "imap_in_inbox": True,
            "message_id_header": "<case-1@test.invalid>",
            "date": "2026-11-12 12:00:00",
        })
        cls.device = cls.env["bf.email.mobile.device"]._issue(
            cls.owner.id, name="Appareil case")
        cls.env.cr.flush()

    def _draft(self, corps, attendue=True):
        return self.env["mail.scheduled.message"].with_user(self.owner).create({
            "subject": "Re: Sujet case",
            "body": corps,
            "model": "bf.email",
            "res_id": self.porteuse.id,
            "partner_ids": [(6, 0, self.owner.partner_id.ids)],
            "scheduled_date": "2031-01-01 12:00:00",
            "author_id": self.owner.partner_id.id,
            "is_note": False,
            "bf_is_draft": True,
            "bf_citation_attendue": attendue,
        })

    def test_la_route_rend_la_case(self):
        """⚠️ `/draft` est un GET avec `?id=`, et il rend le brouillon SANS enveloppe.
        `/draft/save`, lui, rend `{"ok":…, "draft":…}` — les deux ne se lisent pas
        pareil, et le confondre rendait un 405 que j'avais pris pour un refus.
        """
        draft = self._draft("<p>Merci.</p>" + SIGNATURE + CITATION)
        r = self._get(f"/draft?id={draft.id}", headers=self._auth())
        self.assertEqual(r.status_code, 200, r.text[:200])
        self.assertIs(r.json()["citation_attendue"], True)

    def test_la_route_decoche_la_case(self):
        """🔴 Le maillon qui manquait : la clé traverse le contrôleur."""
        draft = self._draft("<p>Merci.</p>" + SIGNATURE + CITATION)
        r = self._post("/draft/save",
                       {"id": draft.id, "citation_attendue": False},
                       headers=self._auth())
        self.assertEqual(r.status_code, 200, r.text[:200])
        draft.invalidate_recordset(["bf_citation_attendue"])
        self.assertFalse(draft.bf_citation_attendue,
                         "la clé n'a pas traversé la route : la case est décorative")
        self.assertIs(r.json()["draft"]["citation_attendue"], False)

    def test_la_route_sans_la_cle_ne_touche_pas_la_case(self):
        """L'écriture reste PARTIELLE : une clé absente n'est pas une bascule."""
        draft = self._draft("<p>Merci.</p>" + SIGNATURE + CITATION)
        r = self._post("/draft/save", {"id": draft.id, "subject": "Objet retouché"},
                       headers=self._auth())
        self.assertEqual(r.status_code, 200, r.text[:200])
        draft.invalidate_recordset(["bf_citation_attendue", "subject"])
        self.assertEqual(draft.subject, "Objet retouché")
        self.assertTrue(draft.bf_citation_attendue, "la case a bougé toute seule")

    def test_decocher_en_form_encode_debloque_la_sauvegarde(self):
        """🔴 Le seul essai qui distingue `bool()` de `_flag()`.

        `_body` retombe sur `kw` quand le Content-Type est mauvais — chemin
        SUPPORTÉ et documenté — et tout arrive alors en CHAÎNE. Or
        `bool("false")` vaut True : sans `_flag` au bord, décocher RECOCHERAIT la
        case, la garde jugerait sur True, et la sauvegarde serait refusée à
        quelqu'un qui vient de décocher. `/handle` et `/report` ont payé exactement
        ça, et le module porte deux essais pour ce défaut. Mes sept
        essais du modèle passent tous un booléen déjà typé : aucun ne le voyait.
        """
        draft = self._draft("<p>Merci.</p>" + SIGNATURE + CITATION)
        r = self.url_open(
            BASE + "/draft/save",
            data={"id": str(draft.id), "body": "Une phrase de plus.",
                  "citation_attendue": "false"},
            headers=self._auth(), timeout=30)
        self.assertEqual(r.status_code, 200, r.text[:300])
        draft.invalidate_recordset(["bf_citation_attendue", "body"])
        self.assertFalse(draft.bf_citation_attendue,
                         "bool(\"false\") a recoché la case")
        self.assertIn("Une phrase de plus.", draft.body)

    def test_null_json_ne_decoche_pas(self):
        """🔴 `null` veut dire « je n'y touche pas », jamais « décoche ».

        Un client qui sérialise un booléen nul émet `"citation_attendue": null`.
        Avec `_flag(None)` → False, la porte se lèverait TOUTE SEULE sur un
        brouillon que l'intégration qui rédige a posé sur un dossier client. L'échec doit
        aller du côté sûr.
        """
        draft = self._draft("<p>Merci.</p>" + SIGNATURE + CITATION)
        r = self._post("/draft/save", {"id": draft.id, "citation_attendue": None},
                       headers=self._auth())
        self.assertEqual(r.status_code, 200, r.text[:300])
        draft.invalidate_recordset(["bf_citation_attendue"])
        self.assertTrue(draft.bf_citation_attendue,
                        "un null a levé la porte tout seul")

    def test_recocher_en_form_encode(self):
        """Le témoin positif du form-encodé : « true » recoche bien."""
        draft = self._draft("<p>Bonjour.</p>" + SIGNATURE, attendue=False)
        r = self.url_open(BASE + "/draft/save",
                          data={"id": str(draft.id), "citation_attendue": "true"},
                          headers=self._auth(), timeout=30)
        self.assertEqual(r.status_code, 200, r.text[:300])
        draft.invalidate_recordset(["bf_citation_attendue"])
        self.assertTrue(draft.bf_citation_attendue)

    def test_la_capacite_est_annoncee(self):
        """Une capacité s'annonce : sinon l'app l'offre là où elle n'existe pas."""
        r = self._get("/config", headers=self._auth())
        self.assertEqual(r.status_code, 200, r.text[:300])
        self.assertEqual(r.json().get("draft_citation_api"), 1)
