from odoo.tests import new_test_user


def monter(cls):
    """Un lot de 10, une gestion, un concierge (groupe maison) et un interne ordinaire."""
    env = cls.env
    cls.societe = env.company
    cls.groupe_concierges = env["res.groups"].create({"name": "Concierges (essai QR)"})
    cls.gestion = new_test_user(env, login="qr_gestion", groups="base.group_user,bf_nfc.group_nfc_manager",
                                password="qr_gestion_mdp")
    cls.concierge = new_test_user(env, login="qr_concierge", groups="base.group_user",
                                  password="qr_concierge_mdp")
    cls.concierge.groups_id = [(4, cls.groupe_concierges.id)]
    cls.interne = new_test_user(env, login="qr_interne", groups="base.group_user",
                                password="qr_interne_mdp")
    cls.societe.bf_qr_groupe_ids = [(6, 0, cls.groupe_concierges.ids)]
    cls.lot = env["bf.qr.batch"].with_user(cls.gestion).create({
        "name": "Lot d'essai", "prefixe": "TST", "quantite": 10})
    cls.lot.action_generer()
    cls.etiquettes = cls.lot.tag_ids.sorted("qr_numero")
    cls.geste_open = env.ref("bf_nfc.gesture_open")
    cls.geste_url = env.ref("bf_nfc.gesture_url")
    cls.geste_note = env.ref("bf_nfc.gesture_note")
    cls.partenaire = env["res.partner"].create({"name": "Salle B-204 (essai)"})
