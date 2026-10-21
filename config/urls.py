from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),

    path('', include("apps_core.core.urls")),

    # services
    path("services/", include("apps_core.services.urls")),

    # bookings
    path("", include("apps_core.bookings.urls")),

    # quotes
    path("", include("apps_core.quotes.urls")),

    # gallery
    path("", include("apps_core.gallery.urls")),

    path("payments/", include("apps_core.payments.urls")),



]

# Serve media files during development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
