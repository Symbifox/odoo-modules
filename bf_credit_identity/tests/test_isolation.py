from freezegun import freeze_time

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import CreditCase


@tagged("post_install", "-at_install", "bf_credit_identity")
class TestIsolation(CreditCase):

    def test_anne_ne_voit_ni_ne_touche_les_rappels_de_bruno(self):
        rappels_bruno = self.mise_en_route(self.env_bruno)
        rappels_anne = self.mise_en_route(self.env_anne)
        # L'essai mesure quelque chose : les rappels de Bruno existent bien.
        self.assertEqual(len(rappels_bruno.sudo().exists()), 4)
        self.assertEqual(rappels_anne.user_id, self.anne)

        Rappel_anne = self.env_anne["bf.credit.reminder"].with_context(active_test=False)
        self.assertEqual(Rappel_anne.search([]), rappels_anne)
        self.assertFalse(Rappel_anne.search([("id", "in", rappels_bruno.ids)]))
        self.assertEqual(Rappel_anne.search_count([]), 4)
        chez_bruno = rappels_bruno.with_env(self.env_anne)
        with self.assertRaises(AccessError):
            chez_bruno.read(["name"])
        with self.assertRaises(AccessError):
            chez_bruno.write({"note": "lu"})
        with self.assertRaises(AccessError):
            chez_bruno.unlink()
        # Rien n'a bougé chez Bruno.
        self.assertFalse(any(rappels_bruno.sudo().mapped("note")))

    def test_l_administratrice_non_plus(self):
        rappels_anne = self.mise_en_route(self.env_anne)
        Rappel_admin = self.env_admin["bf.credit.reminder"].with_context(active_test=False)
        self.assertFalse(Rappel_admin.search([]))
        self.assertEqual(Rappel_admin.read_group([], ["kind"], ["kind"]), [])
        chez_anne = rappels_anne.with_env(self.env_admin)
        with self.assertRaises(AccessError):
            chez_anne.read(["name", "next_date"])
        with self.assertRaises(AccessError):
            chez_anne.write({"next_date": "2040-01-01"})
        with self.assertRaises(AccessError):
            chez_anne.unlink()
        # Seul sudo() passe.
        self.assertEqual(len(rappels_anne.sudo()), 4)

    def test_on_ne_cree_pas_de_rappel_pour_autrui(self):
        with self.assertRaises(AccessError):
            self.env_anne["bf.credit.reminder"].create({
                "name": "Déposé chez Bruno", "kind": "other", "user_id": self.bruno.id,
                "next_date": "2031-03-01",
            })
        with self.assertRaises(AccessError):
            self.env_admin["bf.credit.reminder"].create({
                "name": "Déposé chez Anne", "kind": "other", "user_id": self.anne.id,
                "next_date": "2031-03-01",
            })
        self.assertFalse(self.env["bf.credit.reminder"].sudo().search(
            [("name", "like", "Déposé")]))

    def test_un_rappel_ne_change_pas_de_proprietaire(self):
        rappel = self.mise_en_route(self.env_anne)[:1]
        with self.assertRaisesRegex(UserError, "stays with the person"):
            rappel.write({"user_id": self.bruno.id})
        self.assertEqual(rappel.sudo().user_id, self.anne)
        self.assertFalse(self.env_bruno["bf.credit.reminder"].search([]))

    @freeze_time("2031-02-26")
    def test_les_activites_restent_privees(self):
        rappels = self.mise_en_route(self.env_anne)
        self.env["bf.credit.reminder"]._cron_raise_activities()
        activites = rappels.sudo().activity_ids
        self.assertEqual(len(activites), 1, "seul le dossier Equifax (1er mars) tombe dans 7 jours")
        self.assertEqual(activites.user_id, self.anne)
        self.assertEqual(activites.with_env(self.env_anne).read(["res_name"])[0]["res_name"],
                         "Equifax credit report")
        for env in (self.env_bruno, self.env_admin):
            with self.subTest(personne=env.user.name):
                self.assertFalse(env["mail.activity"].search(
                    [("res_model", "=", "bf.credit.reminder")]))
                with self.assertRaises(AccessError):
                    activites.with_env(env).read(["res_name", "summary"])

    @freeze_time("2031-02-26")
    def test_aucun_abonne(self):
        rappels = self.mise_en_route(self.env_anne)
        self.env["bf.credit.reminder"]._cron_raise_activities()
        rappels[:1].message_post(body="Note pour moi.", message_type="comment",
                                 subtype_xmlid="mail.mt_note")
        # Ni l'assistant, ni l'activité, ni la note n'abonnent la propriétaire.
        self.assertFalse(rappels.sudo().message_follower_ids)
        # Même la propriétaire ne peut abonner personne, elle-même comprise.
        rappels[:1].message_subscribe(partner_ids=[self.bruno.partner_id.id, self.anne.partner_id.id])
        self.assertFalse(rappels[:1].sudo().message_follower_ids)


@tagged("post_install", "-at_install", "bf_credit_identity")
class TestRienNeSort(CreditCase):
    """La tâche planifiée lève des activités sans qu'aucun avis ne parte."""

    def _courriels_vers(self, partenaire):
        return self.env["mail.notification"].sudo().search_count([
            ("res_partner_id", "=", partenaire.id), ("notification_type", "=", "email")])

    def test_la_tache_planifiee_n_envoie_rien(self):
        with freeze_time("2031-02-01"):
            # Rien ne tombe dans les 7 jours : l'assistant ne lève aucune activité.
            rappels = self.mise_en_route(self.env_anne)
        self.assertFalse(rappels.sudo().activity_ids)
        avant_mail = self.env["mail.mail"].sudo().search_count([])
        avant_notif = self._courriels_vers(self.anne.partner_id)
        avant_messages = self.env["mail.message"].sudo().search_count(
            [("model", "=", "bf.credit.reminder")])
        # Tâche planifiée : elle tourne sous l'utilisateur de la tâche (OdooBot).
        with freeze_time("2031-02-26"):
            self.env["bf.credit.reminder"].with_user(
                self.env.ref("base.user_root"))._cron_raise_activities()
        self.assertEqual(len(rappels.sudo().activity_ids), 1,
                         "l'essai doit lever l'activité du dossier Equifax")
        self.assertEqual(self.env["mail.mail"].sudo().search_count([]), avant_mail)
        self.assertEqual(self._courriels_vers(self.anne.partner_id), avant_notif)
        self.assertEqual(self.env["mail.message"].sudo().search_count(
            [("model", "=", "bf.credit.reminder")]), avant_messages)

    def _activite_sans_contexte(self, modele, res_id):
        """Une activité posée par OdooBot SANS ``mail_activity_quick_update`` : le
        cas où Odoo envoie l'avis d'assignation par courriel."""
        return self.env["mail.activity"].with_user(self.env.ref("base.user_root")).sudo().create({
            "res_model_id": self.env["ir.model"]._get_id(modele),
            "res_id": res_id,
            "activity_type_id": self.env.ref("mail.mail_activity_data_todo").id,
            "user_id": self.anne.id,
            "date_deadline": "2031-03-01",
        })

    def test_le_controle_discrimine(self):
        """Témoin : la même activité, sur une fiche ordinaire, envoie bien un avis.

        Sans ce témoin, « aucun courriel » pourrait vouloir dire « la mesure ne voit
        rien ».
        """
        avant = self._courriels_vers(self.anne.partner_id)
        self._activite_sans_contexte("res.partner", self.bruno.partner_id.id)
        self.assertGreater(self._courriels_vers(self.anne.partner_id), avant)

    def test_aucun_avis_meme_sans_le_contexte(self):
        """Le fil d'un rappel n'avise personne : même l'avis d'assignation que la
        tâche planifiée évite par son contexte ne part pas."""
        rappel = self.mise_en_route(self.env_anne)[:1]
        avant = self._courriels_vers(self.anne.partner_id)
        avant_mail = self.env["mail.mail"].sudo().search_count([])
        self._activite_sans_contexte("bf.credit.reminder", rappel.id)
        self.assertEqual(self._courriels_vers(self.anne.partner_id), avant)
        self.assertEqual(self.env["mail.mail"].sudo().search_count([]), avant_mail)
