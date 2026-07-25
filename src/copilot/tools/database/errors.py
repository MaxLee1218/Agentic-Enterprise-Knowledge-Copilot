"""Safe database-adapter failures without SQL or connection detail leakage."""


class DatabaseAdapterError(Exception):
    """Base class for bounded database implementation failures."""


class DatabaseConfigurationError(DatabaseAdapterError):
    """The configured database cannot safely support the requested operation."""


class DatabaseConnectionError(DatabaseAdapterError):
    """A database connection or execution operation failed."""


class DatabaseSchemaNotFoundError(DatabaseAdapterError):
    """The configured database does not contain the registered demo schema."""


class DatabaseStatementTimeoutError(DatabaseAdapterError):
    """The database cancelled a statement after its configured deadline."""


class DatabaseQueryValidationError(DatabaseAdapterError):
    """An internal query violates the registered read-only schema."""
