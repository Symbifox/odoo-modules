#!/usr/bin/env python3
"""
Migration script to update consent request email template URL.

Run this script in the Odoo shell:
    docker exec -it bluefox-odoo odoo shell -d bluefox < /mnt/extra-addons/privacy_consent/scripts/fix_template_url.py

This updates the template to use token-based public URLs (no login required).
"""

import re

# Get the mail template
template = env.ref('privacy_consent.mail_template_consent_request', raise_if_not_found=False)

if not template:
    print("ERROR: Template 'privacy_consent.mail_template_consent_request' not found!")
else:
    body = template.body_html or ""

    # Pattern 1: Match /my/privacy/consent/{{ object.id }} variations
    old_patterns = [
        # href="/my/privacy/consent/{{ object.id }}"
        (r'href="/my/privacy/consent/\{\{\s*object\.id\s*\}\}"',
         'href="/privacy/consent/{{ object.id }}/{{ object.access_token }}"'),
        # href='/my/privacy/consent/{{ object.id }}'
        (r"href='/my/privacy/consent/\{\{\s*object\.id\s*\}\}'",
         "href='/privacy/consent/{{ object.id }}/{{ object.access_token }}'"),
    ]

    new_body = body
    changes_made = False

    for pattern, replacement in old_patterns:
        if re.search(pattern, new_body):
            new_body = re.sub(pattern, replacement, new_body)
            changes_made = True
            print(f"Replaced pattern: {pattern}")

    if changes_made:
        template.write({'body_html': new_body})
        print("SUCCESS: Template updated to use public token-based URLs.")
        print("New URL pattern: /privacy/consent/{{ object.id }}/{{ object.access_token }}")
    else:
        # Check if already using the correct URL
        if '/privacy/consent/{{ object.id }}/{{ object.access_token }}' in body:
            print("Template already uses the correct public URL format. No changes needed.")
        else:
            print("WARNING: Could not find URL pattern to replace.")
            print("Current body sample (looking for href with /my/privacy/consent/):")
            # Find and print lines containing the URL
            for line in body.split('\n'):
                if '/privacy/consent/' in line or '/my/privacy/' in line:
                    print(f"  {line.strip()[:200]}")

env.cr.commit()
print("Done.")
