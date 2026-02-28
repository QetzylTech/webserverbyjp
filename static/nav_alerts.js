(function () {
    const backupsLink = document.getElementById("nav-backups-link");
    const maintenanceLink = document.getElementById("nav-maintenance-link");
    const navToggle = document.getElementById("nav-toggle");
    const targetLink = backupsLink || maintenanceLink;
    if (!targetLink && !navToggle) return;
    const mobileQuery = window.matchMedia("(max-width: 1100px)");

    async function refreshNavAttention() {
        let attention = false;
        try {
            const response = await fetch("/maintenance/nav-alert/state", {
                method: "GET",
                headers: { "X-Requested-With": "XMLHttpRequest" },
                cache: "no-store",
            });
            if (response.ok) {
                const payload = await response.json().catch(() => ({}));
                attention = !!payload.restore_pane_attention;
            }
        } catch (_) {
            attention = false;
        }
        if (targetLink) {
            targetLink.classList.toggle("nav-attention", attention);
        }
        if (navToggle) {
            const mobileActive = !!(mobileQuery && mobileQuery.matches);
            navToggle.classList.toggle("nav-attention", attention && mobileActive);
        }
    }

    refreshNavAttention();
    window.setInterval(refreshNavAttention, 5000);
})();
