document.addEventListener("DOMContentLoaded", () => {
    const serviceDetailPage = document.querySelector(
        ".service-detail-hero"
    );

    if (!serviceDetailPage) {
        return;
    }

    initSmoothSectionNavigation();
});


function initSmoothSectionNavigation() {
    const navigationLinks = document.querySelectorAll(
        ".service-detail-nav a[href^='#']"
    );

    if (!navigationLinks.length) {
        return;
    }

    navigationLinks.forEach((link) => {
        link.addEventListener("click", (event) => {
            const targetId = link.getAttribute("href");

            if (!targetId || targetId === "#") {
                return;
            }

            const target = document.querySelector(targetId);

            if (!target) {
                return;
            }

            event.preventDefault();

            target.scrollIntoView({
                behavior: window.matchMedia(
                    "(prefers-reduced-motion: reduce)"
                ).matches
                    ? "auto"
                    : "smooth",
                block: "start",
            });

            history.replaceState(
                null,
                "",
                targetId
            );
        });
    });
}

