from django.urls import path
from .views import QuoteView

urlpatterns = [
    path("quote/", QuoteView.as_view(), name="quote"),
]