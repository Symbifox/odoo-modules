from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.bf_federation.tests.test_federation import TestFederation


@tagged("post_install", "-at_install", "federation")
class TestPasDeCanal(TestFederation):
    """Un avis de violation n'a pas de canal : un lecteur parlerait au client dans le fil de preuve."""

    def test_d01_un_avis_n_a_pas_de_canal(self):
        manager = self.env["res.users"].create({
            "name": "Gestionnaire VP canal", "login": "gvp-canal-essai", "email": "gvp@canal.example",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("privacy_consent.group_privacy_manager").id])]})
        client = self.peer_b.partner_id
        client.write({"is_company": True, "privacy_officer_email": "rprp@client.example"})
        notice = self.env["privacy.breach.notice"].with_user(manager).create({
            "responsible_id": client.id, "nature": "loss", "circumstances": "x",
            "discovered_at": "2026-10-07 10:00:00", "pi_description": "y"})
        notice.action_send()
        link = notice._federation_link()
        self.assertTrue(link)
        with self.assertRaisesRegex(UserError, "pas de canal"):
            link.with_user(manager).action_open_channel()
        self.assertFalse(link._federation_may_read(manager.partner_id | self.receveur.partner_id))

    def test_d02_aucun_canal_ne_se_rattache_a_un_lien_d_avis(self):
        manager = self.env["res.users"].create({
            "name": "Gestionnaire VP canal 2", "login": "gvp-canal2-essai", "email": "gvp2@canal.example",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("privacy_consent.group_privacy_manager").id])]})
        client = self.peer_b.partner_id
        client.write({"is_company": True, "privacy_officer_email": "rprp@client.example"})
        notice = self.env["privacy.breach.notice"].with_user(manager).create({
            "responsible_id": client.id, "nature": "loss", "circumstances": "x",
            "discovered_at": "2026-10-07 10:00:00", "pi_description": "y"})
        notice.action_send()
        link = notice._federation_link()
        interne = self.env["res.users"].create({
            "name": "Interne canal", "login": "interne-canal-essai",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])]})
        Channel = self.env["discuss.channel"].with_user(interne)
        with self.assertRaisesRegex(UserError, "pas de canal"):
            Channel.create({"name": "Canal", "channel_type": "group", "federation_link_id": link.id})
        canal = Channel.create({"name": "Canal à moi", "channel_type": "group"})
        with self.assertRaisesRegex(UserError, "pas de canal"):
            canal.write({"federation_link_id": link.id})
