(function (global) {
    function syncVerticalScrollbarClass(target) {
        if (!target) return;
        const hasVerticalScrollbar = target.scrollHeight > target.clientHeight + 1;
        target.classList.toggle("has-vscroll", hasVerticalScrollbar);
    }

    function watchVerticalScrollbarClass(target, options = {}) {
        if (!target) return () => {};
        syncVerticalScrollbarClass(target);

        const cleanupFns = [];
        const handleScroll = () => syncVerticalScrollbarClass(target);
        const handleResize = () => syncVerticalScrollbarClass(target);

        target.addEventListener("scroll", handleScroll, { passive: true });
        cleanupFns.push(() => target.removeEventListener("scroll", handleScroll));

        global.addEventListener("resize", handleResize);
        cleanupFns.push(() => global.removeEventListener("resize", handleResize));

        if (global.ResizeObserver) {
            const resizeObserver = new ResizeObserver(() => syncVerticalScrollbarClass(target));
            resizeObserver.observe(target);
            cleanupFns.push(() => resizeObserver.disconnect());
        }

        if (options.observeMutations && global.MutationObserver) {
            const mutationObserver = new MutationObserver(() => syncVerticalScrollbarClass(target));
            mutationObserver.observe(target, { childList: true, subtree: true, characterData: true });
            cleanupFns.push(() => mutationObserver.disconnect());
        }

        return () => {
            cleanupFns.splice(0).reverse().forEach((fn) => {
                try {
                    fn();
                } catch (_) {
                    // Ignore observer/listener teardown failures.
                }
            });
        };
    }

    global.MCWebDomUtils = Object.assign({}, global.MCWebDomUtils || {}, {
        syncVerticalScrollbarClass,
        watchVerticalScrollbarClass,
    });
})(window);
