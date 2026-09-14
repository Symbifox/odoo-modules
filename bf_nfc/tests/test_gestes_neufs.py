"""Les quatre gestes ajoutés à la 18.0.1.2.0 : adresse, passage, tâche, action.

Chacun garde surtout ce qu'il refuse. Ce sont les refus qui feraient d'une
pastille autre chose qu'une pastille.
"""
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestGestesNeufs(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.interne = new_test_user(cls.env, login="geste-interne", groups="base.group_user")
        cls.gestion = new_test_user(cls.env, login="geste-gestion",
                                    groups="base.group_user,bf_nfc.group_nfc_manager")
        cls.env["ir.config_parameter"].sudo().set_param("bf_nfc.fenetre_doublon_secondes", "0")

    def _tag(self, code_geste, **valeurs):
        return self.env["bf.nfc.tag"].create(dict(
            name="Essai %s" % code_geste,
            gesture_id=self.env.ref("bf_nfc.gesture_%s" % code_geste).id, **valeurs))

    # ------------------------------------------------------------------
    # Ouvrir une adresse
    # ------------------------------------------------------------------
    def test_adresse_web_rendue(self):
        tag = self._tag("url", params='{"url": "https://symbifox.com/procedure"}')
        r = tag.with_user(self.interne).taper("app")
        self.assertEqual(r["statut"], "ok")
        self.assertEqual(r["url"], "https://symbifox.com/procedure")

    def test_adresse_dangereuse_refusee(self):
        """🔴 Une pastille qui porterait `javascript:` ferait exécuter le téléphone."""
        for mauvaise in ("javascript:alert(1)", "intent://x#Intent;end", "file:///etc/passwd",
                         "data:text/html,<b>x</b>", "https://", ""):
            tag = self._tag("url", params='{"url": "%s"}' % mauvaise.replace('"', ''))
            r = tag.with_user(self.interne).taper("app")
            self.assertEqual(r["statut"], "refused", "%r doit être refusée" % mauvaise)

    # ------------------------------------------------------------------
    # Consigner un passage
    # ------------------------------------------------------------------
    def test_passage_refuse_avec_une_phrase_claire_sans_droit_d_ecriture(self):
        """⚠️ Odoo 18 : un interne ordinaire ne peut pas écrire sur un contact.

        Le premier essai de ce geste supposait le contraire et tombait. La règle
        d'Odoo est la bonne ; ce qui ne l'était pas, c'est le refus générique
        « politique de sécurité » qu'elle produisait sur le téléphone.
        """
        partenaire = self.env["res.partner"].create({"name": "Contact en lecture seule"})
        tag = self._tag("note", res_model="res.partner", res_id=partenaire.id)
        r = tag.with_user(self.interne).taper("app")
        self.assertEqual(r["statut"], "refused")
        self.assertIn("pas le droit d'écrire", r["message"])
        self.assertNotIn("politique de sécurité", r["message"])

    def test_passage_consigne_en_note_au_nom_de_qui_tape(self):
        redacteur = new_test_user(self.env, login="geste-redacteur",
                                  groups="base.group_user,base.group_partner_manager")
        partenaire = self.env["res.partner"].create({"name": "Immeuble du parcours"})
        tag = self._tag("note", res_model="res.partner", res_id=partenaire.id,
                        place="Porte arrière", params='{"texte": "Ronde du soir"}')
        avant_courriels = self.env["mail.mail"].sudo().search_count([])
        r = tag.with_user(redacteur).taper("app")
        self.assertEqual(r["statut"], "ok", r.get("message"))
        note = self.env["mail.message"].search(
            [("model", "=", "res.partner"), ("res_id", "=", partenaire.id)],
            order="id desc", limit=1)
        self.assertIn("Ronde du soir", note.body)
        self.assertIn("Porte arrière", note.body)
        self.assertEqual(note.author_id, redacteur.partner_id)
        self.assertEqual(note.subtype_id, self.env.ref("mail.mt_note"))
        self.assertEqual(self.env["mail.mail"].sudo().search_count([]), avant_courriels,
                         "Une note ne doit envoyer aucun courriel.")

    def test_passage_n_injecte_pas_de_html(self):
        """⚠️ Le texte est échappé : une pastille ne pose pas de balise dans un chatter."""
        redacteur = new_test_user(self.env, login="geste-redacteur-html",
                                  groups="base.group_user,base.group_partner_manager")
        partenaire = self.env["res.partner"].create({"name": "Cible HTML"})
        tag = self._tag("note", res_model="res.partner", res_id=partenaire.id,
                        params='{"texte": "<img src=x onerror=alert(1)>"}')
        tag.with_user(redacteur).taper("app")
        note = self.env["mail.message"].search(
            [("model", "=", "res.partner"), ("res_id", "=", partenaire.id)], order="id desc", limit=1)
        self.assertNotIn("<img", note.body)

    # ------------------------------------------------------------------
    # Lancer une tâche planifiée
    # ------------------------------------------------------------------
    def _tache(self, active=True):
        return self.env["ir.cron"].sudo().create({
            "name": "Tâche d'essai des pastilles",
            "model_id": self.env["ir.model"]._get_id("res.partner"),
            "state": "code", "code": "pass",
            "interval_number": 1, "interval_type": "days", "active": active,
        })

    def test_tache_refusee_a_un_interne_ordinaire(self):
        tache = self._tache()
        tag = self._tag("cron", res_model="ir.cron", res_id=tache.id)
        r = tag.with_user(self.interne).taper("app")
        self.assertEqual(r["statut"], "refused")
        self.assertFalse(self.env["ir.cron.trigger"].sudo().search([("cron_id", "=", tache.id)]))

    def test_tache_lancee_par_la_gestion_pose_un_declencheur(self):
        """🔴 `_trigger()`, pas une exécution dans la requête."""
        tache = self._tache()
        tag = self._tag("cron", res_model="ir.cron", res_id=tache.id)
        r = tag.with_user(self.gestion).taper("app")
        self.assertEqual(r["statut"], "ok", r.get("message"))
        self.assertTrue(self.env["ir.cron.trigger"].sudo().search([("cron_id", "=", tache.id)]))

    def test_tache_inactive_refusee_franchement(self):
        """🔴 Une tâche inactive avalerait son déclencheur en silence."""
        tache = self._tache(active=False)
        tag = self._tag("cron", res_model="ir.cron", res_id=tache.id)
        r = tag.with_user(self.gestion).taper("app")
        self.assertEqual(r["statut"], "refused")
        self.assertIn("désactivée", r["message"])

    # ------------------------------------------------------------------
    # Exécuter une action
    # ------------------------------------------------------------------
    def test_action_refusee_a_un_interne_ordinaire(self):
        partenaire = self.env["res.partner"].create({"name": "Cible d'action"})
        action = self.env["ir.actions.server"].create({
            "name": "Marquer", "model_id": self.env["ir.model"]._get_id("res.partner"),
            "state": "code", "code": "env['res.partner'].browse(%d).write({'ref': 'ACTION'})" % partenaire.id,
        })
        tag = self._tag("action", res_model="ir.actions.server", res_id=action.id)
        r = tag.with_user(self.interne).taper("app")
        self.assertEqual(r["statut"], "refused")
        partenaire.invalidate_recordset(["ref"])
        self.assertFalse(partenaire.ref)

    def test_catalogue_masque_les_gestes_de_gestion_a_un_interne(self):
        gestes = self.env["bf.nfc.gesture"].search([])
        visibles = gestes.filtered(lambda g: not g.reserve_gestion).mapped("code")
        self.assertIn("url", visibles)
        self.assertNotIn("cron", visibles)
        self.assertNotIn("action", visibles)

    def test_la_saisie_suit_le_geste(self):
        self.assertEqual(self.env.ref("bf_nfc.gesture_url").saisie, "url")
        self.assertEqual(self.env.ref("bf_nfc.gesture_note").saisie, "cible_texte")
        self.assertEqual(self.env.ref("bf_nfc.gesture_cron").saisie, "cible")
