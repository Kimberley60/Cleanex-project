document.addEventListener('DOMContentLoaded', () => {
    const navbar = document.querySelector('.navbar');

    let lastScrollTop = 0;

    window.addEventListener('scroll', () => {
        const currentScroll =
            window.pageYOffset || document.documentElement.scrollTop;

        // Apply scrolled styling
        if (currentScroll > 50) {
            navbar.classList.add('scrolled');
        } else {
            navbar.classList.remove('scrolled');
        }

        // Always show at very top
        if (currentScroll <= 50) {
            navbar.classList.remove('navbar-hidden');
        }
        // Scrolling down
        else if (currentScroll > lastScrollTop) {
            navbar.classList.add('navbar-hidden');
        }
        // Scrolling up
        else {
            navbar.classList.remove('navbar-hidden');
        }

        lastScrollTop = currentScroll <= 0 ? 0 : currentScroll;
    });
});