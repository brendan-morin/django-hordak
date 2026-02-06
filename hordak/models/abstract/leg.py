import warnings
from typing import Tuple

from django.db import models
from django.db.models import DecimalField, F
from django.db.models.functions import Coalesce
from django.utils.translation import gettext_lazy as _
from djmoney.models.fields import MoneyField
from moneyed import Money

from hordak import exceptions
from hordak.defaults import (
    DECIMAL_PLACES,
    MAX_DIGITS,
    UUID_DEFAULT,
    get_internal_currency, ACCOUNT_MODEL, TRANSACTION_MODEL,
)
from hordak.utilities.currency import Balance
from hordak.utilities.db_functions import GetBalance
from hordak.utils import AccountType, mysql_simulate_trigger

#: Debit
DEBIT = "debit"
#: Credit
CREDIT = "credit"


class LegQuerySet(models.QuerySet):
    """Utilities available to querysets of Legs"""

    def sum_to_debit_and_credit(self) -> Tuple[Balance, Balance]:
        """Sum the Legs of the QuerySet to get balance objects for both credits and debits

        Example:

            >>> total_debits, total_credits = Leg.objects.sum_to_debit_and_credit()
        """
        result = self.values("currency").annotate(
            total_credit=Coalesce(models.Sum("credit"), 0, output_field=DecimalField()),
            total_debit=Coalesce(models.Sum("debit"), 0, output_field=DecimalField()),
        )
        credits = Balance([Money(r["total_credit"], r["currency"]) for r in result])
        debits = Balance([Money(r["total_debit"], r["currency"]) for r in result])

        return credits, debits

    def sum_to_balance(self, account_type=None):
        """Sum the Legs of the QuerySet to get a single :class:`Balance` object

        Specifying ``account_type`` for the account will ensure the resulting
        balance is signed (ie +/-) correctly. Otherwise this method
        will perform an additional database query to determine the account
        type as best it can (and will issue a warning if it fails).

        Example:

            >>> balance = Leg.objects.sum_to_balance()
        """
        credits, debits = self.sum_to_debit_and_credit()

        if not account_type:
            results = self.order_by().values("account__type").distinct()
            account_types = [AccountType(r["account__type"]) for r in results]
            if len(account_types) == 1:
                account_type = account_types[0]

        if not account_type and credits != debits:
            # If we cannot determine an account type and the result is non-zero
            # then we should warn the user that they may get an unexpected sign
            warnings.warn(
                f"Could not auto-determine account type for the current queryset in sum_to_balance() "
                f"(we found account types {account_types} for the selected legs). "
                f"This may result in an unexpected sign on the returned balance. We recommend you "
                f"provide sum_to_balance(account_type=...) to avoid this ambiguity."
            )

        if account_type in (AccountType.asset, AccountType.expense):
            return debits - credits
        else:
            return credits - debits

    def with_account_balance_after(self):
        """Get the balance of the account associated with each leg following the transaction

        Annotate the queryset with the `account_balance_after` property. This is the account
        balance following after the leg happened. Useful for rendering account statements.

        Example:

            >>> legs = my_account.legs.with_account_balance_after()
            >>> for leg in legs:
            >>>     print(f"{leg.transaction.date} {leg.type_short} {leg.amount} {leg.balance_after}")
            2000-01-01 CR €100.00 €100.00
            2000-01-01 CR €10.00 €110.00
        """
        return self.annotate(
            account_balance_after=GetBalance(
                F("account_id"),
                as_of=F("transaction__date"),
                as_of_leg_id=F("id"),
            )
        )

    def with_account_balance_before(self):
        """Get the balance of the account associated with each leg prior to the transaction

        Annotate the queryset with the `account_balance_before` property. This is the account
        balance before after the leg happened.

        Example:

            >>> legs = my_account.legs.with_account_balance_before()
            >>> for leg in legs:
            >>>     print(f"{leg.transaction.date} {leg.type_short} {leg.amount} {leg.balance_before}")
            2000-01-01 CR €100.00 €0.00
            2000-01-01 CR €10.00 €100.00
        """
        return self.annotate(
            account_balance_before=GetBalance(
                F("account_id"),
                as_of=F("transaction__date"),
                as_of_leg_id=F("id") - 1,
            )
        )

    def debits(self):
        """Filter for legs that are debits"""
        return self.filter(debit__isnull=False)

    def credits(self):
        """Filter for legs that are credits"""
        return self.filter(credit__isnull=False)


class LegManager(models.Manager):
    def get_by_natural_key(self, uuid):
        return self.get(uuid=uuid)


CustomLegManager = LegManager.from_queryset(LegQuerySet)


class AbstractLeg(models.Model):
    """The leg of a transaction

    Represents a single amount either into or out of a transaction. All legs for a transaction
    must sum to zero, all legs must be of the same currency.

    Attributes:

        uuid (UUID): UUID for transaction leg. Use to prevent leaking of IDs (if desired).
        transaction (Transaction): Transaction to which the Leg belongs.
        account (Account): Account the leg is transferring to/from.
        amount (Money): The amount being transferred
        description (str): Optional user-provided description
        type (str): :attr:`hordak.models.DEBIT` or :attr:`hordak.models.CREDIT`.
        account_balance_after (Balance): The account balance before this transaction.
            Only populated when account is queried using `Leg.objects.with_account_balance_after()`
        account_balance_before (Balance): The account balance after this transaction.
            Only populated when account is queried using `Leg.objects.with_account_balance_before()`
    """

    uuid = models.UUIDField(
        default=UUID_DEFAULT, editable=False, verbose_name=_("uuid")
    )
    transaction = models.ForeignKey(
        TRANSACTION_MODEL,
        related_name="legs",
        on_delete=models.CASCADE,
        verbose_name=_("transaction"),
    )
    account = models.ForeignKey(
        ACCOUNT_MODEL,
        related_name="legs",
        on_delete=models.CASCADE,
        verbose_name=_("account"),
    )
    credit = MoneyField(
        max_digits=MAX_DIGITS,
        decimal_places=DECIMAL_PLACES,
        help_text="Amount of this credit, or NULL if not a credit",
        default_currency=get_internal_currency,
        currency_field_name="currency",
        verbose_name=_("credit amount"),
        default=None,
        null=True,
        blank=True,
    )
    debit = MoneyField(
        max_digits=MAX_DIGITS,
        decimal_places=DECIMAL_PLACES,
        help_text="Amount of this debit, or NULL if not a debit",
        default_currency=get_internal_currency,
        currency_field_name="currency",
        verbose_name=_("debit amount"),
        default=None,
        null=True,
        blank=True,
    )
    description = models.TextField(
        default="", blank=True, verbose_name=_("description")
    )

    objects = CustomLegManager()

    def __str__(self):
        return (
            f"{self.type.title()} {self.account.name} "
            f"({self.account.full_code}) {self.amount} {self.type_short}"
        )

    def __init__(self, *args, amount: Money = None, **kwargs):
        if amount is not None:
            warnings.warn(
                "Specifying `amount` when creating a Leg is deprecated. "
                "Instead specify either the `credit` argument (for what would would previously be "
                "a positive amount) or `debit` (for what would previously be a negative amount). "
                "Both these arguments should be positive `Money` values. This warning will become an "
                "error in Hordak 3.0.",
                DeprecationWarning,
            )
            if amount.amount > 0:
                kwargs["credit"] = amount
                kwargs["debit"] = None
            else:
                kwargs["credit"] = None
                kwargs["debit"] = abs(amount)

        super().__init__(*args, **kwargs)

    def save(self, *args, **kwargs):
        if self.credit is not None and self.credit.amount == 0:
            raise exceptions.ZeroAmountError("Cannot credit account by zero")
        if self.debit is not None and self.debit.amount == 0:
            raise exceptions.ZeroAmountError("Cannot debit account by zero")
        if self.debit is None and self.credit is None:
            raise exceptions.NeitherCreditNorDebitPresentError(
                "Either credit or debit must be set"
            )
        if self.debit is not None and self.credit is not None:
            raise exceptions.BothCreditAndDebitPresentError(
                "Either credit or debit must be set"
            )
        if self.credit is not None and self.credit.amount < 0:
            raise exceptions.CreditOrDebitIsNegativeError(
                f"Credit is negative: {self.credit} "
            )
        if self.debit is not None and self.debit.amount < 0:
            raise exceptions.CreditOrDebitIsNegativeError(
                f"Debit is negative: {self.debit} "
            )

        leg = super().save(*args, **kwargs)
        mysql_simulate_trigger("check_leg", self.id, self.transaction_id)
        return leg

    def natural_key(self):
        return (self.uuid,)

    @property
    def type(self):
        if self.debit:
            return DEBIT
        elif self.credit:
            return CREDIT
        else:
            # This should have been caught earlier by the database integrity check.
            # If you are seeing this then something is wrong with your DB checks.
            raise exceptions.InvalidOrMissingAccountTypeError()

    @property
    def type_short(self):
        if self.type == DEBIT:
            return "DR"
        else:
            return "CR"

    @property
    def amount(self) -> Money:
        return self.credit or self.debit

    def is_debit(self):
        return self.type == DEBIT

    def is_credit(self):
        return self.type == CREDIT

    class Meta:
        verbose_name = _("Leg")