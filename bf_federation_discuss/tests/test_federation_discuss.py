"""Le canal d'un objet fédéré : la fenêtre parle, le registre garde, l'anneau ne tourne pas."""

from markupsafe import Markup

from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.bf_federation.tests.test_federation import TestFederation


@tagged("post_install", "-at_install", "federation", "federation_discuss")
class TestFederationDiscuss(TestFederation):

    def _lien(self, task):
        return self.env["federation.link"].with_context(active_test=False).search(
            [("res_model", "=", "project.task"), ("res_id", "=", task.id)], limit=1)

    def _lien_miroir(self, task):
        return self.env["federation.link"].with_context(active_test=False).search(
            [("peer_id", "=", self.peer_a.id), ("remote_ref", "=", str(task.id))], limit=1)

    def test_c01_aucun_canal_tant_que_personne_ne_l_ouvre(self):
        task = self._share()
        link = self._lien(task)
        self.assertFalse(link.has_channel, "un canal que personne n'a ouvert est une porte vide")
        self.assertFalse(self.env["discuss.channel"].search([("federation_link_id", "=", link.id)]))

    def test_c02_ouvrir_le_canal_le_cree_une_fois(self):
        task = self._share()
        link = self._lien(task)
        action = link.action_open_channel()
        canal = self.env["discuss.channel"].browse(action["res_id"])
        self.assertTrue(canal.exists())
        self.assertEqual(canal.federation_link_id, link)
        self.assertIn(self.peer_b.name, canal.name)
        link.invalidate_recordset()
        self.assertTrue(link.has_channel)
        # deuxième clic : le même canal
        action2 = link.action_open_channel()
        self.assertEqual(action2["res_id"], canal.id)
        self.assertEqual(self.env["discuss.channel"].search_count([("federation_link_id", "=", link.id)]), 1)

    def test_c03_un_message_du_canal_entre_au_registre_et_part_chez_le_pair(self):
        task = self._share()
        miroir = self._mirror_of(task)
        link = self._lien(task)
        canal = self.env["discuss.channel"].browse(link.action_open_channel()["res_id"])
        canal.message_post(body=Markup("<p>On se voit jeudi ?</p>"), message_type="comment",
                           subtype_xmlid="mail.mt_comment")
        au_registre = self.env["mail.message"].search(
            [("model", "=", "project.task"), ("res_id", "=", task.id), ("body", "ilike", "jeudi")])
        self.assertEqual(len(au_registre), 1, "le canal écrit au registre, une seule fois")
        self.assertNotIn("&lt;p&gt;", au_registre.body,
                         "🔴 message_post échappe une chaîne : il faut du Markup, sinon le HTML "
                         "arrive en clair et le marqueur 🔒 n'est plus en tête du texte")
        self._flush()
        chez_le_pair = self.env["mail.message"].search(
            [("model", "=", "project.task"), ("res_id", "=", miroir.id), ("body", "ilike", "jeudi")])
        self.assertEqual(len(chez_le_pair), 1, "et le socle le porte chez le pair, sans genre nouveau")

    def test_c04_un_message_recu_reparait_dans_le_canal(self):
        task = self._share()
        miroir = self._mirror_of(task)
        link = self._lien(task)
        canal = self.env["discuss.channel"].browse(link.action_open_channel()["res_id"])
        # le pair écrit sur son miroir : le message revient chez nous, puis dans le canal
        miroir.with_user(self.receveur).message_post(
            body=Markup("<p>Jeudi me va très bien.</p>"), message_type="comment", subtype_xmlid="mail.mt_comment")
        self._flush()
        dans_le_canal = self.env["mail.message"].search(
            [("model", "=", "discuss.channel"), ("res_id", "=", canal.id), ("body", "ilike", "Jeudi me va")])
        self.assertEqual(len(dans_le_canal), 1)

    def test_c05_l_anneau_ne_tourne_pas(self):
        """canal → chatter → réseau → chatter du pair → canal du pair, et ça s'arrête là."""
        task = self._share()
        miroir = self._mirror_of(task)
        link = self._lien(task)
        link_miroir = self._lien_miroir(task)
        canal_ici = self.env["discuss.channel"].browse(link.action_open_channel()["res_id"])
        canal_pair = self.env["discuss.channel"].browse(link_miroir.action_open_channel()["res_id"])
        canal_ici.message_post(body=Markup("<p>Un tour et pas deux.</p>"), message_type="comment",
                               subtype_xmlid="mail.mt_comment")
        self._flush()
        self._flush()  # une deuxième passe : si l'anneau tournait, il aurait tourné
        for modele, res_id, attendu, quoi in (
                ("project.task", task.id, 1, "le registre d'ici"),
                ("discuss.channel", canal_ici.id, 1, "le canal d'ici"),
                ("project.task", miroir.id, 1, "le registre du pair"),
                ("discuss.channel", canal_pair.id, 1, "le canal du pair")):
            trouve = self.env["mail.message"].search_count(
                [("model", "=", modele), ("res_id", "=", res_id), ("body", "ilike", "Un tour et pas deux")])
            self.assertEqual(trouve, attendu, "%s porte %s exemplaire(s), pas %s" % (quoi, attendu, trouve))
        self.assertFalse(self.Outbox.search([("state", "=", "pending")]),
                         "et plus rien n'attend de partir")

    def test_c06_les_notes_de_federation_restent_au_registre(self):
        """« L'échéance a bougé » est un journal, pas une conversation."""
        task = self._share()
        link = self._lien(task)
        canal = self.env["discuss.channel"].browse(link.action_open_channel()["res_id"])
        avant = self.env["mail.message"].search_count([("model", "=", "discuss.channel"), ("res_id", "=", canal.id)])
        task.write({"date_deadline": "2026-10-01 12:00:00"})
        self._flush()
        apres = self.env["mail.message"].search_count([("model", "=", "discuss.channel"), ("res_id", "=", canal.id)])
        self.assertEqual(avant, apres, "le canal ne se remplit pas d'avis de service")
        notes = self.env["mail.message"].search(
            [("model", "=", "project.task"), ("res_id", "=", task.id), ("body", "ilike", "échéance")])
        self.assertTrue(notes, "mais le registre, lui, les garde")

    def test_c07_un_marqueur_prive_ne_traverse_pas_depuis_le_canal(self):
        task = self._share()
        miroir = self._mirror_of(task)
        link = self._lien(task)
        canal = self.env["discuss.channel"].browse(link.action_open_channel()["res_id"])
        canal.message_post(body=Markup("<p>🔒 Entre nous, son budget est serré.</p>"),
                           message_type="comment", subtype_xmlid="mail.mt_comment")
        self._flush()
        chez_le_pair = self.env["mail.message"].search_count(
            [("model", "=", "project.task"), ("res_id", "=", miroir.id), ("body", "ilike", "budget est serré")])
        self.assertEqual(chez_le_pair, 0, "le marqueur d'exclusion vaut depuis le canal aussi")
        ici = self.env["mail.message"].search_count(
            [("model", "=", "project.task"), ("res_id", "=", task.id), ("body", "ilike", "budget est serré")])
        self.assertEqual(ici, 1, "et il reste chez son auteur")

    def test_c08_un_lien_ferme_n_ouvre_pas_de_canal(self):
        task = self._share()
        link = self._lien(task)
        task.write({"federation_peer_id": False})
        self._flush()
        link.invalidate_recordset()
        with self.assertRaises(UserError):
            link.action_open_channel()

    def test_c09_les_membres_du_canal_sont_les_gens_d_ici(self):
        task = self._share()
        link = self._lien(task)
        canal = self.env["discuss.channel"].browse(link.action_open_channel()["res_id"])
        membres = canal.channel_member_ids.mapped("partner_id")
        self.assertIn(self.env.user.partner_id, membres)
        self.assertNotIn(self.peer_b.partner_id, membres,
                         "le pair n'est pas membre : il a son canal chez lui")

    def test_c10_un_message_hors_federation_ne_declenche_rien(self):
        """Le crochet voit TOUTE écriture de chatter, y compris par XML-RPC et par
        les crons. Sur un modèle non fédérable, ou sur un objet fédérable qui n'est
        pas partagé, il doit ne rien faire et ne rien lever."""
        partenaire = self.env["res.partner"].create({"name": "Quelqu'un hors fédération"})
        partenaire.message_post(body=Markup("<p>Un mot sur un modèle non fédérable.</p>"),
                                message_type="comment", subtype_xmlid="mail.mt_comment")
        libre = self.env["project.task"].create(
            {"name": "Tâche jamais partagée", "project_id": self.project_ferme.id})
        libre.message_post(body=Markup("<p>Un mot sur une tâche non fédérée.</p>"),
                           message_type="comment", subtype_xmlid="mail.mt_comment")
        partagee = self._share()
        partagee.message_post(body=Markup("<p>Un mot sans canal ouvert.</p>"),
                              message_type="comment", subtype_xmlid="mail.mt_comment")
        self.assertEqual(
            self.env["discuss.channel"].search_count([("federation_link_id", "!=", False)]), 0,
            "aucun canal ne naît tout seul, quelle que soit la porte d'écriture")
        self._flush()

    def test_c11_un_canal_federe_ne_se_peuple_pas_librement(self):
        """🔴 add_members est publique : un membre peut en inviter un autre. Sur un
        canal fédéré, entrer dans le canal donne le droit de parler chez le pair,
        puisque tout ce qui s'y écrit part au chatter puis sur le réseau. On n'y
        admet donc que les gens qui pourraient déjà LIRE l'objet lié."""
        ferme = self.env["project.project"].create(
            {"name": "Projet fermé", "privacy_visibility": "followers"})
        ferme.federation_peer_ids = [(4, self.peer_b.id)]
        task = self.env["project.task"].create(
            {"name": "Tâche du projet fermé", "project_id": ferme.id,
             "federation_peer_id": self.peer_b.id})
        self._flush()
        link = self._lien(task)
        canal = self.env["discuss.channel"].browse(link.action_open_channel()["res_id"])

        etranger = self.env["res.users"].create({
            "name": "Curieux sans accès", "login": "curieux.canal",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])]})
        canal.sudo().add_members(partner_ids=etranger.partner_id.ids)
        self.assertNotIn(etranger.partner_id, canal.channel_member_ids.mapped("partner_id"),
                         "🔴 quelqu'un sans accès à la tâche est entré dans son canal")

        # Et par l'autre porte : on entre aussi par `users`, pas seulement par `partners`.
        canal.sudo()._add_members(users=etranger)
        canal.invalidate_recordset()
        self.assertNotIn(etranger.partner_id, canal.channel_member_ids.mapped("partner_id"),
                         "🔴 la porte `users` n'était pas gardée")

        # Quelqu'un qui peut lire la tâche, lui, entre.
        lecteur = self.env["res.users"].create({
            "name": "Personne du projet", "login": "lecteur.canal",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("project.group_project_manager").id])]})
        canal.sudo().add_members(partner_ids=lecteur.partner_id.ids)
        canal.invalidate_recordset()
        self.assertIn(lecteur.partner_id, canal.channel_member_ids.mapped("partner_id"))
