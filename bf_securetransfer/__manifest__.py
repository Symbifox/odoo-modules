{
    'name': 'Transfert sécurisé (Secure Transfer)',
    'version': '18.0.1.17.4',
    'category': 'Website',
    'summary': "Transfert de fichiers sécurisé : téléversement direct navigateur → S3, "
               "liens tokenisés avec expiration et mot de passe, journal d'accès "
               "inaltérable (Loi 25), purge automatique, multi-marques",
    'description': """
Blue Fox — Transfert sécurisé (Secrets)
=======================================

Transfert de fichiers « WeTransfer maison » propulsé par Odoo :

* **Téléversement direct navigateur → S3** (IDrive E2, résidence Canada) par
  URL présignées — PUT simple sous le seuil, multipart au-delà (fichiers
  multi-Go, reprise sur erreur). Les octets ne transitent jamais par Odoo.
* **Liens tokenisés** avec expiration (1 / 7 / 30 jours), mot de passe
  optionnel (pbkdf2_sha512) et budget de téléchargements.
* **Journal d'accès append-only chaîné** (hash-chain SHA-256) : chaque
  consultation, téléchargement, expiration et purge est horodaté serveur —
  la pièce de traçabilité exigée par la Loi 25.
* **Intégrité** : taille vérifiée par HEAD et ETag épinglé à la finalisation,
  revérifié avant chaque téléchargement (bloque le remplacement d'un objet
  après coup).
* **Purge automatique** : les objets S3 sont supprimés à l'expiration ;
  les métadonnées et le journal sont conservés (rétention paramétrable).
* **Multi-marques** : la page publique et les courriels sont habillés selon
  le domaine d'arrivée (Host), avec repli sur la marque par défaut.
* **Filigrane au téléchargement** (option par marque) : chaque PDF est estampé
  au nom du destinataire et à l'horodatage du téléchargement. Odoo sert alors
  les octets lui-même ; les autres fichiers gardent la redirection directe.
* **Certificat d'accès (PDF)** : le journal chaîné rendu opposable — verdict
  d'intégrité recalculé à l'impression, tailles confirmées par le serveur,
  horodatages UTC explicites et la recette pour recalculer la chaîne soi-même.

Les clés d'accès S3 vivent dans ``odoo.conf`` (ou l'environnement), jamais
en base de données.
    """,
    'author': 'Blue Fox Inc.',
    'website': 'https://bluefoxconsultant.com',
    'license': 'Other proprietary',
    'depends': [
        'web',
        'mail',
        'portal',
    ],
    # boto3/botocore are baked into both tenant images (dedicated pip layer).
    # Declared here so Odoo blocks install on an image that lacks them (the
    # check runs BEFORE load, so this is a hard gate, not the s3.py lazy-import
    # UserError — that only covers a boto3 that breaks at runtime).
    # reportlab draws the download watermark overlay; the PDF merge itself goes
    # through odoo.tools.pdf, which is core. All four tenant images carry
    # reportlab (4.1.0), so this gate costs nothing and catches an image without.
    'external_dependencies': {
        'python': ['boto3', 'reportlab'],
    },
    'data': [
        'security/secure_transfer_security.xml',
        'security/ir.model.access.csv',
        'data/secure_transfer_sequence.xml',
        'data/secure_transfer_brand_data.xml',
        'data/secure_transfer_cron.xml',
        'data/secure_transfer_mail_template.xml',
        'views/secure_transfer_views.xml',
        'views/secure_transfer_brand_views.xml',
        'views/secure_transfer_access_log_views.xml',
        'views/res_config_settings_views.xml',
        'views/res_users_views.xml',
        'views/secure_transfer_portal_templates.xml',
        'views/reveal_link_wizard_views.xml',
        'views/secure_send_wizard_views.xml',
        'views/extend_expiry_wizard_views.xml',
        'views/menu_views.xml',
        'report/secure_transfer_certificate.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
    # Populates the en_CA slot of the noupdate mail templates so contacts get
    # e-mails in their language (see hooks.py + migrations/18.0.1.1.0).
    'post_init_hook': 'post_init_hook',
}
