from datetime import date

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from hordak.defaults import UUID_DEFAULT


class TransactionManager(models.Manager):
    def get_by_natural_key(self, uuid):
        return self.get(uuid=uuid)


class AbstractTransaction(models.Model):
    """Represents a transaction

    A transaction is a movement of funds between two accounts. Each transaction
    will have two or more legs, each leg specifies an account and an amount.

    .. note:

        When working with Hordak Transaction objects you will typically need to do so
        within a database transaction. This is because the database has integrity checks in
        place to ensure the validity of the transaction (i.e. money in = money out).

    See Also:

        :meth:`Account.transfer_to()` is a useful shortcut to avoid having to create transactions manually.

    Examples:

        You can manually create a transaction as follows::

            from django.db import transaction as db_transaction
            from hordak.models import Transaction, Leg

            with db_transaction.atomic():
                transaction = Transaction.objects.create()
                Leg.objects.create(transaction=transaction, account=my_account1, amount=Money(100, 'EUR'))
                Leg.objects.create(transaction=transaction, account=my_account2, amount=Money(-100, 'EUR'))

    Attributes:

        uuid (models.UUIDField): UUID for transaction. Use to prevent leaking of IDs (if desired).
        timestamp (datetime): The datetime when the object was created.
        date (date): The date when the transaction actually occurred, as this may be different to
            :attr:`timestamp`.
        description (str): Optional user-provided description

    """

    uuid = models.UUIDField(
        default=UUID_DEFAULT, editable=False, verbose_name=_("uuid")
    )
    timestamp = models.DateTimeField(
        default=timezone.now,
        help_text="The creation date of this transaction object",
        verbose_name=_("timestamp"),
    )
    date = models.DateField(
        default=timezone.now,
        help_text="The date on which this transaction occurred",
        verbose_name=_("date"),
    )
    description = models.TextField(
        default="", blank=True, verbose_name=_("description")
    )

    objects = TransactionManager()

    class Meta:
        get_latest_by = "date"
        verbose_name = _("transaction")

    def get_balance(self):
        return self.legs.sum_to_balance()

    def natural_key(self):
        return (self.uuid,)
