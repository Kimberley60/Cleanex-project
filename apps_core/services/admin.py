from django.contrib import admin
from .models import (
    Service,
    ServiceCategory,
    ServiceFeature,
    ServiceFAQ,
    ExtraService,
)


class ServiceFeatureInline(admin.TabularInline):
            model = ServiceFeature
            extra = 1

            fields = (
                "title",
                "description",
                "icon",
                "display_order",
                "is_active",
            )

            ordering = (
                "display_order",
                "id",
            )


class ServiceFAQInline(admin.StackedInline):
            model = ServiceFAQ
            extra = 0

            fields = (
                "question",
                "answer",
                "display_order",
                "is_active",
            )

            ordering = (
                "display_order",
                "id",
            )


@admin.register(ServiceCategory)
class ServiceCategoryAdmin(admin.ModelAdmin):

            list_display = (
                "name",
                "slug",
                "display_order",
                "is_active",
                "updated_at",
            )

            list_editable = (
                "display_order",
                "is_active",
            )

            list_filter = (
                "is_active",
            )

            search_fields = (
                "name",
                "slug",
                "description",
            )

            prepopulated_fields = {
                "slug": ("name",),
            }

            ordering = (
                "display_order",
                "name",
            )


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):

            list_display = (
                "name",
                "category",
                "base_price",
                "price_per_hour",
                "duration_hours",
                "is_featured",
                "is_active",
                "created_at",
            )

            list_filter = (
                "category",
                "is_active",
                "is_featured",
                "created_at",
            )

            search_fields = (
                "name",
                "slug",
                "short_description",
                "description",
            )

            prepopulated_fields = {
                "slug": ("name",),
            }

            ordering = (
                "-created_at",
            )

            fieldsets = (
                (
                    "Basic Information",
                    {
                        "fields": (
                            "name",
                            "slug",
                            "category",
                            "short_description",
                            "description",
                        ),
                    },
                ),
                (
                    "Pricing & Duration",
                    {
                        "fields": (
                            "base_price",
                            "price_per_hour",
                            "duration_hours",
                        ),
                    },
                ),
                (
                    "Service Media",
                    {
                        "fields": (
                            "image",
                            "icon",
                        ),
                    },
                ),
                (
                    "Service Detail Hero",
                    {
                        "fields": (
                            "hero_image",
                            "hero_title",
                            "hero_subtitle",
                            "primary_button_text",
                            "primary_button_url",
                            "secondary_button_text",
                            "secondary_button_url",
                        ),
                    },
                ),
                (
                    "Display & Status",
                    {
                        "fields": (
                            "display_order",
                            "is_active",
                            "is_featured",
                        ),
                    },
                ),
            )

            inlines = [
                ServiceFeatureInline,
                ServiceFAQInline,
            ]


@admin.register(ServiceFeature)
class ServiceFeatureAdmin(admin.ModelAdmin):

            list_display = (
                "title",
                "service",
                "display_order",
                "is_active",
            )

            list_filter = (
                "is_active",
                "service",
            )

            search_fields = (
                "title",
                "description",
                "service__name",
            )

            ordering = (
                "service",
                "display_order",
                "id",
            )


@admin.register(ServiceFAQ)
class ServiceFAQAdmin(admin.ModelAdmin):

            list_display = (
                "question",
                "service",
                "display_order",
                "is_active",
            )

            list_filter = (
                "is_active",
                "service",
            )

            search_fields = (
                "question",
                "answer",
                "service__name",
            )

            ordering = (
                "service",
                "display_order",
                "id",
            )


@admin.register(ExtraService)
class ExtraServiceAdmin(admin.ModelAdmin):

            list_display = (
                "name",
                "price",
                "is_active",
            )

            list_filter = (
                "is_active",
            )

            search_fields = (
                "name",
            )

            ordering = (
                "name",
            )

