(function () {
    const PANE_ANIMATION_CLASSES = [
        "pane-slide-in-x",
        "pane-slide-out-x",
        "pane-slide-in-y",
        "pane-slide-out-y",
    ];
    const MAKE_WAY_ANIMATION_CLASSES = ["pane-make-way-x", "pane-make-way-y"];

    function clearClasses(target, classNames) {
        if (!target) return;
        classNames.forEach((name) => target.classList.remove(name));
    }

    function playAnimationClass(target, className, options) {
        if (!target || !className) return;
        const keepClassOnEnd = !!(options && options.keepClassOnEnd);
        target.classList.add(className);
        const cleanup = () => {
            if (!keepClassOnEnd) {
                target.classList.remove(className);
            }
            target.removeEventListener("animationend", cleanup);
        };
        target.addEventListener("animationend", cleanup);
    }

    function axis(isStacked) {
        return isStacked ? "y" : "x";
    }

    window.MCWebPaneAnimations = {
        clearPaneAnimation(target) {
            clearClasses(target, PANE_ANIMATION_CLASSES);
        },
        playPaneAnimation(target, direction, isStacked, options) {
            if (!target) return;
            clearClasses(target, PANE_ANIMATION_CLASSES);
            const className = `pane-slide-${direction}-${axis(!!isStacked)}`;
            playAnimationClass(target, className, options || {});
        },
        clearMakeWayAnimation(target) {
            clearClasses(target, MAKE_WAY_ANIMATION_CLASSES);
        },
        playMakeWayAnimation(target, isStacked) {
            if (!target) return;
            clearClasses(target, MAKE_WAY_ANIMATION_CLASSES);
            const className = `pane-make-way-${axis(!!isStacked)}`;
            playAnimationClass(target, className, {});
        },
        floatPaneForClose(target) {
            if (!target) return;
            const rect = target.getBoundingClientRect();
            target.style.display = "flex";
            target.style.position = "fixed";
            target.style.left = `${Math.round(rect.left)}px`;
            target.style.top = `${Math.round(rect.top)}px`;
            target.style.width = `${Math.round(rect.width)}px`;
            target.style.height = `${Math.round(rect.height)}px`;
            target.style.margin = "0";
            target.style.zIndex = "1400";
        },
        clearFloatingPaneStyles(target) {
            if (!target) return;
            target.style.position = "";
            target.style.left = "";
            target.style.top = "";
            target.style.width = "";
            target.style.height = "";
            target.style.margin = "";
            target.style.zIndex = "";
            target.style.display = "";
        },
    };
})();

