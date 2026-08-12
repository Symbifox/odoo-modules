# -*- coding: utf-8 -*-
"""Normalisation GSM-7 des SMS sortants (anti-fragmentation).

Un SMS tient dans 160 caractères en GSM-7, mais 70 seulement dès qu'un
caractère hors table force l'UCS-2. VOIP.ms n'assemblant pas les segments,
un message fragmenté arrive en plusieurs messages distincts chez le
destinataire. On vérifie ici que la normalisation ramène le message en
GSM-7 (emoji retirés, ponctuation typographique translittérée) et que
l'option d'aplatissement des accents circonflexe/tréma se comporte comme
attendu (défaut OFF : accents conservés).
"""
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestGsm7Normalize(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.M = cls.env["sms.archive.message"]

    def _norm(self, text, flatten=False):
        return self.M._normalize_gsm7(text, flatten_accents=flatten)

    def _segs(self, text):
        return len(self.M._split_segments(text))

    # ── Détection d'encodage ───────────────────────────────────────
    def test_is_gsm7(self):
        self.assertTrue(self.M._is_gsm7("Deja pret, c'est bon !"))
        self.assertTrue(self.M._is_gsm7("Été à Paris, il a gagné !"))  # É é à = GSM-7
        self.assertFalse(self.M._is_gsm7("Prêt ?"))   # ê (circonflexe) hors GSM-7
        self.assertFalse(self.M._is_gsm7("Noël"))     # ë (tréma) hors GSM-7
        self.assertFalse(self.M._is_gsm7("ça va"))    # ç MINUSCULE hors GSM-7 (piège !)
        self.assertFalse(self.M._is_gsm7("Salut ⚡"))  # emoji

    # ── Emoji retiré → message court redevient 1 SMS ───────────────
    def test_emoji_stripped_single_segment(self):
        src = "Bonjour Erik, sans probleme, je m'occupe de tout. " * 3  # ~150 car.
        src_emoji = src + "⚡"
        self.assertGreater(self._segs(src_emoji), 1)          # UCS-2 -> fragmente
        out = self._norm(src_emoji)
        self.assertNotIn("⚡", out)
        self.assertTrue(self.M._is_gsm7(out))
        self.assertEqual(self._segs(out), 1)                  # 1 seul SMS

    # ── Ponctuation typographique translittérée ────────────────────
    def test_typography(self):
        self.assertEqual(self._norm("c’est «bon»…"), 'c\'est "bon"...')
        self.assertEqual(self._norm("A — B"), "A - B")
        self.assertEqual(self._norm("un œuf"), "un oeuf")
        self.assertEqual(self._norm("A : B"), "A : B")   # nbsp -> espace

    # ── nbsp / doubles espaces laissées par un retrait tassées ─────
    def test_collapse_spaces_keeps_newlines(self):
        self.assertEqual(self._norm("Salut ⚡ toi"), "Salut toi")
        self.assertEqual(self._norm("Ligne1 ⚡\nLigne2"), "Ligne1\nLigne2")

    # ── Accents : OFF conserve, ON aplatit ; é/è/à/ç toujours gardés ─
    def test_flatten_accents_toggle(self):
        self.assertEqual(self._norm("même", flatten=False), "même")   # OFF: gardé
        self.assertEqual(self._norm("même", flatten=True), "meme")    # ON: aplati
        # accents GSM-7 préservés dans les deux cas
        for fl in (False, True):
            self.assertEqual(self._norm("café à côté", flatten=fl)[:5], "café ")

    # ── Texte déjà GSM-7 renvoyé tel quel (no-op) ──────────────────
    def test_gsm7_noop(self):
        src = "Déjà GSM-7, rien à changer ! (é è à)"
        self.assertTrue(self.M._is_gsm7(src))
        self.assertEqual(self._norm(src), src)

    # ── Idempotence + garantie GSM-7 en mode aplatissement ─────────
    def test_idempotent_and_gsm7_when_flattening(self):
        samples = ["Prêt ⚡ ?", "Coût élevé — «vôtre»…", "Île 😊 déjà"]
        for s in samples:
            once = self._norm(s, flatten=True)
            self.assertTrue(self.M._is_gsm7(once), f"pas GSM-7: {once!r}")
            self.assertEqual(self._norm(once, flatten=True), once)

    def test_default_flatten_off(self):
        """Le réglage runtime par défaut conserve les accents (choix par défaut)."""
        self.assertFalse(self.M._gsm7_flatten_accents_enabled())

    # ── Segments bornés en OCTETS UTF-8 (contrôle VOIP.ms) ─────────
    def test_split_segments_utf8_byte_budget(self):
        """VOIP.ms refuse tout segment > 160 octets UTF-8 (« sms_toolong »),
        même s'il tient en 160 septets GSM-7. Vécu 2026-07-23 : un segment
        de 160 caractères contenant un seul « è » (161 octets) a été refusé.
        Chaque segment produit doit donc tenir dans 160 octets."""
        cases = [
            # 160 « a » + 1 « é » : 161 car. GSM-7 mais 162 octets → 2 segments
            "a" * 160 + "é",
            # cas type du bogue : >160 car. GSM-7 dont un accent avant la
            # coupe → le 1er segment de 160 car. faisait 161 octets
            "Bonjour! Merci pour votre retour. La revue du dossier est "
            "terminée et tout est conforme. On se reparle très vite pour "
            "planifier la suite des travaux prévus à l'agenda, d'accord? "
            "A bientot!",
            # accents GSM-7 en rafale : 100 « é » = 200 octets → 2 segments
            "é" * 100,
        ]
        for src in cases:
            self.assertTrue(self.M._is_gsm7(src))
            segs = self.M._split_segments(src)
            self.assertEqual("".join(segs), src)  # rien de perdu
            for seg in segs:
                self.assertLessEqual(len(seg.encode("utf-8")), 160, repr(seg))

    def test_split_segments_ascii_unchanged(self):
        """ASCII pur : le budget reste 160 caractères pleins (pas de
        sur-découpage), et l'extension GSM-7 compte toujours double."""
        self.assertEqual(len(self.M._split_segments("a" * 160)), 1)
        self.assertEqual(len(self.M._split_segments("a" * 161)), 2)
        # 80 paires ESC+{ = 160 septets = 1 segment ; une de plus déborde
        self.assertEqual(len(self.M._split_segments("{" * 80)), 1)
        self.assertEqual(len(self.M._split_segments("{" * 81)), 2)
