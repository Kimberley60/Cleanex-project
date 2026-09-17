from django.contrib import admin
from .models import (
    WhyChooseSection,
    WhyChooseFeature,
    CompanyStatistic,
    TestimonialSection,
    Testimonial,
    FAQCategory,
    FAQ,
)


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


class FAQInline(admin.StackedInline):
    model = FAQ

    extra = 0

    fieldsets = (
        (
            "Content",
            {
                "fields": (
                    "question",
                    "answer",
                )
            },
        ),
        (
            "Settings",
            {
                "fields": (
                    "display_order",
                    "is_featured",
                    "is_active",
                )
            },
        ),
    )


@admin.register(FAQCategory)
class FAQCategoryAdmin(admin.ModelAdmin):

    list_display = (
        "name",
        "slug",
        "display_order",
        "is_active",
        "updated_at",
    )

    list_filter = (
        "is_active",
    )

    search_fields = (
        "name",
        "slug",
    )

    list_editable = (
        "display_order",
        "is_active",
    )

    prepopulated_fields = {
        "slug": ("name",),
    }

    ordering = (
        "display_order",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
    )

    inlines = [
        FAQInline,
    ]

    fieldsets = (
        (
            "Category Information",
            {
                "fields": (
                    "name",
                    "slug",
                )
            },
        ),
        (
            "Display Settings",
            {
                "fields": (
                    "display_order",
                    "is_active",
                )
            },
        ),
        (
            "Timestamps",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )


@admin.register(FAQ)
class FAQAdmin(admin.ModelAdmin):

    list_display = (
        "question",
        "category",
        "is_featured",
        "display_order",
        "is_active",
        "updated_at",
    )

    list_filter = (
        "category",
        "is_active",
        "is_featured",
    )

    search_fields = (
        "question",
        "answer",
    )

    list_editable = (
        "is_featured",
        "display_order",
        "is_active",
    )

    ordering = (
        "display_order",
        "id",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
    )

    fieldsets = (
        (
            "Content",
            {
                "fields": (
                    "category",
                    "question",
                    "answer",
                )
            },
        ),
        (
            "Display Settings",
            {
                "fields": (
                    "display_order",
                    "is_featured",
                    "is_active",
                )
            },
        ),
        (
            "Timestamps",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )