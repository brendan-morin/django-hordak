"""
Design Overview
---------------

The core models consist of:

- ``Account`` - Such as 'Accounts Receivable', a bank account, etc.
  Accounts can be arranged as a tree structure,
  where the balance of the parent account is the summation of the balances of all its children.
- ``Transaction`` - Represents a movement between accounts. Each transaction must have two or more legs.
- ``Leg`` - Represents a flow of money into (debit) or out of (credit) a transaction.
  Debits are represented by negative amounts, and credits by positive amounts.
  The sum of all a transaction's legs must equal zero.
  This is enforced with a database constraint.

Additionally, there are models which related to the import of external bank statement data:

- ``StatementImport`` - Represents a simple import of zero or more statement
  lines relating to a specific ``Account``.
- ``StatementLine`` - Represents a statement line. ``StatementLine.create_transaction()`` may be called to
  create a transaction for the statement line.
"""

from datetime import date

from django.db import connection, models
from django.db import transaction
from django.db import transaction as db_transaction
from django.db.models import JSONField
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from djmoney.settings import CURRENCY_CHOICES
from hordak.defaults import (
    DECIMAL_PLACES,
    MAX_DIGITS,
    UUID_DEFAULT,
)
from hordak.models.abstract.account import AbstractAccount
from hordak.models.abstract.leg import AbstractLeg
from hordak.models.abstract.statement_import import AbstractStatementImport
from hordak.models.abstract.statement_line import AbstractStatementLine
from hordak.models.abstract.transaction import AbstractTransaction


def json_default():
    return {}


def get_currency_choices():
    return CURRENCY_CHOICES


class Account(AbstractAccount):
    """
    Concrete model for AbstractAccount. Usage requires 'hordak' be added to INSTALLED_APPS, which will result in hordak
    managing models and migrations outside of user apps.

    See AbstractAccount for full model details.
    """
    pass


class Transaction(AbstractTransaction):
    pass


class Leg(AbstractLeg):
    pass


class StatementImport(AbstractStatementImport):
    pass


class StatementLine(AbstractStatementLine):
    pass

