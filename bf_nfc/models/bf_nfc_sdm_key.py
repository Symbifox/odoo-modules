"""Les clés des pastilles signées (NTAG 424 DNA), une paire par société.

🔴 **Une clé posée ne se relit jamais.** Ni à l'écran, ni par l'API : les champs
chiffrés sont réservés à l'administrateur système, et même lui n'y lit qu'un
jeton Fernet. On la pose, on la remplace, on la retire. Une clé qui s'affiche se
photographie.

⚠️ Jusqu'à la 2.2.0, les clés vivaient en clair dans deux paramètres système, que
seul l'administrateur système pouvait poser : la gestion des pastilles ne pouvait
donc pas administrer la porte signée. La migration 2.3.0 les déplace ici et les
efface. La lecture de l'ancien emplacement reste en repli, pour qu'une instance
où quelqu'un les aurait reposées à la main ne cesse pas de fonctionner en silence.
"""
import logging
import re

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from . import chiffrement

_logger = logging.getLogger(__name__)

HEX_32 = re.compile(r"^[0-9A-Fa-f]{32}$")


class BfNfcSdmKey(models.Model):
    _name = "bf.nfc.sdm.key"
    _description = "Clés des pastilles signées"
    _order = "company_id"
    _rec_name = "company_id"

    company_id = fields.Many2one("res.company", string="Société", required=True,
                                 ondelete="cascade", index=True)
    meta_key_enc = fields.Char(groups="base.group_system", copy=False)
    file_key_enc = fields.Char(groups="base.group_system", copy=False)
    # ⚠️ Facultative, et son absence se paie à la gravure : une puce dont la clé
    # d'usine n'a pas été remplacée se regrave par n'importe qui. Personne ne peut
    # pour autant forger sa signature, qui dépend des deux autres clés.
    master_key_enc = fields.Char(groups="base.group_system", copy=False)
    set_date = fields.Datetime(string="Posées le", readonly=True)
    set_by_id = fields.Many2one("res.users", string="Posées par", readonly=True)
    state = fields.Selection(
        [("ok", "Lisibles"), ("illisibles", "À reposer")],
        string="État", compute="_compute_state",
        help="« À reposer » : la clé de chiffrement a changé depuis la pose, les "
             "clés ne se déchiffrent plus et les pastilles signées sont refusées.")

    _sql_constraints = [
        ("societe_unique", "unique(company_id)", "Cette société a déjà ses clés."),
    ]

    def _compute_state(self):
        for ligne in self:
            meta, fichier = ligne.sudo()._en_clair()
            ligne.state = "ok" if meta and fichier else "illisibles"

    def _en_clair(self):
        self.ensure_one()
        return (chiffrement.dechiffrer(self.env, self.meta_key_enc),
                chiffrement.dechiffrer(self.env, self.file_key_enc))

    @api.model
    def _cles_de(self, company):
        """(clé de métadonnées, clé de fichier) d'une société, en clair, ou (None, None).

        Réservé aux contrôleurs qui vérifient une signature : jamais rendu à un écran.
        """
        ligne = self.sudo().search([("company_id", "=", company.id)], limit=1)
        if ligne:
            return ligne._en_clair()
        icp = self.env["ir.config_parameter"].sudo()

        def ancien(suffixe):
            return icp.get_param("bf_nfc.%s.%s" % (suffixe, company.id)) \
                or icp.get_param("bf_nfc.%s" % suffixe)
        return ancien("sdm_meta_key"), ancien("sdm_file_key")

    @api.model
    def _cle_maitresse_de(self, company):
        """La clé 0 d'une société, en clair, ou rien si elle n'a pas été posée."""
        ligne = self.sudo().search([("company_id", "=", company.id)], limit=1)
        if not ligne:
            return None
        return chiffrement.dechiffrer(self.env, ligne.master_key_enc)

    @api.model
    def _poser_la_maitresse(self, company, valeur):
        """Pose la clé 0. Réservé à la gestion, comme les deux autres."""
        if not self.env.user.has_group("bf_nfc.group_nfc_manager"):
            raise AccessError(_("Poser la clé maîtresse est réservé à la gestion."))
        valeur = (valeur or "").strip()
        if not HEX_32.match(valeur):
            raise UserError(_("La clé maîtresse doit compter 32 caractères "
                              "hexadécimaux (16 octets AES)."))
        ligne = self.sudo().search([("company_id", "=", company.id)], limit=1)
        if not ligne:
            raise UserError(_("Posez d'abord la paire de clés de cette société."))
        ligne.master_key_enc = chiffrement.chiffrer(self.env, valeur.upper())
        return ligne

    @api.model
    def _chiffrer_une_cle(self, valeur, nom):
        """Valide une clé saisie et rend sa forme chiffrée. Le clair ne sort pas d'ici."""
        valeur = (valeur or "").strip()
        if not valeur:
            return False
        if not HEX_32.match(valeur):
            raise UserError(_("La clé %s doit compter 32 caractères hexadécimaux "
                              "(16 octets AES).", nom))
        return chiffrement.chiffrer(self.env, valeur.upper())

    @api.model
    def _poser_chiffre(self, company, meta_enc, fichier_enc):
        """Range une paire DÉJÀ chiffrée. Réservé à la gestion.

        ⚠️ Relit les deux avant d'écrire : une paire qu'on ne sait plus déchiffrer
        (clé de chiffrement remplacée entre la saisie et le clic) serait posée
        illisible, et les pastilles signées se feraient refuser sans qu'on sache
        pourquoi.
        """
        if not self.env.user.has_group("bf_nfc.group_nfc_manager"):
            raise AccessError(_("Poser les clés des pastilles signées est réservé à la gestion."))
        if not all(chiffrement.dechiffrer(self.env, jeton) for jeton in (meta_enc, fichier_enc)):
            raise UserError(_("Les clés saisies ne se relisent pas : la clé de chiffrement "
                              "a changé entre la saisie et l'enregistrement. Ressaisissez-les."))
        return self._ranger(company, meta_enc, fichier_enc)

    def _ranger(self, company, meta_enc, fichier_enc):
        valeurs = {"meta_key_enc": meta_enc, "file_key_enc": fichier_enc,
                   "set_date": fields.Datetime.now(), "set_by_id": self.env.user.id}
        ligne = self.sudo().search([("company_id", "=", company.id)], limit=1)
        if ligne:
            ligne.write(valeurs)
        else:
            ligne = self.sudo().create(dict(valeurs, company_id=company.id))
        return ligne

    @api.model
    def _poser(self, company, meta, fichier):
        """Chiffre et range la paire d'une société. Réservé à la gestion."""
        if not self.env.user.has_group("bf_nfc.group_nfc_manager"):
            raise AccessError(_("Poser les clés des pastilles signées est réservé à la gestion."))
        meta, fichier = (meta or "").strip(), (fichier or "").strip()
        for nom, valeur in ((_("de métadonnées"), meta), (_("de fichier"), fichier)):
            if not HEX_32.match(valeur):
                raise UserError(_("La clé %s doit compter 32 caractères hexadécimaux "
                                  "(16 octets AES).", nom))
        return self._ranger(company, chiffrement.chiffrer(self.env, meta.upper()),
                            chiffrement.chiffrer(self.env, fichier.upper()))

    @api.model
    def _migrer_les_parametres(self):
        """Déplace les clés en clair des paramètres système vers ce modèle, puis les efface.

        🔴 **N'efface que ce qui a été repris.** Une paire incomplète (seule la clé
        de métadonnées posée) ou mal formée (un espace, 30 caractères, un préfixe
        « 0x ») n'est pas déplaçable : l'effacer ferait disparaître de la base les
        16 octets gravés en usine dans des NTAG 424, qui ne se relisent nulle part.
        Ces paramètres-là restent, et un avertissement les nomme.
        """
        icp = self.env["ir.config_parameter"].sudo()
        anciens = icp.search([("key", "=like", "bf_nfc.sdm_%_key%")])
        if not anciens:
            return 0
        valeurs = {p.key: p.value for p in anciens}
        deplacees, repris = 0, set()
        for societe in self.env["res.company"].sudo().search([]):
            noms = []
            paire = []
            for suffixe in ("sdm_meta_key", "sdm_file_key"):
                propre = "bf_nfc.%s.%s" % (suffixe, societe.id)
                general = "bf_nfc.%s" % suffixe
                nom = propre if propre in valeurs else (general if general in valeurs else None)
                noms.append(nom)
                paire.append((valeurs.get(nom) or "").strip())
            if not all(paire) or not all(HEX_32.match(v) for v in paire):
                continue
            self.sudo().with_user(self.env.ref("base.user_root"))._poser(societe, *paire)
            deplacees += 1
            repris.update(n for n in noms if n)
        restants = anciens.filtered(lambda p: p.key not in repris)
        if restants:
            _logger.warning(
                "bf_nfc : %s laissés en place, faute d'une paire complète et bien formée "
                "à déplacer. Reposez les clés depuis Réglages, puis supprimez ces paramètres.",
                ", ".join(restants.mapped("key")))
        (anciens - restants).unlink()
        return deplacees
