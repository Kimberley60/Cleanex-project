from django.urls import path
from .views import ServiceListView, ServiceDetailView

urlpatterns = [
    path("", ServiceListView.as_view(), name="services"),
    path("<slug:slug>/", ServiceDetailView.as_view(), name="service_detail"),
]