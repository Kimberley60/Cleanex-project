from decimal import Decimal


def calculate_quote(
    service,
    property_size,
    rooms,
    bathrooms,
    extras=None
):
    """
    Quote price estimation logic
    """

    price = Decimal(service.base_price)

    # room pricing
    price += Decimal(rooms) * Decimal("400")

    # bathroom pricing
    price += Decimal(bathrooms) * Decimal("300")

    # property size pricing
    price += Decimal(property_size) * Decimal("1.5")

    # extras text parsing (simple version)
    if extras:
        price += Decimal("1000")

    return price