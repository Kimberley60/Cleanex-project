
document.addEventListener('DOMContentLoaded', () => {
    const navbar = document.querySelector('.navbar');

    // Stop safely if this page has no navbar.
    if (!navbar) {
        return;
    }

    const SCROLL_THRESHOLD = 50;
    let lastScrollTop = window.scrollY;
    let ticking = false;

    const updateNavbar = () => {
        const currentScroll = Math.max(
            0,
            window.scrollY || document.documentElement.scrollTop
        );

        // Apply the refined scrolled appearance.
        navbar.classList.toggle(
            'scrolled',
            currentScroll > SCROLL_THRESHOLD
        );

        // Keep the navbar visible at the top of the page.
        if (currentScroll <= SCROLL_THRESHOLD) {
            navbar.classList.remove('navbar-hidden');
        } else if (currentScroll > lastScrollTop) {
            // Hide while scrolling down.
            navbar.classList.add('navbar-hidden');
        } else if (currentScroll < lastScrollTop) {
            // Reveal while scrolling up.
            navbar.classList.remove('navbar-hidden');
        }

        lastScrollTop = currentScroll;
        ticking = false;
    };

    // Limit repeated updates during rapid scrolling.
    window.addEventListener(
        'scroll',
        () => {
            if (!ticking) {
                window.requestAnimationFrame(updateNavbar);
                ticking = true;
            }
        },
        { passive: true }
    );

    // Set the correct initial state if the page opens mid-scroll.
    updateNavbar();
});