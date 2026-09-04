"""Un refus ferme le lien d'origine — décision de gouvernance du 2026-08-03.

La règle, telle qu'elle a été arrêtée :

    Un consentement refusé ne se retourne pas en accordé sur le lien
    d'origine. Le refus annule ce lien ; redonner l'occasion de consentir
    passe par une NOUVELLE demande, avec son propre identifiant et son
    propre jeton, pour que la chaîne « qui a reçu quoi » reste lisible et
    qu'un refus ne soit jamais écrasé par un accord — ni l'inverse.

Le contrat qui en découle, et que ce fichier éprouve :

1. Le lien reçu par courriel n'accorde plus rien une fois le refus enregistré —
   ni par la route publique, ni par la route authentifiée, ni par un POST forgé.
2. Le lien reste LISIBLE : la personne doit pouvoir constater ce qu'elle a
   refusé, et quand. C'est le pouvoir d'accorder qui s'éteint, pas la page.
3. Le refus est immuable : plus de remise en brouillon, donc plus de bouton
   « Accorder » sur le MÊME enregistrement.
4. Rouvrir la porte crée une DEMANDE DISTINCTE — autre identifiant, autre jeton,
   avis en vigueur du jour — chaînée au refus, qui reste intact au dossier.
"""

import re
from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import HttpCase, TransactionCase, tagged

MARQUEUR_COURANT = "MARQUEUR-AVIS-COURANT-REFUS"
MARQUEUR_PERIME = "MARQUEUR-AVIS-PERIME-REFUS"


class RefusalFixture:
    """Jeu d'essai partagé par les deux cas : un avis en deux versions.

    La version périmée est celle épinglée sur le consentement refusé ; la
    version courante est celle qu'une nouvelle demande doit épingler.
    """

    def _build_fixture(self):
        self.Consent = self.env["privacy.consent"]
        self.Version = self.env["privacy.notice.version"]

        self.purpose = self.env["privacy.purpose"].create({
            "code": "REFUS_LIEN",
            "name": "Finalité d'essai — refus et lien d'origine",
            "default_validity_days": 180,
            "plain_language_summary": "Résumé de repli de la finalité.",
        })
        self.notice = self.env["privacy.notice"].create({
            "name": "Avis d'essai — refus et lien d'origine",
            "purpose_id": self.purpose.id,
            "body_fr": f"<p>{MARQUEUR_COURANT}</p>",
        })
        self.version_perimee = self.Version.create({
            "notice_id": self.notice.id,
            "version": "1.0",
            "body": f"<p>{MARQUEUR_PERIME}</p>",
            "effective_date": fields.Date.today() - timedelta(days=30),
        })
        self.version_courante = self.Version.create({
            "notice_id": self.notice.id,
            "version": "2.0",
            "body": f"<p>{MARQUEUR_COURANT}</p>",
            "effective_date": fields.Date.today(),
        })
        self.notice.invalidate_recordset()

        self.partner = self.env["res.partner"].create({
            "name": "Sujet du refus",
            "email": "refus@example.invalid",
        })

    def _refus(self, **extra):
        vals = {
            "subject_partner_id": self.partner.id,
            "purpose_id": self.purpose.id,
            "notice_id": self.notice.id,
            # Épinglé sur la version PÉRIMÉE : l'état d'une personne qui a
            # refusé avant la publication de la v2.0.
            "notice_version_id": self.version_perimee.id,
            "status": "refused",
            "refused_at": fields.Datetime.now(),
        }
        vals.update(extra)
        return self.Consent.create(vals)


@tagged("post_install", "-at_install", "privacy_consent", "privacy_refusal")
class TestRefusalClosesLinkHttp(HttpCase, RefusalFixture):
    """Le lien d'origine, exercé en HTTP réel."""

    def setUp(self):
        super().setUp()
        self._build_fixture()

    def _url_detail(self, consent):
        return f"/privacy/consent/{consent.id}/{consent.access_token}"

    def _url_renew(self, consent):
        return f"{self._url_detail(consent)}/renew"

    def _csrf(self, html):
        trouve = re.search(
            r'name="csrf_token"\s+value="([^"]+)"', html
        ) or re.search(r'value="([^"]+)"\s+name="csrf_token"', html)
        return trouve.group(1) if trouve else None

    # ------------------------------------------------------------------
    # Le lien n'accorde plus rien
    # ------------------------------------------------------------------

    def test_get_renew_is_refused_with_its_own_reason(self):
        """Un GET sur le lien de renouvellement n'ouvre jamais d'écran d'octroi.

        ⚠ Deux formes de route coexistent selon les copies : celles qui portent
        l'écran de reconsentement acceptent le GET et redirigent avec le motif
        `refused_link_closed` ; les copies plus anciennes exposent `/renew` en
        POST seulement et répondent 405. Les deux satisfont le contrat — le lien
        n'accorde rien — donc le test vise le contrat, pas la forme. Ce qui
        serait un échec, c'est un 200 : une page rendue, donc un écran offert.
        """
        consent = self._refus()
        reponse = self.url_open(self._url_renew(consent), allow_redirects=False)

        self.assertIn(reponse.status_code, (302, 303, 405))
        if reponse.status_code != 405:
            self.assertIn("refused_link_closed", reponse.headers.get("Location", ""))

    def test_forged_post_creates_nothing(self):
        """⚠ Le contrôle doit vivre dans le CONTRÔLEUR, pas dans le gabarit.

        Retirer le bouton de la page ne suffirait pas : l'URL de renouvellement
        est publique, elle a circulé par courriel, et un POST se rejoue. Ce test
        poste directement, case cochée, en sautant l'écran.
        """
        consent = self._refus()
        avant = self.Consent.search_count([])

        reponse = self.url_open(
            self._url_renew(consent),
            data={"notice_read": "1"},
            allow_redirects=False,
        )

        consent.invalidate_recordset()
        self.assertEqual(consent.status, "refused")
        self.assertFalse(consent.renewed_to_id)
        self.assertEqual(
            self.Consent.search_count([]), avant,
            "Un POST forgé sur un refus a fait naître un consentement.",
        )
        self.assertNotEqual(reponse.status_code, 500)

    def test_respond_route_still_rejects_a_second_answer(self):
        """La route de réponse ne rouvre pas non plus la porte.

        Elle exigeait déjà ``pending`` ; on le verrouille par un test, car c'est
        l'autre chemin par lequel un « oui » pourrait écraser un « non ».
        """
        consent = self._refus()
        self.url_open(
            f"{self._url_detail(consent)}/respond",
            data={"action": "grant"},
            allow_redirects=False,
        )
        consent.invalidate_recordset()
        self.assertEqual(consent.status, "refused")

    # ------------------------------------------------------------------
    # …mais la page reste lisible
    # ------------------------------------------------------------------

    def test_detail_page_stays_readable_and_offers_no_grant(self):
        consent = self._refus()
        reponse = self.url_open(self._url_detail(consent))

        self.assertEqual(
            reponse.status_code, 200,
            "Le jeton doit rester valide en LECTURE : la personne doit pouvoir "
            "constater ce qu'elle a refusé.",
        )
        html = reponse.text
        self.assertIn("Vous avez refusé cette demande", html)
        self.assertNotIn(
            f"/privacy/consent/{consent.id}/{consent.access_token}/renew", html,
            "La page de détail offre encore le lien de reconsentement.",
        )
        self.assertNotIn("Accorder maintenant", html)

    def test_detail_page_points_to_the_new_request_once_issued(self):
        consent = self._refus()
        consent.action_new_request_after_refusal()
        consent.invalidate_recordset()
        nouveau = consent.renewed_to_id

        html = self.url_open(self._url_detail(consent)).text
        self.assertIn("nouvelle demande", html)
        self.assertIn(
            f"/privacy/consent/{nouveau.id}/{nouveau.access_token}", html,
            "La page du refus doit mener à la nouvelle demande.",
        )
        self.assertNotIn(
            "a été renouvelé", html,
            "Après un refus ce n'est pas un renouvellement : le dire ainsi "
            "revient à annoncer que le refus a été renouvelé.",
        )

    def test_the_new_request_link_does_work(self):
        """Le nouveau lien, lui, accorde — sinon la décision fermerait tout."""
        consent = self._refus()
        consent.action_new_request_after_refusal()
        consent.invalidate_recordset()
        nouveau = consent.renewed_to_id

        page = self.url_open(f"/privacy/consent/{nouveau.id}/{nouveau.access_token}")
        self.assertEqual(page.status_code, 200)
        csrf = self._csrf(page.text)
        self.assertTrue(csrf, "Le formulaire de réponse doit porter un jeton CSRF.")

        self.url_open(
            f"/privacy/consent/{nouveau.id}/{nouveau.access_token}/respond",
            data={"csrf_token": csrf, "action": "grant"},
        )
        nouveau.invalidate_recordset()
        consent.invalidate_recordset()
        self.assertEqual(nouveau.status, "granted")
        self.assertEqual(
            consent.status, "refused",
            "L'octroi de la nouvelle demande a modifié le refus d'origine.",
        )


@tagged("post_install", "-at_install", "privacy_consent", "privacy_refusal")
class TestRefusalClosesLinkBackend(TransactionCase, RefusalFixture):
    """Le dossier dorsal : refus immuable, nouvelle demande distincte."""

    def setUp(self):
        super().setUp()
        self._build_fixture()

    # ------------------------------------------------------------------
    # Le refus est immuable
    # ------------------------------------------------------------------

    def test_reset_to_draft_is_refused(self):
        """⚠ La remise en brouillon rendait le bouton « Accorder » au MÊME
        enregistrement : le refus était écrasé sur place, sans nouvel
        identifiant ni nouveau jeton."""
        consent = self._refus()
        with self.assertRaises(UserError):
            consent.action_reset_to_draft()
        self.assertEqual(consent.status, "refused")

    def test_action_grant_still_refuses_a_refused_consent(self):
        consent = self._refus()
        with self.assertRaises(UserError):
            consent.action_grant()

    def test_action_renew_still_refuses_a_refused_consent(self):
        """`action_renew` ne visait déjà que « accordé » et « expiré ».

        On le verrouille : c'est le bouton voisin, et l'élargir « pour rendre
        service » réintroduirait exactement le défaut fermé ici.
        """
        consent = self._refus()
        with self.assertRaises(UserError):
            consent.action_renew()

    # ------------------------------------------------------------------
    # La nouvelle demande
    # ------------------------------------------------------------------

    def test_new_request_is_a_distinct_record_with_a_distinct_token(self):
        consent = self._refus()
        jeton_origine = consent.access_token

        consent.action_new_request_after_refusal()
        consent.invalidate_recordset()
        nouveau = consent.renewed_to_id

        self.assertTrue(nouveau, "Aucune nouvelle demande n'a été créée.")
        self.assertNotEqual(nouveau.id, consent.id)
        self.assertTrue(nouveau.access_token)
        self.assertNotEqual(
            nouveau.access_token, jeton_origine,
            "⚠ Le jeton a été recopié : le lien d'origine accorderait de "
            "nouveau, et la décision serait vidée de son effet.",
        )
        self.assertEqual(nouveau.status, "pending")
        self.assertTrue(nouveau.requested_at)

    def test_the_refusal_survives_untouched(self):
        consent = self._refus()
        refuse_le = consent.refused_at
        jeton = consent.access_token

        consent.action_new_request_after_refusal()
        consent.invalidate_recordset()

        self.assertEqual(consent.status, "refused")
        self.assertEqual(consent.refused_at, refuse_le)
        self.assertEqual(
            consent.access_token, jeton,
            "Le jeton du refus a changé : la personne qui rouvre son courriel "
            "ne retrouverait plus la trace de ce qu'elle a refusé.",
        )

    def test_the_chain_is_walkable_both_ways(self):
        """« Faut juste qu'on ne brise pas la chaîne de qui reçoit quoi. »"""
        consent = self._refus()
        consent.action_new_request_after_refusal()
        consent.invalidate_recordset()
        nouveau = consent.renewed_to_id

        self.assertEqual(nouveau.renewed_from_id, consent)
        self.assertEqual(consent.renewed_to_id, nouveau)

        action = consent.action_view_renewal_history()
        ids_du_domaine = action["domain"][0][2]
        self.assertIn(consent.id, ids_du_domaine)
        self.assertIn(nouveau.id, ids_du_domaine)

    def test_the_new_request_pins_the_current_notice_version(self):
        """⚠ `notice_version_id` est `copy=True`.

        Sans imposer la version courante, la nouvelle demande hériterait du
        texte épinglé sur le refus — la personne se prononcerait sur un avis
        périmé, et la preuve épinglerait ce texte périmé.
        """
        consent = self._refus()
        self.assertEqual(consent.notice_version_id, self.version_perimee)

        consent.action_new_request_after_refusal()
        consent.invalidate_recordset()

        self.assertEqual(consent.renewed_to_id.notice_version_id, self.version_courante)

    def test_signature_identifiers_are_not_inherited(self):
        champs = self.Consent._fields
        signature = {
            c: "valeur-heritee"
            for c in ("docuseal_submission_id", "libresign_file_uuid")
            if c in champs
        }
        if not signature:
            self.skipTest("Aucun champ de signature sur ce module.")

        consent = self._refus(**signature)
        consent.action_new_request_after_refusal()
        consent.invalidate_recordset()

        for champ in signature:
            self.assertFalse(consent.renewed_to_id[champ])

    def test_it_cannot_be_issued_twice(self):
        """Deux clics ne doivent pas produire deux demandes concurrentes."""
        consent = self._refus()
        consent.action_new_request_after_refusal()
        consent.invalidate_recordset()
        with self.assertRaises(UserError):
            consent.action_new_request_after_refusal()

    def test_it_only_applies_to_a_refusal(self):
        granted = self.Consent.create({
            "subject_partner_id": self.partner.id,
            "purpose_id": self.purpose.id,
            "notice_id": self.notice.id,
            "status": "granted",
            "granted_at": fields.Datetime.now(),
        })
        with self.assertRaises(UserError):
            granted.action_new_request_after_refusal()

    def test_a_subject_without_email_is_told_so(self):
        """⚠ `_send_consent_request_email` sort en SILENCE sans adresse.

        La demande passerait alors en « En attente », avec une date d'envoi,
        sans que rien ne soit parti.
        """
        muet = self.env["res.partner"].create({"name": "Sujet sans adresse"})
        consent = self._refus(subject_partner_id=muet.id)

        consent.action_new_request_after_refusal()
        consent.invalidate_recordset()
        nouveau = consent.renewed_to_id

        corps = "".join(nouveau.message_ids.mapped("body"))
        self.assertIn("AUCUN courriel", corps)
