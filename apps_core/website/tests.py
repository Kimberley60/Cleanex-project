from django.test import TestCase
from django.urls import reverse

from .models import HeroSection, HeroSlide


class HeroSlideBookingUrlTests(TestCase):
    def test_legacy_book_path_resolves_to_named_booking_url(self):
        slide = HeroSlide(primary_button_url="/book/")

        self.assertEqual(slide.primary_button_href(), reverse("booking"))

    def test_booking_path_resolves_to_named_booking_url(self):
        slide = HeroSlide(primary_button_url="/booking/")

        self.assertEqual(slide.primary_button_href(), reverse("booking"))

    def test_other_urls_are_preserved(self):
        slide = HeroSlide(primary_button_url="/services/")

        self.assertEqual(slide.primary_button_href(), "/services/")

    def test_blank_url_is_preserved(self):
        slide = HeroSlide(primary_button_url="")

        self.assertEqual(slide.primary_button_href(), "")


class HomeHeroBookingButtonTests(TestCase):
    def test_home_hero_book_now_uses_booking_url(self):
        hero_section = HeroSection.objects.create(
            name="Home Hero",
            page="home",
            is_active=True,
        )
        HeroSlide.objects.create(
            hero_section=hero_section,
            title="Clean Homes",
            subtitle="Professional cleaning",
            image="hero/test.png",
            primary_button_text="Book Now",
            primary_button_url="/book/",
            is_active=True,
        )

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Book Now")
        self.assertContains(response, f'href="{reverse("booking")}"')
        self.assertNotContains(response, 'href="/book/"')
