import functools
import logging
from datetime import datetime, timedelta

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)

# Ce qui n'a jamais rien à conserver : des paquets d'actifs reconstruits à
# chaque déploiement, des icônes, le contenu binaire d'un tableur intégré, et
# les pièces de conservation de ce module (sans quoi le contrôle d'accès
# tournerait en rond).
MODELES_EXCLUS = (
    "ir.ui.view",
    "ir.asset",
    "ir.module.module",
    "ir.actions.report",
    "spreadsheet.dashboard",
    "bf.attachment.version",
)

# Les formats que les deux éditeurs bureautiques savent écrire, plus le PDF.
EXTENSIONS = (
    "csv,doc,docx,dotx,fodp,fods,fodt,md,odp,ods,odt,otp,ots,ott,"
    "pdf,ppt,pptx,rtf,txt,xls,xlsx"
)

MAX_VERSIONS = 20
MAX_JOURS = 0  # 0 = pas de limite d'âge
TAILLE_MAX_MO = 50


class BfAttachmentVersion(models.Model):
    """Un état antérieur du contenu d'une pièce jointe.

    Le contenu conservé vit dans une AUTRE ``ir.attachment`` (``content_id``)
    plutôt que dans un champ binaire de ce modèle. Deux raisons, et la seconde
    est la vraie :

    1. On récupère gratuitement la route de téléchargement, l'indexation et le
       ramasse-miettes du magasin de fichiers.
    2. Le magasin range par sha1 : la pièce de conservation retrouve le fichier
       déjà écrit et n'ajoute **aucun octet**. Le ramasse-miettes, lui, garde
       tout fichier encore référencé par une ligne d'``ir_attachment``, donc
       l'écrasement de l'original ne l'emporte pas.
    """

    _name = "bf.attachment.version"
    _description = "Version d'une pièce jointe"
    _order = "attachment_id, numero desc"

    attachment_id = fields.Many2one(
        "ir.attachment",
        string="Pièce jointe",
        required=True,
        index=True,
        ondelete="cascade",
    )
    content_id = fields.Many2one(
        "ir.attachment",
        string="Contenu conservé",
        ondelete="cascade",
        help="La pièce jointe qui porte les octets d'avant le remplacement.",
    )
    numero = fields.Integer(string="Version", required=True, default=1)
    name = fields.Char(string="Nom du fichier", required=True)
    mimetype = fields.Char(string="Type MIME")
    file_size = fields.Integer(string="Taille (octets)")
    checksum = fields.Char(string="Empreinte", index=True)
    origine = fields.Selection(
        [
            ("onlyoffice", "Éditeur ONLYOFFICE"),
            ("collabora", "Éditeur Collabora"),
            ("interface", "Interface Odoo"),
            ("autre", "Autre"),
        ],
        string="Origine du remplacement",
        default="autre",
    )
    res_model = fields.Char(
        string="Modèle lié", related="attachment_id.res_model", readonly=True)
    res_id = fields.Many2oneReference(
        string="Enregistrement lié", related="attachment_id.res_id",
        model_field="res_model", readonly=True)

    _sql_constraints = [
        (
            "numero_unique",
            "unique(attachment_id, numero)",
            "Deux versions ne peuvent pas porter le même numéro pour une même pièce jointe.",
        ),
    ]

    @api.depends("name", "numero")
    def _compute_display_name(self):
        for version in self:
            version.display_name = "%s (v%s)" % (version.name or "", version.numero)

    # ------------------------------------------------------------------
    # Réglages
    # ------------------------------------------------------------------
    def _bf_param(self, cle, defaut):
        return self.env["ir.config_parameter"].sudo().get_param(
            "bf_attachment_version." + cle, defaut)

    @api.model
    def _bf_actif(self):
        valeur = self._bf_param("actif", None)
        if valeur in (None, False, ""):
            return True
        return str(valeur).strip().lower() not in ("0", "false", "no", "non")

    @api.model
    def _bf_extensions(self):
        brut = self._bf_param("extensions", EXTENSIONS) or ""
        return {e.strip().lower().lstrip(".") for e in brut.split(",") if e.strip()}

    @api.model
    def _bf_modeles_exclus(self):
        brut = self._bf_param("modeles_exclus", ",".join(MODELES_EXCLUS)) or ""
        exclus = {m.strip() for m in brut.split(",") if m.strip()}
        # Les pièces de conservation ne se versionnent JAMAIS, quoi que dise le
        # réglage : les en retirer ferait boucler le contrôle d'accès.
        exclus.add("bf.attachment.version")
        return exclus

    @api.model
    def _bf_entier(self, cle, defaut):
        try:
            return int(self._bf_param(cle, defaut) or 0)
        except (TypeError, ValueError):
            return defaut

    @api.model
    def _bf_max_versions(self):
        return max(0, self._bf_entier("max_versions", MAX_VERSIONS))

    @api.model
    def _bf_max_jours(self):
        return max(0, self._bf_entier("max_jours", MAX_JOURS))

    @api.model
    def _bf_taille_max_octets(self):
        return max(0, self._bf_entier("taille_max_mo", TAILLE_MAX_MO)) * 1024 * 1024

    # ------------------------------------------------------------------
    # Contrôle d'accès : une version suit la pièce jointe d'origine
    # ------------------------------------------------------------------
    @api.model
    def _search(self, domain, offset=0, limit=None, order=None):
        """Ne rendre que les versions dont la pièce parente est lisible.

        ``ir.attachment`` fait déjà porter ses permissions par l'enregistrement
        auquel il est rattaché. Sans ce filtre, n'importe quel usager interne
        lirait l'ancien contenu d'une pièce qu'il ne peut pas ouvrir, ce qui
        transformerait le module en contournement de règles.

        Le filtrage se fait sur les lignes DÉJÀ retenues, pas sur toute la
        table : les versions sont peu nombreuses, alors qu'un balayage de
        ``ir_attachment`` coûterait cher à chaque lecture.
        """
        requete = super()._search(domain, offset, limit, order)
        if self.env.su or self.env.is_superuser():
            return requete
        lignes = self.env.execute_query(requete.select(
            self._field_to_sql(self._table, "id"),
            self._field_to_sql(self._table, "attachment_id"),
        ))
        if not lignes:
            return requete
        pieces = {ligne[1] for ligne in lignes if ligne[1]}
        # Le domaine nomme `id`, donc ir.attachment n'y ajoute pas son filtre
        # implicite sur res_field : les pièces de champ restent visibles si la
        # personne y a droit.
        permises = set(self.env["ir.attachment"]._search([("id", "in", list(pieces))]))
        gardees = [ligne[0] for ligne in lignes if ligne[1] in permises]
        return self.browse(gardees)._as_query(order)

    def _check_access(self, operation):
        """Rendre `has_access` cohérent avec ce qui se passe vraiment.

        ⚠️ **Ce n'est pas ici que la lecture est bloquée.** Mesuré sur banc :
        sans cette surcharge, `read()` et l'accès à un champ sur une version
        obtenue par `browse(identifiant deviné)` lèvent DÉJÀ `AccessError`, et
        une recherche ne la rend pas. C'est `_search` qui tient ces trois
        chemins, et quatre essais le prouvent en tombant quand on le retire.

        Ce qui manquait est plus étroit : `has_access("read")` répondait **oui**
        sur une version illisible. Un appelant qui demande « ai-je le droit ? »
        avant d'agir recevait donc une réponse fausse, alors que l'action, elle,
        aurait été refusée. La surcharge porte sur `_check_access` et non sur
        `check_access` parce que c'est lui qui alimente `has_access` et
        `_filtered_access` en plus de `check_access`.
        """
        resultat = super()._check_access(operation)
        if self.env.su or not self:
            return resultat
        deja = resultat[0] if resultat else self.browse()
        reste = self - deja
        if not reste:
            return resultat
        pieces = reste.sudo().mapped("attachment_id")
        permises = set(
            self.env["ir.attachment"]._search([("id", "in", pieces.ids)])
        ) if pieces else set()
        interdites = reste.sudo().filtered(
            lambda version: version.attachment_id.id not in permises)
        if not interdites:
            return resultat
        toutes = deja | self.browse(interdites.ids)
        return toutes, functools.partial(self._bf_refus_acces, operation)

    def _bf_refus_acces(self, operation):
        return AccessError(_(
            "Vous n'avez pas accès à la pièce jointe dont vient cette version."))

    # ------------------------------------------------------------------
    # Cycle de vie
    # ------------------------------------------------------------------
    # La pièce de conservation part avec la version SANS code de notre part :
    # `BaseModel.unlink` supprime lui-même, en sudo, toute `ir.attachment` dont
    # le couple (res_model, res_id) désigne un enregistrement effacé. C'est
    # justement pour ça que le contenu porte `res_model='bf.attachment.version'`
    # plutôt qu'un simple lien. Un `unlink` maison ici lèverait `MissingError`
    # en essayant de supprimer une pièce déjà partie.
    # `test_suppression_de_la_piece_emporte_versions_et_contenus` tient cet
    # acquis : si Odoo changeait d'avis, il tomberait.

    # ------------------------------------------------------------------
    # Écriture des instantanés
    # ------------------------------------------------------------------
    @api.model
    def _bf_enregistrer(self, instantanes):
        """Créer les versions décrites par ``instantanes``.

        Appelée en ``sudo`` par le crochet d'``ir.attachment`` : la personne qui
        remplace le contenu a le droit d'écrire la pièce, pas forcément celui de
        créer des enregistrements de ce modèle.
        """
        Piece = self.env["ir.attachment"].sudo()
        versions = self.browse()
        for instantane in instantanes:
            dernier = self.sudo().search(
                [("attachment_id", "=", instantane["attachment_id"])],
                order="numero desc", limit=1)
            version = self.sudo().create({
                "attachment_id": instantane["attachment_id"],
                "numero": (dernier.numero or 0) + 1,
                "name": instantane["name"],
                "mimetype": instantane["mimetype"],
                "file_size": instantane["file_size"],
                "checksum": instantane["checksum"],
                "origine": instantane["origine"],
            })
            contenu = Piece.with_context(bf_sans_version=True).create({
                "name": instantane["name"],
                "raw": instantane["raw"],
                "mimetype": instantane["mimetype"],
                "res_model": "bf.attachment.version",
                "res_id": version.id,
            })
            version.content_id = contenu.id
            versions |= version
        versions._bf_appliquer_retention()
        return versions

    def _bf_appliquer_retention(self):
        """Ne garder que les N versions les plus récentes de chaque pièce."""
        plafond = self._bf_max_versions()
        if not plafond or not self:
            return
        for piece_id in set(self.mapped("attachment_id").ids):
            toutes = self.sudo().search(
                [("attachment_id", "=", piece_id)], order="numero desc")
            surplus = toutes[plafond:]
            if surplus:
                surplus.unlink()

    @api.model
    def _cron_purger(self):
        """Appliquer les deux plafonds à tout le parc, pas seulement au dernier écrit."""
        plafond = self._bf_max_versions()
        jours = self._bf_max_jours()
        supprimees = 0

        if jours:
            limite = fields.Datetime.to_string(datetime.now() - timedelta(days=jours))
            vieilles = self.sudo().search([("create_date", "<", limite)])
            supprimees += len(vieilles)
            vieilles.unlink()

        if plafond:
            self.env.cr.execute(
                """
                SELECT attachment_id FROM bf_attachment_version
                GROUP BY attachment_id HAVING count(*) > %s
                """,
                (plafond,),
            )
            for (piece_id,) in self.env.cr.fetchall():
                toutes = self.sudo().search(
                    [("attachment_id", "=", piece_id)], order="numero desc")
                surplus = toutes[plafond:]
                supprimees += len(surplus)
                surplus.unlink()

        if supprimees:
            _logger.info("Versions de pièces jointes purgées : %s", supprimees)
        return supprimees

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_telecharger(self):
        self.ensure_one()
        if not self.content_id:
            raise UserError(_("Cette version n'a plus de contenu conservé."))
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%s?download=true" % self.content_id.id,
            "target": "self",
        }

    def action_restaurer(self):
        """Remettre ce contenu dans la pièce jointe d'origine.

        La restauration passe par ``write`` sans court-circuit : elle crée donc
        elle-même une version de ce qu'elle remplace. Revenir en arrière ne perd
        rien non plus.
        """
        self.ensure_one()
        if not self.content_id:
            raise UserError(_("Cette version n'a plus de contenu conservé."))
        if not self.attachment_id:
            raise UserError(_("La pièce jointe d'origine n'existe plus."))
        self.attachment_id.check("write")
        octets = self.content_id.sudo().raw
        # Volontairement PAS en sudo : la version que la restauration crée doit
        # porter le nom de la personne qui restaure, pas celui du superusager.
        self.attachment_id.write({
            "raw": octets,
            "mimetype": self.mimetype or self.attachment_id.mimetype,
        })
        return True
