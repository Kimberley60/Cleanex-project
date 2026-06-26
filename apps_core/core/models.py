from django.db import models



# Why Choose Us Section
class WhyChooseSection(models.Model):

    title = models.CharField(
        max_length=200
    )

    subtitle = models.TextField()

    primary_button_text = models.CharField(
        max_length=50
    )

    primary_button_url = models.CharField(
        max_length=255
    )

    is_active = models.BooleanField(
        default=True
    )

    def __str__(self):
        return self.title


# Why Choose Us Feature

class WhyChooseFeature(models.Model):

    title = models.CharField(
        max_length=100
    )

    description = models.TextField()

    icon = models.CharField(
        max_length=50,
        help_text="Bootstrap icon name"
    )

    display_order = models.PositiveIntegerField(
        default=0
    )

    is_active = models.BooleanField(
        default=True
    )

    class Meta:
        ordering = ["display_order"]

    def __str__(self):
        return self.title


# Company Statistics Section
class CompanyStatistic(models.Model):

    value = models.CharField(
        max_length=50
    )

    label = models.CharField(
        max_length=100
    )

    display_order = models.PositiveIntegerField(
        default=0
    )

    is_active = models.BooleanField(
        default=True
    )

    class Meta:
        ordering = ["display_order"]

    def __str__(self):
        return self.label


# Testimonial Section
class TestimonialSection(models.Model):

    title = models.CharField(
        max_length=200
    )

    subtitle = models.TextField()

    is_active = models.BooleanField(
        default=True
    )

    def __str__(self):
        return self.title



class Testimonial(models.Model):

    customer_name = models.CharField(
        max_length=100
    )

    customer_location = models.CharField(
        max_length=100,
        blank=True
    )

    review = models.TextField()

    rating = models.PositiveSmallIntegerField(
        default=5
    )

    customer_image = models.ImageField(
        upload_to="testimonials/",
        blank=True,
        null=True
    )

    service_name = models.CharField(
        max_length=100,
        blank=True
    )

    featured = models.BooleanField(
        default=True
    )

    display_order = models.PositiveIntegerField(
        default=0
    )

    is_active = models.BooleanField(
        default=True
    )

    class Meta:
        ordering = ["display_order"]

    def __str__(self):
        return self.customer_name