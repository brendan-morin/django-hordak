from datetime import date

from django.db import connection, models
from django.db import transaction as db_transaction
from django.db.models import Case, DecimalField, F, JSONField, Sum, When
from django.db.models.functions import Coalesce
from django.utils.translation import gettext_lazy as _
from djmoney.settings import CURRENCY_CHOICES
from moneyed import CurrencyDoesNotExist, Money
from mptt.models import MPTTModel, TreeForeignKey, TreeManager
from django.apps import apps

from hordak import exceptions
from hordak.defaults import (
    DEFAULT_CURRENCY,
    UUID_DEFAULT,
    ACCOUNT_MODEL, TRANSACTION_MODEL, LEG_MODEL,
)

from hordak.utilities.currency import Balance
from hordak.utilities.db_functions import GetBalance
from hordak.utilities.dreprecation import deprecated
from hordak.utils import mysql_simulate_trigger, AccountType, account_default_currencies


class AccountQuerySet(models.QuerySet):
    """Utilities available to querysets of Accounts"""

    def net_balance(self):
        """Get the total balance of all accounts in this queryset"""
        # TODO: Do aggregation of JSONB balance structures in custom db function.
        #       Will avoid having to pull all accounts back.
        return sum((account.balance for account in self.with_balances()), Balance())

    def with_balances(
        self,
        to_field_name="balance",
        as_of: date = None,
        as_of_leg_id: int = None,
    ):
        """Annotate the account queryset with account balances

        This is a much more performant way to calculate account balances,
        especially when calculating balances for a lot of accounts.

        You can get the balance at a particular point in time by specifying
        ``as_of`` and (optionally) ``as_of_leg_id``.

        Note that you will get better performance by setting the ``as_of``
        to ``None`` (the default). This is because the underlying custom database function
        can avoid a join.

        Example:

            >>> # Will execute in a single database query
            >>> for account in Account.objects.with_balances():
            >>>     print(account.balance)
        """
        field = GetBalance(F("id"), as_of=as_of, as_of_leg_id=as_of_leg_id)
        return self.annotate(
            **{
                to_field_name: field,
            }
        )

    def with_balances_orm(self, to_field_name="balance"):
        calculation = Sum(
            Coalesce("legs__credit", 0, output_field=DecimalField())
            - Coalesce("legs__debit", 0, output_field=DecimalField())
        )
        sign = Case(When(type__in=("AS", "EX"), then=-1), default=1)
        return self.annotate(
            **{
                to_field_name: calculation * sign,
            }
        )


class AccountManager(TreeManager):
    def get_by_natural_key(self, uuid):
        return self.get(uuid=uuid)



class AbstractAccount(MPTTModel):
    """Represents an account

    An account may have a parent, and may have zero or more children. Only root
    accounts can have a type, all child accounts are assumed to have the same
    type as their parent.

    An account's balance is calculated as the sum of all of the transaction Leg's
    referencing the account.

    Attributes:

        uuid (UUID): UUID for account. Use to prevent leaking of IDs (if desired).
        name (str): Name of the account. Required.
        parent (Account|None): Parent account, nonen if root account
        balance (Balance): Account balance, only populated when account is queried using
            ``Account.objects.with_balances()``
        code (str): Account code. Must combine with account codes of parent
            accounts to get fully qualified account code.
        type (str): Type of account as defined by ``AccountType``. Can only be set on
            root accounts. Child accounts are assumed to have the same time as their parent.
        is_bank_account (bool): Is this a bank account. This implies we can import bank statements into
            it and that it only supports a single currency.


    """

    # Warning: Will be removed in Hordak 3. Use AccountType directly instead.
    TYPES = AccountType

    uuid = models.UUIDField(
        default=UUID_DEFAULT, editable=False, verbose_name=_("uuid")
    )
    name = models.CharField(max_length=255, verbose_name=_("name"))
    parent = TreeForeignKey(
        ACCOUNT_MODEL,
        null=True,
        blank=True,
        related_name="children",
        db_index=True,
        on_delete=models.CASCADE,
        verbose_name=_("parent"),
    )
    code = models.CharField(max_length=6, null=True, blank=True, verbose_name=_("code"))
    full_code = models.CharField(
        max_length=255,
        db_index=True,
        unique=True,
        null=True,
        blank=True,
        verbose_name=_("full_code"),
    )
    # TODO: Implement this child_code_width field, as it is probably a good idea
    # child_code_width = models.PositiveSmallIntegerField(default=1)
    type = models.CharField(
        max_length=2, choices=AccountType.choices, blank=True, verbose_name=_("type")
    )
    is_bank_account = models.BooleanField(
        default=False,
        blank=True,
        help_text="Is this a bank account. This implies we can import bank "
        "statements into it and that it only supports a single currency",
        verbose_name=_("is bank account"),
    )
    currencies = JSONField(
        db_index=True,
        default=account_default_currencies,
        verbose_name=_("currencies"),
    )

    objects = AccountManager.from_queryset(AccountQuerySet)()

    class MPTTMeta:
        order_insertion_by = ["code"]

    class Meta:
        unique_together = (("parent", "code"),)
        verbose_name = _("account")
        abstract = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._initial_code = self.code

    def save(self, *args, **kwargs):
        is_creating = not bool(self.pk)
        if is_creating:
            update_fields = None
        else:
            # See issues #19 & #31. It seems that on Django 1.2, django-mptt's left/right
            # tree fields get overwritten on save. The solution here is to exclude them from
            # being modified upon saving by using the save methods' update_fields argument.
            update_fields = [
                "uuid",
                "name",
                "parent",
                "code",
                "type",
                "is_bank_account",
                "currencies",
            ]

        super().save(*args, update_fields=update_fields, **kwargs)

        if connection.vendor == "mysql":
            # We need updated lft/rght/tree_id values for the mysql_run_manual_trigger() call
            self.refresh_from_db()

        mysql_simulate_trigger(
            "update_full_account_codes", self.lft, self.rght, self.tree_id
        )

        do_refresh = False

        # If we've just created a non-root node then we're going to need to load
        # the type back from the DB (as it is set by trigger)
        if is_creating and not self.is_root_node():
            do_refresh = True

        # If we've just create this account or if the code has changed then we're
        # going to need to reload from the DB (full_code is set by trigger)
        if is_creating or self._initial_code != self.code:
            do_refresh = True

        if do_refresh:
            self.refresh_from_db()

    @classmethod
    def validate_accounting_equation(cls):
        """Check that all accounts sum to 0"""
        accounts = cls.objects.root_nodes().with_balances()
        balances = [a.balance * a.sign for a in accounts]

        if sum(balances, Balance()) != 0:
            raise exceptions.AccountingEquationViolationError(
                "Account balances do not sum to zero. They sum to {}".format(
                    sum(balances)
                )
            )

    def __str__(self):
        name = self.name or "Unnamed Account"
        if self.is_leaf_node():
            try:
                balance = self.get_balance()
            except (ValueError, CurrencyDoesNotExist):
                if self.full_code:
                    return "{} {}".format(self.full_code, name)
                else:
                    return name
            else:
                if self.full_code:
                    return "{} {} [{}]".format(self.full_code, name, balance)
                else:
                    return "{} [{}]".format(name, balance)

        else:
            return name

    def natural_key(self):
        return (self.uuid,)

    @property
    def sign(self):
        """
        Returns 1 if a credit should increase the value of the
        account, or -1 if a credit should decrease the value of the
        account.

        This is based on the account type as is standard accounting practice.
        The signs can be derrived from the following expanded form of the
        accounting equation:

            Assets = Liabilities + Equity + (Income - Expenses)

        Which can be rearranged as:

            0 = Liabilities + Equity + Income - Expenses - Assets

        Further details here: https://en.wikipedia.org/wiki/Debits_and_credits

        """
        return -1 if self.type in (AccountType.asset, AccountType.expense) else 1

    def get_balance(self, as_of=None, leg_query=None, **kwargs):
        """Get the balance for this account, including child accounts

        .. note::

            Note that we recommend using :meth:`AccountQuerySet.with_balances()` where possible
            as it will almost certainly be more performant when fetching balances
            for multiple accounts.

        Args:
            as_of (Date): Only include transactions on or before this date
            kwargs (dict): Will be used to filter the transaction legs

        Returns:
            Balance

        See Also:
            :meth:`get_simple_balance()`
        """
        if "raw" in kwargs:
            raise DeprecationWarning(
                "The `raw` parameter to Account.get_balance() is no longer available."
            )
        balances = [
            account.get_simple_balance(as_of=as_of, leg_query=leg_query, **kwargs)
            for account in self.get_descendants(include_self=True)
        ]
        return sum(balances, Balance())

    def get_simple_balance(self, as_of=None, leg_query=None, **kwargs):
        """Get the balance for this account, ignoring all child accounts

        Args:
            as_of (Date): Only include transactions on or before this date
            raw (bool): If true the returned balance should not have its sign
                        adjusted for display purposes.
            leg_query (models.Q): Django Q-expression, will be used to filter the transaction legs.
                                  allows for more complex filtering than that provided by ``**kwargs``.
            kwargs (dict): Will be used to filter the transaction legs

        Returns:
            Balance
        """
        if "raw" in kwargs:
            raise DeprecationWarning(
                "The `raw` parameter to Account.get_simple_balance() is no longer available."
            )
        legs = self.legs
        if as_of:
            legs = legs.filter(transaction__date__lte=as_of)

        if leg_query or kwargs:
            leg_query = leg_query or models.Q()
            legs = legs.filter(leg_query, **kwargs)

        return legs.sum_to_balance(account_type=self.type) + self._zero_balance()

    def _zero_balance(self):
        """Get a balance for this account with all currencies set to zero"""
        return Balance([Money("0", currency) for currency in self.currencies])

    @db_transaction.atomic()
    def transfer_to(self, to_account, amount, **transaction_kwargs):
        """Create a transaction which credits self and debits ``to_account``.

        See https://en.wikipedia.org/wiki/Double-entry_bookkeeping.

        This is a shortcut utility method which simplifies the process of
        transferring where ``self`` is Cr and ``to_account`` is Dr.

        For example:

          * Transferring income -> income will result in the former increasing and the latter decreasing
          * Transferring income -> asset (i.e. bank) will result in the balance of both increasing
          * Transferring asset -> asset will result in the former decreasing and the latter increasing

        .. note::

            .. code-block::

                      LHS                          RHS
                {asset | expense} <-> {income | liability | equity}

                Transfers LHS (A) -> RHS (B) will decrease A and increase B
                Transfers LHS (A) -> LHS (B) will decrease A and increase B
                Transfers RHS (A) -> LHS (B) will increase A and increase B
                Transfers RHS (A) -> RHS (B) will increase A and decrease B

        Args:

            to_account (Account): The destination account.
            amount (Money): The amount to be transferred.
            transaction_kwargs: Passed through to transaction creation. Useful for setting the
                transaction ``description`` or ``date`` fields.
        """
        if not isinstance(amount, Money):
            raise TypeError("amount must be of type Money")

        transaction = apps.get_model(TRANSACTION_MODEL).objects.create(**transaction_kwargs)

        apps.get_model(LEG_MODEL).objects.create(transaction=transaction, account=self, credit=amount)
        apps.get_model(LEG_MODEL).objects.create(transaction=transaction, account=to_account, debit=amount)

        return transaction

    @deprecated(
        "accounting_transfer_to() has been renamed to transfer_to(). Update your "
        "code to call transfer_to() directly. This will become an error in Hordak 3."
    )
    def accounting_transfer_to(self, *args, **kwargs):
        return self.transfer_to(*args, **kwargs)
