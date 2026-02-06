from datetime import date

from django.db import connection, models
from django.db import transaction
from django.db import transaction as db_transaction
from django.db.models import JSONField
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.apps import apps

from hordak.defaults import (
    DECIMAL_PLACES,
    MAX_DIGITS,
    UUID_DEFAULT,
    ACCOUNT_MODEL, TRANSACTION_MODEL, STATEMENT_IMPORT_MODEL, LEG_MODEL,
)
from hordak.utils import json_default


class StatementLineManager(models.Manager):
    def get_by_natural_key(self, uuid):
        return self.get(uuid=uuid)


class AbstractStatementLine(models.Model):
    """Records a single imported bank statement line

    A StatementLine is purely a utility to aid in the creation of transactions
    (in the process known as reconciliation). StatementLines have no impact on
    account balances.

    However, the :meth:`StatementLine.create_transaction()` method can be used to create
    a transaction based on the information in the StatementLine.

    Attributes:

        uuid (UUID): UUID for statement line. Use to prevent leaking of IDs (if desired).
        timestamp (datetime): The datetime when the object was created.
        date (date): The date given by the statement line
        statement_import (StatementImport): The import to which the line belongs
        amount (Decimal): The amount for the statement line, positive or negative.
        description (str): Any description/memo information provided
        transaction (Transaction): Optionally, the transaction created for this statement line. This normally
            occurs during reconciliation. See also :meth:`StatementLine.create_transaction()`.
    """

    uuid = models.UUIDField(
        default=UUID_DEFAULT, editable=False, verbose_name=_("uuid")
    )
    timestamp = models.DateTimeField(default=timezone.now, verbose_name=_("timestamp"))
    date = models.DateField(verbose_name=_("date"))
    statement_import = models.ForeignKey(
        STATEMENT_IMPORT_MODEL,
        related_name="lines",
        on_delete=models.CASCADE,
        verbose_name=_("statement import"),
    )
    amount = models.DecimalField(
        max_digits=MAX_DIGITS, decimal_places=DECIMAL_PLACES, verbose_name=_("amount")
    )
    description = models.TextField(
        default="", blank=True, verbose_name=_("description")
    )
    type = models.CharField(max_length=50, default="", verbose_name=_("type"))
    # TODO: Add constraint to ensure transaction amount = statement line amount
    # TODO: Add constraint to ensure one statement line per transaction
    transaction = models.ForeignKey(
        TRANSACTION_MODEL,
        default=None,
        blank=True,
        null=True,
        help_text="Reconcile this statement line to this transaction",
        on_delete=models.SET_NULL,
        verbose_name=_("transaction"),
    )
    source_data = JSONField(
        default=json_default,
        help_text="Original data received from the data source.",
        verbose_name=_("source data"),
    )

    objects = StatementLineManager()

    def natural_key(self):
        return (self.uuid,)

    @property
    def is_reconciled(self):
        """Has this statement line been reconciled?

        Determined as ``True`` if :attr:`transaction` has been set.

        Returns:
            bool: ``True`` if reconciled, ``False`` if not.
        """
        return bool(self.transaction)

    @db_transaction.atomic()
    def create_transaction(self, to_account):
        """Create a transaction for this statement amount and account, into to_account

        This will also set this StatementLine's ``transaction`` attribute to the newly
        created transaction.

        Args:
            to_account (Account): The account the transaction is into / out of.

        Returns:
            Transaction: The newly created (and committed) transaction.

        """
        from_account = self.statement_import.bank_account

        transaction = apps.get_model(TRANSACTION_MODEL).objects.create()
        if self.amount > 0:
            apps.get_model(LEG_MODEL).objects.create(
                transaction=transaction, account=from_account, debit=self.amount
            )
            apps.get_model(LEG_MODEL).objects.create(
                transaction=transaction, account=to_account, credit=self.amount
            )
        else:
            apps.get_model(LEG_MODEL).objects.create(
                transaction=transaction, account=from_account, credit=abs(self.amount)
            )
            apps.get_model(LEG_MODEL).objects.create(
                transaction=transaction, account=to_account, debit=abs(self.amount)
            )

        transaction.date = self.date
        transaction.save()

        self.transaction = transaction
        self.save()
        return transaction

    class Meta:
        verbose_name = _("statementLine")
