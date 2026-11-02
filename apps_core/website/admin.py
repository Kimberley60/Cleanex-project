from django.contrib import admin
from .models import HeroSection, HeroSlide, ProcessStep


# Register your models here.


class HeroSlideInline(admin.StackedInline):
    model = HeroSlide

    extra = 0

    fieldsets = (
        (
            "Content",
            {
                "fields": (
                    "title",
                    "subtitle",
                    "image",
                )
            },
        ),
        (
            "Primary CTA",
            {
                "fields": (
                    "primary_button_text",
                    "primary_button_url",
                )
            },
        ),
        (
            "Secondary CTA",
            {
                "fields": (
                    "secondary_button_text",
                    "secondary_button_url",
                )
            },
        ),
        (
            "Settings",
            {
                "fields": (
                    "display_order",
                    "is_active",
                )
            },
        ),
    )


@admin.register(HeroSection)
class HeroSectionAdmin(admin.ModelAdmin):

    list_display = (
        "name",
        "page",
        "autoplay",
        "autoplay_speed",
        "is_active",
    )

    list_filter = (
        "page",
        "is_active",
    )

    search_fields = (
        "name",
    )

    inlines = [
        HeroSlideInline,
    ]

    fieldsets = (
        (
            "Section Information",
            {
                "fields": (
                    "name",
                    "page",
                )
            },
        ),
        (
            "Carousel Settings",
            {
                "fields": (
                    "autoplay",
                    "autoplay_speed",
                )
            },
        ),
        (
            "Status",
            {
                "fields": (
                    "is_active",
                )
            },
        ),
    )



@admin.register(HeroSlide)
class HeroSlideAdmin(admin.ModelAdmin):

    list_display = (
        "title",
        "hero_section",
        "display_order",
        "is_active",
    )

    list_filter = (
        "hero_section",
        "is_active",
    )

    search_fields = (
        "title",
        "subtitle",
    )

    list_editable = (
        "display_order",
        "is_active",
    )

    ordering = (
        "hero_section",
        "display_order",
    )



@admin.register(ProcessStep)
class ProcessStepAdmin(admin.ModelAdmin):
    list_display = (
        "step_number",
        "title",
        "is_active",
    )

    fields = (
        "step_number",
        "title",
        "description",
        "icon",
        "image",
        "is_active",
    )

    ordering = ("step_number",)