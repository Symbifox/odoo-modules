"""L'empreinte d'une version d'avis atteste le texte RÉELLEMENT stocké.

Ce fichier verrouille le défaut qui a survécu des mois sans que rien ne le
signale : ``create()`` hachait la chaîne REÇUE, alors que le champ ``body`` est
un ``Html`` assaini à l'écriture. Un attribut en guillemets simples suffisait —
sans même changer la longueur du corps — pour que le sceau n'atteste plus aucun
texte. Mesuré en production sur plusieurs instances : la majorité des versions
d'avis étaient concernées, dont une part sans aucune empreinte.

⚠ Le test décisif est ``test_create_seals_stored_body_not_input``. Sans lui, la
régression se réintroduit silencieusement à la première refonte de ``create()``,
et personne ne le verra : rien dans l'interface ne rend une empreinte fausse
visible, et ``verify_integrity()`` n'est appelé par aucun rapport.

Ce fichier est volontairement IDENTIQUE entre ``cq_consent`` et
``privacy_consent`` : aucun ``env.ref()`` de module, aucun xmlid. Les tests
propres au rescellement se neutralisent d'eux-mêmes là où les deux champs
n'existent pas encore.
"""

from odoo.tests import TransactionCase, tagged


@tagged("privacy_consent", "privacy_notice_version", "privacy_integrity")
class TestNoticeVersionIntegrity(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Version = cls.env["privacy.notice.version"]
        cls.purpose = cls.env["privacy.purpose"].create({
            "code": "INTEGRITY_TEST",
            "name": "Finalité d'essai — intégrité",
            "default_validity_days": 365,
        })
        cls.notice = cls.env["privacy.notice"].create({
            "name": "Avis d'essai — intégrité",
            "purpose_id": cls.purpose.id,
            "body_fr": "<p>Corps français d'origine.</p>",
        })
        cls.partner = cls.env["res.partner"].create({
            "name": "Sujet d'essai intégrité",
            "email": "integrite@example.invalid",
        })

    def _version(self, body, numero="1.0"):
        return self.Version.create({
            "notice_id": self.notice.id,
            "version": numero,
            "body": body,
        })

    def _forcer_empreinte(self, version, valeur):
        """Poser une empreinte en SQL, en contournant la garde d'immuabilité.

        ⚠ ``flush_all()`` AVANT le SQL brut, ``invalidate_all()`` après. Sans le
        flush, les écritures ORM encore en attente — dont le ``hash`` que
        ``create()`` vient de poser — sont écrites APRÈS l'``UPDATE`` et
        l'écrasent : le test passait alors au vert en n'ayant rien altéré du
        tout. C'est exactement le genre de test qui rassure sans rien prouver.
        """
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE privacy_notice_version SET hash = %s WHERE id = %s",
            (valeur, version.id),
        )
        self.env.invalidate_all()

    # ------------------------------------------------------------------
    # create()
    # ------------------------------------------------------------------

    def test_create_seals_stored_body_not_input(self):
        """RÉGRESSION — l'empreinte doit porter sur le corps PERSISTÉ.

        ⚠ Le cas d'espèce : Odoo normalise ``class='x'`` en ``class="x"`` à
        l'écriture. La longueur ne bouge pas, donc aucun contrôle de taille ne
        l'attrape. Hacher ``vals["body"]`` produisait une empreinte qui
        n'attestait aucun texte.
        """
        entree = "<p class='x'>Guillemets simples.</p>"
        version = self._version(entree)
        version.invalidate_recordset()

        stocke = str(version.body)
        self.assertNotEqual(
            stocke, entree,
            "Le cas d'essai ne vaut plus rien : Odoo n'assainit plus ce corps. "
            "Trouver une autre transformation avant de supprimer ce test.",
        )
        self.assertEqual(version.hash, version._body_hash(stocke))
        self.assertTrue(version.verify_integrity())

    def test_create_seals_plain_body(self):
        version = self._version("<p>Corps sans piège.</p>")
        version.invalidate_recordset()
        self.assertTrue(version.verify_integrity())

    def test_create_respects_explicit_hash(self):
        """Une empreinte fournie explicitement n'est pas écrasée.

        C'est ce qui permet à une reprise de données de poser une valeur
        calculée hors ORM sans que ``create()`` la recalcule dans son dos.
        """
        empreinte = "0" * 64
        version = self.Version.create({
            "notice_id": self.notice.id,
            "version": "1.1",
            "body": "<p>Corps.</p>",
            "hash": empreinte,
        })
        self.assertEqual(version.hash, empreinte)

    # ------------------------------------------------------------------
    # verify_integrity()
    # ------------------------------------------------------------------

    def test_verify_integrity_detects_tampering(self):
        """Une empreinte qui ne correspond plus doit être DÉTECTÉE.

        L'altération passe par SQL : c'est le seul chemin par lequel une
        empreinte fausse peut apparaître une fois la garde en place, et c'est
        exactement l'état dans lequel les trois locataires ont été trouvés.
        """
        version = self._version("<p>Corps scellé.</p>")
        self.assertTrue(version.verify_integrity())
        self._forcer_empreinte(version, "f" * 64)
        self.assertFalse(
            version.verify_integrity(),
            "Une empreinte falsifiée doit être détectée, sans quoi le contrôle "
            "d'intégrité ne vaut rien.",
        )

    def test_verify_integrity_false_without_hash(self):
        """Pas d'empreinte du tout = pas d'intégrité établie.

        ⚠ Une part des versions en production étaient dans cet état. Répondre
        « vrai » par absence de contradiction serait le pire des comportements.
        """
        version = self._version("<p>Corps.</p>")
        self._forcer_empreinte(version, None)
        self.assertFalse(version.verify_integrity())

    def test_body_hash_is_stable(self):
        """La formule d'empreinte ne doit pas dériver.

        ⚠ La reprise de données duplique volontairement cette formule (une
        migration ne doit pas dépendre du code applicatif courant). Deux
        formules divergentes rouvriraient exactement l'écart qu'on vient de
        fermer, et rien ne le signalerait. D'où la valeur de référence en dur :
        c'est SHA-256 sur l'encodage UTF-8, sans normalisation d'aucune sorte.
        """
        self.assertEqual(
            self.Version._body_hash("<p>abc</p>"),
            "25d0f020f6881a92742eadf0dda2a864025b35ec0008886497ed1d21efe99d20",
        )
        # Un corps absent doit se hacher comme la chaîne vide, jamais comme
        # « False » : sans ça, une version sans corps et une version au corps
        # vide porteraient deux empreintes différentes pour le même néant.
        self.assertEqual(
            self.Version._body_hash(False),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )
        self.assertEqual(self.Version._body_hash(False), self.Version._body_hash(""))
        self.assertNotEqual(self.Version._body_hash("a"), self.Version._body_hash("b"))

    # ------------------------------------------------------------------
    # write() — garde d'immuabilité et rescellement
    # ------------------------------------------------------------------

    def test_write_reseals_when_no_consent_attached(self):
        """Amender un texte que personne n'a signé est légitime — mais le sceau
        doit suivre. Avant correctif, l'empreinte gardait celle de l'ANCIEN
        corps et ``verify_integrity()`` répondait faux pour le reste de sa vie."""
        version = self._version("<p>Premier jet.</p>")
        self.assertEqual(version.consent_count, 0)

        version.write({"body": "<p class='y'>Second jet, corrigé.</p>"})
        version.invalidate_recordset()

        self.assertTrue(
            version.verify_integrity(),
            "Un corps amendé sans consentement rattaché doit être re-scellé.",
        )

    def test_write_guard_refuses_body_when_consent_attached(self):
        """Une fois un consentement rattaché, le texte est figé — et l'empreinte
        reste celle du texte que la personne a effectivement lu."""
        version = self._version("<p>Texte opposable.</p>")
        self.env["privacy.consent"].create({
            "subject_partner_id": self.partner.id,
            "purpose_id": self.purpose.id,
            "notice_version_id": version.id,
            "status": "granted",
        })
        version.invalidate_recordset()
        self.assertEqual(version.consent_count, 1)

        corps_avant, empreinte_avant = str(version.body), version.hash
        version.write({"body": "<p>Réécriture interdite.</p>"})
        version.invalidate_recordset()

        self.assertEqual(str(version.body), corps_avant)
        self.assertEqual(version.hash, empreinte_avant)
        self.assertTrue(version.verify_integrity())

    def test_write_guard_refuses_direct_hash_write(self):
        """Forcer l'empreinte à la main sur une version signée doit être refusé,
        sinon la garde ne protège que le corps et laisse falsifier le sceau."""
        version = self._version("<p>Texte opposable.</p>")
        self.env["privacy.consent"].create({
            "subject_partner_id": self.partner.id,
            "purpose_id": self.purpose.id,
            "notice_version_id": version.id,
            "status": "granted",
        })
        version.invalidate_recordset()
        empreinte_avant = version.hash

        version.write({"hash": "0" * 64})
        version.invalidate_recordset()
        self.assertEqual(version.hash, empreinte_avant)

    def test_write_of_other_field_leaves_hash_alone(self):
        """Écrire un champ sans rapport ne doit pas toucher au sceau."""
        version = self._version("<p>Corps.</p>")
        empreinte_avant = version.hash
        version.write({"version": "1.9"})
        version.invalidate_recordset()
        self.assertEqual(version.hash, empreinte_avant)
        self.assertTrue(version.verify_integrity())

    # ------------------------------------------------------------------
    # Mention de rescellement — locataires seulement
    # ------------------------------------------------------------------

    def _reseal_fields_present(self):
        champs = self.Version._fields
        return "hash_resealed_at" in champs and "hash_reseal_note" in champs

    def test_reseal_mention_never_set_by_orm(self):
        """⚠ Le cœur de la décision du 2026-08-02 : un sceau recalculé ne doit
        JAMAIS pouvoir se lire comme un sceau contemporain du consentement.

        Les deux champs sont posés par la migration et par elle seule. Si
        ``create()`` ou ``write()`` se mettaient à les renseigner, la distinction
        disparaîtrait et on retomberait dans le surclaim du certificat de
        destruction.
        """
        if not self._reseal_fields_present():
            self.skipTest("Champs de rescellement absents de ce module.")

        version = self._version("<p class='x'>Corps.</p>")
        version.invalidate_recordset()
        self.assertFalse(version.hash_resealed_at)
        self.assertFalse(version.hash_reseal_note)

        version.write({"body": "<p>Amendé.</p>"})
        version.invalidate_recordset()
        self.assertTrue(version.verify_integrity())
        self.assertFalse(
            version.hash_resealed_at,
            "Un rescellement de write() n'est pas une reprise de données : il ne "
            "doit pas porter la mention réservée à la migration.",
        )

    def test_reseal_mention_accompanies_reseal_timestamp(self):
        """Une date de rescellement sans motif serait une mention muette."""
        if not self._reseal_fields_present():
            self.skipTest("Champs de rescellement absents de ce module.")

        rescellees = self.Version.search([("hash_resealed_at", "!=", False)])
        for version in rescellees:
            self.assertTrue(
                version.hash_reseal_note,
                f"Version {version.id} rescellée sans motif inscrit.",
            )

    def test_existing_versions_are_all_verifiable(self):
        """Filet de sécurité sur les DONNÉES, pas seulement sur le code.

        ⚠ Ce test échoue si une reprise a été oubliée sur ce locataire, ou si un
        chemin d'écriture non couvert réintroduit une empreinte fausse. Il est
        volontairement placé ici : c'est le seul contrôle du dépôt qui regarde
        l'état réel de la base plutôt qu'un cas fabriqué.
        """
        mauvaises = [
            v.id for v in self.Version.search([]) if not v.verify_integrity()
        ]
        self.assertFalse(
            mauvaises,
            f"Versions d'avis dont l'empreinte n'atteste aucun texte : {mauvaises}",
        )
