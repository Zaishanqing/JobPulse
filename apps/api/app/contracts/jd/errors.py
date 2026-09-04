class UnsupportedSchemaVersion(ValueError):
    def __init__(self, schema_type: str, version: str):
        self.schema_type = schema_type
        self.version = version
        super().__init__(f"Unsupported {schema_type} schema version: {version}")
