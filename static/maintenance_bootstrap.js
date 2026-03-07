/*
 * Maintenance page shell/bootstrap helpers.
 * Keeps maintenance-specific startup separate from the shared shell wiring.
 */
(function (global) {
    function initMaintenanceShell() {
        const shell = global.MCWebShell || null;
        if (shell && typeof shell.startThemePreferenceWatcher === "function") {
            shell.startThemePreferenceWatcher();
        }
        if (shell && typeof shell.startSidebarNav === "function") {
            shell.startSidebarNav();
        }
    }

    global.initMaintenanceShell = initMaintenanceShell;
})(window);
