"""Un appel n'est pas une conversation.

``call.archive.call`` ne porte aucun numéro de téléphone et son ``thread_id`` est
obligatoire : le fil est la seule case où le numéro d'un appel peut vivre. Le
module s'en servait donc comme d'un porteur, et un simple appel ouvrait une
« conversation » ou ramenait dans la boîte celle qu'on venait d'archiver. Sur une
base réelle, la moitié des fils n'existaient que pour ça.

Ce que ces essais tiennent :

1. un appel ne désarchive JAMAIS un fil, le message si ;
2. le fil qu'un appel doit ouvrir naît archivé, celui d'un message naît actif ;
3. la Messagerie ne liste pas les fils muets d'appels, ni dans la boîte ni dans
   les archives, mais garde le fil neuf qu'on vient d'ouvrir pour composer ;
4. l'import d'un journal d'appels retrouve un fil archivé au lieu de buter sur la
   contrainte d'unicité, qui couvre aussi les archivés.
"""

from io import BytesIO

from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("bf_sms_archive", "post_install", "-at_install")
class TestAppelsSansConversation(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # base.group_user en plus du groupe SMS : l'import apparie les contacts,
        # et un usager qui ne peut pas lire res.partner n'irait jamais jusque-là.
        cls.user = new_test_user(
            cls.env, login="sms_appels",
            groups="base.group_user,bf_sms_archive.group_sms_user",
        )
        cls.Thread = cls.env["sms.archive.thread"]
        cls.Call = cls.env["call.archive.call"]
        cls.Msg = cls.env["sms.archive.message"]

    def _fil(self, phone, active=True, **extra):
        vals = {
            "phone_normalized": phone,
            "owner_id": self.user.id,
            "active": active,
        }
        vals.update(extra)
        return self.Thread.create(vals)

    def _appel(self, phone, date_ms="1757800000000", **kw):
        return self.Call._ingest_one(
            phone_raw=phone, owner_id=self.user.id, call_type=kw.get("call_type", "incoming"),
            date_ms=date_ms, duration=kw.get("duration", 12),
            contact_name=kw.get("contact_name"), batch_id="essai",
        )

    def _message(self, phone, date_ms="1757800000000", body="Bonjour"):
        return self.Msg._ingest_one(
            phone_raw=phone, owner_id=self.user.id, direction="in",
            body=body, date_ms=date_ms, batch_id="essai",
        )

    # ── 1. Désarchivage ────────────────────────────────────────────

    def test_appel_ne_desarchive_pas_un_fil(self):
        fil = self._fil("+15145551001", active=False)
        self._appel("+15145551001")
        self.assertFalse(
            fil.with_context(active_test=False).read(["active"])[0]["active"],
            "un appel a ramené dans la boîte un fil qu'on avait archivé",
        )

    def test_message_desarchive_toujours_un_fil(self):
        fil = self._fil("+15145551002", active=False)
        self._message("+15145551002")
        self.assertTrue(
            fil.with_context(active_test=False).read(["active"])[0]["active"],
            "un message doit ramener la conversation dans la boîte",
        )

    def test_appel_puis_message_le_fil_revient(self):
        """L'appel laisse le fil archivé, le message qui suit le rouvre."""
        fil = self._fil("+15145551003", active=False)
        self._appel("+15145551003")
        self._message("+15145551003")
        self.assertTrue(fil.with_context(active_test=False).read(["active"])[0]["active"])

    # ── 2. Naissance du fil ────────────────────────────────────────

    def test_fil_ne_d_un_appel_nait_archive(self):
        rec, cree = self._appel("+15145551004")
        self.assertTrue(cree)
        fil = rec.thread_id
        self.assertFalse(fil.active, "le fil ouvert par un appel doit naître archivé")
        self.assertEqual(fil.phone_normalized, "+15145551004")
        self.assertEqual(fil.call_count, 1)

    def test_fil_ne_d_un_message_nait_actif(self):
        rec, cree = self._message("+15145551005")
        self.assertTrue(cree)
        self.assertTrue(rec.thread_id.active)

    def test_le_journal_lit_le_fil_archive(self):
        """Le fil archivé reste le porteur du numéro : rien ne se perd."""
        rec, _ = self._appel("+15145551006", contact_name="Garage")
        journal = self.Call.search_read(
            [("id", "=", rec.id)], ["thread_id", "call_type", "duration"])
        self.assertEqual(len(journal), 1)
        self.assertEqual(
            rec.thread_id.with_context(active_test=False).phone_normalized,
            "+15145551006",
        )

    # ── 3. Messagerie ──────────────────────────────────────────────

    def _fils_listes(self, archived=False):
        rows = self.Thread.with_user(self.user).get_messenger_threads(archived=archived)
        return {r["id"] for r in rows}

    def test_messagerie_ignore_les_fils_muets(self):
        muet, _ = self._appel("+15145551007")
        parlant = self._fil("+15145551008")
        self._message("+15145551008")
        self.assertIn(parlant.id, self._fils_listes())
        self.assertNotIn(muet.thread_id.id, self._fils_listes())
        self.assertNotIn(muet.thread_id.id, self._fils_listes(archived=True),
                         "un fil muet n'a pas plus sa place dans les archives")

    def test_messagerie_garde_un_fil_neuf_sans_message(self):
        """Ouvrir une conversation pour composer ne doit pas la faire disparaître."""
        ligne = self.env["sms.archive.line"].create({
            "label": "Essai", "did": "5145550009", "owner_id": self.user.id,
        })
        fil_id = self.Thread.with_user(self.user).start_conversation(
            ligne.id, "+15145551009")
        self.assertIn(fil_id, self._fils_listes())

    def test_messagerie_garde_un_fil_qui_a_des_deux(self):
        """Une vraie conversation où on s'est aussi parlé au téléphone reste là."""
        fil = self._fil("+15145551010")
        self._message("+15145551010")
        self._appel("+15145551010")
        self.assertIn(fil.id, self._fils_listes())

    # ── 4. Import d'un journal d'appels ────────────────────────────

    def test_import_appels_retrouve_un_fil_archive(self):
        """La contrainte d'unicité couvre les archivés : l'import doit les voir.

        Avec la quasi-totalité des fils archivés en production, un préchargement « actifs
        seulement » butait sur UNIQUE(phone_normalized, owner_id) au premier
        numéro déjà connu, et la transaction emportait tout le lot."""
        fil = self._fil("+15145551011", active=False)
        xml = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<calls count="1" backup_set="essai-appels">'
            b'<call number="+15145551011" duration="42" date="1757800000000"'
            b' type="2" presentation="1" contact_name="(Unknown)" />'
            b'</calls>'
        )
        wizard = self.env["sms.archive.import.wizard"].with_user(self.user).create({})
        stats = wizard._parse_and_import_calls(BytesIO(xml))

        self.assertEqual(stats["created"], 1)
        self.assertEqual(
            self.Thread.with_context(active_test=False).search_count(
                [("phone_normalized", "=", "+15145551011"),
                 ("owner_id", "=", self.user.id)]),
            1,
            "l'import a ouvert un second fil pour un numéro déjà connu",
        )
        # ⚠️ pas de recherche par lot d'import : `backup_set` vit sur la balise
        # racine, dont l'événement `end` n'arrive qu'APRÈS les appels, si bien
        # que `import_batch_id` reste vide sur un import XML.
        appel = self.Call.search([("thread_id", "=", fil.id)])
        self.assertEqual(len(appel), 1)
        self.assertEqual(appel.duration, 42)
        self.assertFalse(fil.with_context(active_test=False).read(["active"])[0]["active"])

    def test_import_appels_ouvre_un_fil_archive(self):
        """Un numéro inconnu importé depuis un journal d'appels naît archivé."""
        xml = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<calls count="1" backup_set="essai-appels-neuf">'
            b'<call number="+15145551012" duration="7" date="1757800001000"'
            b' type="1" presentation="1" contact_name="" />'
            b'</calls>'
        )
        wizard = self.env["sms.archive.import.wizard"].with_user(self.user).create({})
        stats = wizard._parse_and_import_calls(BytesIO(xml))
        self.assertEqual(stats["created"], 1)
        fil = self.Thread.with_context(active_test=False).search(
            [("phone_normalized", "=", "+15145551012"), ("owner_id", "=", self.user.id)])
        self.assertEqual(len(fil), 1)
        self.assertFalse(fil.active)
