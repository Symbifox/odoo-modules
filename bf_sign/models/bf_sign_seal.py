"""Digital seal (PAdES / PKCS#7) for the final signed PDF.

Once all signers have signed and the document is sealed visually + certified,
this layer embeds an **invisible cryptographic signature** with an organisation
certificate (the installed company), so any PDF reader (Adobe) shows the document as
*signed and not tampered* — the DocuSeal-style automatic seal. Implemented with
**pyHanko** (modern, cryptography-based PDF signing — no oscrypto).

The sealing private key is generated once (self-signed X.509) and stored
**Fernet-encrypted** in ``ir.config_parameter``. The Fernet key itself is read,
in priority order, from the environment, ``odoo.conf``, then — as a self-service
fallback so an admin can do the initial setup from the UI — the
``bf_sign.fernet_key`` system parameter. env/conf always win so a deployment can
keep the key out of the database (see ``res.config.settings`` and ``SECURITY.md``).
"""

import logging
import os

from odoo import _, api, models
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)

try:
    from cryptography.fernet import Fernet
except ImportError:  # pragma: no cover
    Fernet = None

CERT_PARAM = "bf_sign.seal_cert"
KEY_PARAM = "bf_sign.seal_key"
FERNET_PARAM = "bf_sign.fernet_key"


def _fernet_key(env=None):
    """Fernet key, in priority order: env var, odoo.conf, then (if ``env`` is
    given) the ``bf_sign.fernet_key`` system parameter.

    env/conf take precedence over the DB so a deployment can always pin or
    harden the key out of the database; the DB source exists only so an admin
    can perform the initial setup from Settings.
    """
    if not Fernet:
        return None
    from odoo.tools import config
    for src in (
        os.environ.get("BF_SIGN_FERNET_KEY"),
        config.get("bf_sign_fernet_key"),
        os.environ.get("BF_SECAWARE_FERNET_KEY"),
        config.get("bf_secaware_fernet_key"),
    ):
        if src:
            return src.encode() if isinstance(src, str) else src
    if env is not None:
        param = env["ir.config_parameter"].sudo().get_param(FERNET_PARAM)
        if param:
            return param.encode() if isinstance(param, str) else param
    _logger.error(
        "bf_sign: no Fernet key — set BF_SIGN_FERNET_KEY (env), "
        "bf_sign_fernet_key (odoo.conf), or generate one in Settings.")
    return None


def _fernet_key_source(env=None):
    """Where the active key comes from: 'conf' (env/odoo.conf), 'db', or None."""
    from odoo.tools import config
    if (os.environ.get("BF_SIGN_FERNET_KEY") or config.get("bf_sign_fernet_key")
            or os.environ.get("BF_SECAWARE_FERNET_KEY")
            or config.get("bf_secaware_fernet_key")):
        return "conf"
    if env is not None and env["ir.config_parameter"].sudo().get_param(FERNET_PARAM):
        return "db"
    return None


def _encrypt(value, env=None):
    if not value:
        return ""
    if not Fernet:
        raise UserError(_("Le paquet « cryptography » est requis."))
    key = _fernet_key(env)
    if not key:
        raise UserError(_(
            "Aucune clé de chiffrement configurée. Définissez BF_SIGN_FERNET_KEY "
            "(environnement) ou bf_sign_fernet_key (odoo.conf), ou générez une clé "
            "dans Paramètres → Signature électronique."))
    return Fernet(key).encrypt(value.encode()).decode()


def _decrypt(encrypted, env=None):
    if not encrypted:
        return ""
    key = _fernet_key(env)
    if not key:
        return ""
    try:
        return Fernet(key).decrypt(encrypted.encode()).decode()
    except Exception:  # noqa: BLE001
        _logger.warning("bf_sign: seal secret decrypt failed.")
        return ""


class BfSignSeal(models.AbstractModel):
    _name = "bf.sign.seal"
    _description = "Sceau numérique PAdES — certificat et signature PDF (pyHanko)"

    @api.model
    def _icp(self):
        return self.env["ir.config_parameter"].sudo()

    @api.model
    def _require_admin(self):
        """Gate the key-management entry points.

        This is an AbstractModel, so it has no table and no ``ir.model.access``
        row can apply to it — yet its public methods are still dispatched over
        RPC like any other model's. Anything that reads or writes the sealing
        key material therefore has to check rights itself; there is no ACL layer
        underneath to fall back on.

        The sealing methods (``seal_pdf``, ``verify_pdf``, ``has_cert``) are
        deliberately NOT gated: they run inside the signing flow, where the
        environment belongs to a portal or public signer.
        """
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_(
                "Seul un administrateur peut consulter ou modifier la clé de "
                "scellement."))

    @api.model
    def has_cert(self):
        return bool(self._icp().get_param(CERT_PARAM) and self._icp().get_param(KEY_PARAM))

    # ── Fernet key management (self-service setup) ───────────────────────────
    @api.model
    def fernet_key_source(self):
        """'conf' (env/odoo.conf), 'db', or None — for the Settings status line."""
        self._require_admin()
        return _fernet_key_source(self.env)

    @api.model
    def _check_fernet_key(self, value):
        """Validate that ``value`` is a usable Fernet key (32 url-safe b64 bytes)."""
        if not Fernet:
            raise UserError(_("Le paquet « cryptography » est requis."))
        try:
            Fernet(value.encode() if isinstance(value, str) else value)
        except Exception as exc:  # noqa: BLE001
            raise UserError(_(
                "Clé Fernet invalide (attendu : 32 octets encodés en base64 "
                "url-safe).")) from exc

    @api.model
    def store_fernet_key(self, value):
        """Persist a pasted Fernet key in the DB (``bf_sign.fernet_key``).

        Refuses to swap an in-use key while a seal certificate exists — that
        would make the encrypted certificate undecryptable.
        """
        self._require_admin()
        value = (value or "").strip()
        if not value:
            return
        self._check_fernet_key(value)
        current = _fernet_key(self.env)
        new_bytes = value.encode()
        if current and current != new_bytes and self.has_cert():
            raise UserError(_(
                "Une clé de chiffrement est déjà active et un certificat de "
                "scellement existe : changer la clé rendrait le certificat "
                "illisible. Supprimez d'abord le certificat si vous devez "
                "changer de clé."))
        self._icp().set_param(FERNET_PARAM, value)

    @api.model
    def action_generate_fernet_key(self):
        """Generate a fresh Fernet key and store it in the DB (one-time setup)."""
        self._require_admin()
        if not Fernet:
            raise UserError(_("Le paquet « cryptography » est requis."))
        if _fernet_key(self.env):
            raise UserError(_("Une clé de chiffrement est déjà configurée."))
        key = Fernet.generate_key().decode()
        self._icp().set_param(FERNET_PARAM, key)
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {
                "title": _("Clé de chiffrement générée"),
                "message": _(
                    "Une clé a été générée et enregistrée en base. Conservez-en "
                    "une copie hors ligne (coffre de mots de passe) — elle est "
                    "nécessaire pour relire les documents scellés : %s") % key,
                "type": "success", "sticky": True},
        }

    # ── Certificate management ───────────────────────────────────────────────
    @api.model
    def action_generate_cert(self):
        """Generate a self-signed organisation sealing certificate (once)."""
        self._require_admin()
        if self.has_cert():
            raise UserError(_("Un certificat de scellement existe déjà."))
        from datetime import datetime, timedelta, timezone

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        org = self.env.company.name or "Les services de consultation Blue Fox, Inc."
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, org),
            x509.NameAttribute(NameOID.COMMON_NAME, "%s — Sceau de signature" % org),
        ])
        now = datetime.now(timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=3653))  # ~10 ans
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True, content_commitment=True,
                    key_encipherment=False, data_encipherment=False,
                    key_agreement=False, key_cert_sign=False, crl_sign=False,
                    encipher_only=False, decipher_only=False),
                critical=True)
            .sign(key, hashes.SHA256()))
        cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
        key_pem = key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()).decode()
        icp = self._icp()
        icp.set_param(CERT_PARAM, _encrypt(cert_pem, self.env))
        icp.set_param(KEY_PARAM, _encrypt(key_pem, self.env))
        # Generating the certificate activates sealing (the toggle is an off-switch).
        if not icp.get_param("bf_sign.pdf_seal_enabled"):
            icp.set_param("bf_sign.pdf_seal_enabled", "1")
        try:
            import pyhanko  # noqa: F401
            msg = _("Le certificat de scellement « %s » a été créé. Les nouveaux "
                    "documents seront scellés automatiquement.") % org
            ntype = "success"
        except ImportError:
            msg = _("Certificat « %s » créé, mais la dépendance pyHanko n'est pas "
                    "installée : le sceau ne sera pas apposé tant qu'elle n'est pas "
                    "disponible.") % org
            ntype = "warning"
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {"title": _("Certificat généré"), "message": msg,
                       "type": ntype, "sticky": ntype == "warning"},
        }

    @api.model
    def _signer(self):
        """Build a pyHanko SimpleSigner from the stored (decrypted) PEM cert+key."""
        from asn1crypto import keys as akeys, pem, x509 as ax509
        from pyhanko.sign import signers
        cert_pem = _decrypt(self._icp().get_param(CERT_PARAM), self.env)
        key_pem = _decrypt(self._icp().get_param(KEY_PARAM), self.env)
        if not cert_pem or not key_pem:
            return None
        _, _, cert_der = pem.unarmor(cert_pem.encode())
        _, _, key_der = pem.unarmor(key_pem.encode())
        return signers.SimpleSigner(
            signing_cert=ax509.Certificate.load(cert_der),
            signing_key=akeys.PrivateKeyInfo.load(key_der),
            cert_registry=None)

    # ── Sealing / verification ───────────────────────────────────────────────
    @api.model
    def seal_pdf(self, pdf_bytes, reason=None, location=None, tsa_url=None):
        """Embed an invisible PAdES/PKCS#7 signature; return the sealed PDF bytes."""
        import io

        from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
        from pyhanko.sign import signers, timestamps

        signer = self._signer()
        if not signer:
            raise UserError(_("Aucun certificat de scellement configuré."))
        ts = timestamps.HTTPTimeStamper(tsa_url) if tsa_url else None
        meta = signers.PdfSignatureMetadata(
            field_name="BFSeal",
            reason=reason or _("Scellé numériquement"),
            location=location or "")
        writer = IncrementalPdfFileWriter(io.BytesIO(pdf_bytes))
        out = signers.sign_pdf(writer, meta, signer=signer, timestamper=ts)
        return out.getvalue()

    @api.model
    def verify_pdf(self, pdf_bytes):
        """True if the embedded PAdES signature is intact (content unmodified)."""
        try:
            import io

            from asn1crypto import pem, x509 as ax509
            from pyhanko.pdf_utils.reader import PdfFileReader
            from pyhanko.sign.validation import validate_pdf_signature
            from pyhanko_certvalidator import ValidationContext

            cert_pem = _decrypt(self._icp().get_param(CERT_PARAM), self.env)
            roots = []
            if cert_pem:
                _, _, der = pem.unarmor(cert_pem.encode())
                roots = [ax509.Certificate.load(der)]
            reader = PdfFileReader(io.BytesIO(pdf_bytes))
            sigs = reader.embedded_signatures
            if not sigs:
                return False
            vc = ValidationContext(trust_roots=roots, allow_fetching=False)
            status = validate_pdf_signature(sigs[0], signer_validation_context=vc)
            # intact = byte range unchanged; valid = CMS signature valid. The cert
            # is self-signed (org seal), so we anchor it as a trust root ourselves.
            return bool(status.intact and status.valid)
        except Exception as exc:  # noqa: BLE001
            _logger.warning("bf_sign: seal verification failed: %s", exc)
            return False
