"""Response models of the LMI API.

There is one module per resource, plus :mod:`lmi_api.schemas.common` for the
payloads more than one resource returns. The models are deliberately separate
from the ORM classes: the API shape is the contract the dashboard and the
generated client depend on, and it must not follow an internal column rename.
"""
