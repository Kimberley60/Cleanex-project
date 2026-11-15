from django.urls import path
from .views import (
    check_booking_payment_status,
    initiate_booking_payment,
    mpesa_callback,
)


app_name = "payments"

urlpatterns = [
    path(
        "booking/<int:booking_id>/initiate/",
        initiate_booking_payment,
        name="initiate_booking_payment",
    ),
    path(
        "booking/<int:booking_id>/status/",
        check_booking_payment_status,
        name="check_booking_payment_status",
    ),
    path(
        "mpesa/callback/",
        mpesa_callback,
        name="mpesa_callback",
    ),
]
