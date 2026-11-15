from django.urls import path
from .views import (
    QuoteSuccessView,
    QuoteView,
    accept_customer_quote,
    decline_customer_quote,
)

urlpatterns = [
    path("quote/", QuoteView.as_view(), name="quote"),
    path(
        "quote/success/",
        QuoteSuccessView.as_view(),
        name="quote_success",
    ),
    path(
        "quote/<int:quote_id>/accept/",
        accept_customer_quote,
        name="accept_customer_quote",
    ),
    path(
        "quote/<int:quote_id>/decline/",
        decline_customer_quote,
        name="decline_customer_quote",
    ),
]
