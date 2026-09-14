"""Bind every existing connection to the server that issued it.

Up to 18.0.4.0.2 a connection did not remember its server. They were all
issued by their configuration's current address, so that address is recorded
now, in the form the module compares against (trimmed, no trailing slash,
lower case). Without it, every connected person would have to reconnect.
"""


def migrate(cr, version):
    cr.execute(
        """
        UPDATE bf_nc_user_credential c
           SET nc_server = lower(rtrim(btrim(cfg.nextcloud_base_url), '/'))
          FROM nextcloud_document_config cfg
         WHERE c.config_id = cfg.id
           AND c.nc_server IS NULL
        """
    )
