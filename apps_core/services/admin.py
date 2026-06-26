from django.contrib import admin
from .models import Service, ServiceFeature, ExtraService


# Register your models here.

class ServiceFeatureInline(admin.TabularInline):
    model = ServiceFeature
    extra = 1


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "base_price",
        "duration_hours",
        "is_active",
        "created_at",
    )

    list_filter = (
        "is_active",
        "created_at",
    )

    search_fields = (
        "name",
        "description",
    )

    prepopulated_fields = {"slug": ("name",)}

    ordering = ("-created_at",)

    inlines = [ServiceFeatureInline]


@admin.register(ExtraService)
class ExtraServiceAdmin(admin.ModelAdmin):
    list_display = ("name", "price")
    search_fields = ("name",)



