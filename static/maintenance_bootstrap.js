/*
 * Maintenance page shell/bootstrap helpers.
 * Keeps theme + mobile nav wiring separate from maintenance business logic.
 */
(function (global) {
    function initTheme() {
        const darkModeQuery = window.matchMedia("(prefers-color-scheme: dark)");
        function applyThemePreference() {
            document.documentElement.classList.toggle("theme-dark", darkModeQuery.matches);
        }
        applyThemePreference();
        if (darkModeQuery.addEventListener) {
            darkModeQuery.addEventListener("change", applyThemePreference);
        } else if (darkModeQuery.addListener) {
            darkModeQuery.addListener(applyThemePreference);
        }
    }

    function initMobileNav() {
        const toggle = document.getElementById("nav-toggle");
        const sidebar = document.getElementById("side-nav");
        const backdrop = document.getElementById("nav-backdrop");
        if (!(toggle && sidebar && backdrop)) return;

        function closeNav() {
            sidebar.classList.remove("open");
            backdrop.classList.remove("open");
            toggle.classList.remove("nav-open");
            toggle.setAttribute("aria-expanded", "false");
        }

        function toggleNav() {
            const nextOpen = !sidebar.classList.contains("open");
            sidebar.classList.toggle("open", nextOpen);
            backdrop.classList.toggle("open", nextOpen);
            toggle.classList.toggle("nav-open", nextOpen);
            toggle.setAttribute("aria-expanded", nextOpen ? "true" : "false");
        }

        toggle.addEventListener("click", toggleNav);
        backdrop.addEventListener("click", closeNav);
        window.addEventListener("resize", () => {
            if (window.innerWidth > 1100) closeNav();
        });
    }

    function initMaintenanceShell() {
        initTheme();
        initMobileNav();
    }

    global.initMaintenanceShell = initMaintenanceShell;
})(window);
