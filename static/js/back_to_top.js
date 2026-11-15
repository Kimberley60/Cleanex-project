document.addEventListener("DOMContentLoaded", () => {
    const backToTop = document.querySelector(".back-to-top");

    if (!backToTop) {
        return;
    }

    const SCROLL_THRESHOLD = 600;
    const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    let ticking = false;

    const updateVisibility = () => {
        const scrollY = window.scrollY || document.documentElement.scrollTop;
        backToTop.classList.toggle("is-visible", scrollY > SCROLL_THRESHOLD);
        ticking = false;
    };

    window.addEventListener(
        "scroll",
        () => {
            if (!ticking) {
                window.requestAnimationFrame(updateVisibility);
                ticking = true;
            }
        },
        { passive: true }
    );

    backToTop.addEventListener("click", () => {
        window.scrollTo({
            top: 0,
            left: 0,
            behavior: motionQuery.matches ? "auto" : "smooth",
        });
        backToTop.blur();
    });

    updateVisibility();
});
