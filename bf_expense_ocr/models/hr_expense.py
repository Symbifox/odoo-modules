# -*- coding: utf-8 -*-
"""Lire un reçu de repas et en tirer le total, les taxes et le pourboire.

Le socle est **`bf_ai_bridge`**, c'est-à-dire le service local
`claude-chatbot-bridge` et, derrière lui, `claude -p` sur l'**abonnement Claude
du locataire**. C'est le chemin réellement câblé dans cette maison : le pont
choisit le répertoire d'identifiants d'après le locataire déclaré, de sorte
qu'un système annonçant `bsi` est lu sur l'abonnement de BSI.

⚠️ Ce n'est PAS `bf_llm`. Cette passerelle-là ne parle qu'à des API HTTP avec
clé (`anthropic`, `openai`, `openai_compatible`), et c'est exactement pourquoi
son fournisseur est semé désactivé et sans clé : personne ne paie au jeton
quand l'abonnement est déjà là. Un module d'extraction bâti dessus ne peut pas
fonctionner ici, quoi qu'en dise sa suite de tests.

Le vocabulaire des champs d'état (`ocr_state`, `ocr_scanned_date`,
`ocr_confidence`, `ocr_raw_response`, `ocr_error_message`) reste celui de
`bf_invoice_ocr` : deux surfaces OCR dans la même base doivent se lire pareil.

Le schéma d'extraction vit côté pont (`/ocr/receipt`), avec ses garde-fous
d'injection. Ce module apporte le garde-fou arithmétique, qui est ce qui
décide si on écrit quoi que ce soit.
"""

import json
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

#: Les types de pièces jointes qu'on accepte de faire lire. Un téléphone
#: produit du JPEG ou du HEIC converti ; un scanneur, du PDF.
MIMES_LISIBLES = (
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
)

#: Tolérance d'écart sur le contrôle de balance du reçu, en devise.
#: Deux sous : un reçu peut arrondir sa TPS et sa TVQ séparément.
TOLERANCE_BALANCE = 0.02

#: Au-delà de cette part du sous-total, un « pourboire » déduit par
#: soustraction n'est plus un pourboire mais une erreur de lecture ailleurs.
POURBOIRE_MAX_RATIO = 0.40

#: Le point d'entrée du pont qui lit un reçu. Le schéma d'extraction et les
#: garde-fous d'injection vivent là-bas, avec le prompt.
ENDPOINT = "/ocr/receipt"

#: Le pont borne sa propre lecture à 90 s ; on lui laisse un peu de marge.
TIMEOUT = 120


def _nombre(valeur):
    """Un nombre exploitable, ou None. Le modèle rend parfois « 26,30 $ »."""
    if valeur is None or isinstance(valeur, bool):
        return None
    if isinstance(valeur, (int, float)):
        return float(valeur)
    texte = str(valeur).strip().replace(" ", "").replace(" ", "")
    texte = texte.replace("$", "").replace("CAD", "").replace(",", ".")
    try:
        return float(texte)
    except (TypeError, ValueError):
        return None


class HrExpense(models.Model):
    _inherit = "hr.expense"

    ocr_state = fields.Selection(
        [
            ("none", "Non lue"),
            ("pending", "En cours"),
            ("done", "Lue"),
            ("doubt", "À vérifier"),
            ("error", "Erreur"),
        ],
        string="Lecture du reçu",
        default="none",
        copy=False,
        tracking=True,
    )
    ocr_scanned_date = fields.Datetime(string="Lu le", copy=False)
    ocr_confidence = fields.Float(string="Confiance", copy=False)
    ocr_raw_response = fields.Text(string="Extraction brute", copy=False)
    ocr_error_message = fields.Char(string="Erreur de lecture", copy=False)

    # ------------------------------------------------------------------
    # La pièce jointe à lire
    # ------------------------------------------------------------------

    def _ocr_attachment(self):
        """La pièce jointe lisible la plus récente, ou un recordset vide.

        La principale d'abord — c'est celle que le téléversement mobile vient
        de poser — puis la plus récente qui soit d'un type qu'on sait lire.
        """
        self.ensure_one()
        principale = self.message_main_attachment_id
        if principale and principale.mimetype in MIMES_LISIBLES:
            return principale
        return self.env["ir.attachment"].search(
            [
                ("res_model", "=", "hr.expense"),
                ("res_id", "=", self.id),
                ("mimetype", "in", list(MIMES_LISIBLES)),
            ],
            order="id desc",
            limit=1,
        )

    # ------------------------------------------------------------------
    # La lecture
    # ------------------------------------------------------------------

    def action_ocr_scan(self):
        """Bouton : lire le reçu joint et préremplir la dépense."""
        self.ensure_one()
        if self.state not in ("draft", "reported"):
            return False

        piece = self._ocr_attachment()
        if not piece:
            self.write({
                "ocr_state": "error",
                "ocr_error_message": _("Aucune photo ni PDF joint à lire."),
            })
            return False
        if not piece.datas:
            self.write({
                "ocr_state": "error",
                "ocr_error_message": _("La pièce jointe est vide."),
            })
            return False

        self.write({"ocr_state": "pending", "ocr_error_message": False})

        # ⚠️ Tout peut lever ici, et rien ne doit coûter sa photo à
        # l'utilisateur : `tenant()` lève quand le locataire n'est pas déclaré,
        # `call()` laisse remonter les exceptions du transport (socket absente,
        # service muet, réponse malformée), et le pont peut rendre une erreur
        # dans son enveloppe quand c'est la lecture qui a échoué.
        Pont = self.env["bf.ai.bridge"]
        try:
            charge = {
                "org": Pont.tenant(),
                "image_base64": piece.datas.decode() if isinstance(
                    piece.datas, bytes) else piece.datas,
                "filename": piece.name or "recu.jpg",
            }
            res = Pont.call(ENDPOINT, charge, timeout=TIMEOUT)
        except Exception as e:  # noqa: BLE001 — on veut vraiment tout attraper ici
            _logger.warning("Lecture du reçu impossible pour la dépense %s : %s",
                            self.id, e)
            self.write({
                "ocr_state": "error",
                "ocr_error_message": str(e)[:255],
            })
            return False

        if not isinstance(res, dict) or res.get("error") or not res.get("data"):
            motif = (res or {}).get("error") if isinstance(res, dict) else None
            self.write({
                "ocr_state": "error",
                "ocr_error_message": (motif or _("Lecture sans données"))[:255],
                "ocr_raw_response": json.dumps(res, ensure_ascii=False)
                if isinstance(res, dict) else "",
            })
            return False

        return self._apply_ocr_result(res["data"])

    # ------------------------------------------------------------------
    # Le garde-fou
    # ------------------------------------------------------------------

    @api.model
    def _ocr_reconcilie(self, donnees):
        """Le reçu balance-t-il, et que vaut le pourboire ?

        Rend `(total, pourboire, motif)`. Un `motif` non vide veut dire qu'on
        ne pose rien : c'est le seul contrat de cette méthode.
        """
        total = _nombre(donnees.get("total"))
        sous_total = _nombre(donnees.get("subtotal"))
        taxes = sum(filter(None, (
            _nombre(donnees.get("gst")),
            _nombre(donnees.get("qst")),
            _nombre(donnees.get("other_taxes")),
        ))) or 0.0
        pourboire = _nombre(donnees.get("tip"))

        if total is None:
            return None, None, _("Le total n'a pas été lu.")
        if total <= 0:
            return None, None, _("Le total lu n'est pas un montant (%s).") % total
        if sous_total is None:
            return None, None, _("Le sous-total avant taxes n'a pas été lu.")

        if pourboire is None:
            # Le pourboire n'est pas imprimé : c'est le résidu, s'il est plausible.
            residu = round(total - (sous_total + taxes), 2)
            if residu < -TOLERANCE_BALANCE:
                return None, None, _(
                    "Le reçu ne balance pas : sous-total et taxes (%(part).2f) "
                    "dépassent le total (%(total).2f)."
                ) % {"part": sous_total + taxes, "total": total}
            if residu <= TOLERANCE_BALANCE:
                pourboire = 0.0
            elif residu > sous_total * POURBOIRE_MAX_RATIO:
                return None, None, _(
                    "L'écart entre le total et le détail (%(residu).2f) est trop "
                    "grand pour être un pourboire."
                ) % {"residu": residu}
            else:
                pourboire = residu

        if pourboire < 0:
            return None, None, _("Le pourboire lu est négatif.")

        ecart = abs((sous_total + taxes + pourboire) - total)
        if ecart > TOLERANCE_BALANCE:
            return None, None, _(
                "Le reçu ne balance pas : sous-total + taxes + pourboire = "
                "%(somme).2f, total lu = %(total).2f."
            ) % {"somme": sous_total + taxes + pourboire, "total": total}

        return total, pourboire, None

    # ------------------------------------------------------------------
    # L'application
    # ------------------------------------------------------------------

    def _apply_ocr_result(self, donnees):
        self.ensure_one()
        brut = json.dumps(donnees, indent=2, ensure_ascii=False)
        total, pourboire, motif = self._ocr_reconcilie(donnees)

        if motif:
            # 🔴 Rien n'est posé. On ne sait pas lequel des nombres est faux ;
            # en préremplir un présenterait une supposition comme un fait.
            self.write({
                "ocr_state": "doubt",
                "ocr_scanned_date": fields.Datetime.now(),
                "ocr_confidence": _nombre(donnees.get("confidence")) or 0.0,
                "ocr_raw_response": brut,
                "ocr_error_message": motif[:255],
            })
            return False

        vals = {
            "ocr_state": "done",
            "ocr_scanned_date": fields.Datetime.now(),
            "ocr_confidence": _nombre(donnees.get("confidence")) or 0.0,
            "ocr_raw_response": brut,
            "ocr_error_message": False,
            "total_amount_currency": total,
            "tip_amount_currency": pourboire,
        }

        if donnees.get("date"):
            try:
                vals["date"] = fields.Date.to_date(donnees["date"])
            except (ValueError, TypeError):
                pass

        if donnees.get("currency"):
            devise = self.env["res.currency"].search(
                [("name", "=", str(donnees["currency"]).upper())], limit=1)
            if devise:
                vals["currency_id"] = devise.id

        # Le nom seulement s'il n'a pas été écrit à la main : la description
        # que la personne a tapée vaut mieux que le nom du commerçant.
        commercant = (donnees.get("merchant_name") or "").strip()
        if commercant and self._ocr_nom_est_generique():
            vals["name"] = commercant[:200]

        self.write(vals)
        return True

    def _ocr_nom_est_generique(self):
        """Le nom vient-il du fichier plutôt que d'une personne ?

        `create_expense_from_attachments` nomme la dépense d'après le fichier
        téléversé (`IMG_4821`). Ce nom-là se remplace ; « Dîner avec le
        client X », non.
        """
        self.ensure_one()
        if not self.name:
            return True
        piece = self._ocr_attachment()
        if not piece:
            return False
        base = ".".join(piece.name.split(".")[:-1]) or piece.name
        return self.name.strip() == base.strip()

    # ------------------------------------------------------------------
    # Les deux portes du téléversement
    # ------------------------------------------------------------------

    def _ocr_auto_actif(self):
        return bool((self.company_id or self.env.company).expense_ocr_auto)

    def attach_document(self, **kwargs):
        """Une pièce jointe posée sur une dépense existante."""
        res = super().attach_document(**kwargs)
        for depense in self:
            if depense._ocr_auto_actif() and depense.ocr_state in ("none", False):
                depense._ocr_scan_silencieux()
        return res

    @api.model
    def create_expense_from_attachments(self, attachment_ids=None, view_type="list"):
        """La photo qui CRÉE la dépense — le geste du téléphone."""
        action = super().create_expense_from_attachments(
            attachment_ids=attachment_ids, view_type=view_type)
        if self.env.company.expense_ocr_auto:
            domaine = (action.get("domain") or [])
            ids = domaine[0][2] if domaine else []
            for depense in self.browse(ids):
                depense._ocr_scan_silencieux()
        return action

    def _ocr_scan_silencieux(self):
        """Lire sans jamais casser le geste qui a déclenché la lecture."""
        self.ensure_one()
        try:
            self.action_ocr_scan()
        except Exception:  # noqa: BLE001
            _logger.exception("Lecture automatique du reçu %s en échec", self.id)

    # ------------------------------------------------------------------
    # Le rattrapage
    # ------------------------------------------------------------------

    @api.model
    def _cron_ocr_batch(self, limite=20):
        """Rattraper les dépenses en brouillon jamais lues.

        ⚠️ Éteint par défaut (voir `data/ocr_cron.xml`) : un cron qui envoie
        des photos de reçus à un tiers ne s'allume pas tout seul.
        """
        candidates = self.search(
            [("state", "=", "draft"), ("ocr_state", "=", "none")],
            order="create_date desc",
            limit=limite,
        )
        a_lire = candidates.filtered(lambda d: d._ocr_attachment())
        _logger.info("Lecture des reçus : %d dépense(s) à lire", len(a_lire))
        for depense in a_lire:
            try:
                depense.action_ocr_scan()
                self.env.cr.commit()
            except Exception:  # noqa: BLE001
                _logger.exception("Lecture du reçu %s en échec", depense.id)
                self.env.cr.rollback()
        return len(a_lire)
