from django.contrib import admin
from .models import WhyChooseSection,WhyChooseFeature,CompanyStatistic,TestimonialSection,Testimonial


@admin.register(WhyChooseSection)
class WhyChooseSectionAdmin(admin.ModelAdmin):
    pass

@admin.register(WhyChooseFeature)
class WhyChooseFeatureAdmin(admin.ModelAdmin):

    list_display = (
        "title",
        "display_order",
        "is_active"
    )

    list_editable = (
        "display_order",
        "is_active"
    )


@admin.register(CompanyStatistic)
class CompanyStatisticAdmin(admin.ModelAdmin):

    list_display = (
        "label",
        "value",
        "display_order"
    )

    list_editable = (
        "display_order",
    )


# Testimonial Section

@admin.register(TestimonialSection)
class TestimonialSectionAdmin(admin.ModelAdmin):
    pass

@admin.register(Testimonial)
class TestimonialAdmin(admin.ModelAdmin):

    list_display = (
        "customer_name",
        "service_name",
        "featured",
        "display_order",
        "is_active"
    )

    list_editable = (
        "featured",
        "display_order",
        "is_active"
    )