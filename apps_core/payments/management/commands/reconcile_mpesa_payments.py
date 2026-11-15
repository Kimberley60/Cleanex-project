import logging
import time
from django.core.management.base import BaseCommand, CommandError
from apps_core.payments.models import MpesaCallbackMetadata, Payment
from apps_core.payments.services import (
    reconcile_callback_metadata,
    verify_payment,
)

logger = logging.getLogger(__name__)


DEFAULT_LIMIT = 100
MAX_LIMIT = 500


class Command(BaseCommand):
    help = (
        "Reconcile unresolved M-Pesa payments and pending callback "
        "metadata using STK Push Query."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=DEFAULT_LIMIT,
            help=(
                f"Maximum payments and pending callback metadata records "
                f"to process per selection (default {DEFAULT_LIMIT}, "
                f"maximum {MAX_LIMIT})."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help=(
                "Count eligible payments and callback metadata records "
                "without querying M-Pesa."
            ),
        )
        parser.add_argument(
            "--payment-id",
            type=int,
            default=None,
            help="Reconcile only the payment with this primary key.",
        )
        parser.add_argument(
            "--delay",
            type=float,
            default=1.0,
            help=(
                "Seconds to wait after each provider verification attempt "
                "(default 1.0; use 0 to disable)."
            ),
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        payment_id = options["payment_id"]
        delay = options["delay"]

        if limit < 1 or limit > MAX_LIMIT:
            raise CommandError(
                f"--limit must be between 1 and {MAX_LIMIT}."
            )

        if payment_id is not None and payment_id < 1:
            raise CommandError("--payment-id must be a positive integer.")

        if delay < 0:
            raise CommandError("--delay must be greater than or equal to 0.")

        pending_metadata_payment_ids = MpesaCallbackMetadata.objects.filter(
            status="pending"
        ).values("payment_id")

        payment_queryset = (
            Payment.objects
            .filter(
                status__in=["processing", "unknown"],
            )
            .exclude(checkout_request_id__isnull=True)
            .exclude(checkout_request_id="")
            .exclude(pk__in=pending_metadata_payment_ids)
        )
        metadata_queryset = (
            MpesaCallbackMetadata.objects
            .filter(
                status="pending",
                payment__status__in=["processing", "unknown", "paid"],
            )
            .select_related("payment")
        )

        if payment_id is not None:
            payment_queryset = payment_queryset.filter(pk=payment_id)
            metadata_queryset = metadata_queryset.filter(
                payment_id=payment_id
            )

        payments = list(
            payment_queryset.order_by("created_at", "pk")[:limit]
        )
        metadata_records = list(
            metadata_queryset.order_by("received_at", "pk")[:limit]
        )

        if options["dry_run"]:
            self.stdout.write(
                f"Eligible payments in selected batch: {len(payments)}. "
                f"Eligible callback metadata records in selected batch: "
                f"{len(metadata_records)}. "
                "Dry run: no provider queries were made and no payment "
                "records were changed. Callback metadata records were "
                "not changed."
            )
            return

        checked = paid = failed = unresolved = skipped = 0

        for payment in payments:
            checked += 1
            previous_attempt_count = payment.verification_attempt_count

            try:
                result = verify_payment(payment)
            except Exception:
                unresolved += 1
                logger.exception(
                    "Unexpected error reconciling payment %s.",
                    payment.pk,
                )
                continue

            if result.status == "paid":
                paid += 1
            elif result.status == "failed":
                failed += 1
            elif getattr(result, "_verification_attempted", True) is False:
                skipped += 1
            else:
                unresolved += 1

            if (
                isinstance(result, Payment)
                and result.verification_attempt_count
                > previous_attempt_count
                and delay
            ):
                time.sleep(delay)

        metadata_checked = 0
        metadata_applied = 0
        metadata_pending = 0
        metadata_failed = 0
        metadata_errors = 0

        for record in metadata_records:
            metadata_checked += 1
            previous_attempt_count = record.attempt_count

            try:
                result = reconcile_callback_metadata(record)
            except Exception:
                metadata_errors += 1
                logger.exception(
                    "Unexpected error reconciling callback metadata %s.",
                    record.pk,
                )
                continue

            if result.status == "applied":
                metadata_applied += 1
            elif result.status == "failed":
                metadata_failed += 1
            else:
                metadata_pending += 1

            if result.attempt_count > previous_attempt_count and delay:
                time.sleep(delay)

        self.stdout.write(
            "Reconciliation complete: "
            f"checked={checked}, paid={paid}, failed={failed}, "
            f"unresolved={unresolved}, skipped={skipped}, "
            "callback_metadata="
            f"checked:{metadata_checked} applied:{metadata_applied} "
            f"pending:{metadata_pending} failed:{metadata_failed} "
            f"errors:{metadata_errors}."
        )
