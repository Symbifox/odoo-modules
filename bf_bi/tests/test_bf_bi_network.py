"""Garde réseau : aucune connexion de données ne joint une adresse interne.

Les essais HTTP passent par un vrai petit serveur sur 127.0.0.1 : la garde se juge sur
ce que la bibliothèque joint vraiment, pas sur une fonction remplacée.
"""
import datetime
import ipaddress
import os
import ssl
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import requests

from odoo.tests import BaseCase, common as odoo_common, tagged
from odoo.tools import config

from odoo.addons.bf_bi.models import bf_bi_network as network


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    seen = []
    posted = []
    second_port = None

    def log_message(self, *args):
        pass

    def do_GET(self):
        _Handler.seen.append((self.path, dict(self.headers)))
        port = self.server.server_address[1]
        if self.path.startswith("/ok"):
            self._send(200, b'{"ok": true}', {"Content-Length": "12"})
        elif self.path == "/same-host":
            self._send(302, b"", {"Location": "/ok"})
        elif self.path.startswith("/to-second"):
            self._send(302, b"", {"Location": "http://127.0.0.2:%s/ok-second" % _Handler.second_port})
        elif self.path.startswith("/to-backslash"):
            # urlparse y lit 127.0.0.1 (même origine), requests se connecte à 127.0.0.2.
            self._send(302, b"", {"Location": "http://127.0.0.2:%s\\@127.0.0.1:%s/ok" % (_Handler.second_port, port)})
        elif self.path == "/to-metadata":
            self._send(302, b"", {"Location": "http://169.254.169.254/latest/meta-data/"})
        elif self.path == "/to-other-loopback":
            self._send(302, b"", {"Location": "http://127.0.0.2:%s/ok" % port})
        elif self.path == "/to-file":
            self._send(302, b"", {"Location": "file:///etc/passwd"})
        elif self.path == "/loop":
            self._send(302, b"", {"Location": "/loop"})
        elif self.path == "/big-declared":
            self._send(200, b"x" * 2048, {"Content-Length": "2048"})
        elif self.path == "/slow":
            self.send_response(200)
            self.end_headers()
            for _ in range(30):  # un octet par 0,1 s : jamais 15 s de silence, mais 3 s au total
                self.wfile.write(b"x")
                self.wfile.flush()
                time.sleep(0.1)
        elif self.path == "/slow-big":
            self.send_response(200)
            self.end_headers()
            try:
                for _ in range(8):  # 70 000 octets toutes les 0,4 s : plus d'un morceau lu à chaque fois
                    self.wfile.write(b"x" * 70000)
                    self.wfile.flush()
                    time.sleep(0.4)
            except OSError:
                pass
        elif self.path == "/drip-forever":
            self.send_response(200)
            self.end_headers()
            self._drip(b"x")
        elif self.path == "/drip-headers":
            self.wfile.write(b"HTTP/1.0 200 OK\r\nX-Lent: ")
            self._drip(b"a")
        elif self.path == "/declared-huge":
            self.send_response(200)
            self.send_header("Content-Length", str(10 ** 9))
            self.end_headers()
            self._drip(b"x")
        elif self.path == "/big-streamed":
            self._send(200, b"x" * 2048, {})  # sans longueur : la fin de la connexion clôt le corps
        else:
            self._send(404, b"secret interne", {})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        _Handler.posted.append(self.rfile.read(length))
        self.do_GET()

    def _drip(self, byte):
        # Un octet toutes les 0,3 s, sans jamais fermer : seul le client peut y mettre fin.
        try:
            for _ in range(600):
                self.wfile.write(byte)
                self.wfile.flush()
                time.sleep(0.3)
        except OSError:
            pass

    def _send(self, status, body, headers):
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)


@tagged("post_install", "-at_install")
class TestBfBiNetwork(BaseCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.addClassCleanup(cls.server.server_close)
        cls.addClassCleanup(cls.server.shutdown)
        cls.tls_port = cls._https_server().server_address[1]
        # Un second serveur sur une autre adresse de bouclage : une autre origine.
        second = ThreadingHTTPServer(("127.0.0.2", 0), _Handler)
        _Handler.second_port = second.server_address[1]
        threading.Thread(target=second.serve_forever, daemon=True).start()
        cls.addClassCleanup(second.server_close)
        cls.addClassCleanup(second.shutdown)

    @classmethod
    def _https_server(cls):
        """Même serveur, en TLS, avec un certificat autosigné pour 127.0.0.1."""
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID

        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
                .not_valid_after(now + datetime.timedelta(hours=1))
                .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                               critical=False)
                .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                .sign(key, hashes.SHA256()))
        folder = tempfile.mkdtemp(prefix="bf_bi_tls_")
        cls.certfile = os.path.join(folder, "cert.pem")
        keyfile = os.path.join(folder, "key.pem")
        with open(cls.certfile, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))
        with open(keyfile, "wb") as f:
            f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cls.certfile, keyfile)
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.addClassCleanup(server.server_close)
        cls.addClassCleanup(server.shutdown)
        return server

    def setUp(self):
        super().setUp()
        _Handler.seen.clear()
        # Le cadre d'essai d'Odoo bloque toute requête hors 127.0.0.1 AVANT notre garde : on
        # lui retire la main, pour que ce soit bien la garde qui refuse (en prod, il n'existe pas).
        unblock = patch.object(requests.sessions.Session, "send", odoo_common._super_send)
        unblock.start()
        self.addCleanup(unblock.stop)

    def allow(self, value):
        return patch.dict(config.options, {"bf_bi_allowed_private_hosts": value})

    def url(self, path):
        return "http://127.0.0.1:%s%s" % (self.port, path)

    # -- adresses ---------------------------------------------------------------

    def test_internal_addresses_are_refused(self):
        for address in ("127.0.0.1", "10.1.2.3", "172.17.0.2", "192.168.1.10", "169.254.169.254",
                        "100.64.0.1", "0.0.0.0", "224.0.0.1", "::1", "fd00::1", "fe80::1",
                        "::ffff:10.0.0.1", "::ffff:127.0.0.1"):
            with self.subTest(address=address), self.assertRaises(network.BlockedAddress):
                network.vet(address, 443)

    def test_public_addresses_pass(self):
        self.assertEqual(network.vet("8.8.8.8", 443), "8.8.8.8")
        self.assertEqual(network.vet("2606:4700:4700::1111", 443), "2606:4700:4700::1111")

    def test_a_name_with_one_private_address_is_refused(self):
        with patch.object(network, "resolve", return_value=["93.184.215.14", "10.0.0.7"]):
            with self.assertRaises(network.BlockedAddress):
                network.vet("api.example.com", 443)
        with patch.object(network, "resolve", return_value=["93.184.215.14"]):
            self.assertEqual(network.vet("api.example.com", 443), "93.184.215.14")

    def test_server_allowlist_by_name_and_by_network(self):
        with patch.object(network, "resolve", return_value=["172.18.0.5"]):
            with self.assertRaises(network.BlockedAddress):
                network.vet("grist-interne", 8484)
            with self.allow("grist-interne"):
                self.assertEqual(network.vet("GRIST-INTERNE.", 8484), "172.18.0.5")
            with self.allow("172.18.0.0/16"):
                self.assertEqual(network.vet("grist-interne", 8484), "172.18.0.5")
            with self.allow("10.0.0.0/8, autre-hote"), self.assertRaises(network.BlockedAddress):
                network.vet("grist-interne", 8484)

    def test_the_allowlist_is_never_read_from_the_database(self):
        # La seule exception vit dans odoo.conf : un administrateur du locataire écrit dans
        # ir.config_parameter, pas dans la configuration du serveur.
        import inspect
        source = inspect.getsource(network)
        self.assertNotIn("get_param", source)
        self.assertNotIn(".env", source)

    # -- HTTP : la garde dans la connexion même -----------------------------------------

    def test_http_to_loopback_is_refused_before_any_request(self):
        with self.assertRaises(network.BlockedAddress):
            network.http_get(self.url("/ok"))
        self.assertEqual(_Handler.seen, [], "aucune requête ne doit partir")

    def test_http_to_an_allowed_host_works(self):
        with self.allow("127.0.0.1"):
            status, body, _ctype = network.http_get(self.url("/ok"))
        self.assertEqual((status, body), (200, b'{"ok": true}'))

    def test_redirect_to_an_internal_address_is_refused(self):
        with self.allow("127.0.0.1/32"), self.assertRaises(network.BlockedAddress):
            network.http_get(self.url("/to-other-loopback"))

    def test_redirect_with_a_secret_stays_on_the_same_host(self):
        with self.allow("127.0.0.0/8"):
            status, _body, _ctype = network.http_get(
                self.url("/same-host"), headers={"X-Api-Key": "k"}, secret_bound=True)
            self.assertEqual(status, 200)
            with self.assertRaises(network.BlockedAddress):
                network.http_get(self.url("/to-other-loopback"), headers={"X-Api-Key": "k"}, secret_bound=True)
            # Sans secret, la redirection vers un autre hôte (permis) est suivie : rien n'y écoute.
            with self.assertRaises(requests.ConnectionError):
                network.http_get(self.url("/to-other-loopback"))

    def test_strip_mode_follows_the_redirect_without_the_secret(self):
        with self.allow("127.0.0.0/8"):
            status, body, _ctype = network.http_get(self.url("/to-second"), headers={"Authorization": "Bearer k"},
                                                    params={"key": "k"}, secret_bound=network.STRIP)
            self.assertEqual((status, body), (200, b'{"ok": true}'))
            first, second = _Handler.seen[-2], _Handler.seen[-1]
            self.assertEqual(first[1].get("Authorization"), "Bearer k")
            self.assertTrue(second[0].startswith("/ok-second"))
            self.assertNotIn("Authorization", second[1])
            self.assertNotIn("key=", second[0])
            # Sans le mode STRIP, la même redirection est refusée (le secret partirait).
            with self.assertRaises(network.BlockedAddress):
                network.http_get(self.url("/to-second"), headers={"Authorization": "Bearer k"}, secret_bound=True)

    def test_a_backslash_cannot_carry_the_secret_to_another_host(self):
        # L'origine est jugée comme la connexion la lit (urllib3) : le secret ne part pas vers 127.0.0.2.
        with self.allow("127.0.0.0/8"):
            with self.assertRaises(network.BlockedAddress):
                network.http_get(self.url("/to-backslash"), headers={"Authorization": "Basic k"}, secret_bound=True)
            self.assertEqual([path for path, _headers in _Handler.seen], ["/to-backslash"])
            # En mode STRIP, l'autre hôte est reconnu comme tel : suivi, mais sans le secret.
            _Handler.seen.clear()
            network.http_get(self.url("/to-backslash"), headers={"Authorization": "Basic k"},
                             params={"key": "k"}, secret_bound=network.STRIP)
            self.assertEqual(len(_Handler.seen), 2)
            self.assertNotIn("Authorization", _Handler.seen[1][1])
            self.assertNotIn("key=", _Handler.seen[1][0])

    def test_origin_is_read_like_the_connection(self):
        self.assertEqual(network.origin("http://a.test\\@b.test:8080/x").hostname, "a.test")
        self.assertEqual(network.origin("HTTPS://B.test:8443/x"), ("https", "b.test", 8443))
        self.assertEqual(network.origin("http://u:p@[::1]:8080/x"), ("http", "::1", 8080))

    def test_strip_mode_still_vets_each_hop(self):
        # Sans en-têtes ni paramètres, le saut repasse quand même la garde d'adresse.
        with self.allow("127.0.0.1"):
            for path in ("/to-metadata", "/to-second"):
                with self.subTest(path=path), self.assertRaises(network.BlockedAddress):
                    network.http_get(self.url(path), headers={"Authorization": "Bearer k"}, secret_bound=network.STRIP)

    def test_post_sends_the_form_and_never_follows_a_redirect(self):
        _Handler.posted.clear()
        with self.allow("127.0.0.1"):
            status, body, _ctype = network.http_post(self.url("/ok"), {"grant_type": "x", "secret": "s"})
            self.assertEqual((status, body), (200, b'{"ok": true}'))
            self.assertEqual(_Handler.posted[-1], b"grant_type=x&secret=s")
            with self.assertRaises(requests.TooManyRedirects):
                network.http_post(self.url("/same-host"), {"secret": "s"})
        self.assertEqual(sum(1 for path, _h in _Handler.seen if path == "/ok"), 1, "la redirection n'a pas été suivie")
        with self.assertRaises(network.BlockedAddress):
            network.http_post(self.url("/ok"), {"secret": "s"})  # la garde vaut aussi pour un POST

    def test_redirect_to_another_scheme_and_loops_are_refused(self):
        with self.allow("127.0.0.1"):
            with self.assertRaises(requests.TooManyRedirects):
                network.http_get(self.url("/to-file"))
            with self.assertRaises(requests.TooManyRedirects):
                network.http_get(self.url("/loop"))
        # La première requête et 5 redirections, pas une de plus (nombre écrit en dur : le
        # comparer à MAX_REDIRECTS laisserait passer une limite relevée).
        self.assertEqual(sum(1 for path, _headers in _Handler.seen if path == "/loop"), 6)

    def test_answers_are_capped(self):
        with self.allow("127.0.0.1"):
            for path in ("/big-declared", "/big-streamed"):
                with self.subTest(path=path), self.assertRaises(network.TooLarge):
                    network.http_get(self.url(path), max_bytes=1024)
            status, body, _ctype = network.http_get(self.url("/big-streamed"), max_bytes=4096)
            self.assertEqual((status, len(body)), (200, 2048))

    def test_a_dribbling_server_hits_the_total_timeout(self):
        with self.allow("127.0.0.1"), patch.object(network, "TOTAL_TIMEOUT", 1), \
                self.assertRaises(requests.Timeout):
            network.http_get(self.url("/slow"))

    def test_the_read_loop_keeps_the_deadline_without_the_timer(self):
        # Le corps arrive par morceaux de 64 Kio : la boucle de lecture juge l'échéance
        # elle-même, sans attendre le minuteur (retiré ici) ni la fin d'un corps de 3 s.
        sans_minuteur = lambda seconds, sockets: threading.Timer(seconds, lambda: None)
        with self.allow("127.0.0.1"), patch.object(network, "TOTAL_TIMEOUT", 1), \
                patch.object(network, "cut_after", sans_minuteur):
            debut = time.monotonic()
            with self.assertRaises(requests.Timeout):
                network.http_get(self.url("/slow-big"))
            self.assertLess(time.monotonic() - debut, 2.5)

    def test_a_server_that_never_closes_is_cut_at_the_deadline(self):
        # Le corps comme les en-têtes : chaque octet arrive bien avant le délai par lecture.
        for path in ("/drip-forever", "/drip-headers"):
            with self.subTest(path=path), self.allow("127.0.0.1"), patch.object(network, "TOTAL_TIMEOUT", 2):
                debut = time.monotonic()
                with self.assertRaises(requests.Timeout):
                    network.http_get(self.url(path))
                self.assertLess(time.monotonic() - debut, 6)

    def test_over_tls_too_a_server_that_never_closes_is_cut(self):
        # En HTTPS, l'enveloppe TLS détache le socket que crée la garde : le minuteur doit
        # quand même couper la connexion vivante.
        original = network._session

        def session_trusting_the_test_certificate():
            session = original()
            session.verify = self.certfile
            return session

        with self.allow("127.0.0.1"), patch.object(network, "_session", session_trusting_the_test_certificate):
            base = "https://127.0.0.1:%s" % self.tls_port
            self.assertEqual(network.http_get(base + "/ok")[:2], (200, b'{"ok": true}'))
            for path in ("/drip-forever", "/drip-headers"):
                with self.subTest(path=path), patch.object(network, "TOTAL_TIMEOUT", 2):
                    debut = time.monotonic()
                    with self.assertRaises(requests.Timeout):
                        network.http_get(base + path)
                    self.assertLess(time.monotonic() - debut, 6)

    def test_a_mapped_public_ipv4_is_judged_as_that_ipv4(self):
        # Même résultat avant et après le correctif de CVE-2024-4032 de Python.
        self.assertEqual(network.vet("::ffff:8.8.8.8", 443), "::ffff:8.8.8.8")
        with self.assertRaises(network.BlockedAddress):
            network.vet("::ffff:10.0.0.1", 443)

    def test_server_denylist_refuses_even_public_addresses(self):
        with patch.dict(config.options, {"bf_bi_denied_hosts": "8.8.8.0/24, hote-public.test"}):
            with self.assertRaises(network.BlockedAddress):
                network.vet("8.8.8.8", 443)
            with patch.object(network, "resolve", return_value=["93.184.215.14"]), \
                    self.assertRaises(network.BlockedAddress):
                network.vet("hote-public.test", 443)
            self.assertEqual(network.vet("1.1.1.1", 443), "1.1.1.1")
        # La liste de refus l'emporte sur la liste permise.
        with patch.dict(config.options, {"bf_bi_denied_hosts": "127.0.0.1", "bf_bi_allowed_private_hosts": "127.0.0.1"}), \
                self.assertRaises(network.BlockedAddress):
            network.vet("127.0.0.1", 80)

    def test_ipv6_prefixes_carrying_ipv4_are_refused(self):
        for address in ("64:ff9b::a00:1", "64:ff9b:1::a00:1", "2002:a00:1::1", "2001:0:4136:e378::1", "::a00:1"):
            with self.subTest(address=address), self.assertRaises(network.BlockedAddress):
                network.vet(address, 443)

    def test_secret_redirect_compares_the_port_too(self):
        from urllib.parse import urlparse as u
        same = network._same_origin_or_upgrade
        self.assertTrue(same(u("https://a.test/x"), u("https://a.test:443/y")))
        self.assertTrue(same(u("http://a.test/x"), u("https://a.test/y")))
        self.assertFalse(same(u("http://a.test/x"), u("https://a.test:8443/y")))
        self.assertFalse(same(u("http://a.test:8080/x"), u("https://a.test/y")))
        self.assertFalse(same(u("https://a.test/x"), u("https://b.test/y")))
        self.assertFalse(same(u("https://a.test/x"), u("http://a.test/y")))
        # Même schéma, autre port : un autre service, peut-être d'un autre maître.
        self.assertFalse(same(u("https://a.test/x"), u("https://a.test:8443/y")))
        self.assertFalse(same(u("http://a.test:8080/x"), u("http://a.test/y")))

    def test_an_announced_oversize_is_refused_before_reading(self):
        # Le serveur annonce 1 Go et distille : refus sur la longueur déclarée, tout de suite,
        # sans attendre l'échéance.
        with self.allow("127.0.0.1"), patch.object(network, "TOTAL_TIMEOUT", 5):
            debut = time.monotonic()
            with self.assertRaises(network.TooLarge):
                network.http_get(self.url("/declared-huge"))
            self.assertLess(time.monotonic() - debut, 2)

    def test_proxy_settings_of_the_environment_are_ignored(self):
        # Un mandataire joindrait l'hôte À NOTRE PLACE, hors de la garde.
        with self.allow("127.0.0.1"), patch.dict(os.environ, {"HTTP_PROXY": "http://10.9.9.9:3128",
                                                              "http_proxy": "http://10.9.9.9:3128"}):
            self.assertEqual(network.http_get(self.url("/ok"))[0], 200)

    def test_error_bodies_come_back_only_to_the_caller(self):
        with self.allow("127.0.0.1"):
            status, body, _ctype = network.http_get(self.url("/nope"))
        self.assertEqual(status, 404)
        self.assertEqual(body, b"secret interne")  # c'est l'appelant qui ne le montre pas
