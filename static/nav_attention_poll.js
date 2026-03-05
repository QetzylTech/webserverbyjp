(function () {
    const homeLink = document.getElementById("nav-home-link");
    const backupsLink = document.getElementById("nav-backups-link");
    const navToggle = document.getElementById("nav-toggle");
    if (!homeLink && !backupsLink && !navToggle) return;
    const mobileQuery = window.matchMedia("(max-width: 1100px)");
    let navAttentionTimer = null;

    function clearNavAttentionClasses(node) {
        if (!node) return;
        node.classList.remove("nav-attention", "nav-attention-red", "nav-attention-yellow", "nav-attention-green");
    }

    function applyHomeAttention(level) {
        clearNavAttentionClasses(homeLink);
        const isHomePage = !!homeLink?.classList.contains("active");
        if (level === "red") {
            homeLink?.classList.add("nav-attention-red");
        } else if (level === "yellow") {
            homeLink?.classList.add("nav-attention-yellow");
        } else if (level === "green" && !isHomePage) {
            homeLink?.classList.add("nav-attention-green");
        }
    }

    function applyBackupsAttention(active) {
        clearNavAttentionClasses(backupsLink);
        if (active) {
            backupsLink?.classList.add("nav-attention");
        }
    }

    function applyMobileToggleAttention(homeLevel, restoreAttention) {
        clearNavAttentionClasses(navToggle);
        const mobileActive = !!(mobileQuery && mobileQuery.matches);
        if (!mobileActive) return;
        if (homeLevel === "red" || restoreAttention) {
            navToggle?.classList.add("nav-attention-red");
            return;
        }
        if (homeLevel === "yellow") {
            navToggle?.classList.add("nav-attention-yellow");
            return;
        }
        if (homeLevel === "green") {
            navToggle?.classList.add("nav-attention-green");
        }
    }

    async function refreshNavAttention() {
        if (document.hidden) return;
        let restoreAttention = false;
        let homeAttention = "none";
        try {
            const response = await fetch("/maintenance/nav-alert/state", {
                method: "GET",
                headers: { "X-Requested-With": "XMLHttpRequest" },
                cache: "no-store",
            });
            if (response.ok) {
                const payload = await response.json().catch(() => ({}));
                restoreAttention = !!payload.restore_pane_attention;
                const level = String(payload.home_attention || "").trim().toLowerCase();
                if (level === "red" || level === "yellow" || level === "green") {
                    homeAttention = level;
                }
            }
        } catch (_) {
            restoreAttention = false;
            homeAttention = "none";
        }
        applyHomeAttention(homeAttention);
        applyBackupsAttention(restoreAttention);
        applyMobileToggleAttention(homeAttention, restoreAttention);
    }

    refreshNavAttention();
    navAttentionTimer = window.setInterval(refreshNavAttention, 10000);
    document.addEventListener("visibilitychange", () => {
        if (!document.hidden) {
            refreshNavAttention();
        }
    });
    window.addEventListener("beforeunload", () => {
        if (navAttentionTimer) {
            window.clearInterval(navAttentionTimer);
            navAttentionTimer = null;
        }
    });
})();
