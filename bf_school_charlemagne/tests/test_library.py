"""The library alone: the XML of the guide, the reports, OpenPGP and SFTP.

Pure Python, so it also runs outside Odoo (`python3 -m unittest`, with the module folder on the path).
The SFTP tests talk to a real `sftp-server`, the OpenPGP ones to a real `gpg`; each is skipped when its
program is not on the machine.
"""
import os
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

try:
    from odoo.tests import BaseCase as Base, tagged
    from odoo.addons.bf_school_charlemagne.charlemagne import definitions, fic, gpg, reports, sftp, xml_spec
except ImportError:  # outside Odoo
    Base = unittest.TestCase

    def tagged(*_args):
        return lambda cls: cls
    from charlemagne import definitions, fic, gpg, reports, sftp, xml_spec

SFTP_SERVER = next((p for p in ("/usr/lib/openssh/sftp-server", "/usr/lib/ssh/sftp-server",
                                "/usr/libexec/openssh/sftp-server") if os.path.exists(p)), None)

RJ = {"CD_PERMN": "ABCD12345678", "CD_ORGNS": "689001", "CD_COURS": "132406", "AN_OBTEN_RESLT": "2027",
      "SESN_OBTEN_RESLT": "06", "NO_GROUP_ELEVE": "01", "NOTE_ECOLE_BRUT": "77"}


@tagged("post_install", "-at_install")
class TestXml(Base):

    def build(self, kind, code, data):
        root, errors = xml_spec.build_transaction(definitions.TRANSACTIONS[(kind, code)], data, kind)
        return root, [error[:3] for error in errors]  # (number, tag, text); the key and parameters are for the application

    def test_a_result_follows_the_guides_elements_in_order(self):
        root, errors = self.build("RJ", "CRE", RJ)
        self.assertEqual(errors, [])
        self.assertEqual(root.tag, "CHSIRERL_RESLT_LOCAL_RJ_CRE")
        self.assertEqual([child.tag for child in root], list(RJ))

    def test_the_order_is_the_guides_whatever_the_order_of_the_data(self):
        root, _errors = self.build("RJ", "CRE", dict(reversed(list(RJ.items()))))
        self.assertEqual([child.tag for child in root], list(RJ))

    def test_what_is_missing_or_too_long_or_not_a_number_is_said(self):
        _root, errors = self.build("RJ", "CRE", dict(RJ, CD_PERMN="ABCD1234567890123", AN_OBTEN_RESLT="20x7", CD_COURS=""))
        by_tag = {tag: number for number, tag, _text in errors}
        self.assertEqual(by_tag, {"CD_PERMN": "CHA-600801", "AN_OBTEN_RESLT": "CHA-600801", "CD_COURS": "LOCAL-001"})

    def test_a_declaration_reports_format_errors_under_its_own_number(self):
        _root, errors = self.build("DJ", "CRE", {"CD_PERMN": "X" * 13})
        self.assertIn(("CHA-610101", "CD_PERMN"), [(n, t) for n, t, _x in errors])

    def test_an_unknown_element_is_refused(self):
        _root, errors = self.build("RJ", "CRE", dict(RJ, SURPRISE="1"))
        self.assertIn("LOCAL-003", [number for number, _tag, _text in errors])

    def test_dates_are_real_dates(self):
        _root, errors = self.build("DJ", "CRE", {"DT_FIN_FREQN": "2027-02-30"})
        self.assertIn("DT_FIN_FREQN", [tag for _n, tag, _t in errors])
        _root, errors = self.build("DJ", "CRE", {"DT_FIN_FREQN": "2027-02-28"})
        self.assertNotIn("DT_FIN_FREQN", [tag for _n, tag, _t in errors])

    def test_latin1_look_alikes_replace_what_latin1_cannot_carry(self):
        root, errors = self.build("DJ", "CRE", {"NOM": "L’Heureux", "PRENM": "Œdipe"})
        self.assertEqual([e for e in errors if e[1] in ("NOM", "PRENM")], [])
        self.assertEqual(root.find("NOM").text, "L'Heureux")
        self.assertEqual(root.find("PRENM").text, "OEdipe")
        _root, errors = self.build("DJ", "CRE", {"NOM": "中"})
        self.assertIn("LOCAL-004", [number for number, tag, _t in errors if tag == "NOM"])

    def test_a_structure_nested_in_a_declaration_is_built(self):
        data = {"CHFFREJS_DECLR_JEUNE_SPECF": {"IND_ELEVE_PENSN": "N", "GROUP_ELEVE": "1"}}
        root, errors = self.build("DJ", "CRE", data)
        self.assertNotIn("CHFFREJS_DECLR_JEUNE_SPECF", [tag for _n, tag, _t in errors])
        self.assertEqual(root.find("CHFFREJS_DECLR_JEUNE_SPECF/IND_ELEVE_PENSN").text, "N")

    def test_the_file_is_iso_8859_1_and_starts_as_the_guide_says(self):
        root, _errors = self.build("RJ", "CRE", RJ)
        blob = xml_spec.build_command("689000", "X", [{
            "service": "TRA", "organism": "689001", "type": "RJ", "lot": {"nolot": 7, "responsible": "Gaëlle Côté"},
            "transactions": [{"code": "CRE", "id": "ABCD12345678-RJCRE", "organism": "689001", "root": root}]}])
        self.assertTrue(blob.startswith(b'<?xml version="1.0" encoding="ISO-8859-1"?>'))
        self.assertIn("Gaëlle Côté".encode("latin-1"), blob)
        tree = ET.fromstring(blob)
        self.assertEqual(tree.tag, "commande")
        self.assertEqual(tree.findtext("sigleSysteme"), "Charlemagne")
        self.assertEqual(tree.findtext("codeOrganismeTransmetteur"), "689000")
        demand = tree.find("demande")
        self.assertEqual((demand.get("codeTransactionDemandeService"), demand.findtext("typeTransaction"),
                          demand.findtext("modeTraitementMaj"), demand.findtext("nbTransactionsSystemeTransmises")),
                         ("TRA", "RJ", "P", "1"))
        transaction = demand.find("transactionSysteme")
        self.assertEqual((transaction.get("codeTransactionSysteme"), transaction.get("idTransaction")),
                         ("CRE", "ABCD12345678-RJCRE"))

    def test_a_transmission_holds_999_requests_at_most(self):
        with self.assertRaises(ValueError):
            xml_spec.build_command("689000", "X", [{"service": "TRA", "type": "RJ", "lot": {}, "transactions": []}] * 1000)

    def test_a_request_for_a_file_is_built_from_its_parameters(self):
        spec = fic.FIC["OCC"][1]
        root, errors = xml_spec.build_transaction(spec, {"AN_EPREU": "2027", "SESN_EPREU": "06"}, "FI")
        self.assertEqual((errors, root.tag), ([], "TRANSACTION_FI_OCC"))
        _root, errors = xml_spec.build_transaction(spec, {"AN_EPREU": "2027"}, "FI")
        self.assertEqual([error[1] for error in errors], ["SESN_EPREU"])

    def test_an_empty_tag_takes_an_element_off_only_in_a_modification_of_an_exam_result(self):
        """Guide 6.29: to take an element off a result, a modification sends the tag empty."""
        data = {"CD_PERMN": "ABCD12345678", "CD_ORGNS": "689001", "CD_COURS": "087304", "AN_EPREU_DNR": "2027",
                "SESN_EPREU_DNR": "06", "NO_GROUP_ELEVE": "01", "NOTE_CALC_PART_OBJEC": "", "NOTE_ECOLE_BRUT": "70"}
        root, errors = xml_spec.build_transaction(definitions.TRANSACTIONS[("UJ", "MOD")], data, "UJ", "MOD")
        self.assertEqual(errors, [])
        self.assertIsNotNone(root.find("NOTE_CALC_PART_OBJEC"))
        self.assertFalse(root.findtext("NOTE_CALC_PART_OBJEC"))
        self.assertEqual(root.findtext("NOTE_ECOLE_BRUT"), "70")
        self.assertIn(b"<NOTE_CALC_PART_OBJEC", ET.tostring(root))
        creation, _errors = xml_spec.build_transaction(definitions.TRANSACTIONS[("UJ", "CRE")], data, "UJ", "CRE")
        self.assertIsNone(creation.find("NOTE_CALC_PART_OBJEC"), "a creation sends what it has")
        root, errors = xml_spec.build_transaction(definitions.TRANSACTIONS[("UJ", "MOD")], dict(data, CD_PERMN=""), "UJ", "MOD")
        self.assertEqual([e[:2] for e in errors], [("LOCAL-001", "CD_PERMN")], "an element the Ministry requires stays required")
        root, errors = xml_spec.build_transaction(definitions.TRANSACTIONS[("UJ", "MOD")], data, "UJ")
        self.assertIsNone(root.find("NOTE_CALC_PART_OBJEC"), "no empty tag unless the transaction is said to be a modification")

    def test_every_definition_is_generated_whole(self):
        self.assertEqual(sorted(definitions.TRANSACTIONS), [
            ("DJ", "CRE"), ("DJ", "MOD"), ("DJ", "SUP"), ("IJ", "CRE"), ("IJ", "SUP"), ("RJ", "CRE"), ("RJ", "MOD"),
            ("UJ", "CRE"), ("UJ", "MOD")])
        tags = [c.tag for c in definitions.TRANSACTIONS[("DJ", "CRE")].children]
        self.assertEqual(tags[:5], ["CD_PERMN", "NOM", "PRENM", "AN_SCOLR_PPS", "CD_ORGNS"])
        self.assertIn("ANCIE_DT_DEBUT_FREQN", [c.tag for c in definitions.TRANSACTIONS[("DJ", "MOD")].children])
        self.assertEqual([c.tag for c in definitions.TRANSACTIONS[("DJ", "SUP")].children][-1], "DT_DEBUT_FREQN")


@tagged("post_install", "-at_install")
class TestReports(Base):

    COMMAND = b"""<?xml version="1.0" encoding="ISO-8859-1"?>
<ECH01020299 organismeEmetteur="Ministere" codeRapport="CHA01020299">
 <commande nomFichier="SYM_1.CHA" formatTransmission="XML" noCommande="4569">
  <codeTransactionCommande>COM</codeTransactionCommande><sigleSysteme>Charlemagne</sigleSysteme>
  <codeOrganismeTransmetteur>689000</codeOrganismeTransmetteur>
  <nbDemandesTransmises>2</nbDemandesTransmises><nbDemandesAcceptees>1</nbDemandesAcceptees>
  <nbDemandesRefusees>1</nbDemandesRefusees><nbDemandesSuspends>0</nbDemandesSuspends>
  <listeMessageCommande><messageCommande><noMessage>ECH-207002</noMessage><consequence>R</consequence>
  <message>Count differs</message></messageCommande></listeMessageCommande>
 </commande></ECH01020299>"""

    REQUEST = b"""<?xml version="1.0" encoding="ISO-8859-1"?>
<ECH01020298 noCommande="4569" nomFichier="SYM_1.CHA">
 <Demande><noDemande>9001</noDemande><typeTransaction>RJ</typeTransaction>
  <listeCompteurTransaction><compteurTransaction><nbTransactionsTransmises>3</nbTransactionsTransmises>
  </compteurTransaction></listeCompteurTransaction>
  <listeMessageDemande><messageDemande><noMessageDemande>CHA-1</noMessageDemande>
  <consequenceDemande>A</consequenceDemande><descriptionMessageDemande>Late</descriptionMessageDemande>
  </messageDemande></listeMessageDemande></Demande>
 <listeTransaction>
  <transactionSysteme><codeTransactionSysteme>CRE</codeTransactionSysteme>
   <identificationTransaction>A-RJCRE-1</identificationTransaction></transactionSysteme>
  <transactionSysteme><codeTransactionSysteme>CRE</codeTransactionSysteme>
   <identificationTransaction>B-RJCRE-2</identificationTransaction>
   <listeMessageTransactionSysteme><messageTransactionSysteme>
    <noMessageTransaction>CHA-600813</noMessageTransaction><consequenceTransaction>R</consequenceTransaction>
    <descriptionMessageTransactionSysteme>Already there</descriptionMessageTransactionSysteme>
   </messageTransactionSysteme></listeMessageTransactionSysteme></transactionSysteme>
  <transactionSysteme><codeTransactionSysteme>MOD</codeTransactionSysteme>
   <identificationTransaction>C-RJMOD-3</identificationTransaction>
   <listeMessageTransactionSysteme><messageTransactionSysteme>
    <noMessageTransaction>CHA-2</noMessageTransaction><consequenceTransaction>A</consequenceTransaction>
    <descriptionMessageTransactionSysteme>Noted</descriptionMessageTransactionSysteme>
   </messageTransactionSysteme></listeMessageTransactionSysteme></transactionSysteme>
 </listeTransaction></ECH01020298>"""

    def test_the_command_report(self):
        report = reports.parse_command_report(self.COMMAND)
        self.assertEqual((report["number"], report["requests"], report["accepted"], report["refused"]), ("4569", 2, 1, 1))
        self.assertEqual(report["messages"], [{"number": "ECH-207002", "consequence": "R", "text": "Count differs"}])

    def test_the_request_report_by_the_id_the_school_gave(self):
        report = reports.parse_request_report(self.REQUEST)
        self.assertEqual(sorted(report["transactions"]), ["A-RJCRE-1", "B-RJCRE-2", "C-RJMOD-3"])
        outcomes = {ident: reports.transaction_outcome(tx["messages"]) for ident, tx in report["transactions"].items()}
        self.assertEqual(outcomes, {"A-RJCRE-1": "accepted", "B-RJCRE-2": "rejected", "C-RJMOD-3": "accepted_message"})
        self.assertEqual(report["requests"][0]["messages"][0]["text"], "Late")

    def test_a_held_back_transaction_is_held_back_and_a_rejection_wins(self):
        self.assertEqual(reports.transaction_outcome([{"consequence": "S"}]), "suspended")
        self.assertEqual(reports.transaction_outcome([{"consequence": "A"}, {"consequence": "R"}]), "rejected")
        self.assertEqual(reports.transaction_outcome([]), "accepted")


@tagged("post_install", "-at_install")
@unittest.skipUnless(shutil.which("gpg"), "gpg is not installed")
class TestGpg(Base):

    @classmethod
    def setUpClass(cls):
        with gpg.Keyring() as school, gpg.Keyring() as ministry, gpg.Keyring() as other:
            cls.school_fpr = gpg.generate_test_key(school, "School <school@example.invalid>", "school-pass")
            cls.ministry_fpr = gpg.generate_test_key(ministry, "Ministry <mel@example.invalid>", "ministry-pass")
            cls.other_fpr = gpg.generate_test_key(other, "Other <other@example.invalid>", "other-pass")
            cls.school_secret = gpg.export_key(school, cls.school_fpr, secret=True)
            cls.school_public = gpg.export_key(school, cls.school_fpr)
            cls.ministry_secret = gpg.export_key(ministry, cls.ministry_fpr, secret=True)
            cls.ministry_public = gpg.export_key(ministry, cls.ministry_fpr)
            cls.other_secret = gpg.export_key(other, cls.other_fpr, secret=True)
            cls.other_public = gpg.export_key(other, cls.other_fpr)

    def _seal(self, data=b"<commande/>", signer_secret=None, signer_fpr=None, passphrase="school-pass"):
        with gpg.Keyring(passphrase) as ring:
            ring.import_key(signer_secret or self.school_secret)
            ring.import_key(self.ministry_public)
            return ring.sign_and_encrypt(data, self.ministry_fpr, signer_fpr or self.school_fpr)

    def test_the_file_is_binary_and_readable_only_by_the_ministry(self):
        sealed = self._seal(b"<commande>identities</commande>")
        self.assertNotIn(b"identities", sealed)
        self.assertFalse(sealed.startswith(b"-----BEGIN"), "binary mode, as the guide asks")
        with gpg.Keyring("ministry-pass") as ring:
            ring.import_key(self.ministry_secret)
            ring.import_key(self.school_public)
            plain, ok, by = ring.decrypt_and_verify(sealed, signer=self.school_fpr)
        self.assertEqual((plain, ok), (b"<commande>identities</commande>", True))
        self.assertEqual(by, self.school_fpr)

    def test_a_signature_by_another_key_is_not_the_schools(self):
        sealed = self._seal(signer_secret=self.other_secret, signer_fpr=self.other_fpr, passphrase="other-pass")
        with gpg.Keyring("ministry-pass") as ring:
            ring.import_key(self.ministry_secret)
            ring.import_key(self.school_public)
            _plain, ok, _by = ring.decrypt_and_verify(sealed, signer=self.school_fpr)
        self.assertFalse(ok)

    def test_a_good_signature_of_another_key_the_ministry_knows_is_not_the_schools(self):
        sealed = self._seal(signer_secret=self.other_secret, signer_fpr=self.other_fpr, passphrase="other-pass")
        with gpg.Keyring("ministry-pass") as ring:
            ring.import_key(self.ministry_secret)
            ring.import_key(self.school_public)
            ring.import_key(self.other_public)  # the signature checks out: it is only not the school's
            _plain, ok, by = ring.decrypt_and_verify(sealed, signer=self.school_fpr)
            self.assertFalse(ok)
            self.assertEqual(by, self.other_fpr)
            self.assertTrue(ring.decrypt_and_verify(sealed, signer=self.other_fpr)[1])

    def test_a_signature_of_a_key_that_has_expired_is_not_the_ministrys(self):
        class LaterKeyring(gpg.Keyring):
            """The same ring three days on: the keys of the tests last a day."""

            def _run(self, args, data=None, binary=False):
                later = (datetime.now(timezone.utc) + timedelta(days=3)).strftime("%Y%m%dT%H%M%S")
                return super()._run(["--faked-system-time", later] + list(args), data, binary)

        sealed = self._seal()
        with gpg.Keyring("ministry-pass") as ring:
            ring.import_key(self.ministry_secret)
            ring.import_key(self.school_public)
            self.assertTrue(ring.decrypt_and_verify(sealed, signer=self.school_fpr)[1], "valid today")
        with LaterKeyring("ministry-pass") as ring:
            ring.import_key(self.ministry_secret)
            ring.import_key(self.school_public)
            plain, ok, _by = ring.decrypt_and_verify(sealed, signer=self.school_fpr)
            self.assertEqual(plain, b"<commande/>")
            self.assertFalse(ok, "an expired key's signature is not trusted")

    def test_a_wrong_passphrase_refuses_to_decrypt_even_after_the_second_try(self):
        sealed = self._seal()
        with gpg.Keyring("not-the-passphrase") as ring:
            ring.import_key(self.ministry_secret)
            ring.import_key(self.school_public)
            with self.assertRaises(gpg.GpgError):
                ring.decrypt_and_verify(sealed, signer=self.school_fpr)

    def test_a_file_encrypted_for_someone_else_is_not_read(self):
        sealed = self._seal()
        with gpg.Keyring("other-pass") as ring:
            ring.import_key(self.other_secret)
            with self.assertRaises(gpg.GpgError):
                ring.decrypt_and_verify(sealed)

    def test_a_wrong_passphrase_refuses_to_sign(self):
        with self.assertRaises(gpg.GpgError):
            self._seal(passphrase="wrong")

    def test_the_ring_leaves_nothing_behind(self):
        with gpg.Keyring("school-pass") as ring:
            ring.import_key(self.school_secret)
            home = ring.home
            self.assertTrue(os.path.isdir(home))
        self.assertFalse(os.path.exists(home))

    def test_a_ring_of_the_two_keys_tells_which_is_secret(self):
        with gpg.Keyring("school-pass") as ring:
            ring.import_key(self.school_secret)
            ring.import_key(self.ministry_public)
            self.assertEqual(ring.fingerprints(secret=True)[0], self.school_fpr)
            self.assertIn(self.ministry_fpr, ring.fingerprints())

    def test_a_garbage_key_is_refused(self):
        with gpg.Keyring() as ring, self.assertRaises(gpg.GpgError):
            ring.import_key("not a key")


@tagged("post_install", "-at_install")
@unittest.skipUnless(SFTP_SERVER, "sftp-server is not installed")
class TestSftp(Base):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="bft")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "INTRANT"))
        os.makedirs(os.path.join(self.root, "LISTE"))
        self.local = os.path.join(self.root, "local.bin")
        with open(self.local, "wb") as handle:
            handle.write(os.urandom(150000))

    def client(self):
        return sftp.SftpClient([SFTP_SERVER, "-d", self.root])

    def test_a_file_goes_up_and_comes_back_intact(self):
        with self.client() as c:
            c.put(self.local, "INTRANT/a.CHA")
            self.assertEqual(c.listdir("INTRANT"), ["a.CHA"])
            back = os.path.join(self.root, "back.bin")
            self.assertEqual(c.get("INTRANT/a.CHA", back), 150000)
        with open(self.local, "rb") as a, open(back, "rb") as b:
            self.assertEqual(a.read(), b.read())

    def test_the_name_appears_only_complete_and_a_second_upload_replaces(self):
        with self.client() as c:
            c.put(self.local, "INTRANT/a.CHA")
            c.put(self.local, "INTRANT/a.CHA")
            self.assertEqual(c.listdir("INTRANT"), ["a.CHA"], "no .part left behind")

    def test_remove_and_the_server_s_own_errors(self):
        with self.client() as c:
            c.put(self.local, "INTRANT/a.CHA")
            c.remove("INTRANT/a.CHA")
            self.assertEqual(c.listdir("INTRANT"), [])
            with self.assertRaises(sftp.SftpError) as caught:
                c.get("INTRANT/none", os.path.join(self.root, "x"))
            self.assertEqual(caught.exception.code, sftp.NO_SUCH_FILE)
            with self.assertRaises(sftp.SftpError):
                c.listdir("NOPE")


@tagged("post_install", "-at_install")
class TestSftpArguments(Base):
    """What the library refuses before it speaks to any server: these need no `sftp-server`."""

    def test_a_server_that_does_not_start_is_an_error_not_a_hang(self):
        with self.assertRaises(sftp.SftpError):
            with sftp.SftpClient(["/nonexistent/sftp-server"]):
                pass

    def test_credentials_are_not_sent_to_a_host_nobody_checked(self):
        with self.assertRaises(sftp.SftpError):
            sftp.SshSftp("echanges.example.invalid", 22, "user", password="x", known_host="")

    def test_the_ssh_command_checks_the_host_key_and_ends_the_options_before_the_host(self):
        client = sftp.SshSftp("echanges.example.invalid", 2222, "689001", password="x", known_host="h ssh-ed25519 AAAA")
        try:
            command, env = client._prepare()
            self.assertIn("StrictHostKeyChecking=yes", command)
            self.assertEqual(command[-3:], ["--", "689001@echanges.example.invalid", "sftp"])
            self.assertEqual(command[command.index("-p") + 1], "2222")
            self.assertNotIn("x", command, "a password never goes on a command line")
        finally:
            client.__exit__()

    def test_a_host_or_an_account_is_never_taken_for_an_ssh_option(self):
        for host, user in (("-oProxyCommand=x", "user"), ("a b", "user"), ("host;rm", "user"),
                           ("echanges.example.invalid", "-oProxyCommand=x"), ("echanges.example.invalid", "")):
            with self.assertRaises(sftp.SftpError, msg=(host, user)):
                sftp.SshSftp(host, 22, user, password="x", known_host="h ssh-ed25519 AAAA")
        sftp.SshSftp("echanges.example.invalid", 22, "689001", password="x", known_host="h ssh-ed25519 AAAA")

    def test_a_host_holds_no_at_sign_because_ssh_cuts_the_target_at_the_last_one(self):
        """`ssh -- ABC123@h@evil.example` connects to evil.example as `ABC123@h`: the host cannot carry the cut."""
        for host in ("h@evil.example", "evil.example@", "@evil.example"):
            with self.assertRaises(sftp.SftpError, msg=host):
                sftp.SshSftp(host, 22, "ABC123", password="x", known_host="h ssh-ed25519 AAAA")
        client = sftp.SshSftp("echanges.example.invalid", 22, "ABC123@h", password="x", known_host="h ssh-ed25519 AAAA")
        try:
            self.assertEqual(client._prepare()[0][-2], "ABC123@h@echanges.example.invalid", "the account may; the host decides")
        finally:
            client.__exit__()


if __name__ == "__main__":
    unittest.main()
