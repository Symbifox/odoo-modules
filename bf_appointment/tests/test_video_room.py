"""La salle de vidéoconférence, et le défaut structurel qui l'avait effacée.

Ce fichier existe à cause d'un cas précis. Le lot 2.49.0 a ajouté l'accroche
des consentements dans un SECOND `def action_confirm`, dans le même corps de
classe que celui qui existait déjà. Python garde le dernier, sans un mot :
la génération de l'URL visio — dont `action_confirm` est le SEUL appelant —
a cessé d'exister le jour du déploiement, et les rendez-vous confirmés
ensuite sont sortis avec l'adresse de la salle GÉNÉRIQUE partagée au lieu de
leur salle dédiée. Une centaine de tests passaient toujours : aucun ne
touchait la visio.

D'où les deux familles ci-dessous. La première vérifie le COMPORTEMENT (une
confirmation pose une salle). La seconde vérifie la STRUCTURE, parce que le
défaut n'était pas dans une règle métier : il était dans le fait qu'un nom
puisse être défini deux fois sans que rien ne le signale.

Le fournisseur employé ici est Jitsi, à dessein : `_generate_jitsi_url` est
purement local (paramètre système + jeton), là où Nextcloud Talk ferait un
appel réseau qu'un test n'a pas à dépendre.
"""

import ast
import collections
import pathlib
from datetime import timedelta

import pytz

from odoo import Command, fields
from odoo.tests import TransactionCase, tagged


@tagged("bf_appointment", "bf_appointment_video")
class TestVideoRoom(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, tz="UTC"))
        attendances = [
            Command.create({
                "name": f"All day {d}",
                "dayofweek": str(d),
                "hour_from": 0.0,
                "hour_to": 24.0,
                "day_period": "morning",
            })
            for d in range(7)
        ]
        cls.calendar = cls.env["resource.calendar"].create({
            "name": "24/7 Video",
            "attendance_ids": attendances,
            "tz": "UTC",
        })
        cls.resource = cls.env["resource.resource"].create({
            "name": "Video material",
            "calendar_id": cls.calendar.id,
            "resource_type": "material",
            "tz": "UTC",
        })
        cls.combination = cls.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([cls.resource.id])],
        })
        cls.partner = cls.env["res.partner"].create({
            "name": "Video Booker",
            "email": "video@test.invalid",
        })
        cls.env["ir.config_parameter"].sudo().set_param(
            "bf_appointment.jitsi_domain", "meet.test.invalid")
        cls.booking_type = cls.env["resource.booking.type"].create({
            "name": "Video Type",
            "duration": 1.0,
            "slot_duration": 1.0,
            "modifications_deadline": 0.0,
            "combination_assignment": "sorted",
            "resource_calendar_id": cls.calendar.id,
            "video_provider": "jitsi",
            # Hors sujet ici, et surtout : évite qu'une confirmation parte
            # demander un consentement par courriel pendant un test.
            "requires_recording_consent": False,
            "combination_rel_ids": [
                Command.create({"sequence": 0, "combination_id": cls.combination.id}),
            ],
        })

    def _slot(self):
        now = fields.Datetime.context_timestamp(
            self.env["resource.booking"], fields.Datetime.now())
        creneaux = self.booking_type._bf_candidate_slots(
            now + timedelta(hours=1), now + timedelta(days=7), limit=1)
        self.assertTrue(creneaux, "un calendrier 24/7 doit produire des créneaux")
        return creneaux[0].astimezone(pytz.utc).replace(tzinfo=None)

    def _booking(self):
        return self.booking_type._bf_create_booking(
            self._slot(), partners=self.partner, confirm=False)

    # -- comportement -------------------------------------------------------

    def test_confirm_pose_une_salle_dediee(self):
        """LE test qui manquait le 2026-08-22."""
        booking = self._booking()
        self.assertFalse(booking.video_room_token)
        booking.action_confirm()
        self.assertTrue(
            booking.video_room_token,
            "action_confirm doit fabriquer le jeton de salle")
        self.assertEqual(
            booking.videocall_location,
            "https://meet.test.invalid/bf-%s-%s" % (
                booking.id, booking.video_room_token),
            "le lien visio doit être celui de CETTE réservation")

    def test_confirm_ne_touche_rien_sans_fournisseur(self):
        self.booking_type.video_provider = "none"
        booking = self._booking()
        booking.action_confirm()
        self.assertFalse(booking.video_room_token)

    def test_la_salle_est_propre_a_la_reservation(self):
        """Deux réservations ne doivent jamais partager une salle.

        C'est exactement ce que la régression produisait : tout le monde
        atterrissait dans la salle générique commune.
        """
        premiere = self._booking()
        premiere.action_confirm()
        seconde = self.booking_type._bf_create_booking(
            self.booking_type._bf_candidate_slots(
                fields.Datetime.context_timestamp(
                    self.env["resource.booking"], fields.Datetime.now()
                ) + timedelta(hours=1),
                fields.Datetime.context_timestamp(
                    self.env["resource.booking"], fields.Datetime.now()
                ) + timedelta(days=7),
                limit=4,
            )[3].astimezone(pytz.utc).replace(tzinfo=None),
            partners=self.partner, confirm=False)
        seconde.action_confirm()
        self.assertTrue(premiere.videocall_location)
        self.assertTrue(seconde.videocall_location)
        self.assertNotEqual(
            premiere.videocall_location, seconde.videocall_location,
            "chaque rendez-vous a sa propre salle")

    def test_les_consentements_restent_accroches_a_la_confirmation(self):
        """La fusion des deux `action_confirm` ne doit rien avoir perdu."""
        self.booking_type.requires_recording_consent = True
        booking = self._booking()
        booking.action_confirm()
        # La salle est posée ET l'état de consentement est calculable : les
        # deux corps cohabitent dans la même méthode.
        self.assertTrue(booking.videocall_location)
        self.assertIn(
            booking.bf_consent_state,
            ("granted", "requested", "refused", "missing"))


@tagged("bf_appointment", "bf_appointment_video", "bf_appointment_structure")
class TestPasDeDefinitionEnDouble(TransactionCase):
    """Aucun nom ne doit être défini deux fois dans un même corps de classe.

    Python remplace silencieusement le premier par le second. Sur un modèle
    Odoo, c'est une surcharge qui disparaît sans avertissement, sans erreur,
    et sans qu'aucun test fonctionnel ne s'en aperçoive tant qu'il n'existe
    pas de test sur la partie effacée. Le contrôle coûte une seconde et
    couvre tout le module, pas seulement la méthode qui a eu le problème.
    """

    def test_aucune_methode_ni_champ_defini_deux_fois(self):
        racine = pathlib.Path(__file__).resolve().parent.parent
        doublons = []
        for fichier in sorted(racine.rglob("*.py")):
            if "__pycache__" in fichier.parts:
                continue
            arbre = ast.parse(fichier.read_text(encoding="utf-8"))
            for noeud in ast.walk(arbre):
                if not isinstance(noeud, ast.ClassDef):
                    continue
                lignes = collections.defaultdict(list)
                for corps in noeud.body:
                    if isinstance(corps, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        lignes[corps.name].append(corps.lineno)
                    elif isinstance(corps, ast.Assign):
                        for cible in corps.targets:
                            if isinstance(cible, ast.Name):
                                lignes[cible.id].append(corps.lineno)
                for nom, positions in lignes.items():
                    if len(positions) > 1:
                        doublons.append(
                            "%s : %s.%s défini aux lignes %s" % (
                                fichier.relative_to(racine), noeud.name, nom,
                                ", ".join(str(p) for p in positions)))
        self.assertFalse(
            doublons,
            "Définitions en double (la dernière écrase les précédentes) :\n"
            + "\n".join(doublons))
