"""Composition d'appel par le PBX — validation, garde-fous, trame AMI.

Aucun test ne touche un vrai PBX : ``bf.softphone.ami.originate`` est remplacé
par un espion, et l'analyse de trame est éprouvée contre une socket factice.
Ce qui compte ici n'est pas qu'un appel parte, c'est que les appels qui NE
doivent PAS partir soient refusés.
"""

from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged

from ..models import res_users as res_users_module


class FakeSocket:
    """Socket en mémoire : rend ``script`` par morceaux, garde ce qu'on envoie."""

    def __init__(self, script, chunk=7):
        self._pending = [script[i:i + chunk] for i in range(0, len(script), chunk)]
        self.sent = b""
        self.closed = False

    def recv(self, _size):
        return self._pending.pop(0) if self._pending else b""

    def sendall(self, data):
        self.sent += data

    def settimeout(self, _timeout):
        pass

    def close(self):
        self.closed = True


@tagged("post_install", "-at_install")
class TestSoftphoneOriginate(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = cls.env["res.users"].create({
            "name": "Poste d'essai",
            "login": "softphone-originate-test",
            "groups_id": [(4, cls.env.ref("bf_softphone.group_softphone_user").id)],
        })
        cls.user.sudo().write({
            "sip_extension": "1001",
            "sip_callback_number": "514 555-0142",
        })
        ICP = cls.env["ir.config_parameter"].sudo()
        ICP.set_param("bf_softphone.trunk", "trunk-sortant")
        ICP.set_param("bf_softphone.originate_context", "from-internal")

    def setUp(self):
        super().setUp()
        # Le compteur d'étranglement vit dans le module : sans purge, l'ordre des
        # tests changerait leur résultat.
        res_users_module._CALL_HITS.clear()

    def _as_user(self):
        return self.env["res.users"].with_user(self.user)

    # ── Trame AMI ─────────────────────────────────────────────────────
    def test_header_refuses_line_breaks(self):
        """Un CR/LF dans une valeur laisserait injecter une seconde action AMI."""
        Ami = self.env["bf.softphone.ami"]
        with self.assertRaises(UserError):
            Ami._header("5145550100\r\nAction: Command")
        with self.assertRaises(UserError):
            Ami._header("Jean\nQuiRitBien")

    def test_no_parameter_is_called_context(self):
        """Odoo confisque tout kwarg `context` d'un appel de modèle pour en faire
        le contexte d'environnement : une méthode qui en expose un ne peut pas
        être appelée à distance. Vécu à la QA."""
        import inspect
        signature = inspect.signature(
            type(self.env["bf.softphone.ami"]).originate)
        self.assertNotIn("context", signature.parameters)
        self.assertIn("dialplan_context", signature.parameters)

    def test_frame_skips_empty_values(self):
        frame = self.env["bf.softphone.ami"]._frame(
            "Originate", [("Channel", "PJSIP/1001"), ("Account", "")])
        self.assertIn(b"Channel: PJSIP/1001\r\n", frame)
        self.assertNotIn(b"Account:", frame)
        self.assertTrue(frame.endswith(b"\r\n\r\n"))

    def test_read_block_ignores_banner(self):
        """La bannière d'accueil arrive collée à la première réponse."""
        sock = FakeSocket(
            b"Asterisk Call Manager/8.0.0\r\n"
            b"Response: Success\r\nMessage: Authentication accepted\r\n\r\n"
        )
        block, rest = self.env["bf.softphone.ami"]._read_block(sock, b"")
        self.assertEqual(block["response"], "Success")
        self.assertEqual(block["message"], "Authentication accepted")
        self.assertEqual(rest, b"")

    def test_read_block_keeps_remainder(self):
        sock = FakeSocket(
            b"Response: Success\r\n\r\nResponse: Success\r\nActionID: bfsp-2\r\n\r\n")
        first, rest = self.env["bf.softphone.ami"]._read_block(sock, b"")
        second, _rest = self.env["bf.softphone.ami"]._read_block(sock, rest)
        self.assertEqual(first["response"], "Success")
        self.assertEqual(second["actionid"], "bfsp-2")

    # ── Validation des numéros ────────────────────────────────────────
    def test_rejects_non_nanpa(self):
        Users = self._as_user()
        for bad in ("911", "9911", "+33123456789", "411", "", "abc"):
            with self.assertRaises(UserError, msg="accepté à tort : %r" % bad):
                Users.softphone_originate(bad)

    def test_normalises_ten_digits(self):
        self.assertEqual(
            self.env["res.users"]._softphone_nanpa("(514) 555-0100"), "+15145550100")
        self.assertEqual(
            self.env["res.users"]._softphone_nanpa("15145550100"), "+15145550100")
        self.assertEqual(self.env["res.users"]._softphone_nanpa("911"), "")

    # ── Modes de sonnerie ─────────────────────────────────────────────
    def _spy_originate(self):
        return patch.object(
            type(self.env["bf.softphone.ami"]), "originate", return_value={"ok": True})

    def test_callback_rings_the_number_through_the_trunk(self):
        with self._spy_originate() as spy:
            result = self._as_user().softphone_originate("514 555-0100", ring="callback")
        kwargs = spy.call_args.kwargs
        self.assertEqual(kwargs["channel"], "PJSIP/15145550142@trunk-sortant")
        self.assertEqual(kwargs["exten"], "15145550100")
        self.assertEqual(result["ring"], "callback")
        self.assertEqual(result["number"], "+15145550100")

    def test_the_trunk_leg_never_presents_a_number_we_do_not_own(self):
        """Mesuré en production : l'afficheur portait le CORRESPONDANT, le
        fournisseur l'a remplacé par le DID du compte, le téléphone a sonné sous
        une identité d'affaires — donc personne n'a répondu et la deuxième jambe
        n'est jamais partie."""
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_softphone.outbound_cid", "514 555-0100")
        with self._spy_originate() as spy:
            result = self._as_user().softphone_originate("514 555-0143", ring="callback")
        callerid = spy.call_args.kwargs["callerid"]
        self.assertIn("5145550100", callerid)          # un DID à nous
        self.assertNotIn("<15145550143>", callerid)    # jamais le correspondant
        self.assertIn("555-0143", callerid)            # mais visible dans le nom
        self.assertEqual(result["shows_as"], "514 555-0100")

    def test_the_extension_leg_may_show_the_correspondent(self):
        """Cette jambe reste sur le PBX : aucun opérateur ne la réécrit."""
        with self._spy_originate() as spy:
            result = self._as_user().softphone_originate("514 555-0143", ring="extension")
        self.assertIn("<15145550143>", spy.call_args.kwargs["callerid"])
        self.assertEqual(result["shows_as"], "514 555-0143")

    def test_extension_rings_the_sip_endpoint(self):
        with self._spy_originate() as spy:
            self._as_user().softphone_originate("5145550100", ring="extension")
        self.assertEqual(spy.call_args.kwargs["channel"], "PJSIP/1001")

    def test_unknown_ring_mode_is_refused(self):
        with self.assertRaises(UserError):
            self._as_user().softphone_originate("5145550100", ring="carrier-pigeon")

    def test_callback_without_number_says_so(self):
        self.user.sudo().write({"sip_callback_number": False})
        self.user.partner_id.sudo().write({"mobile": False, "phone": False})
        with self.assertRaises(UserError):
            self._as_user().softphone_originate("5145550100", ring="callback")

    def test_config_falls_back_to_partner_mobile(self):
        self.user.sudo().write({"sip_callback_number": False})
        self.user.partner_id.sudo().write({"mobile": "514 555-0142"})
        config = self._as_user().softphone_call_config()
        self.assertEqual(config["callback_number"], "+15145550142")
        self.assertEqual(config["default_ring"], "callback")

    # ── Garde-fous ────────────────────────────────────────────────────
    def test_group_is_required(self):
        outsider = self.env["res.users"].create({
            "name": "Sans téléphone", "login": "softphone-outsider"})
        with self.assertRaises(AccessError):
            self.env["res.users"].with_user(outsider).softphone_originate("5145550100")

    def test_throttle_stops_a_loop(self):
        Users = self._as_user()
        with self._spy_originate():
            for _i in range(res_users_module._CALL_MAX):
                Users.softphone_originate("5145550100")
            with self.assertRaises(UserError):
                Users.softphone_originate("5145550100")

    def test_call_is_journalled(self):
        Call = self.env["call.archive.call"]
        before = Call.sudo().search_count([])
        with self._spy_originate():
            result = self._as_user().softphone_originate("5145550100")
        self.assertEqual(Call.sudo().search_count([]), before + 1)
        record = Call.sudo().browse(result["call_id"])
        self.assertEqual(record.call_type, "outgoing")
        self.assertEqual(record.import_batch_id, "softphone")

    def test_disabled_when_ami_is_not_configured(self):
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("bf_softphone.ami_host", "")
        ICP.set_param("bf_softphone.ami_user", "")
        self.assertFalse(self._as_user().softphone_call_config()["enabled"])
