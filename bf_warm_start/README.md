# Warm Start

Makes the **first page after a restart fast**. Without it, every new Odoo process makes the
first person who opens a backend page wait while it reads the translation files of every
installed module.

## License

LGPL-3. See the repository [LICENSE](../LICENSE) and `__manifest__.py` for details.

## The problem

To build the backend page, Odoo 18 computes a hash of the web client translations for the
user's language (`ir.http.get_web_translations_hash`). The translations come from
`code_translations`, a cache that lives for the whole process and is filled on demand, one
`.po` file per module. On a database with a few hundred modules, filling it takes seconds:
about **10 s for 457 files** on a production database with 545 modules (`fr_CA`).

The cache is never shared between processes. With `workers = 10`, each worker pays the same
cost on its first page, and a restart (every deployment) gives you ten new workers. Measured
on that database: a backend page took 0.1 s on a warm worker and 7 to 30 s on a new one.

## What the module does

When the registry finishes loading (`_register_hook`), it loads the web client translations of
every installed module for the languages of the active internal users, then computes the
translation hash once.

Add the module to **`server_wide_modules`** in `odoo.conf` to get the full benefit in prefork
mode (`workers` > 0):

    server_wide_modules = base,web,bf_warm_start

The prefork master then preloads the registry before forking the workers, so the warm-up runs
**once, in the master**, and every worker inherits a warm registry and warm translations. The
database it preloads is the single existing database that `dbfilter` lets through; with none,
or more than one, nothing is preloaded.

Why not `db_name`? Odoo also preloads with `db_name`, but when that database is missing at
start-up (during a restore, for example) Odoo **creates it** and installs `base` with the default
administrator, and `dbfilter` would then serve that empty database. This module never creates a
database.

Without the server-wide line, each worker warms itself when it loads its registry, on its first
request. In threaded mode (`workers = 0`) nothing is preloaded: the single process warms itself
once.

The warm-up only runs for the `odoo server` command. It is skipped in `odoo shell` and other
commands, during `-i`, `-u` or `--stop-after-init` runs, in tests, and in the gevent (websocket)
process. It never stops a registry from loading: it runs in a savepoint, and an error is logged
and ignored.

## What it costs

The restart itself gets longer by the warm-up time (registry load, plus about 10 s per language on
that database), because the master warms up before serving. Requests wait in the socket queue
meanwhile, instead of waiting later on each worker. The master keeps a full registry in memory
(about 0.5 GB with 545 modules), shared with the workers until they write to it.

## Log line

    bf_warm_start: fr_CA web translations loaded for 545 modules in 9.8 s (hash in 0.6 s)

## Requirements

- Odoo 18.0 (Community or Enterprise)
- `web`
