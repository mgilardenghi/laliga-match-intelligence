"""Team payloads."""

from pydantic import BaseModel, ConfigDict


class Team(BaseModel):
    """One club as the API returns it.

    ``id`` is the stable identifier a client uses to link to a team; ``name`` is
    the canonical spelling stored in the database, not a source-specific alias.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
