from django.db import models
from django.db.models import JSONField
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from hordak.defaults import UUID_DEFAULT, ACCOUNT_MODEL
from hordak.utils import json_default


class StatementImportManager(models.Manager):
    def get_by_natural_key(self, uuid):
        return self.get(uuid=uuid)


class AbstractStatementImport(models.Model):
    """Records an import of a bank statement

    Attributes:

        uuid (UUID): UUID for statement import. Use to prevent leaking of IDs (if desired).
        timestamp (datetime): The datetime when the object was created.
        bank_account (Account): The account the import is for (should normally point to an asset
            account which represents your bank account)

    """

    uuid = models.UUIDField(
        default=UUID_DEFAULT, editable=False, verbose_name=_("uuid")
    )
    timestamp = models.DateTimeField(default=timezone.now, verbose_name=_("timestamp"))
    # TODO: Add constraint to ensure destination account expects statements (copy 0007)
    bank_account = models.ForeignKey(
        ACCOUNT_MODEL,
        related_name="imports",
        on_delete=models.CASCADE,
        verbose_name=_("bank account"),
    )
    source = models.CharField(
        max_length=20,
        help_text="A value uniquely identifying where this data came from. "
        'Examples: "csv", "teller.io".',
        verbose_name=_("source"),
    )
    extra = JSONField(
        default=json_default,
        help_text="Any extra data relating to the import, probably specific "
        "to the data source.",
        verbose_name=_("extra"),
    )

    objects = StatementImportManager()

    def natural_key(self):
        return (self.uuid,)

    class Meta:
        verbose_name = _("statementImport")
        abstract = True
