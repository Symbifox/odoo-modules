/**
 * Give scoped applications a service worker of their own.
 *
 * Odoo registers its worker on `scope: "/odoo"` (webclient.js) and serves the
 * script with `Service-Worker-Allowed: /odoo`. A scoped application lives under
 * `/scoped_app/<path>`, outside that scope, so it ran uncontrolled: no offline
 * page, and nothing for Chromium's installability check to interrogate, which
 * is what separates a real installed application from a home-screen shortcut.
 *
 * The ceiling is raised server-side (see `controllers/webmanifest.py`); this
 * adds the second registration under it. Same script, second scope: the worker
 * already answers navigations with the cached `/odoo/offline` page, which is
 * exactly what a scoped app needs.
 *
 * Registering from the backend rather than only from inside the scoped app is
 * deliberate: the worker has to already exist when the browser evaluates the
 * scoped page, and a repeat registration is a no-op.
 */
if (navigator.serviceWorker) {
    navigator.serviceWorker
        .register("/web/service-worker.js", { scope: "/scoped_app" })
        .catch(() => {
            // A refused registration must never break the web client. It costs
            // the install prompt, nothing else.
        });
}
