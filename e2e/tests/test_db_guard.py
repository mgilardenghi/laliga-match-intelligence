"""The browser tests' database helper refuses every project but a throwaway one.

The helper writes to the database, and a test can be run without `make`, so its
own guard is the last defence for the developer's `lmi` stack. The cases need no
Docker: a refused name fails before any command is built.
"""

import pytest
from db import psql


@pytest.mark.empty
@pytest.mark.parametrize(
    "project",
    ["", "lmi", "LMI", " lmi", "lmi-e2e ", "lmi-e2e;ls", "other", "lmi-e2e-", "lmi_e2e"],
)
def test_the_database_helper_refuses_a_project_that_is_not_a_throwaway(
    monkeypatch: pytest.MonkeyPatch, project: str
) -> None:
    monkeypatch.setenv("E2E_PROJECT", project)

    with pytest.raises(pytest.fail.Exception, match="not a throwaway project name"):
        psql("SELECT 1;")
