from django.db.models import Avg
from apps_core.website.models import ProcessStep
from apps_core.core.models import (
    WhyChooseSection,
    WhyChooseFeature,
    CompanyStatistic,
    TestimonialSection,
    Testimonial,
    FAQCategory,
    FAQ,
)




def website_content(request):
    """
    Provides shared, CMS-managed website content to templates.

    This context processor is intended for content that can be
    reused across multiple public-facing pages.

    Page-specific data should remain inside the relevant view.
    """

    return {
        # =====================================================
        # HOW IT WORKS
        # =====================================================

        "process_steps": ProcessStep.objects.filter(
            is_active=True
        ),

        # =====================================================
        # WHY CHOOSE CLEANEX
        # =====================================================

        "why_choose": WhyChooseSection.objects.filter(
            is_active=True
        ).first(),

        "why_features": WhyChooseFeature.objects.filter(
            is_active=True
        ),

        "statistics": CompanyStatistic.objects.filter(
            is_active=True
        ),



        # =====================================================
        # TESTIMONIALS
        # =====================================================

        "testimonial_section": TestimonialSection.objects.filter(
            is_active=True
        ).first(),

        "testimonials": Testimonial.objects.filter(
            is_active=True,
            featured=True,
        ),

        "testimonial_average": Testimonial.objects.filter(
            is_active=True,
            featured=True,
        ).aggregate(
            average=Avg("rating")
        )["average"],





        # =====================================================
        # FAQ
        # =====================================================

        "faq_categories": FAQCategory.objects.filter(
            is_active=True,
        ),

        "faqs": FAQ.objects.filter(
            is_active=True,
        ).select_related(
            "category",
        ),
    }

