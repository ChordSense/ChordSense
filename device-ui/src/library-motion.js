// Short interaction-only effects; nothing animates while the Library is idle.
export function createLibraryMotion(preference) {
    const active = new Map();

    function cancel(element) {
        const previous = active.get(element);
        if (previous) {
            active.delete(element);
            previous.cancel();
        }
    }

    function cancelAll() {
        for (const element of active.keys()) cancel(element);
    }

    function enter(element, offset, duration) {
        cancel(element);
        if (preference.matches || !element?.animate) return;
        const animation = element.animate([
            { opacity: 0, transform: offset },
            { opacity: 1, transform: "translate(0, 0)" },
        ], { duration, easing: "ease-out", iterations: 1 });
        active.set(element, animation);
        const cleanup = () => {
            if (active.get(element) === animation) active.delete(element);
        };
        animation.onfinish = cleanup;
        animation.oncancel = cleanup;
    }

    preference.addEventListener("change", () => {
        if (preference.matches) cancelAll();
    });

    return {
        open: element => enter(element, "translateY(10px)", 180),
        tab: (element, recordings) => enter(element, `translateX(${recordings ? 10 : -10}px)`, 140),
        cancel,
        cancelAll,
    };
}
