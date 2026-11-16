from django.db import migrations, models
from django.utils import timezone


def seed_mpesa_query_throttle(apps, schema_editor):
    MpesaQueryThrottle = apps.get_model(
        "payments",
        "MpesaQueryThrottle",
    )
    MpesaQueryThrottle.objects.get_or_create(
        pk=1,
        defaults={
            "window_started_at": timezone.now(),
            "request_count": 0,
            "blocked_until": None,
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0005_mpesacallbackresult"),
    ]

    operations = [
        migrations.CreateModel(
            name="MpesaQueryThrottle",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("window_started_at", models.DateTimeField()),
                (
                    "request_count",
                    models.PositiveIntegerField(default=0),
                ),
                (
                    "blocked_until",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True),
                ),
            ],
            options={
                "verbose_name": "M-Pesa query throttle",
                "verbose_name_plural": "M-Pesa query throttle",
            },
        ),
        migrations.RunPython(
            seed_mpesa_query_throttle,
            migrations.RunPython.noop,
        ),
    ]
