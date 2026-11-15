"""The model registry: every public model name and a lazy constructor.

The names are declared up front so that a task adding a model only writes its
own module and never edits another task's file. Importing a model is deferred to
:func:`make_model`, so a name whose module does not exist yet raises
``ModuleNotFoundError`` instead of breaking the import of this package.
"""

import importlib
from collections.abc import Mapping
from typing import Final, Literal

from lmi_core.modeling.base import Model

MODEL_NAMES: Final[tuple[str, ...]] = (
    "home_win",
    "base_rate",
    "market",
    "elo",
    "dixon_coles",
)
"""Every model name, in report order."""

ModelKind = Literal["baseline", "market", "model"]

MODEL_KINDS: Final[Mapping[str, ModelKind]] = {
    "home_win": "baseline",
    "base_rate": "baseline",
    "market": "market",
    "elo": "model",
    "dixon_coles": "model",
}

_TARGETS: Final[Mapping[str, str]] = {
    "home_win": "lmi_core.modeling.baselines:HomeWinModel",
    "base_rate": "lmi_core.modeling.baselines:BaseRateModel",
    "market": "lmi_core.modeling.market:MarketModel",
    "elo": "lmi_core.modeling.elo:EloModel",
    "dixon_coles": "lmi_core.modeling.dixon_coles:DixonColesModel",
}


def make_model(name: str, *, seed: int) -> Model:
    """Return a fresh model for ``name``.

    Raises:
        ValueError: ``name`` is not a registered model name.
        ModuleNotFoundError: the module of a registered model does not exist
            yet. The three models that are not baselines are added by later
            tasks, which is why the import is lazy.
    """
    target = _TARGETS.get(name)
    if target is None:
        known = ", ".join(MODEL_NAMES)
        raise ValueError(f"unknown model {name!r}; known models are {known}")
    module_name, _, class_name = target.partition(":")
    module = importlib.import_module(module_name)
    model_class: type[Model] = getattr(module, class_name)
    return model_class(seed=seed)
