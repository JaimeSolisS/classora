"""The import pipeline (#122): CSV text in a JSON body, parsed in the request
into staging rows, reviewed, then committed by a worker in batches. Kinds
register through ``administration.service.register_import_kind``."""
