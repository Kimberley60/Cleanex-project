document.addEventListener("DOMContentLoaded", () => {

    const carousel = document.querySelector(".hero-carousel");

    if (!carousel) return;

    const slides = carousel.querySelectorAll(".hero-slide");
    const dots = carousel.querySelectorAll(".hero-dot");

    const autoplay =
        carousel.dataset.autoplay === "true";

    const speed =
        parseInt(carousel.dataset.speed) || 5000;

    let currentIndex = 0;

    function showSlide(index) {

        slides.forEach(slide => {
            slide.classList.remove("active");
        });

        dots.forEach(dot => {
            dot.classList.remove("active");
        });

        slides[index].classList.add("active");

        if (dots[index]) {
            dots[index].classList.add("active");
        }

        currentIndex = index;
    }

    function nextSlide() {

        let next =
            (currentIndex + 1) % slides.length;

        showSlide(next);
    }

    dots.forEach((dot, index) => {

        dot.addEventListener("click", () => {
            showSlide(index);
        });

    });

    if (autoplay && slides.length > 1) {

        setInterval(() => {
            nextSlide();
        }, speed);

    }

});