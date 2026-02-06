from django.db import transaction, connection

from django import models
from django.utils.translation import gettext_lazy as _
from djmoney.settings import CURRENCY_CHOICES, DEFAULT_CURRENCY


def json_default():
    return {}


def get_currency_choices():
    return CURRENCY_CHOICES


class AccountType(models.TextChoices):
    # Eg. Cash in bank
    asset = "AS", _("Asset")
    # Eg. Loans, bills paid after the fact (in arrears)
    liability = "LI", _("Liability")
    # Eg. Sales, housemate contributions
    income = "IN", _("Income")
    # Eg. Office supplies, paying bills
    expense = "EX", _("Expense")
    # Eg. Money from shares
    equity = "EQ", _("Equity")
    # Used to represent currency conversions
    trading = "TR", _("Currency Trading")


def account_default_currencies():
    return (DEFAULT_CURRENCY,)


def mysql_simulate_trigger(proc_name, *args):
    # MySQL/MariaDB does not support deferred constraint triggers (unlike postgres),
    # and also does not support triggers updating the table they are triggered from.
    # So this function allows us to trigger manual function calls on transaction finish.
    # Enforcing this at the application level is not idea. If this is important to you
    # then use postgres.
    # (https://stackoverflow.com/a/15300941/1908381)
    def _mysql_call_proc():
        with connection.cursor() as curs:
            curs.callproc(proc_name, args)

    if connection.vendor == "mysql":
        with connection.cursor():
            transaction.on_commit(_mysql_call_proc)
