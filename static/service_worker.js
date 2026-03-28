const STATIC_CACHE = "mcweb-static-v5";
const HTML_CACHE = "mcweb-html-v5";
const FRAGMENT_CACHE = "mcweb-fragments-v5";
const OFFLINE_FALLBACK_URL = "/static/offline.html";
const OFFLINE_FRAGMENT_HTML = "<main id=\"mcweb-page-root\" class=\"content\" data-page-key=\"offline\" data-page-title=\"Offline\" data-page-styles='[]' data-page-scripts='[]'><div class=\"wrap page-panes\"><section class=\"panel pane-primary\"><div class=\"pane-head\"><h1 class=\"pane-title\">Server offline</h1></div><div class=\"pane-content\"><p>Cached content is unavailable for this page. Reconnect to load data.</p></div></section></div></main>";

const PRECACHE_STATIC_URLS = [
    OFFLINE_FALLBACK_URL,
    "/static/global.css",
    "/static/custom_select.js",
    "/static/http_client.js",
    "/static/page_activity_runtime.js",
    "/static/pane_animations.js",
    "/static/offline_recovery.js",
    "/static/page_module_registry.js",
    "/static/app_shell.js",
    "/static/dom_runtime_utils.js",
    "/static/log_render_utils.js",
    "/static/file_viewer_runtime.js",
    "/static/file_page_data_runtime.js",
    "/static/file_page_modals.js",
    "/static/file_browser_page.js",
    "/static/home_log_runtime.js",
    "/static/home_time_utils.js",
    "/static/dashboard_home.css",
    "/static/dashboard_home_page.js",
    "/static/file_browser.css",
    "/static/documentation.css",
    "/static/documentation_core.js",
    "/static/maintenance_api_runtime.js",
    "/static/maintenance_page.css",
    "/static/maintenance_page_utils.js",
    "/static/maintenance_page_files.js",
    "/static/maintenance_page_rules.js",
    "/static/maintenance_page_history.js",
    "/static/maintenance_page_modals.js",
    "/static/maintenance_page_core.js",
    "/static/maintenance_page.js",
    "/doc/server_setup_doc.md",
];

const PRECACHE_PAGE_ROUTES = [
    "/",
    "/backups",
    "/minecraft-logs",
    "/crash-logs",
    "/maintenance",
    "/readme",
];

async function cacheStaticResponse(request, response) {
    if (!response || !response.ok) return;
    const cache = await caches.open(STATIC_CACHE);
    try {
        await cache.put(request, response.clone());
    } catch (_) {
        // Ignore cache failures.
    }
}

async function matchStatic(request) {
    const cache = await caches.open(STATIC_CACHE);
    const cached = await cache.match(request);
    return cached || null;
}

async function handleNavigate(request) {
    try {
        const response = await fetch(request);
        if (response && response.ok) {
            const cache = await caches.open(HTML_CACHE);
            try {
                await cache.put(request, response.clone());
            } catch (_) {
                // Ignore cache failures.
            }
        }
        return response;
    } catch (_) {
        const cache = await caches.open(HTML_CACHE);
        const cached = await cache.match(request) || await cache.match(request, { ignoreSearch: true });
        if (cached) return cached;
        const fallback = await matchStatic(new Request(OFFLINE_FALLBACK_URL));
        if (fallback) return fallback;
        return new Response("Server offline.", {
            status: 503,
            headers: { "Content-Type": "text/plain; charset=utf-8" },
        });
    }
}

async function handleFragment(request) {
    const cache = await caches.open(FRAGMENT_CACHE);
    try {
        const response = await fetch(request);
        if (response && response.ok) {
            try {
                await cache.put(request, response.clone());
            } catch (_) {
                // Ignore cache failures.
            }
        }
        return response;
    } catch (_) {
        const cached = await cache.match(request) || await cache.match(request, { ignoreSearch: true });
        if (cached) return cached;
        return new Response(OFFLINE_FRAGMENT_HTML, {
            status: 200,
            headers: { "Content-Type": "text/html; charset=utf-8" },
        });
    }
}

async function handleStatic(request) {
    try {
        const response = await fetch(request);
        cacheStaticResponse(request, response.clone()).catch(() => {});
        return response;
    } catch (err) {
        const cached = await matchStatic(request);
        if (cached) return cached;
        throw err;
    }
}

async function precacheStaticAssets() {
    try {
        const cache = await caches.open(STATIC_CACHE);
        const tasks = PRECACHE_STATIC_URLS.map(async (url) => {
            try {
                await cache.add(url);
            } catch (_) {
                // Ignore missing assets during install.
            }
        });
        await Promise.all(tasks);
    } catch (_) {
        // Ignore offline install failures.
    }
}

async function precachePages() {
    try {
        const cache = await caches.open(HTML_CACHE);
        const tasks = PRECACHE_PAGE_ROUTES.map(async (path) => {
            try {
                const response = await fetch(path, {
                    headers: { "X-Requested-With": "XMLHttpRequest" },
                    cache: "no-store",
                });
                if (response && response.ok) {
                    await cache.put(path, response.clone());
                }
            } catch (_) {
                // Ignore offline failures.
            }
        });
        await Promise.all(tasks);
    } catch (_) {
        // Ignore offline failures.
    }
}

async function precacheFragments() {
    try {
        const cache = await caches.open(FRAGMENT_CACHE);
        const tasks = PRECACHE_PAGE_ROUTES.map(async (path) => {
            try {
                const request = new Request(path, {
                    headers: {
                        "X-MCWEB-Fragment": "1",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    cache: "no-store",
                });
                const response = await fetch(request);
                if (response && response.ok) {
                    await cache.put(request, response.clone());
                }
            } catch (_) {
                // Ignore offline failures.
            }
        });
        await Promise.all(tasks);
    } catch (_) {
        // Ignore offline failures.
    }
}

self.addEventListener("install", (event) => {
    event.waitUntil((async () => {
        await precacheStaticAssets();
        await precachePages();
        await precacheFragments();
    })());
    self.skipWaiting();
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys().then((keys) => Promise.all(
            keys.filter((key) => ![STATIC_CACHE, HTML_CACHE, FRAGMENT_CACHE].includes(key))
                .map((key) => caches.delete(key))
        ))
    );
    self.clients.claim();
});

self.addEventListener("fetch", (event) => {
    const req = event.request;
    if (req.method !== "GET") return;

    const url = new URL(req.url);
    if (url.origin !== self.location.origin) return;

    const isFragment = req.headers.get("X-MCWEB-Fragment") === "1";
    if (isFragment) {
        event.respondWith(handleFragment(req));
        return;
    }

    if (req.mode === "navigate") {
        event.respondWith(handleNavigate(req));
        return;
    }

    if (url.pathname.startsWith("/static/")) {
        event.respondWith(handleStatic(req));
        return;
    }
});
