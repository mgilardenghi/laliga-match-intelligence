"""Season payloads."""

from pydantic import BaseModel, ConfigDict


class Season(BaseModel):
    """One league season as the API returns it.

    Only the two fields a client sorts or displays are exposed. ``code`` is the
    human-readable identifier, for example ``2025-26``, and ``start_year`` is
    the calendar year the season began in, which orders the seasons.
    """

    model_config = ConfigDict(from_attributes=True)

    code: str
    start_year: int
