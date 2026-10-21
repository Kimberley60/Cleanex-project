from django.urls import path
from .views import initiate_booking_payment, mpesa_callback


app_name = "payments"

urlpatterns = [
    path(
        "booking/<int:booking_id>/initiate/",
        initiate_booking_payment,
        name="initiate_booking_payment",
    ),
    path(
        "mpesa/callback/",
        mpesa_callback,
        name="mpesa_callback",
    ),
]
