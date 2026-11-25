"""La pastille : un code public, une cible, un geste, et rien de secret.

🔴 **Le code n'est pas un mot de passe.** Une pastille se lit à quatre
centimètres, sans consentement et sans trace, et son contenu se recopie sur une
puce vierge pour moins d'un dollar. Tout ce que le code doit permettre, c'est de
RETROUVER la pastille. L'identité vient d'ailleurs : du jeton de l'appareil
appairé, de la session du navigateur, ou de la signature de la puce.

⚠️ ``taper()`` est le seul chemin d'exécution. Les trois contrôleurs
n'implémentent que leur façon d'établir l'identité, puis appellent cette
méthode. Un geste ajouté demain hérite donc du journal, de la garde contre le
double tapotement et du point de reprise sans qu'on y touche.
"""
import base64
import hmac as hmac_std
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools.misc import hmac as odoo_hmac

_logger = logging.getLogger(__name__)

# Sans I, O, 0 ni 1 : un code se lit à voix haute et se retape au clavier quand
# le téléphone de quelqu'un ne veut rien savoir.
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
LONGUEUR_CODE = 8

# Fenêtre pendant laquelle un deuxième tapotement identique rend le résultat du
# premier au lieu de rejouer le geste. Android distribue parfois deux fois la
# même étiquette, et un doigt qui hésite fait la même chose.
PARAM_FENETRE = "bf_nfc.fenetre_doublon_secondes"
FENETRE_DEFAUT = 20

# Un tapotement fait sans réseau est accepté jusqu'à trois jours après, pas plus.
# Au-delà, une heure « notée par le téléphone » ne se distingue plus d'une heure
# inventée : on refuse, et la ligne de journal dit pourquoi.
PARAM_DIFFERE_HEURES = "bf_nfc.differe_max_heures"
DIFFERE_DEFAUT_HEURES = 72
# En deçà de cet écart, l'heure du téléphone est celle du serveur à la dérive
# d'horloge près : le tapotement est direct, pas différé.
TOLERANCE_HORLOGE = timedelta(minutes=2)


class _Question(Exception):
    """Un geste a posé une question : on défait ce qu'il a pu toucher."""

    def __init__(self, question):
        super().__init__("question")
        self.question = question


class BfNfcTag(models.Model):
    _name = "bf.nfc.tag"
    _description = "NFC tag"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "name"

    # ⚠️ Suivi sur tout ce qui change ce que la pastille FAIT ou au nom de qui elle
    # agit : sans lui, personne ne pouvait dire qui avait changé la cible ou le
    # compte d'une pastille signée. Pas sur les compteurs, qui bougent à chaque
    # tapotement et noieraient le fil.
    name = fields.Char(string="Label", required=True, translate=False, tracking=True)
    code = fields.Char(
        required=True, copy=False, index=True, readonly=True,
        default=lambda self: self._generer_code(),
        help="What the tag carries. Public: it is used to find the tag, "
             "not to prove anything.",
    )
    active = fields.Boolean(default=True, tracking=True)
    company_id = fields.Many2one(
        "res.company", string="Company", required=True,
        default=lambda self: self.env.company,
    )
    gesture_id = fields.Many2one(
        "bf.nfc.gesture", string="Gesture", required=True, ondelete="restrict", tracking=True,
    )
    gesture_writes = fields.Boolean(related="gesture_id.writes", readonly=True)
    res_model = fields.Char(string="Target model", tracking=True)
    # 🔴 PAS de suivi sur ``res_id`` : Odoo ne sait pas suivre un Many2oneReference.
    # Avec ``tracking=True``, toute écriture de la fiche visée levait « Unsupported
    # tracking on field res_id » au vidage final de la transaction, donc après le
    # clic, jamais dans un essai qui s'arrête avant. ``res_model`` reste
    # suivi, et ``cible`` dit à l'écran quelle fiche est visée.
    res_id = fields.Many2oneReference(
        string="Target record ID", model_field="res_model",
    )
    # 🔴 Ce que l'écran montre, à la place du nom technique tapé à la main
    # (« project.task ») et d'une « Fiche cible » bloquée tant qu'il était vide.
    # Calculé depuis ``res_model``/``res_id``, qui restent la vérité en base : les
    # satellites, l'application et le journal les lisent déjà.
    cible = fields.Reference(
        selection="_selection_cible", string="Target record",
        compute="_compute_cible", inverse="_inverse_cible",
        help="The record type, then the record. The types offered are "
             "those in the “Record types” list and those a gesture "
             "requires.",
    )
    params = fields.Char(
        string="Parameters", tracking=True,
        help="Optional JSON passed to the gesture, for example "
             "{\"minutes\": 15}. Set here, by managers: the person "
             "tapping cannot override it.",
    )
    place = fields.Char(
        string="Placed on", tracking=True,
        help="Where the tag is stuck. This is what you read when looking "
             "for the one to remove.",
    )
    date_expiry = fields.Date(
        string="Expires on", tracking=True,
        help="After this date, the tag politely refuses. It is not "
             "deleted: we still want to know what it did.",
    )
    confirm_required = fields.Boolean(
        string="Confirm before acting", default=True, tracking=True,
        help="Applies to taps coming from a browser. A gesture that "
             "writes nothing always goes through without confirmation.",
    )

    # Porte signée (NTAG 424 DNA). Une pastille remise à quelqu'un qui n'a pas
    # de compte agit au nom de user_id, et seule la signature de la puce
    # autorise le geste.
    sdm_enabled = fields.Boolean(string="Signed tag (SDM)", tracking=True)
    sdm_uid = fields.Char(
        string="Chip UID", copy=False, index=True, tracking=True,
        help="The 7 bytes engraved at the factory, in hexadecimal. This "
             "is how a signed tag is recognized: its address carries no "
             "code, only the encrypted block the chip builds.",
    )
    sdm_counter = fields.Integer(
        string="Last counter seen", readonly=True, copy=False,
        help="The chip's read counter never goes back. A replayed tap "
             "carries a counter already seen and is refused.",
    )
    user_id = fields.Many2one(
        "res.users", string="Acts on behalf of", tracking=True,
        help="Required for a signed tag, ignored elsewhere: through the "
             "other two doors, the person tapping is the one acting.",
    )

    choice_ids = fields.One2many("bf.nfc.tag.choice", "tag_id", string="Menu "
                                                                       "choices", copy=True)
    gesture_kind = fields.Selection(related="gesture_id.kind", readonly=True)
    url = fields.Char(
        string="Engraved address", compute="_compute_url",
        help="What the chip carries. A signed tag has no fixed address: "
             "the chip builds it at every read.",
    )

    tap_count = fields.Integer(string="Taps", readonly=True, copy=False)
    last_tap_date = fields.Datetime(string="Last tap", readonly=True, copy=False)
    tap_ids = fields.One2many("bf.nfc.tap", "tag_id", string="Log")
    note = fields.Text()

    _sql_constraints = [
        ("code_unique", "unique(code)", "This tag code already exists."),
    ]

    # ------------------------------------------------------------------
    # La fiche visée, lisible
    # ------------------------------------------------------------------
    @api.model
    def _modeles_cibles(self, gestion=None):
        """Les modèles qu'une pastille peut viser, pour le site ET l'application.

        La liste « Types de fiche », plus les modèles qu'un geste du catalogue exige
        et ceux qu'un satellite déclare. Un modèle absent de cette base est écarté
        plutôt que de faire tomber l'écran.
        """
        if gestion is None:
            gestion = self.env.user.has_group("bf_nfc.group_nfc_manager")
        noms = self.env["bf.nfc.target.type"]._noms(gestion)
        Geste = self.env["bf.nfc.gesture"].sudo()
        gestes = Geste.search([("target_model_id", "!=", False)])
        if not gestion:
            gestes = gestes.filtered(lambda g: not g.reserve_gestion)
        noms += gestes.mapped("target_model")
        noms += Geste._modeles_supplementaires()
        vus, rendu = set(), []
        for nom in noms:
            if nom and nom not in vus and nom in self.env:
                vus.add(nom)
                rendu.append(nom)
        return rendu

    @api.model
    def _selection_cible(self):
        # ⚠️ Toujours la liste de la GESTION : c'est elle qui crée et modifie les
        # pastilles, et un interne qui ouvre la fiche en lecture doit voir le type
        # d'une pastille de tâche planifiée plutôt qu'un champ vide. Plus les types
        # déjà portés par des pastilles existantes, pour qu'une pastille créée avant
        # un retrait de la liste s'affiche encore.
        noms = self._modeles_cibles(gestion=True)
        for groupe in self.sudo()._read_group([("res_model", "!=", False)], ["res_model"]):
            if groupe[0] not in noms and groupe[0] in self.env:
                noms.append(groupe[0])
        IrModel = self.env["ir.model"].sudo()
        return [(nom, IrModel._get(nom).name or nom) for nom in noms]

    @api.depends("res_model", "res_id")
    def _compute_cible(self):
        for tag in self:
            # ⚠️ ``exists()`` en sudo : une fiche supprimée depuis la pose rendrait
            # une référence vers rien, et le formulaire tomberait en l'affichant.
            if tag.res_model and tag.res_id and tag.res_model in self.env \
                    and self.env[tag.res_model].sudo().browse(tag.res_id).exists():
                tag.cible = "%s,%s" % (tag.res_model, tag.res_id)
            else:
                tag.cible = False

    def _inverse_cible(self):
        for tag in self:
            if tag.cible:
                tag.write({"res_model": tag.cible._name, "res_id": tag.cible.id})
            else:
                tag.write({"res_model": False, "res_id": False})

    @api.onchange("gesture_id")
    def _onchange_gesture_cible(self):
        """Prévient tout de suite quand la fiche ne convient pas au geste choisi."""
        attendu = self.gesture_id.sudo().target_model
        if attendu and self.cible and self.cible._name != attendu:
            return {"warning": {
                "title": _("Record type"),
                "message": _("The gesture “%(geste)s” targets a record of "
                             "type “%(type)s”. Choose one of that type.",
                             geste=self.gesture_id.name,
                             type=self.env["ir.model"].sudo()._get(attendu).name),
            }}

    @api.depends("code", "sdm_enabled")
    def _compute_url(self):
        # 🔴 `get_base_url()` PAR pastille. Le module Site web la redéfinit avec
        # un `ensure_one` : appelée sur la liste entière, elle levait, et
        # « Mes pastilles » rendait un 500 chez tout locataire qui a le site web.
        # Un essai qui ne lit l'adresse que d'une pastille à la fois ne le voit pas.
        for tag in self:
            if tag.sdm_enabled or not tag.code:
                tag.url = False
            else:
                tag.url = "%s/nfc/%s" % (tag.get_base_url().rstrip("/"), tag.code)

    def _qr_base64(self):
        """Le code QR de l'adresse gravée, en PNG base64, ou rien pour une signée.

        ⚠️ Fabriqué en mémoire et incrusté dans le rapport, pas servi par
        `/report/barcode` : wkhtmltopdf irait chercher l'image par l'adresse
        publique de l'instance, que le conteneur ne joint pas toujours derrière
        son mandataire, et l'étiquette sortirait avec un cadre vide.
        """
        self.ensure_one()
        if not self.url:
            return False
        png = self.env["ir.actions.report"].barcode("QR", self.url, width=400, height=400)
        return base64.b64encode(png).decode()

    # ------------------------------------------------------------------
    # Code
    # ------------------------------------------------------------------
    @api.model
    def _generer_code(self):
        """Un code court, tiré au hasard, qui n'existe pas déjà.

        ⚠️ ``secrets`` et pas ``random`` : le code est court, et un générateur
        prévisible rendrait la liste des pastilles devinable même s'il ne
        donne accès à rien par lui-même.
        """
        for _essai in range(12):
            code = "".join(secrets.choice(ALPHABET) for _ in range(LONGUEUR_CODE))
            if not self.sudo().search_count([("code", "=", code)]):
                return code
        raise UserError(_("Could not draw a free tag code."))

    def action_regenerer_code(self):
        """Change le code : la pastille physique devient inerte et doit être regravée."""
        for tag in self:
            tag.code = tag._generer_code()
        return True

    # ------------------------------------------------------------------
    # Résolution et cible
    # ------------------------------------------------------------------
    @api.model
    def _resoudre(self, code):
        """Retrouve une pastille par son code, en sudo, sans rien exécuter.

        🔴 Rend un recordset vide quand le code est inconnu OU archivé : le
        contrôleur en fait un 404 franc. Jamais de redirection silencieuse vers
        une page d'accueil, parce qu'une pastille déjà gravée qui atterrit
        ailleurs ne se corrige plus.
        """
        if not code or not isinstance(code, str):
            return self.sudo().browse()
        return self.sudo().search([("code", "=", code.strip().upper())], limit=1)

    @api.model
    def _resoudre_signee(self, uid_hex):
        """Retrouve une pastille signée par l'UID gravé en usine."""
        if not uid_hex:
            return self.sudo().browse()
        return self.sudo().search([
            ("sdm_uid", "=ilike", uid_hex.strip()),
            ("sdm_enabled", "=", True),
        ], limit=1)

    # ------------------------------------------------------------------
    # La suite d'un tapotement signé
    # ------------------------------------------------------------------
    # 🔴 Jusqu'à la 2.6.1, le compteur n'était consommé qu'à l'exécution. Une
    # pastille à confirmation ou à menu laissait donc son adresse jouable
    # jusqu'au clic de la personne : recopiée depuis l'historique ou un journal
    # de mandataire, elle se jouait à sa place. Le compteur se consomme
    # maintenant à la première ouverture valide, et la page remet un jeton de
    # suite, signé par la base, qui seul ouvre le POST.
    DUREE_SUITE = 30 * 60  # un relevé à plusieurs champs se remplit sur place

    def _consommer_compteur(self, compteur):
        """Retient ``compteur`` s'il est neuf. Rend False si quelqu'un est passé avant.

        ⚠️ Un UPDATE conditionnel, pas un « lire puis écrire » : deux ouvertures
        simultanées de la même adresse lisent toutes deux l'ancien compteur, et
        les deux passeraient. Ici la seconde attend la première, relit la
        condition, et ne touche aucune ligne.

        ⚠️ ``COALESCE`` : une pastille jamais tapée porte NULL en base, pas 0,
        et ``NULL < 61`` n'est pas vrai. Le premier tapotement était refusé.
        """
        self.ensure_one()
        self.flush_recordset(["sdm_counter"])
        self.env.cr.execute(
            "UPDATE bf_nfc_tag SET sdm_counter = %s "
            "WHERE id = %s AND COALESCE(sdm_counter, 0) < %s",
            (int(compteur), self.id, int(compteur)))
        retenu = self.env.cr.rowcount == 1
        self.invalidate_recordset(["sdm_counter"])
        return retenu

    def _jeton_de_suite(self, compteur):
        """Le jeton que la page remet au POST suivant : pastille, compteur, échéance."""
        self.ensure_one()
        expire = int(datetime.now(timezone.utc).timestamp()) + self.DUREE_SUITE
        charge = "%d.%d.%d" % (self.id, int(compteur), expire)
        return "%s.%s" % (charge, odoo_hmac(self.env(su=True), "bf_nfc.suite_signee", charge))

    @api.model
    def _lire_jeton_de_suite(self, jeton):
        """Rend (pastille, compteur, None) quand le jeton ouvre encore la suite.

        Refusé quand il est altéré ou échu, quand un tapotement plus récent l'a
        dépassé, ou quand la suite est close : un geste l'a déjà menée à un
        résultat (réussi, refusé ou en erreur), ce que dit le journal.
        """
        refus = _("This tap has already been used. Bring the phone close "
                  "to the tag again.")
        morceaux = (jeton or "").split(".")
        if len(morceaux) != 4 or not all(m.isdigit() for m in morceaux[:3]):
            return self.browse(), 0, refus
        charge = ".".join(morceaux[:3])
        attendu = odoo_hmac(self.env(su=True), "bf_nfc.suite_signee", charge)
        if not hmac_std.compare_digest(attendu, morceaux[3]):
            _logger.warning("Pastille signée : jeton de suite altéré")
            return self.browse(), 0, refus
        tag_id, compteur, expire = (int(m) for m in morceaux[:3])
        if expire < datetime.now(timezone.utc).timestamp():
            return self.browse(), 0, refus
        tag = self.sudo().browse(tag_id).exists()
        if not tag or not tag.sdm_enabled or tag.sdm_counter != compteur:
            return self.browse(), 0, refus
        if self.env["bf.nfc.tap"].sudo().search_count([
                ("tag_id", "=", tag.id), ("door", "=", "signed"),
                ("counter", "=", compteur)], limit=1):
            return self.browse(), 0, refus
        return tag, compteur, None

    def _cible(self, superutilisateur=False):
        """L'enregistrement visé, lu avec les droits de la personne qui tape.

        ⚠️ ``sudo(False)`` explicite : ``self`` arrive presque toujours en sudo
        (la pastille a été retrouvée par son code), et sans ce retour en arrière
        le geste lirait la fiche du voisin sans que personne ne s'en aperçoive.
        """
        self.ensure_one()
        if not (self.res_model and self.res_id):
            return self.env["bf.nfc.tag"].browse()
        if self.res_model not in self.env:
            raise UserError(_("The model “%s” does not exist on this system.", self.res_model))
        modele = self.env[self.res_model]
        if superutilisateur:
            return modele.browse(self.res_id)
        cible = modele.sudo(False).browse(self.res_id)
        # 🔴 Contrôle EXPLICITE, et pas seulement le ``sudo(False)`` ci-dessus.
        # Un champ déjà chargé dans le cache de la transaction se relit sans
        # repasser par les droits : le premier essai écrit pour cette garde la
        # voyait passer alors qu'elle ne contrôlait rien, parce que la fiche
        # venait d'être créée dans la même transaction. Le contrôle explicite ne
        # dépend pas de ce qui se trouve en cache.
        cible.check_access("read")
        return cible

    def _params(self):
        """Les paramètres de la pastille, et eux seuls.

        🔴 **Rien de ce que la personne qui tape envoie n'entre ici.** Jusqu'à la
        2.2.0, la chaîne de requête du navigateur et le dict ``params`` de
        l'application s'ajoutaient PAR-DESSUS ceux de la pastille : un
        ``?url=`` remplaçait l'adresse gravée, un ``?equipe=`` envoyait le billet
        (créé en sudo) dans l'équipe de son choix, y compris par la porte signée,
        qui est publique. Ce qu'une pastille fait se décide à sa création, par la
        gestion. Ce que qui tape apporte a ses propres canaux, bornés : ``choix``
        et ``texte``.
        """
        self.ensure_one()
        if not self.params:
            return {}
        try:
            charge = json.loads(self.params)
        except ValueError:
            raise UserError(_("This tag's parameters are not valid JSON."))
        if not isinstance(charge, dict):
            raise UserError(_("A tag's parameters must be a JSON object."))
        return charge

    # ------------------------------------------------------------------
    # Le tapotement
    # ------------------------------------------------------------------
    # 🔴 `api.private` : sans lui, tout interne appelait `taper` par RPC
    # (`/web/dataset/call_kw`) en choisissant la porte et le COMPTEUR d'une
    # pastille signée. Remettre le compteur à zéro rouvrait le rejeu des anciens
    # tapotements signés ; le pousser très haut rendait la pastille inerte. Seuls
    # les contrôleurs, qui établissent l'identité et vérifient la signature,
    # appellent cette méthode.
    @api.private
    def taper(self, porte, appareil=None, compteur=None,
              choix=None, texte=None, quand=None, nonce=None, appareil_id=None,
              reponses=None):
        """Exécute le geste et journalise, que ça passe ou non.

        Rend un dictionnaire ``{statut, titre, message, url, tap_id}``, plus
        ``choix`` quand le geste pose une question. Ne lève jamais pour une
        erreur métier : l'erreur devient une phrase à l'écran, et la ligne de
        journal la garde.

        🔴 Le point de reprise n'est pas un ornement. Sans lui, un geste en deux
        écritures dont la seconde échoue laisse la première en base pendant que
        l'écran annonce un échec : la personne retape, et la première écriture
        part deux fois. C'est le défaut exact du webhook natif d'Odoo.

        🔴 **Une question n'écrit rien.** Un geste peut rendre des choix au lieu
        d'agir (« Prendre 30 min », « J'arrive ») : tout ce qu'il a touché avant
        est défait avec le point de reprise, et rien n'est journalisé. Le choix
        revient ensuite par ``choix``, et c'est ce second appel qui agit.

        ⚠️ ``nonce`` rend un envoi rejoué idempotent : la file hors ligne du
        téléphone peut renvoyer trois fois le même tapotement, le geste n'est
        fait qu'une fois. ``quand`` est l'heure notée par le téléphone.

        ``reponses`` remplit le ``formulaire`` qu'un geste a demandé (un relevé,
        l'identité d'une personne sans compte) : un dict de textes courts, borné
        ici, que seul le geste interprète.
        """
        self.ensure_one()
        tag = self.sudo()
        acteur = self.env.user
        trace = {"appareil": appareil, "compteur": compteur, "acteur": acteur,
                 "choix": choix, "nonce": nonce, "appareil_id": appareil_id}

        if nonce:
            deja = self.env["bf.nfc.tap"].sudo().search([
                ("nonce", "=", nonce), ("user_id", "=", acteur.id)], limit=1)
            if deja:
                return tag._rendu(deja)

        moment, differe, refus_heure = tag._moment(quand)
        trace.update({"quand": moment, "differe": differe})

        refus = tag._refus_eventuel(porte) or refus_heure
        if not refus and differe and not tag.gesture_id.accepte_differe \
                and tag.gesture_id.kind != "menu":
            refus = _("This gesture cannot be sent later: tap the tag "
                      "again, on site and with a network.")
        if refus:
            return tag._journaliser(porte, "refused", refus, **trace)

        double = tag._tapotement_recent(acteur, choix=choix, moment=moment)
        if double:
            return dict(tag._rendu(double), statut="duplicate")

        geste = tag.gesture_id.sudo(False)
        valeurs = tag._params()
        valeurs.update({"choix": choix, "quand": moment, "differe": differe, "porte": porte})
        # ⚠️ Seulement quand quelqu'un a écrit : un `texte` absent ne doit pas
        # effacer celui que la pastille porte dans ses paramètres (« Ronde du soir »).
        if texte:
            valeurs["texte"] = texte
        valeurs["reponses"] = self._nettoyer_reponses(reponses)
        try:
            with self.env.cr.savepoint():
                resultat = geste.executer(tag, None, valeurs)
                if resultat.get("choix") is not None:
                    raise _Question(resultat)
        except _Question as question:
            self.env.invalidate_all()
            if differe:
                return tag._journaliser(
                    porte, "refused",
                    _("This tag asks for a choice: tap it again on site."),
                    **trace)
            q = question.question
            return {
                "statut": "choice" if q["choix"] else "info",
                "titre": q.get("titre") or tag.name,
                "message": q.get("message") or "",
                "choix": q["choix"],
                "formulaire": q.get("formulaire") or None,
                "url": None,
                "tap_id": None,
            }
        except (UserError, AccessError) as exc:
            return tag._journaliser(porte, "refused", str(exc), **trace)
        except Exception:  # noqa: BLE001
            _logger.exception("Pastille %s : le geste a levé", tag.code)
            return tag._journaliser(
                porte, "error",
                _("The gesture did not complete. Nothing was left half done."),
                **trace)

        ecrit = tag._journaliser(
            porte, "ok", resultat.get("message") or "",
            titre=resultat.get("titre"), url=resultat.get("url"), **trace)
        tag.write({
            "tap_count": tag.tap_count + 1,
            "last_tap_date": fields.Datetime.now(),
        })
        if compteur is not None:
            tag.sdm_counter = compteur
        return ecrit

    @api.model
    def _nettoyer_reponses(self, reponses):
        """Les réponses d'un formulaire, bornées : 60 champs, des textes de 1 000 caractères.

        ⚠️ Rien n'est interprété ici. Le geste qui a posé le formulaire sait ce que
        chaque champ doit contenir ; le socle ne fait que refuser ce qui ne
        ressemble pas à des réponses (une liste, un objet imbriqué, un roman).
        """
        if not isinstance(reponses, dict):
            return {}
        propres = {}
        for cle, valeur in list(reponses.items())[:60]:
            if not isinstance(cle, str) or not cle or len(cle) > 64:
                continue
            if isinstance(valeur, bool):
                valeur = "oui" if valeur else "non"
            if isinstance(valeur, (int, float)):
                valeur = str(valeur)
            if isinstance(valeur, str):
                propres[cle] = valeur.strip()[:1000]
        return propres

    def _moment(self, quand):
        """L'heure du tapotement : (moment, différé, phrase de refus ou None).

        ⚠️ L'heure vient du téléphone : elle est BORNÉE. Plus de quelques
        minutes dans le futur, c'est une horloge fausse ; plus de trois jours
        dans le passé, ce n'est plus vérifiable. Dans les deux cas on refuse
        plutôt que d'écrire une heure qu'on ne peut pas défendre.
        """
        maintenant = fields.Datetime.now()
        if not quand:
            return maintenant, False, None
        if isinstance(quand, str):
            try:
                lu = datetime.fromisoformat(quand.strip().replace("Z", "+00:00"))
            except ValueError:
                return maintenant, False, _("The time of this tap cannot "
                                            "be read.")
        else:
            lu = quand
        if lu.tzinfo:
            lu = lu.astimezone(timezone.utc).replace(tzinfo=None)
        if lu > maintenant + TOLERANCE_HORLOGE:
            return maintenant, False, _("The time of this tap is in the "
                                        "future: the phone's clock is "
                                        "wrong.")
        if maintenant - lu <= TOLERANCE_HORLOGE:
            return maintenant, False, None
        heures = int(self.env["ir.config_parameter"].sudo().get_param(
            PARAM_DIFFERE_HEURES, DIFFERE_DEFAUT_HEURES))
        if maintenant - lu > timedelta(hours=heures):
            return lu, True, _("This tap is more than %s hours old: it is "
                               "no longer accepted.", heures)
        return lu, True, None

    def _rendu(self, tap):
        return {
            "statut": tap.status,
            "titre": tap.titre,
            "message": tap.message,
            "url": tap.url or None,
            "tap_id": tap.id,
        }

    def _refus_eventuel(self, porte):
        """La phrase de refus, ou rien quand la pastille a le droit d'agir."""
        self.ensure_one()
        if not self.active:
            return _("This tag has been taken out of service.")
        if self.date_expiry and self.date_expiry < fields.Date.context_today(self):
            return _("This tag expired on %s.", self.date_expiry)
        if self.gesture_id.needs_target and not (self.res_model and self.res_id):
            return _("This tag points to no record.")
        if self.gesture_id.target_model_id \
                and self.res_model != self.gesture_id.target_model:
            return _("This tag points to a type of record the gesture "
                     "does not accept.")
        if porte == "signed":
            if not self.sdm_enabled:
                return _("This tag is not a signed tag.")
            if not self.user_id:
                return _("This signed tag names no account to act on "
                         "behalf of.")
        return None

    def _tapotement_recent(self, acteur, choix=None, moment=None):
        """Le tapotement identique d'il y a quelques secondes, s'il existe.

        ⚠️ La fenêtre se compte par pastille, par personne ET par choix : deux
        personnes qui tapent la même pastille au même moment font bien deux
        gestes, et « Je le prends » suivi de « Je le rapporte » aussi.

        ⚠️ Autour de l'heure du TAPOTEMENT, pas de la réception : deux
        tapotements hors ligne rapprochés, reçus ensemble une heure plus tard,
        restent un doublon.
        """
        self.ensure_one()
        fenetre = int(self.env["ir.config_parameter"].sudo().get_param(
            PARAM_FENETRE, FENETRE_DEFAUT))
        if fenetre <= 0:
            return self.env["bf.nfc.tap"].browse()
        centre = moment or fields.Datetime.now()
        return self.env["bf.nfc.tap"].sudo().search([
            ("tag_id", "=", self.id),
            ("user_id", "=", acteur.id),
            ("status", "=", "ok"),
            ("choice_key", "=", choix or False),
            ("tapped_at", ">=", centre - timedelta(seconds=fenetre)),
            ("tapped_at", "<=", centre + timedelta(seconds=fenetre)),
        ], order="tapped_at desc", limit=1)

    def _journaliser(self, porte, statut, message, titre=None, url=None,
                     appareil=None, compteur=None, acteur=None, choix=None,
                     nonce=None, appareil_id=None, quand=None, differe=False):
        """Écrit la ligne de journal et rend ce que le contrôleur doit afficher."""
        self.ensure_one()
        acteur = acteur or self.env.user
        tap = self.env["bf.nfc.tap"].sudo().create({
            "tag_id": self.id,
            "tag_code": self.code,
            "gesture_code": self.gesture_id.code,
            "user_id": acteur.id,
            "door": porte,
            "status": statut,
            "titre": (titre or self.name)[:200],
            "message": (message or "")[:500],
            "url": url or False,
            "device_label": (appareil or "")[:120],
            "counter": compteur or 0,
            "choice_key": choix or False,
            "nonce": nonce or False,
            "device_id": appareil_id or False,
            "tapped_at": quand or fields.Datetime.now(),
            "offline": bool(differe),
        })
        return self._rendu(tap)

    # ------------------------------------------------------------------
    # Confort
    # ------------------------------------------------------------------
    def action_voir_journal(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Log of “%s”", self.name),
            "res_model": "bf.nfc.tap",
            "view_mode": "list,form",
            "domain": [("tag_id", "=", self.id)],
            "context": {"default_tag_id": self.id},
        }

    def action_ouvrir_cible(self):
        self.ensure_one()
        cible = self._cible()
        return {
            "type": "ir.actions.act_window",
            "res_model": cible._name,
            "res_id": cible.id,
            "view_mode": "form",
        }
