import base64
import csv
import io
import unicodedata
from datetime import datetime

from markupsafe import Markup, escape

from odoo import _, fields, models
from odoo.exceptions import UserError

# Les en-têtes reconnus, en français et en anglais, sans accents ni casse. La
# liste reste ouverte : une plateforme qui nomme ses colonnes autrement
# s'ajoute ici. ⚠️ Aucun gabarit d'export de Zeffy ou d'une plateforme
# nationale n'a été validé sur un vrai fichier : à éprouver avec le premier.
ALIASES = {
    "email": ["email", "courriel", "adresse courriel", "e-mail", "adresse e-mail", "mail"],
    "first_name": ["prenom", "first name", "firstname"],
    "last_name": ["nom de famille", "last name", "lastname", "nom"],
    "name": ["nom complet", "full name", "name", "membre", "member"],
    "organization": ["organisation", "organization", "organisme", "entreprise", "company"],
    "member_number": ["numero de membre", "no de membre", "n de membre", "member number", "member id"],
    "type": ["categorie", "type d'adhesion", "type", "membership type", "category", "forfait"],
    "date_start": ["date de debut", "debut", "start date", "date d'adhesion", "membership start"],
    "date_end": ["date de fin", "fin", "end date", "date d'expiration", "expiration", "membership end"],
    "amount": ["montant", "amount", "cotisation", "total"],
    "paid": ["paye", "paid", "statut de paiement", "payment status"],
    "payment_date": ["date de paiement", "payment date", "date du paiement"],
    "external_ref": ["identifiant", "id", "reference", "transaction id", "numero de transaction"],
    "phone": ["telephone", "phone", "tel"],
    "street": ["adresse", "address", "rue", "street"],
    "city": ["ville", "city"],
    "zip": ["code postal", "postal code", "zip"],
    "function": ["profession", "occupation", "titre", "job title"],
}
TRUTHY = {"oui", "yes", "o", "y", "1", "true", "vrai", "paye", "payee", "paid", "complete", "completed", "succeeded"}


def _like_exact(text):
    """La valeur d'un `=ilike` qui ne doit rien attraper d'autre qu'elle-même.

    🔴 `_` et `%` sont des jokers : sans échappement, « jean_paul@exemple.test »
    rapprocherait aussi « jeanXpaul@exemple.test ».
    """
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _norm(text):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return " ".join(text.lower().replace("_", " ").replace("°", "").split())


class MembershipImport(models.TransientModel):
    """Importer une liste de membres sans créer de doublons.

    Le problème réel n'est pas de lire un CSV : c'est que la même personne
    existe déjà, sous un autre courriel ou avec une majuscule de moins, et
    qu'une seconde liste (l'Excel de la trésorière, l'export de l'infolettre)
    dit autre chose que la première. Le rapprochement se fait dans cet ordre :
    numéro de membre, puis courriel, puis nom et code postal. Deux contacts
    qui répondent au même courriel sont un CONFLIT : la ligne est écartée et
    nommée au rapport, jamais rattachée au hasard.

    🔴 Une adhésion importée déjà échue naît « échue », rappels terminés : un
    import ne réveille personne.
    """

    _name = "bf.membership.import"
    _description = "Import d'une liste de membres"

    file = fields.Binary(string="Fichier CSV", required=True)
    filename = fields.Char()
    source = fields.Char(
        string="Source", required=True,
        help="Le nom de la liste (« Zeffy 2026 », « Plateforme nationale »). "
             "Réimporter la même source met à jour au lieu de doubler.",
    )
    type_id = fields.Many2one(
        "bf.membership.type", string="Catégorie par défaut", required=True,
        domain="[('company_id', '=', company_id)]",
    )
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    rows_are_paid = fields.Boolean(
        string="Les lignes sont des adhésions payées", default=True,
        help="Quand le fichier n'a pas de colonne de paiement : un export de "
             "membres d'une plateforme ne liste d'ordinaire que les adhésions "
             "réglées.",
    )
    payment_source = fields.Selection(
        selection=lambda self: self.env["bf.membership"]._fields["payment_source"].selection,
        string="Payé par", default="platform",
    )
    report_html = fields.Html(string="Rapport", readonly=True, sanitize=False)
    state = fields.Selection([("new", "Nouveau"), ("previewed", "Aperçu"), ("done", "Importé")], default="new")

    # ------------------------------------------------------------------

    def _read_rows(self):
        raw = base64.b64decode(self.file or b"")
        for encoding in ("utf-8-sig", "cp1252"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise UserError(_("Le fichier n'est ni en UTF-8 ni en Windows-1252."))
        sample = text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(io.StringIO(text), dialect)
        rows = list(reader)
        if not rows:
            raise UserError(_("Le fichier est vide."))
        header = [_norm(h) for h in rows[0]]
        columns = {}
        for key, names in ALIASES.items():
            for name in names:
                if name in header and key not in columns and header.index(name) not in columns.values():
                    columns[key] = header.index(name)
                    break
        if not ({"email", "name", "last_name", "organization"} & columns.keys()):
            raise UserError(_(
                "Aucune colonne de nom ni de courriel reconnue. En-têtes lus : %s",
                ", ".join(rows[0])))
        unknown = [rows[0][i] for i in range(len(header)) if i not in columns.values()]
        records = []
        for number, row in enumerate(rows[1:], start=2):
            if not any(cell.strip() for cell in row):
                continue
            records.append((number, {k: (row[i].strip() if i < len(row) else "") for k, i in columns.items()}))
        return records, unknown

    def _parse_date(self, value):
        if not value:
            return False
        value = value.strip()[:10]
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
        raise UserError(_("Date illisible : « %s ».", value))

    def _parse_amount(self, value):
        if not value:
            return None
        cleaned = value.replace("$", "").replace("\xa0", "").replace(" ", "").replace(",", ".")
        try:
            return float(cleaned)
        except ValueError:
            return None

    def _find_type(self, value):
        if not value:
            return self.type_id
        Type = self.env["bf.membership.type"].with_context(active_test=False)
        domain = [("company_id", "=", self.company_id.id)]
        found = Type.search(domain + [("code", "=ilike", _like_exact(value))], limit=1) \
            or Type.search(domain + [("name", "=ilike", _like_exact(value))], limit=1)
        return found or self.type_id

    def _find_partner(self, vals):
        """(partenaire, motif) ; partenaire vide et motif rempli = conflit."""
        Partner = self.env["res.partner"].with_context(active_test=False)
        if vals.get("member_number"):
            found = Partner.search([("member_number", "=", vals["member_number"])])
            if len(found) == 1:
                return found, None
        email = (vals.get("email") or "").strip().lower()
        if email:
            found = Partner.search([("email", "=ilike", _like_exact(email))])
            org = vals.get("organization")
            if org and len(found) > 1:
                found = found.filtered(lambda p: p.is_company or p.parent_id.name == org) or found
            if len(found) > 1:
                return Partner, _("%s contacts ont le courriel %s", len(found), email)
            if found:
                return found, None
        name = self._partner_name(vals)
        if name and vals.get("zip"):
            found = Partner.search([("name", "=ilike", _like_exact(name)), ("zip", "=ilike", _like_exact(vals["zip"]))])
            if len(found) == 1:
                return found, None
            if len(found) > 1:
                return Partner, _("%s contacts s'appellent %s au %s", len(found), name, vals["zip"])
        return Partner, None

    def _partner_name(self, vals):
        if vals.get("name"):
            return vals["name"]
        full = " ".join(p for p in (vals.get("first_name"), vals.get("last_name")) if p)
        return full or vals.get("organization") or ""

    def _partner_vals(self, vals, is_company):
        out = {
            "name": vals.get("organization") if is_company else self._partner_name(vals),
            "is_company": is_company,
            "email": vals.get("email") or False,
            "phone": vals.get("phone") or False,
            "street": vals.get("street") or False,
            "city": vals.get("city") or False,
            "zip": vals.get("zip") or False,
        }
        if not is_company:
            out["function"] = vals.get("function") or False
        return {k: v for k, v in out.items() if v not in (False, None, "")}

    # ------------------------------------------------------------------

    def _run(self):
        self.ensure_one()
        today = fields.Date.context_today(self)
        records, unknown = self._read_rows()
        stats = {"partners_created": 0, "partners_matched": 0, "created": 0,
                 "updated": 0, "duplicates": 0, "expired": 0}
        problems = []
        for number, vals in records:
            try:
                with self.env.cr.savepoint():
                    outcome = self._import_row(vals, today)
                for key in outcome:
                    stats[key] += 1
            except (UserError, ValueError) as exc:
                problems.append((number, str(exc.args[0] if exc.args else exc)))
        return stats, problems, unknown, len(records)

    def _import_row(self, vals, today):
        Membership = self.env["bf.membership"]
        outcome = []
        partner, conflict = self._find_partner(vals)
        if conflict:
            raise UserError(conflict)
        mtype = self._find_type(vals.get("type"))
        is_company = mtype.member_kind == "organization" or (
            mtype.member_kind == "both" and vals.get("organization")
            and not (vals.get("name") or vals.get("last_name")))
        if partner:
            outcome.append("partners_matched")
            fill = {k: v for k, v in self._partner_vals(vals, partner.is_company).items()
                    if k not in ("name", "is_company") and not partner[k]}
            if fill:
                partner.write(fill)
        else:
            pvals = self._partner_vals(vals, is_company)
            if not pvals.get("name"):
                raise UserError(_("Ligne sans nom."))
            partner = self.env["res.partner"].create(pvals)
            outcome.append("partners_created")
        if vals.get("member_number") and not partner.member_number:
            clash = self.env["res.partner"].with_context(active_test=False).search(
                [("member_number", "=", vals["member_number"])], limit=1)
            if clash:
                raise UserError(_("Le numéro %s appartient déjà à %s.", vals["member_number"], clash.name))
            partner.sudo().member_number = vals["member_number"]

        date_start = self._parse_date(vals.get("date_start")) or today
        date_end = self._parse_date(vals.get("date_end"))
        amount = self._parse_amount(vals.get("amount"))
        paid = (_norm(vals["paid"]) in TRUTHY) if vals.get("paid") else self.rows_are_paid
        mvals = {
            "partner_id": partner.id,
            "type_id": mtype.id,
            "company_id": self.company_id.id,
            "date_start": date_start,
            "source": self.source,
            "payment_state": "paid" if paid else "to_pay",
            "payment_source": self.payment_source if paid else False,
            "payment_date": self._parse_date(vals.get("payment_date")) or (date_start if paid else False),
        }
        if date_end:
            mvals["date_end"] = date_end
        if amount is not None:
            mvals["amount"] = amount
        effective_end = date_end or mtype._period_end(date_start)

        existing = Membership.browse()
        if vals.get("external_ref"):
            mvals["external_ref"] = vals["external_ref"]
            existing = Membership.search([
                ("company_id", "=", self.company_id.id),
                ("source", "=", self.source),
                ("external_ref", "=", vals["external_ref"]),
            ], limit=1)
        if existing:
            update = {k: v for k, v in mvals.items() if k in ("payment_state", "payment_source", "payment_date", "amount", "date_end")}
            existing.write(update)
            outcome.append("updated")
            return outcome

        same_period = Membership.search([
            ("partner_id", "=", partner.id),
            ("company_id", "=", self.company_id.id),
            ("date_start", "<=", effective_end or date_start),
            "|", ("date_end", "=", False), ("date_end", ">=", date_start),
            ("state", "not in", ("refused",)),
        ], limit=1)
        if same_period:
            outcome.append("duplicates")
            return outcome

        if effective_end and effective_end < today:
            mvals.update(state="expired", reminder_stage="done")
            outcome.append("expired")
        else:
            mvals["state"] = "active" if paid else "waiting"
        mvals.update(decided_by_id=self.env.user.id, decision_date=today)
        # L'assistant est réservé au responsable (droits d'accès) ; l'état
        # d'une ligne importée (échue, en règle) ne se fixe qu'en superutilisateur.
        Membership.sudo().create(mvals)
        outcome.append("created")
        return outcome

    def _report(self, stats, problems, unknown, total, applied):
        title = _("Import fait") if applied else _("Aperçu : rien n'a été écrit")
        rows = [
            (_("Lignes lues"), total),
            (_("Contacts retrouvés"), stats["partners_matched"]),
            (_("Contacts créés"), stats["partners_created"]),
            (_("Adhésions créées"), stats["created"]),
            (_("dont déjà échues (aucun rappel)"), stats["expired"]),
            (_("Adhésions mises à jour (même référence)"), stats["updated"]),
            (_("Déjà au registre pour cette période"), stats["duplicates"]),
            (_("Lignes écartées"), len(problems)),
        ]
        html = Markup("<h4>%s</h4><table class='table table-sm'>") % title
        for label, value in rows:
            html += Markup("<tr><td>%s</td><td class='text-end'>%s</td></tr>") % (label, value)
        html += Markup("</table>")
        if problems:
            html += Markup("<p><b>%s</b></p><ul>") % _("Lignes écartées")
            for number, reason in problems:
                html += Markup("<li>%s %s : %s</li>") % (_("Ligne"), number, reason)
            html += Markup("</ul>")
        if unknown:
            html += Markup("<p class='text-muted'>%s %s</p>") % (
                _("Colonnes ignorées :"), escape(", ".join(unknown)))
        return html

    def action_preview(self):
        self.ensure_one()
        savepoint = self.env.cr.savepoint()
        try:
            result = self._run()
            self.env.flush_all()
        finally:
            savepoint.close(rollback=True)
            self.env.invalidate_all()
        self.write({"report_html": self._report(*result, applied=False), "state": "previewed"})
        return self._reopen()

    def action_import(self):
        self.ensure_one()
        result = self._run()
        self.write({"report_html": self._report(*result, applied=True), "state": "done"})
        return self._reopen()

    def _reopen(self):
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }
