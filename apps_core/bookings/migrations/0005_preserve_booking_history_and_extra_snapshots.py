"""Reversing this migration removes snapshot columns and their values."""

from decimal import Decimal

import django.db.models.deletion
from django.db import migrations, models


def backfill_booking_extra_snapshots(apps, schema_editor):
    BookingExtra = apps.get_model("bookings", "BookingExtra")
    ExtraService = apps.get_model("services", "ExtraService")
    database = schema_editor.connection.alias

    for booking_extra in (
        BookingExtra.objects.using(database)
        .iterator()
    ):
        try:
            if booking_extra.extra_service_id is None:
                raise RuntimeError(
                    f"Cannot backfill BookingExtra #{booking_extra.pk}: "
                    "it has no ExtraService reference."
                )
            extra_service = booking_extra.extra_service
        except ExtraService.DoesNotExist as error:
            raise RuntimeError(
                f"Cannot backfill BookingExtra #{booking_extra.pk}: "
                f"ExtraService #{booking_extra.extra_service_id} does not exist."
            ) from error

        if extra_service is None:
            raise RuntimeError(
                f"Cannot backfill BookingExtra #{booking_extra.pk}: "
                f"ExtraService #{booking_extra.extra_service_id} does not exist."
            )

        booking_extra.extra_service_name = extra_service.name
        booking_extra.extra_service_price = extra_service.price
        booking_extra.save(
            using=database,
            update_fields=[
                "extra_service_name",
                "extra_service_price",
            ],
        )


class Migration(migrations.Migration):

    dependencies = [
        ("bookings", "0004_booking_created_by"),
        ("customers", "0003_customer_county_alter_customer_user_and_more"),
        ("services", "0006_servicefaq_alter_servicefeature_options_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="booking",
            name="customer",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="bookings",
                to="customers.customer",
            ),
        ),
        migrations.AlterField(
            model_name="bookingextra",
            name="extra_service",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                to="services.extraservice",
            ),
        ),
        migrations.AddField(
            model_name="bookingextra",
            name="extra_service_name",
            field=models.CharField(default="", max_length=100),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="bookingextra",
            name="extra_service_price",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0.00"),
                max_digits=8,
            ),
            preserve_default=False,
        ),
        migrations.RunPython(
            backfill_booking_extra_snapshots,
            migrations.RunPython.noop,
        ),
    ]
